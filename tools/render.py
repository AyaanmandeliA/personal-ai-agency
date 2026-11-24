#!/usr/bin/env python3
"""Portable renderer for the agency's edit spec. Needs only ffmpeg, Python, Pillow and numpy,
so it runs in a Claude cloud session (laptop off) as well as on the Mac.

  render.py <spec.json>            render to <work>/renders/<name>.mp4, plus a frame sheet and a report
  render.py <spec.json> --check    validate the spec and list what this renderer cannot do, render nothing

The spec is the creative record; this file is one way to execute it (Edits on the Mac is the other).
Spec fields (times in seconds on the edited timeline, positions 0-1 from the top-left, x/y = centre):
  name, work, fps (30), palette {name: "#hex"}, grade {...}, skin {...}, gain, voice [{"type":"highpass","hz":90}], vignette 0-1,
  clips  [{"file","in","out","zoom":[[t,scale,(y)],...],"grade":{...}}]
  assets [{"kind":"word","file","text","font","size","color","track","strike","shadow"}]   (other images: put a PNG in <work>/assets)
  captions [[t0,t1,"text"], ...]  + caption_style {"font","px","y","color","pill","upper","track"}
  elements [{"type":"image","asset","x","y","scale","t0","t1","motion"|"keyframes","opacity","rotate"},
            {"type":"text","text","font","px","color","pill","x","y","t0","t1","motion"},
            {"type":"line","x","y","w","h","color","t0","t1"},
            {"type":"sfx","file","t","vol"}]
Anything it cannot do is listed in the report under "unsupported" and is never silently replaced.
"""
import json, os, sys, glob, math, subprocess
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H = 1080, 1920
FONT_DIRS = [d for d in (os.environ.get("AGENCY_FONT_DIR"), os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts"),
                         "/System/Library/Fonts/Supplemental", "/usr/share/fonts") if d]
FONTS = {  # key: (file name fragments to look for, variable-font weight or None)
    "instrument": (["InstrumentSerif-Regular"], None), "instrument-italic": (["InstrumentSerif-Italic"], None),
    "playfair": (["PlayfairDisplay-wght", "PlayfairDisplay[wght]", "PlayfairDisplay-Regular"], 600),
    "playfair-italic": (["PlayfairDisplay-Italic"], 600), "playfair-black-italic": (["PlayfairDisplay-Italic"], 900),
    "cormorant": (["CormorantGaramond-wght", "CormorantGaramond[wght]", "CormorantGaramond-SemiBold"], 600),
    "cormorant-italic": (["CormorantGaramond-Italic"], 600), "cormorant-bold-italic": (["CormorantGaramond-Italic"], 700),
    "jost": (["Jost-wght", "Jost[wght]", "Jost-Medium"], 500), "cinzel": (["Cinzel-wght", "Cinzel[wght]", "Cinzel-Regular"], 500),
    "cinzel-bold": (["Cinzel-wght", "Cinzel[wght]", "Cinzel-Bold"], 700), "dmserif": (["DMSerifDisplay-Regular"], None),
    "dmserif-italic": (["DMSerifDisplay-Italic"], None), "didot-italic": (["Didot", "PlayfairDisplay-Italic"], None),
}
_font_cache, UNSUPPORTED = {}, []


def font(key, px):
    k = (key, int(px))
    if k in _font_cache: return _font_cache[k]
    frags, wght = FONTS.get(key, ([key], None))
    path = None
    for d in FONT_DIRS:
        for frag in frags:
            hits = [p for p in glob.glob(os.path.join(d, "**", "*"), recursive=True) if frag.lower() in os.path.basename(p).lower() and p.lower().endswith((".ttf", ".otf", ".ttc"))]
            if hits: path = sorted(hits, key=len)[0]; break
        if path: break
    if not path:
        UNSUPPORTED.append(f"font '{key}' not installed here; DejaVu Serif used instead")
        path = next(iter(glob.glob("/usr/share/fonts/**/DejaVuSerif.ttf", recursive=True) + glob.glob("/System/Library/Fonts/Supplemental/Georgia.ttf")), None)
    f = ImageFont.truetype(path, int(px), index=1 if (key == "didot-italic" and path.endswith(".ttc")) else 0) if path else ImageFont.load_default()
    if wght and path:
        try: f.set_variation_by_axes([wght])
        except Exception: pass
    _font_cache[k] = f
    return f


