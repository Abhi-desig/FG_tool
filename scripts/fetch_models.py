#!/usr/bin/env python3
"""Download every model the shop PC will need, before packaging it.

**Why this exists.** Every weight in this app is lazy: `rembg` fetches BiRefNet
the first time somebody removes a background, `transformers` fetches OPUS-MT the
first time somebody translates a sheet, and `models.download` fetches the
upscaler the first time somebody enlarges an image. That is the right behaviour
on a developer's machine and useless on a build runner, which has none of them
and no reason to run an image job.

`package_windows.py --with-models` bundles whatever is in `models/`. This is what
puts it there.

Gated repos are skipped, not attempted. IndicTrans2 needs terms accepted on
Hugging Face under a specific account (ADR-017); a build runner cannot do that,
and failing the build over a model the shop does not use would be the wrong
trade.

    uv run python scripts/fetch_models.py

Exit codes: 0 everything needed is present, 1 something could not be fetched.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import config, models  # noqa: E402  (after the path insert, by necessity)


def megabytes(path: Path) -> float:
    if path.is_file():
        return path.stat().st_size / 1e6
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1e6


def fetch_onnx(key: str) -> bool:
    """A weight we host ourselves. `download` verifies its SHA-256 and is idempotent."""
    path = models.download(key)
    print(f"OK    {key} — {megabytes(path):.0f} MB")
    return True


def fetch_rembg(key: str) -> bool:
    """A weight rembg owns.

    There is no public "just download it" call, so a session is created and
    thrown away — building one is what triggers the fetch. It lands under
    `U2NET_HOME`, which `config` points at `MODELS_DIR` (config.py:39-40).
    """
    from rembg import new_session

    new_session(key)
    print(f"OK    {key} — {megabytes(config.MODELS_DIR / 'models'):.0f} MB")
    return True


def fetch_hf(key: str) -> bool:
    """A Hugging Face repo.

    Both halves are pulled: `from_pretrained` on the model and on the tokenizer
    populate different files, and a cache with only one of them sends the shop
    PC back to the network for the other.
    """
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

    repo = models.spec(key).repo
    assert repo is not None
    AutoTokenizer.from_pretrained(repo)
    AutoModelForSeq2SeqLM.from_pretrained(repo)
    print(f"OK    {key} — cached under {config.HF_HOME.name}/hub")
    return True


def main() -> int:
    print(f"...   fetching into {config.MODELS_DIR}")
    failed: list[str] = []

    for key, model in models.REGISTRY.items():
        if model.gated:
            print(f"SKIP  {key} — gated repo, needs terms accepted and a token (ADR-017)")
            continue
        if models.is_available(key) and model.kind != "rembg":
            # rembg reports available unconditionally, so it is the one kind
            # that has to be fetched even when the registry says it is fine.
            print(f"OK    {key} — already here")
            continue

        try:
            if model.kind == "onnx":
                fetch_onnx(key)
            elif model.kind == "rembg":
                fetch_rembg(key)
            elif model.kind == "hf":
                fetch_hf(key)
            else:
                print(f"FAIL  {key} — unknown kind {model.kind!r}")
                failed.append(key)
        except Exception as exc:  # noqa: BLE001 - every library here raises its own
            print(f"FAIL  {key} — {exc}")
            failed.append(key)

    if failed:
        print()
        print(f"{len(failed)} model(s) could not be fetched: {', '.join(failed)}")
        print("The package would be missing them, so this is a failure, not a warning.")
        return 1

    print()
    print(f"OK    every model the shop PC needs is in {config.MODELS_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
