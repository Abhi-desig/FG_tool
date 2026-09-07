# Architecture — Focus Toolkit

**Status:** Draft for review · **Last updated:** 2026-08-22

Rationale for each choice lives in [DECISIONS.md](DECISIONS.md). This file states what the system
*is*, so no session has to re-derive it.

---

## Shape of the system

A single Python process serving a built React app to a local browser. No cloud, no containers, no
external database.

```
Desktop shortcut
   → uv starts Python
   → FastAPI listens on 127.0.0.1:8000
   → default browser opens
   → operator works
   → window closed, process stops
```

It looks like a desktop app. That it happens to be a browser is an implementation detail.

## Stack

| Layer | Choice | Why |
|---|---|---|
| Language | **Python 3.13** | Every model (rembg, Real-ESRGAN, IndicTrans2) is Python-only. 3.13 has complete wheel coverage across all deps |
| Env + deps | **uv** | One tool installs Python itself, resolves, and locks. Reproducible on Mac and Windows from one `uv.lock` |
| Server | **FastAPI + uvicorn** | Async, typed, serves both API and static files |
| Database | **`sqlite3` (stdlib)** | Glossary, presets, prompt library, keys, history. Data volume is tiny; an ORM would be a dependency with no payoff |
| Frontend | **React 19 + TypeScript + Vite** | Modern, fast HMR, compiles to plain static files |
| Styling | **Tailwind v4** | shadcn/ui's native styling layer |
| Components | **shadcn/ui** | Copied into the repo from git. Accessible, polished components we compose rather than write |
| Canvas | **DOM text boxes** | Poster editor. Fabric.js was superseded — see ADR-020: DOM gives native Malayalam shaping, keyboard operability, and no new dependency |
| Imaging | **Pillow** | 300 DPI and CMYK output |
| Local AI | **ONNX Runtime** | Background removal + upscaling. Device-agnostic |
| Translation | **PyTorch + transformers + IndicTransToolkit** | IndicTrans2 has no ONNX path. Phase 3 only |
| Cloud AI | **`google-genai`** | Current Gemini SDK. Phase 5 only |

### The build step never reaches the shop PC

`npm run build` runs on the **dev machine** and emits `frontend/dist/`. FastAPI serves that directory
via `StaticFiles`. The shop PC installs Python and nothing else — **no Node, no npm, no build.**

This is what makes a modern frontend stack compatible with a non-technical deployment target.

## Folder layout

```
FG_Tool/
├── pyproject.toml          deps grouped per phase
├── uv.lock                 committed — reproducible installs
├── .python-version         3.13
├── .env.example            placeholders only
├── .gitignore
│
├── backend/
│   ├── main.py             FastAPI app, static mount, browser launch
│   ├── config.py           paths, device detection, model registry
│   ├── db.py               sqlite schema + migrations
│   ├── crypto.py           API-key encryption at rest
│   └── features/
│       ├── fonts.py        Phase 1 — Malayalam conversion
│       ├── images.py       Phase 2 — cutout, upscale, DPI calculator
│       ├── excel.py        Phase 3 — translation + glossary
│       ├── dictionary.py   Phase 3 — the bundled offline word library
│       ├── translit.py     Phase 3 — names and addresses, by rule
│       ├── columns.py      Phase 3 — what each column holds (ADR-035)
│       ├── posters.py      Phase 4 — reads the shop's poster designs
│       └── ai.py           Phase 5 — Gemini client
│
├── data/
│   ├── maps/               vendored ML-TTKarthika.map
│   ├── poster_prompts/     the shop's own poster designs, one file each
│   ├── names/              exceptions.tsv — names the rules cannot derive
│   ├── places/             gazetteer.tsv + structural.tsv, for addresses
│   └── dictionary/         en-ml.tsv.gz (Olam, ODbL) + trade-en-ml.tsv (ours)
│
├── frontend/
│   ├── src/                React source
│   └── dist/               built output — gitignored, served by FastAPI
│
├── models/                 AI weights — gitignored, lives on the HDD
├── tests/
│   └── golden/             malayalam_pairs.tsv and friends
│
├── start.command           Mac launcher
└── start.bat               Windows launcher
```

## Request flow

