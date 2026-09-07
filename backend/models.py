"""Model registry and the one-model-at-a-time memory rule.

Two rules live here, and both exist for a machine, not for elegance:

**Selection is by key.** A request sends ``"realesrgan-x4"``, never a path or a
URL. This is the shape of CVE-2026-40086 (SECURITY.md) and the reason
`bria-rmbg` cannot be reached by accident (LICENSES.md).

**One model at a time.** The shop PC has 12 GB. `loaded()` frees the session on
the way out, and `MODEL_LOCK` keeps two model jobs from overlapping. Holding the
upscaler and the background remover together is what would exhaust that machine.
"""

from __future__ import annotations

import gc
import hashlib
import logging
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend import config

log = logging.getLogger(__name__)

# Serialises every model job. Also enforced by the single-worker executor in
# jobs.py; this is the belt to that braces, because a direct call bypasses jobs.
MODEL_LOCK = threading.Lock()


@dataclass(frozen=True)
class ModelSpec:
    key: str
    label: str
    licence: str
    # "rembg" models are fetched by the library itself; "onnx" ones by us.
    kind: str
    filename: str | None = None
    url: str | None = None
    sha256: str | None = None
    # Static input shape, when the graph has one. (n, c, h, w)
    input_shape: tuple[int, int, int, int] | None = None
    scale: int = 1
    notes: str = ""
    aliases: tuple[str, ...] = field(default_factory=tuple)
    # Hugging Face repo id, for "hf" kind models.
    repo: str | None = None
    # True when the repo requires accepting terms and an access token.
    gated: bool = False


REGISTRY: dict[str, ModelSpec] = {
    "birefnet-general": ModelSpec(
        key="birefnet-general",
        label="BiRefNet general (background removal)",
        licence="MIT",
        kind="rembg",
        notes="Locked by ADR-007. Never swap for bria-rmbg — paid licence.",
    ),
    "realesrgan-x4": ModelSpec(
        key="realesrgan-x4",
        label="Real-ESRGAN x4 (upscaling)",
        licence="BSD-3-Clause",
        kind="onnx",
        filename="realesrgan-x4.onnx",
        # Pinned to an immutable revision, not a branch, so the bytes cannot
        # change under us. Verified against sha256 on every download.
        url=(
            "https://huggingface.co/qualcomm/Real-ESRGAN-x4plus/resolve/"
            "01179a4da7bf5ac91faca650e6afbf282ac93933/Real-ESRGAN-x4plus.onnx"
        ),
        sha256="4e1ae0e47f80d9f4aa2a317c24fde2cb3e49a5381eed6e1d509b4001a4b97ad2",
        input_shape=(1, 3, 128, 128),
        scale=4,
        notes="Static 128px input — tiling is mandatory, see images.py.",
    ),
    "opus-mt-en-ml": ModelSpec(
        key="opus-mt-en-ml",
        label="OPUS-MT English→Malayalam (translation)",
        licence="Apache-2.0",
        kind="hf",
        repo="Helsinki-NLP/opus-mt-en-ml",
        notes=(
            "Open weights, no account needed — the working default. Only 57M "
            "params, so it drops words and picks wrong senses; the glossary and "
            "review grid exist to cover that."
        ),
    ),
    "opus-mt-ml-en": ModelSpec(
        key="opus-mt-ml-en",
        label="OPUS-MT Malayalam→English (round-trip check)",
        licence="Apache-2.0",
        kind="hf",
        repo="Helsinki-NLP/opus-mt-ml-en",
        notes=(
            "Never used to translate the shop's work — only to read the "
            "forward model's Malayalam back into English so the two can be "
            "compared. That is the one check that catches fluent-but-wrong "
            "output, which no surface heuristic can see. Loaded only after the "
            "forward model is freed; one model at a time (ADR-035)."
        ),
    ),
    "indictrans2-en-indic": ModelSpec(
        key="indictrans2-en-indic",
        label="IndicTrans2 English→Indic (translation)",
        licence="MIT",
        kind="hf",
        repo="ai4bharat/indictrans2-en-indic-dist-200M",
        gated=True,
        notes=(
            "Better Malayalam than the default, but the repo is gated: accept "
            "the terms on Hugging Face and set HF_TOKEN. Its own toolkit also "
            "needs transformers<5 — install the 'indictrans' extra. See ADR-017."
        ),
    ),
}

# Models that must never be used. Checked on every lookup so a typo or a
# copy-pasted example cannot introduce a licensing problem. See LICENSES.md.
BANNED: dict[str, str] = {
    "bria-rmbg": "requires a paid commercial licence",
    "briaai/RMBG-1.4": "requires a paid commercial licence",
    "briaai/RMBG-2.0": "requires a paid commercial licence",
    "flux-kontext-dev": "non-commercial licence only",
}


class ModelError(RuntimeError):
    """The model is unknown, banned, missing, or failed verification."""


