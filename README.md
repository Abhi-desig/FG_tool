# Focus Toolkit

A local pre-press assistant for a Kerala printing and advertising shop.

One Python app that opens in your browser from a desktop shortcut and runs entirely on your own
computer. No GPU required, no monthly fee, no account.

> **Status: Phase 0 — documentation only.** No application code yet. The setup steps below describe
> the intended install and will not work until Phase 1 lands. See [ROADMAP.md](ROADMAP.md).

---

## What it does

| # | Feature | Runs | Cost |
|---|---|---|---|
| 1 | Malayalam converter — WhatsApp text → ML-TTKarthika for CorelDRAW | Offline | Free |
| 2 | Image upscaler **+ honest print-size calculator** | Offline | Free |
| 3 | Background remover with protect-subject brush | Offline | Free |
| 4 | Excel English → Malayalam with per-client glossary | Offline | Free |
| 5 | AI photo editing + poster designer | Internet | ~₹4–12/image |

**Features 1–4 are free forever and make no network calls.** Only feature 5 reaches the internet.

The poster designer's trick: **AI makes the picture, the app makes the text.** Every word is a real,
editable, correctly-spelled text layer in a real font — which is exactly why it can produce Malayalam
posters that international design tools cannot.

---

## Install

### Both platforms — install uv once

uv manages Python itself, so you do **not** need to install Python separately.

**macOS**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Windows** (PowerShell)
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### Get the project running

```bash
cd FG_Tool
uv sync
```

That reads `.python-version` and `uv.lock`, downloads Python 3.13 if needed, and installs the exact
locked dependencies. Phase 1 is small — around 30 MB.

### Add later phases as you reach them

Dependencies are grouped so you never install 3.5 GB to use the font converter.

```bash
uv sync --extra images        # Phase 2 — rembg, onnxruntime, Pillow
uv sync --extra translate     # Phase 3 — torch, transformers  (~3.5 GB)
uv sync --extra ai            # Phase 5 — google-genai
```

**On the Acer Nitro V**, swap in the GPU runtime for a large speed-up:

```bash
uv remove onnxruntime && uv add onnxruntime-gpu
```

### Configure

```bash
cp .env.example .env
```

Then edit `.env` — most importantly point `MODELS_DIR` at your **large drive**, not the 112 GB SSD:

```
MODELS_DIR=D:/focus-toolkit/models
```

API keys are **not** set here. Add them in the app's Settings tab, where they are encrypted at rest
— see [SETTINGS.md](SETTINGS.md).

---

## Run

```bash
uv run python -m backend.main
```

The server starts on `http://127.0.0.1:8000` and your browser opens automatically. Close the window
and stop the process to shut down.

### Desktop shortcut

**Windows** — create `start.bat`:
```bat
@echo off
cd /d "%~dp0"
start http://localhost:8000
uv run python -m backend.main
```
Right-click → Send to → Desktop. That is your app icon.

**macOS** — create `start.command` with the same commands, then:
```bash
chmod +x start.command
```

### When the AI features fail

A rejected API key looks exactly like a broken install, which sends you reinstalling things that
were never wrong. This says which it is, and costs nothing:

```bash
uv run python -m backend.diagnose
```

Or double-click `check-ai.bat` (Windows) / `check-ai.command` (macOS). It checks the program with
Google stubbed out, then makes one free call to check the key, and names whichever one is at fault.

Note that Google changed its key format in 2026: keys from
[aistudio.google.com/apikey](https://aistudio.google.com/apikey) now begin `AQ.`, and the older
`AIza` keys stop working in September 2026.

---

## For developers

The frontend is a Vite + React app. **The build step runs only on the dev machine** — the shop PC
never installs Node.

```bash
cd frontend
npm install
npm run dev      # http://localhost:5173, proxied to the Python API
npm run build    # emits frontend/dist/, which FastAPI serves
```

### Releasing the UI

`frontend/dist/` is gitignored during development and **force-added on the release commit**, so
the shop PC — which has no Node — can run a plain clone.

```bash
cd frontend && npm run build && cd ..
git add -f frontend/dist
uv run python scripts/check_release.py
```

**Run the check before every push.** It fails when the committed bundle is not the built one, which
is not hypothetical: `519de89` changed ~2,000 lines of frontend and never re-added `dist`, so the
tracked bundle stayed the pre-Phase-5 UI with no AI screens in it, and nothing reported it. The
check also catches the trap state that leaves — tracked assets showing as deleted while the real
build is untracked, where one `git checkout .` silently reverts the shop PC to the old bundle.

### Tests

```bash
uv run pytest
```

Phase 1's golden tests are the important ones. But note: **a green suite is not the exit gate.**
Most gates in [ROADMAP.md](ROADMAP.md) require checking real output in CorelDRAW or on a real print.

---

## Hardware

Runs on anything. Speed varies; slow is fine at under 20 images a week.

| Machine | Background remove | Upscale 4× |
|---|---|---|
| Acer Nitro V (RTX 4050) | ~2 s | ~3 s |
| Shop PC (i3-9100F, CPU only) | ~15 s | 60–90 s |
| Mac (M-series) | ~4 s | ~8 s |

Disk: ~4 GB of models once Phase 3 is installed. Put them on the big drive.

---

## Documentation

Read these before changing anything — they are the project's context bible.

| File | What it settles |
|---|---|
| [PRD.md](PRD.md) | What we are building, for whom, and explicitly what we are not |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Stack, folder layout, data flow, memory rules |
| [ROADMAP.md](ROADMAP.md) | Five phases and the exit gate each must pass |
| [SETTINGS.md](SETTINGS.md) | API keys, prompt library, preferences |
| [DESIGN.md](DESIGN.md) | Design tokens, component rules, accessibility floor |
| [SECURITY.md](SECURITY.md) | Key handling, input validation, what is out of scope and why |
| [LICENSES.md](LICENSES.md) | Licence register and hard bans — matters, you sell the output |
| [DECISIONS.md](DECISIONS.md) | Why each choice was made, with evidence |
| [CLAUDE.md](CLAUDE.md) | Standing orders for AI coding sessions |

---

## Honest limits

Do not promise these to a client:

- An 800 px WhatsApp logo **will not** become a sharp A3 brochure. It may be fine as a large flex
  banner viewed from a distance. The print-size calculator exists to tell you which, before you start.
- Translation is not perfect. The glossary makes your **repeated** terms reliable; new sentences
  still need a human eye. That is why the review grid is mandatory.
- Upscaling invents detail from low resolution. It **cannot** undo a shaken camera.
- AI cannot reliably write Malayalam into an image — which is precisely why this app draws all text
  itself.
- Google embeds an invisible **SynthID watermark** in AI images. It does not affect printing, but be
  ready if a client asks.
