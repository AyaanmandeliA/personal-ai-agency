# personal-ai-agency

A personal AI agency: a small set of AI roles that take briefs for research, scripting and video editing and hand back work for review. This repository is the public cut of the tooling.

I set the requirements and check the results. AI tools write most of the code.

Status: Working personal system, still developing.

## How it works

- **Intake.** Briefs arrive through Discord, and through Instagram for material sent in from there.
- **Roles.** Work is split across roles: research, scripting and editing, plus a coordinator that routes each job.
- **Two execution paths for video.** Lighter work and rendering run in a cloud session using the portable renderer in this repo. Larger video edits run on a Mac, using a desktop editing app.
- **Review before publish.** Nothing is published automatically. I review the output and decide what, if anything, is published.

## What is in this repo

| Path | What it does |
|---|---|
| `tools/render.py` | Portable video renderer. Reads a JSON edit spec (clips, grade, captions, text and image elements, sound effects) and renders a 1080x1920 MP4 with ffmpeg and Pillow, plus a frame sheet and a report. `--check` validates a spec and lists anything the renderer cannot do. |
| `tools/audio_scan.py` | Measures a video or audio file without listening: silences, level between words, rhythm, voice band, and sudden onsets. Optional word timings and classifier labels can be merged in. |
| `tools/setup.sh` | Installs ffmpeg, Pillow and numpy if missing, for a fresh cloud session. |
| `tools/fonts/` | Playfair Display (regular and italic variable fonts) with its SIL Open Font License text. |
| `.env.example` | Environment variable names used by the tools. |

## What is deliberately left out

Personal context, footage, channel configuration and credentials are not included. Neither are the operating instructions for the roles, the private relay used for the Mac path, or the third-party social skill kits (below).

### Third-party tools used but not included

Two MIT-licensed skill kits by Jake Schincariol are used as reference material by some roles. They are not copied here:

- LinkedIn kit: https://github.com/Jakeschincariol/linkedin-agent-skill
- Instagram kit: https://github.com/Jakeschincariol/instagram-agent-skill

## Requirements

- Python 3 with Pillow and numpy
- ffmpeg on the PATH
- `tools/setup.sh` installs the above on a Debian or Ubuntu style system (uses `apt-get` and `pip`)

Usage:

```
python3 tools/render.py spec.json --check
python3 tools/render.py spec.json
python3 tools/audio_scan.py media.mp4 [--words words.json] [--labels labels.tsv] [--out scan.json]
```

The spec's `work` field names a working folder that holds the clips and an `assets` folder; outputs go to its `renders` folder.

### Fonts

Only Playfair Display is bundled, with its licence. The renderer expects these font families for its named styles and falls back to DejaVu Serif (or Georgia on macOS) with a note in the report when one is missing:

Instrument Serif (regular, italic), Playfair Display (regular, italic), Cormorant Garamond (regular, italic), Jost, Cinzel, DM Serif Display (regular, italic), and optionally Didot. Install them yourself, or point `AGENCY_FONT_DIR` at a folder containing them.

## Configuration

Copy `.env.example` to `.env`. The only variable the code here reads is `AGENCY_FONT_DIR` (optional). `.env` is git-ignored.