def spec(key: str) -> ModelSpec:
    """Resolve a model key. The only way to name a model."""
    if key in BANNED:
        raise ModelError(f"model {key!r} is banned: {BANNED[key]}. See LICENSES.md")
    try:
        return REGISTRY[key]
    except KeyError as exc:
        raise ModelError(
            f"unknown model {key!r}; available: {', '.join(REGISTRY)}"
        ) from exc


def local_path(key: str) -> Path:
    """Where a model's weights live. Under MODELS_DIR, never outside it."""
    model = spec(key)
    if not model.filename:
        raise ModelError(f"{key} is fetched by its own library, not by us")
    path = (config.MODELS_DIR / model.filename).resolve()
    root = config.MODELS_DIR.resolve()
    if not path.is_relative_to(root):
        raise ModelError(f"{key} would resolve outside the models directory")
    return path


def is_available(key: str) -> bool:
    """True if this model can be used without a fresh download."""
    model = spec(key)
    if model.kind == "rembg":
        return True  # rembg fetches on first use
    if model.kind == "hf":
        return hf_cached(key)
    return local_path(key).exists()


def sha256_of(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def verify(key: str) -> bool:
    """True if the local file matches the pinned checksum."""
    model = spec(key)
    if not model.sha256:
        return True
    path = local_path(key)
    if not path.exists():
        return False
    return sha256_of(path) == model.sha256


def download(key: str, on_progress: Callable[[float], None] | None = None) -> Path:
    """Fetch weights to MODELS_DIR and verify them. Idempotent."""
    import urllib.request

    model = spec(key)
    if not model.url:
        raise ModelError(f"{key} has no download URL")

    path = local_path(key)
    if path.exists() and verify(key):
        return path

    config.MODELS_DIR.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    log.info("downloading %s", key)

    with urllib.request.urlopen(model.url, timeout=120) as response:  # noqa: S310
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        with partial.open("wb") as out:
            while block := response.read(1 << 20):
                out.write(block)
                done += len(block)
                if on_progress and total:
                    on_progress(done / total)

    partial.replace(path)
    if not verify(key):
        path.unlink(missing_ok=True)
        raise ModelError(
            f"{key} failed checksum verification — the download was corrupt or "
            f"the upstream file changed. Refusing to use it."
        )
    return path


@contextmanager
def loaded(key: str) -> Iterator[Any]:
    """Load a model, hand it over, and free it on the way out.

    The `finally` block is the memory rule. Never keep the yielded session
    beyond the `with`, and never nest two of these.
    """
    model = spec(key)
    with MODEL_LOCK:
        session: Any = None
        try:
            if model.kind == "onnx":
                import onnxruntime as ort

                path = local_path(key)
                if not path.exists():
                    raise ModelError(f"{key} weights are not downloaded yet")
                session = ort.InferenceSession(
                    str(path), providers=config.providers()
                )
            elif model.kind == "rembg":
                from rembg import new_session

                session = new_session(key)
            elif model.kind == "hf":
                session = _load_hf(model)
            else:
                raise ModelError(f"{key} has unsupported kind {model.kind!r}")
            yield session
        finally:
            del session
            gc.collect()


def _load_hf(model: ModelSpec) -> tuple[Any, Any]:
    """Load a seq2seq translation model and its tokenizer.

    Returns the pair because the tokenizer is useless without the model and both
    must be freed together when `loaded()` exits.
    """
    try:
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
    except ImportError as exc:
        raise ModelError(
            "Translation needs the 'translate' extra: uv sync --extra translate"
        ) from exc

    assert model.repo is not None
    try:
        tokenizer = AutoTokenizer.from_pretrained(model.repo)
        net = AutoModelForSeq2SeqLM.from_pretrained(model.repo)
    except Exception as exc:  # noqa: BLE001 - transformers raises many types
        hint = ""
        if model.gated:
            hint = (
                f" This repo is gated: accept the terms at "
                f"https://huggingface.co/{model.repo} and set HF_TOKEN in .env."
            )
        raise ModelError(f"could not load {model.key}: {exc}.{hint}") from exc
    net.eval()
    return tokenizer, net


def hf_cached(key: str) -> bool:
    """True if a Hugging Face model is already downloaded.

    Checked without touching the network, so the Settings screen stays instant
    and works offline.
    """
    model = spec(key)
    if model.kind != "hf" or not model.repo:
        return False
    folder = "models--" + model.repo.replace("/", "--")
    root = config.HF_HOME / "hub" / folder
    if not root.exists():
        return False
    snapshots = root / "snapshots"
    return snapshots.is_dir() and any(snapshots.iterdir())


def describe() -> list[dict[str, object]]:
    """Registry state for the Settings screen."""
    rows: list[dict[str, object]] = []
    for key, model in REGISTRY.items():
        row: dict[str, object] = {
            "key": key,
            "label": model.label,
            "licence": model.licence,
            "kind": model.kind,
            "available": is_available(key),
        }
        if model.filename:
            path = local_path(key)
            row["size_mb"] = (
                round(path.stat().st_size / 1_048_576, 1) if path.exists() else None
            )
        if model.kind == "hf":
            row["repo"] = model.repo
            row["gated"] = model.gated
        rows.append(row)
    return rows