```
Browser (React)
   │  fetch POST /api/fonts/convert
   ▼
FastAPI route  ── validates input (Pydantic)
   │
   ▼
backend/features/*.py  ── the actual work
   │
   ├──► data/maps/        (Phase 1, pure lookup)
   ├──► data/dictionary/  (Phase 3, pure lookup — ADR-032)
   ├──► models/           (Phases 2–3, load → run → unload)
   ├──► SQLite            (glossary, prompts, settings, history)
   └──► Gemini API        (Phase 5 only — the only outbound network call)
   │
   ▼
JSON response → React renders
```

Every feature module is independent. None imports another. This is what allows a phase to be built,
proven, and left alone.

## Hardware adaptation

The app detects what is available at startup and picks the fastest path. **The code is written once.**

```python
# backend/config.py
import onnxruntime as ort

def pick_providers() -> list[str]:
    available = ort.get_available_providers()
    if "CUDAExecutionProvider" in available:
        return ["CUDAExecutionProvider", "CPUExecutionProvider"]   # Nitro V
    if "CoreMLExecutionProvider" in available:
        return ["CoreMLExecutionProvider", "CPUExecutionProvider"] # Mac
    return ["CPUExecutionProvider"]                                # shop PC
```

**Measured, not estimated.** One 128 px Real-ESRGAN tile, steady state, on this
Mac (2026-08-22):

| Provider | Per tile | Implication |
|---|---|---|
| CoreML | **59 ms** | 4000×3000 blind 4× ≈ 1.3 min |
| CPU | **2 487 ms** | the same job ≈ 56 min |

**That 40× gap is the single most important number in Phase 2.** The shop PC has
no usable GPU, so it takes the CPU column. Two consequences are built into the
code rather than left to judgement:

- The operator picks **2×, 4×, or for-print**, and any input size is accepted
  (ADR-016). The model always runs at its native 4× on the full source and the
  result is resampled, so **2× and 4× cost the same time** — the UI says so.
- Slowness never refuses a job. `images.estimate_seconds()` is shown *before*
  the operator commits, and every job is cancellable. Only `MAX_OUTPUT_PIXELS`
  (300 MP — what can be written as one file) falls back to Lanczos.

Realistic per-image cost with a target, on the shop PC: **2–3 minutes**. The
original plan's "60–90 s" was optimistic. Queue a batch and come back.

Under 20 images a week, a batch that takes ten minutes is fine.

## The memory rule

**Non-negotiable on a 12 GB machine.** Load one model, use it, free it. Never hold the upscaler and
the background remover in memory at the same time.

```python
model = load_model()
try:
    result = model.run(image)
finally:
    del model          # free immediately
```

The API must serialise model work — two concurrent jobs on the shop PC will exhaust RAM. Long jobs
report progress rather than blocking the UI.

## Model storage

Weights and runtimes total roughly **4 GB** (Phases 2–3, including PyTorch — breakdown below). The
shop SSD is 112 GB, so models go on the 932 GB HDD, set from `.env`:

```
MODELS_DIR=D:/focus-toolkit/models
```

`config.py` reads it and sets the environment variables the libraries expect before any model loads.

## Storage budget — corrected

The original plan estimated ~1 GB. Actual:

| Phase | Component | Size | Source |
|---|---|---|---|
| 2 | BiRefNet general (rembg) | **928 MB** | measured |
| 2 | Real-ESRGAN x4 ONNX | **64 MB** | measured |
| 3 | PyTorch | ~2.5 GB | estimated |
| 3 | IndicTrans2 en-indic 200M | ~1 GB | estimated |
| | **Total** | **~4.5 GB** | |

Peak RSS during a background removal is **~1.9 GB** (measured). Comfortable alone
on 12 GB, and precisely why two models must never be resident together.

PyTorch is the surprise. IndicTrans2 has no ONNX path, so the "all local AI runs on ONNX Runtime"
assumption does not hold for Phase 3. This is why Phase 3 dependencies are optional and installed
only when that phase is reached.

## Third-party services

Exactly one: **Google Gemini** (Phase 5), for photo editing and poster artwork.

- `gemini-3.1-flash-image` — photo edits, ~₹4/image
- `gemini-3-pro-image` — poster artwork, ~₹11.5/image, **~₹6 in batch mode**

Batch mode is half price. Poster work tolerates a few minutes' wait, so it should default to batch.

Optional fallback: fal.ai or Replicate (Qwen-Image-Edit). Not wired up unless Gemini proves
insufficient.

**Features 1–4 make no network calls at all.**