def rgba(v, pal, a=255):
    v = pal.get(v, v).lstrip("#")
    return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4)) + ((int(v[6:8], 16),) if len(v) == 8 else (a,))


def draw_text_image(text, fkey, px, color, pal, track=0, shadow=True, strike=None, pill=None, upper=False):
    if upper: text = text.upper()
    f = font(fkey, px); d = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    widths = [d.textlength(ch, font=f) + track for ch in text]
    asc, desc = f.getmetrics(); padx, pady = int(px * 0.45) + 20, int(px * 0.3) + 16
    w, h = int(sum(widths)) + 2 * padx, asc + desc + 2 * pady
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    def put(layer, dx, dy, fill):
        x = padx + dx; dr = ImageDraw.Draw(layer)
        for ch, cw in zip(text, widths): dr.text((x, pady + dy), ch, font=f, fill=fill); x += cw
    if pill:
        ImageDraw.Draw(im).rounded_rectangle([padx * 0.35, pady * 0.35, w - padx * 0.35, h - pady * 0.35], radius=int(h * 0.28), fill=rgba(pill, pal))
    elif shadow:
        s = Image.new("RGBA", (w, h), (0, 0, 0, 0)); put(s, 3, 4, (0, 0, 0, 170)); im.alpha_composite(s.filter(ImageFilter.GaussianBlur(max(4, px // 14))))
    put(im, 0, 0, rgba(color, pal))
    if strike: ImageDraw.Draw(im).line([(padx * 0.6, h * 0.55), (w - padx * 0.6, h * 0.55)], fill=rgba(strike, pal), width=max(5, int(px) // 20))
    return im


def ease(u): u = min(1.0, max(0.0, u)); return 1 - (1 - u) ** 3


def motion_state(e, t):
    """position, scale, rotation and opacity of an element at timeline time t"""
    d = e["t1"] - e["t0"]; lt = t - e["t0"]; x, y, sc, rot, op = e.get("x", 0.5), e.get("y", 0.5), e.get("scale", 1.0), e.get("rotate", 0.0), e.get("opacity", 1.0)
    if e.get("keyframes"):
        ks = sorted(e["keyframes"], key=lambda k: k["time"])
        a = max([k for k in ks if k["time"] <= lt] or [ks[0]], key=lambda k: k["time"]); b = min([k for k in ks if k["time"] >= lt] or [ks[-1]], key=lambda k: k["time"])
        u = 0 if b["time"] == a["time"] else (lt - a["time"]) / (b["time"] - a["time"]); mix = lambda n, dflt: a.get(n, dflt) + (b.get(n, dflt) - a.get(n, dflt)) * u
        x, y, sc, rot = mix("x", x), mix("y", y), mix("scale", sc), mix("rotationDegrees", rot)
    else:
        m = e.get("motion", "fade")
        if m == "pop": u = ease(lt / 0.22); sc *= 0.6 + 0.48 * u - 0.08 * ease((lt - 0.22) / 0.12)
        elif m == "rise": y += 0.035 * (1 - ease(lt / 0.32))
        elif m == "slam": sc *= 1.7 - 0.7 * ease(lt / 0.16)
        elif m == "swing": rot += -9 * (1 - ease(lt / 0.2)) + 3 * math.sin(min(lt, 0.6) / 0.6 * math.pi) * (1 - min(lt, 0.6) / 0.6)
        elif m == "slide-right": x -= 0.5 * (1 - ease(lt / 0.3))
        elif m == "slide-left": x += 0.5 * (1 - ease(lt / 0.3))
        elif m == "drift": x += 0.02 * (lt / max(d, 0.1) - 0.5)
        if m not in ("slam",): op *= ease(lt / 0.14)
    op *= ease((e["t1"] - t) / 0.12)
    return x, y, sc, rot, op


def paste(frame, im, x, y, sc, rot, op):
    w = max(2, int(W * sc)) if im.info.get("fit") else max(2, int(im.width * sc)); h = max(2, int(im.height * w / im.width))
    o = im.resize((w, h), Image.LANCZOS)
    if abs(rot) > 0.05: o = o.rotate(-rot, expand=True, resample=Image.BICUBIC)
    if op < 0.995: o.putalpha(o.getchannel("A").point(lambda v: int(v * op)))
    frame.alpha_composite(o, (int(x * W - o.width / 2), int(y * H - o.height / 2))) if _inside(o, x, y) else _clip_paste(frame, o, x, y)


def _inside(o, x, y):
    l, t = int(x * W - o.width / 2), int(y * H - o.height / 2); return l >= 0 and t >= 0 and l + o.width <= W and t + o.height <= H


def _clip_paste(frame, o, x, y):
    l, t = int(x * W - o.width / 2), int(y * H - o.height / 2)
    box = (max(0, -l), max(0, -t), min(o.width, W - l), min(o.height, H - t))
    if box[2] > box[0] and box[3] > box[1]: frame.alpha_composite(o.crop(box), (max(0, l), max(0, t)))


def grade_lut(g):
    """Approximation of the Edits grade controls as per-channel curves plus a saturation mix. Values are fractions (0.10 = +10)."""
    x = np.arange(256, dtype=np.float32) / 255
    x = x + g.get("brightness", 0) * 0.5
    x = x + g.get("shadows", 0) * 0.6 * (1 - np.clip(x, 0, 1)) ** 3
    x = x + g.get("highlights", 0) * 0.6 * np.clip(x, 0, 1) ** 3
    x = (x - 0.5) * (1 + g.get("contrast", 0)) + 0.5
    t = g.get("temperature", 0) * 0.12
    return [np.clip(x * (1 + k), 0, 1) for k in (t, 0, -t)], g.get("saturation", 1.0) + 0.5 * g.get("vibrance", 0)


def apply_grade(a, lut, sat, skin):
    f = np.stack([np.take(lut[i], a[..., i]) for i in range(3)], axis=-1)
    if abs(sat - 1) > 0.005 or skin:
        y = (f * np.array([0.299, 0.587, 0.114], np.float32)).sum(-1, keepdims=True)
        if abs(sat - 1) > 0.005: f = y + (f - y) * sat
        if skin:  # lift the orange band only: red above green above blue, with some colour in it
            r, g, b = f[..., 0], f[..., 1], f[..., 2]
            m = np.clip((r - g) * 6, 0, 1) * np.clip((g - b) * 6, 0, 1) * np.clip((r - b - 0.08) * 5, 0, 1)
            f = f + (m * skin.get("luminance", 0.15) * 0.35)[..., None]
            ys = (f * np.array([0.299, 0.587, 0.114], np.float32)).sum(-1, keepdims=True)
            f = ys + (f - ys) * (1 + m[..., None] * skin.get("saturation", 0))
    return (np.clip(f, 0, 1) * 255 + 0.5).astype(np.uint8)


def zoom_at(z, lt, d):
    if not z: return 1.0, 0.5
    pts = [((d if k[0] == "end" else k[0]), k[1], k[2] if len(k) > 2 else 0.5) for k in z]
    for p, q in zip(pts, pts[1:]):
        if p[0] <= lt <= q[0]: u = (lt - p[0]) / max(q[0] - p[0], 1e-6); return p[1] + (q[1] - p[1]) * u, p[2] + (q[2] - p[2]) * u
    return (pts[0][1], pts[0][2]) if lt < pts[0][0] else (pts[-1][1], pts[-1][2])


def run(*a, **k): return subprocess.run(a, capture_output=True, text=True, **k)


def main():
    path = sys.argv[1]; spec = json.load(open(path)); check = "--check" in sys.argv
    work = os.path.expanduser(spec["work"]); fps = spec.get("fps", 30); pal = spec.get("palette", {"ivory": "#F3EDE2", "ink": "#0B0B0B"})
    os.makedirs(work + "/assets", exist_ok=True); os.makedirs(work + "/renders", exist_ok=True)
    starts, t = [], 0.0
    for c in spec["clips"]:
        starts.append(t); t += c["out"] - c["in"]
        if not os.path.exists(os.path.join(work, c["file"])): sys.exit(f"missing clip: {c['file']} in {work}")
    total = t; n_frames = int(round(total * fps))
    for a in spec.get("assets", []):
        if a["kind"] == "word":
            draw_text_image(a["text"], a.get("font", "instrument-italic"), a.get("size", 200), a.get("color", "ivory"), pal, a.get("track", 0), a.get("shadow", True), a.get("strike")).save(os.path.join(work, "assets", a["file"]))
        else: UNSUPPORTED.append(f"asset kind '{a['kind']}' ({a.get('file')}) is drawn only by the Mac builder")
    layers = []  # (t0, t1, element, image)
    cs = spec.get("caption_style", {})
    for c in spec.get("captions", []):
        im = draw_text_image(c[2], cs.get("font", "jost"), cs.get("px", 46), cs.get("color", "ivory"), pal, cs.get("track", 2), True, None, cs.get("pill"), cs.get("upper", False))
        layers.append(({"t0": c[0], "t1": c[1], "x": 0.5, "y": cs.get("y", 0.72), "scale": 1.0, "motion": cs.get("motion", "fade")}, im))
    sfx = []
    for e in spec.get("elements", []):
        k = e["type"]
        if e.get("behind"): UNSUPPORTED.append(f"'{e.get('text') or e.get('asset')}' asked to pass behind the speaker; needs a cut-out of the person, Mac only. Drawn in front instead");
        if k == "image":
            p = os.path.join(work, "assets", e["asset"])
            if not os.path.exists(p): UNSUPPORTED.append(f"image '{e['asset']}' not found; skipped"); continue
            im = Image.open(p).convert("RGBA"); im.info["fit"] = True; layers.append((e, im))
        elif k == "text":
            im = draw_text_image(e["text"], e.get("font", "jost"), e.get("px", e.get("size", 24) * 2.2), e.get("color", "ivory"), pal, e.get("track", 0), True, None, e.get("pill"))
            layers.append((dict(e, scale=1.0), im))
        elif k == "line":
            im = Image.new("RGBA", (max(2, int(e["w"] * W)), max(2, int(e.get("h", 0.005) * H))), rgba(e.get("color", "accent"), pal)); layers.append((dict(e, scale=1.0, motion="fade"), im))
        elif k == "sfx":
            f_ = e.get("file") and os.path.join(work, "assets", e["file"])
            if f_ and os.path.exists(f_): sfx.append((f_, e["t"], e.get("vol", 0.25)))
            else: UNSUPPORTED.append(f"sound '{e.get('sound') or e.get('effect') or e.get('file')}' at {e.get('t')}s comes from the Edits library; not available here, left out")
        elif k == "voice": pass
        else: UNSUPPORTED.append(f"element type '{k}' not supported; left out")
    if spec.get("sound"): UNSUPPORTED.append("Edits voice clean-up ('sound') replaced by plain filters; set 'voice' for a high-pass or denoise")
    if any(c.get("transition") for c in spec["clips"]): UNSUPPORTED.append("clip transitions are Edits filters; plain cuts used")
    report = {"name": spec["name"], "seconds": round(total, 2), "frames": n_frames, "fps": fps, "unsupported": sorted(set(UNSUPPORTED)), "renderer": "portable (ffmpeg + Pillow)"}
    if check: print(json.dumps(report, indent=1)); return
    out = f"{work}/renders/{spec['name']}.mp4"; aud = f"{work}/renders/{spec['name']}-audio.m4a"
    # audio: trim and join the clips, gain, optional voice filters, then mix any sound files
    ins, fc, labels = [], [], []
    for i, c in enumerate(spec["clips"]):
        ins += ["-i", os.path.join(work, c["file"])]; fc.append(f"[{i}:a]atrim={c['in']}:{c['out']},asetpts=PTS-STARTPTS[a{i}]"); labels.append(f"[a{i}]")
    chain = [f"volume={spec.get('gain', 1.0)}"]
    for v in [e for e in spec.get("elements", []) if e["type"] == "voice"] and sum([e["chain"] for e in spec.get("elements", []) if e["type"] == "voice"], []) or spec.get("voice", []):
        if v["type"] in ("highpass", "lowpass"): chain.append(f"{v['type']}=f={v['hz']}")
        elif v["type"] == "denoise": chain.append("afftdn=nf=-25")
    fc.append("".join(labels) + f"concat=n={len(labels)}:v=0:a=1," + ",".join(chain) + "[voice]"); mix = ["[voice]"]
    for j, (f_, ts, vol) in enumerate(sfx):
        ins += ["-i", f_]; k = len(spec["clips"]) + j; fc.append(f"[{k}:a]adelay={int(ts * 1000)}:all=1,volume={vol}[s{j}]"); mix.append(f"[s{j}]")
    fc.append("".join(mix) + f"amix=inputs={len(mix)}:normalize=0,alimiter=limit=0.95[out]" if len(mix) > 1 else "[voice]alimiter=limit=0.95[out]")
    r = run("ffmpeg", "-nostdin", "-y", *ins, "-filter_complex", ";".join(fc), "-map", "[out]", "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.3f}", aud)
    has_audio = r.returncode == 0 and os.path.exists(aud)
    if not has_audio: report["audio_note"] = "no usable audio track; exported silent"
    enc = subprocess.Popen(["ffmpeg", "-nostdin", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-"] + (["-i", aud] if has_audio else []) +
                           ["-c:v", "libx264", "-preset", "medium", "-crf", str(spec.get("crf", 18)), "-pix_fmt", "yuv420p", "-movflags", "+faststart"] + (["-c:a", "copy", "-shortest"] if has_audio else []) + [out],
                           stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
    vig = None
    if spec.get("vignette"):
        yy, xx = np.mgrid[0:H, 0:W]; rr = np.sqrt(((xx - W / 2) / 760.0) ** 2 + ((yy - H / 2) / 1250.0) ** 2)
        vig = Image.fromarray(np.dstack([np.zeros((H, W, 3), np.uint8), (np.clip((rr - 0.62) / 0.6, 0, 1) ** 1.6 * 255 * spec["vignette"]).astype(np.uint8)]), "RGBA")
    sheet_at = sorted({min(n_frames - 1, int((e["t0"] + min(e["t1"], e["t0"] + 2.0)) / 2 * fps)) for e, _ in layers} | {0, n_frames // 2, n_frames - 1})[:12]; shots = []
    fi = 0
    for c, st in zip(spec["clips"], starts):
        d = c["out"] - c["in"]; g = dict(spec.get("grade", {}), **c.get("grade", {})); lut, sat = grade_lut(g); skin = dict(spec.get("skin", {}), **c.get("skin", {})) or None
        dec = subprocess.Popen(["ffmpeg", "-nostdin", "-ss", str(c["in"]), "-t", str(d), "-i", os.path.join(work, c["file"]), "-vf", f"fps={fps},scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H}",
                                "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        want = int(round((st + d) * fps)) - int(round(st * fps)); last = None
        for k in range(want):
            buf = dec.stdout.read(W * H * 3)
            a = np.frombuffer(buf, np.uint8).reshape(H, W, 3) if len(buf) == W * H * 3 else last
            if a is None: break
            last = a; t_ = fi / fps; lt = t_ - st
            a = apply_grade(a, lut, sat, skin) if g or skin else a
            fr = Image.fromarray(a, "RGB")
            z, zy = zoom_at(c.get("zoom"), lt, d)
            if abs(z - 1) > 0.0005:
                cw, ch = W / z, H / z; cx, cy = W / 2, min(max(H * zy, ch / 2), H - ch / 2)
                fr = fr.transform((W, H), Image.AFFINE, (1 / z, 0, cx - cw / 2, 0, 1 / z, cy - ch / 2), resample=Image.BICUBIC)
            fr = fr.convert("RGBA")
            if vig: fr.alpha_composite(vig)
            for e, im in layers:
                if e["t0"] <= t_ < e["t1"]: paste(fr, im, *motion_state(e, t_))
            rgb = fr.convert("RGB")
            if fi in sheet_at: shots.append(rgb.resize((270, 480)))
            enc.stdin.write(rgb.tobytes()); fi += 1
        dec.stdout.close(); dec.wait()
    enc.stdin.close(); enc.wait()
    if shots:
        sh = Image.new("RGB", (270 * min(6, len(shots)), 480 * math.ceil(len(shots) / 6)))
        for i, s in enumerate(shots): sh.paste(s, (270 * (i % 6), 480 * (i // 6)))
        sh.save(f"{work}/renders/{spec['name']}-frames.jpg", quality=85)
    report.update(file=out, bytes=os.path.getsize(out) if os.path.exists(out) else 0, frames_written=fi, sheet=f"{work}/renders/{spec['name']}-frames.jpg")
    json.dump(report, open(f"{work}/renders/{spec['name']}-report.json", "w"), indent=1); print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
