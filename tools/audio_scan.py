#!/usr/bin/env python3
"""Measure the sound of a video or audio file without hearing it. Needs ffmpeg and numpy only (Mac or cloud).

  audio_scan.py <media> [--words words.json] [--labels labels.tsv] [--out scan.json]

words.json : [[start, end, "word"], ...] from speech-to-text, so speech can be told apart from everything else.
labels.tsv : optional output of the Mac sound classifier (time <tab> label:confidence,...), merged into the timeline.
Everything here is a measurement or a classifier's label. None of it is a judgement of how the sound feels.
"""
import sys, json, subprocess, math
import numpy as np

SR, WIN, HOP = 22050, 2048, 512


def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def main():
    media = sys.argv[1]
    raw = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", media, "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"], capture_output=True).stdout
    x = np.frombuffer(raw, np.float32)
    if len(x) < SR // 2:
        print(json.dumps({"ok": False, "why": "no audio track, or under half a second of sound"})); return
    dur = len(x) / SR
    n = 1 + (len(x) - WIN) // HOP
    idx = np.arange(WIN)[None, :] + HOP * np.arange(n)[:, None]
    fr = x[idx] * np.hanning(WIN)[None, :]
    mag = np.abs(np.fft.rfft(fr, axis=1)); freqs = np.fft.rfftfreq(WIN, 1 / SR); t = (np.arange(n) * HOP + WIN / 2) / SR
    rms = np.sqrt((x[idx] ** 2).mean(1)); db = 20 * np.log10(np.maximum(rms, 1e-7))
    power = mag ** 2; tot = power.sum(1) + 1e-12
    centroid = (power * freqs).sum(1) / tot
    cum = np.cumsum(power, 1) / tot[:, None]; rolloff = freqs[np.argmax(cum >= 0.95, axis=1)]
    flat = np.exp(np.log(mag + 1e-9).mean(1)) / (mag.mean(1) + 1e-9)
    flux = np.maximum(mag[1:] - mag[:-1], 0).sum(1); flux = np.concatenate([[0], flux]) / (np.median(mag.sum(1)) + 1e-9)

    words = json.load(open(arg("--words"))) if arg("--words") else []
    speech = np.zeros(n, bool)
    for s, e, _ in words: speech |= (t >= s - 0.05) & (t <= e + 0.08)
    have_words = bool(words)

    # silence: quieter than 40 dB below the loud parts, for a quarter of a second or more
    loud = np.percentile(db, 95); floor = loud - 40; quiet = db < floor
    sil, i = [], 0
    while i < n:
        if quiet[i]:
            j = i
            while j < n and quiet[j]: j += 1
            if t[j - 1] - t[i] >= 0.25: sil.append([round(float(t[i]), 2), round(float(t[j - 1]), 2)])
            i = j
        else: i += 1

    # sound in the gaps between words: a bed of music or room noise shows up as steady level where nobody is speaking
    gaps = []
    if have_words:
        ws = sorted(words); prev = 0.0
        for s, e, _ in ws + [[dur, dur, ""]]:
            if s - prev >= 0.3: gaps.append((prev + 0.05, s - 0.05))
            prev = max(prev, e)
    gap_mask = np.zeros(n, bool)
    for a, b in gaps: gap_mask |= (t >= a) & (t <= b)
    speech_db = float(np.median(db[speech])) if speech.any() else None
    gap_db = float(np.median(db[gap_mask])) if gap_mask.any() else None
    bed = None
    if speech_db is not None and gap_db is not None:
        diff = speech_db - gap_db
        bed = {"gap_level_db": round(gap_db, 1), "speech_level_db": round(speech_db, 1), "difference_db": round(diff, 1),
               "gap_flatness": round(float(np.median(flat[gap_mask])), 3),
               "reading": "continuous sound under the voice is likely (music or room tone)" if diff < 22 else "gaps between words are close to silent; no continuous bed measured"}

    # onsets: sudden rises in the spectrum. Those that do not sit on a word start are candidate effects, cuts in the music, or hits
    thr = np.median(flux) + 4.5 * (np.median(np.abs(flux - np.median(flux))) + 1e-9)
    peaks = [k for k in range(2, n - 2) if flux[k] > thr and flux[k] == flux[k - 2:k + 3].max()]
    starts = np.array([w[0] for w in words]) if have_words else np.array([])
    events = []
    for k in peaks:
        near_word = have_words and np.min(np.abs(starts - t[k])) < 0.09
        in_speech = bool(speech[k]) if have_words else None
        seg = slice(k, min(n, k + 12)); c = float(centroid[seg].mean()); decay = float(db[k] - db[min(n - 1, k + 10)])
        kind = "short bright click or tick" if c > 3500 and decay > 10 else "low thump or impact" if c < 900 else "broadband burst (whoosh, cut or consonant)" if float(flat[seg].mean()) > 0.25 else "tonal onset (note, chime or voiced sound)"
        events.append({"t": round(float(t[k]), 2), "strength": round(float(flux[k] / thr), 2), "shape": kind, "on_word_start": bool(near_word), "inside_speech": in_speech})
    non_speech = [e for e in events if e["inside_speech"] is False or (e["inside_speech"] is None)]
    strong = sorted([e for e in events if not e["on_word_start"]], key=lambda e: -e["strength"])[:40]

    # rhythm: a steady pulse in the onset strength suggests music with a beat
    env = flux - flux.mean(); ac = np.correlate(env, env, "full")[len(env) - 1:]; fps = SR / HOP
    lo, hi = int(fps * 60 / 180), int(fps * 60 / 60)
    tempo = None
    if hi < len(ac) and ac[0] > 0:
        k = lo + int(np.argmax(ac[lo:hi])); score = float(ac[k] / ac[0])
        tempo = {"bpm": round(60 * fps / k, 1), "pulse_strength": round(score, 3), "reading": "a regular beat is present" if score > 0.12 else "no regular beat measured"}

    # voice band: how much of the voice's energy sits above 5 kHz. Almost none means it was filtered (telephone or radio treatment) or badly recorded
    voice = None
    if speech.any():
        hi = power[speech][:, freqs > 5000].sum() / (power[speech].sum() + 1e-12); hdb = float(10 * np.log10(hi + 1e-12))
        voice = {"energy_above_5khz_db": round(hdb, 1), "reading": "voice is band-limited (filtered, or a low-quality source)" if hdb < -38 else "voice is full-band; no telephone or radio filter measured"}

    # per-second timeline
    secs = []
    labels = {}
    if arg("--labels"):
        for line in open(arg("--labels")):
            p = line.rstrip("\n").split("\t")
            if len(p) == 2 and p[1]:
                labels.setdefault(int(float(p[0])), []).extend([(a.rsplit(":", 1)[0], float(a.rsplit(":", 1)[1])) for a in p[1].split(",")])
    for s in range(int(math.ceil(dur))):
        m = (t >= s) & (t < s + 1)
        if not m.any(): continue
        row = {"s": s, "db": round(float(db[m].mean()), 1), "speech": bool(speech[m].mean() > 0.3) if have_words else None,
               "centroid_hz": int(centroid[m].mean()), "onsets_off_words": sum(1 for e in events if s <= e["t"] < s + 1 and not e["on_word_start"])}
        if s in labels:
            best = {}
            for name, conf in labels[s]: best[name] = max(best.get(name, 0), conf)
            row["classifier"] = [f"{k} {v:.2f}" for k, v in sorted(best.items(), key=lambda kv: -kv[1])[:4]]
        secs.append(row)
    out = {"ok": True, "seconds": round(dur, 2), "peak_db": round(float(20 * np.log10(np.abs(x).max() + 1e-9)), 1), "loud_level_db": round(float(loud), 1),
           "silences": sil, "silence_total_s": round(sum(b - a for a, b in sil), 2), "between_words": bed, "rhythm": tempo, "voice": voice,
           "onsets_total": len(events), "onsets_not_on_a_word_start": len([e for e in events if not e["on_word_start"]]), "onsets_outside_speech": len(non_speech) if have_words else None,
           "strongest_onsets_off_words": sorted(strong, key=lambda e: e["t"]), "timeline": secs,
           "method": "ffmpeg decode, 22.05 kHz mono; level, spectrum and onset measurements; classifier labels merged if supplied. Measured, not heard."}
    js = json.dumps(out, indent=1)
    if arg("--out"): open(arg("--out"), "w").write(js)
    brief = {k: out[k] for k in ("seconds", "peak_db", "silence_total_s", "between_words", "rhythm", "voice", "onsets_total", "onsets_not_on_a_word_start", "onsets_outside_speech")}
    print(json.dumps(brief, indent=1))


if __name__ == "__main__":
    main()
