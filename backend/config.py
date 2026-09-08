"""Paths, host settings and hardware detection.

Read once at startup. Everything here comes from `.env` or a safe default —
see `.env.example`.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Localhost only. SECURITY.md: binding to 0.0.0.0 would expose client artwork
# and the API key to everyone on the shop Wi-Fi, and there is no auth layer.
HOST = "127.0.0.1"
PORT = int(os.getenv("PORT", "8000"))
OPEN_BROWSER = os.getenv("OPEN_BROWSER", "true").lower() not in {"false", "0", "no"}

# Developer controls, off for the operator. Today this is the poster style
# override: the poster screen chooses a style on its own, and the only way to
# exercise the other eight is to pin one deliberately (ADR-037). It is a
# spending control as much as a UI one — each pinned run is a real charge — so
# it stays off unless someone sets it, and the route refuses the parameter
# rather than ignoring it.
#
#     DEV_TOOLS=true uv run python -m backend.main
DEV_TOOLS = os.getenv("DEV_TOOLS", "false").lower() in {"true", "1", "yes"}

# Models live on the big drive, not the 112 GB SSD.
MODELS_DIR = Path(os.getenv("MODELS_DIR", ROOT / "models")).expanduser()

# rembg downloads its own weights (BiRefNet is ~930 MB). Left alone it would put
# them in the home directory, which on the shop PC sits on the 112 GB SSD.
# Must be set before rembg is imported anywhere; an explicit env var still wins.
os.environ.setdefault("U2NET_HOME", str(MODELS_DIR))
os.environ.setdefault("REMBG_HOME", str(MODELS_DIR))

# Same for Hugging Face (the translation models). Default is ~/.cache, which on
# the shop PC is the small SSD.
#
# Note that this directory is not only ours: `HF_HOME` is a well-known variable,
# so anything else on the machine that uses the Hugging Face libraries writes
# here too, and a stray `.agent_harnesses.json` from another tool has already
# turned up (NEXT.md 3.18). Harmless, but do not treat `models/hf/` as "only
# weights" — never wipe it wholesale, and check what a file is before deleting
# it. The weights themselves live under `models/hf/hub`.
HF_HOME = Path(os.getenv("HF_HOME", MODELS_DIR / "hf")).expanduser()
os.environ.setdefault("HF_HOME", str(HF_HOME))

DATA_DIR = ROOT / "data"
FRONTEND_DIST = ROOT / "frontend" / "dist"
DB_PATH = Path(os.getenv("DB_PATH", ROOT / "data.db")).expanduser()

# Job inputs and outputs. Cleared on startup — SECURITY.md says client artwork
# must not accumulate on disk.
WORK_DIR = Path(os.getenv("WORK_DIR", ROOT / "tmp")).expanduser()

# Any real image file is accepted — a print shop handles large scans and raw
# camera output routinely. This ceiling exists only so a runaway or hostile
# upload cannot fill the disk; it is far above any genuine photograph.
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "1024")) * 1024 * 1024

# Locked by ADR-007. bria-rmbg needs a paid commercial licence; changing this
# string is the exact mistake LICENSES.md exists to prevent.
BACKGROUND_MODEL = "birefnet-general"

_DEVICE_OVERRIDE = os.getenv("DEVICE", "auto").lower()


def providers() -> list[str]:
    """ONNX execution providers, fastest available first.

    Written once, adapts per machine: CUDA on the Nitro V, CoreML on the Mac,
    CPU on the shop PC. Unused until Phase 2 — onnxruntime is not installed yet,
    so a missing import is an expected outcome, not an error.
    """
    cpu = ["CPUExecutionProvider"]
    if _DEVICE_OVERRIDE == "cpu":
        return cpu

    try:
        import onnxruntime as ort
    except ImportError:
        return cpu

    available = set(ort.get_available_providers())
    auto = _DEVICE_OVERRIDE == "auto"
    if _DEVICE_OVERRIDE == "cuda" or (auto and "CUDAExecutionProvider" in available):
        return ["CUDAExecutionProvider", *cpu]
    if _DEVICE_OVERRIDE == "coreml" or (auto and "CoreMLExecutionProvider" in available):
        return ["CoreMLExecutionProvider", *cpu]
    return cpu


def device_name() -> str:
    """Human-readable device, for the settings screen."""
    return {
        "CUDAExecutionProvider": "GPU (CUDA)",
        "CoreMLExecutionProvider": "GPU (CoreML)",
        "CPUExecutionProvider": "CPU",
    }[providers()[0]]
