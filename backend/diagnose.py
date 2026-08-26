"""Answers one question: when the AI features fail, is it us or is it Google?

That question used to cost an afternoon. A 401 from Google reads exactly like a
broken install, so the operator re-pastes the key, reinstalls the extra, and
gets nowhere — because nothing on this machine was ever wrong. This checks the
two halves separately and says which one is at fault:

**Part A — the machinery.** Google is stubbed out, so no network and no money.
It exercises the whole path anyway: build the prompt, parse an image, parse a
fenced JSON layout, put the operator's wording back if the AI altered it, price
the job, and record a refusal as free rather than as spend. If this fails, the
install is broken and the key is irrelevant.

**Part B — the key.** One free call (`models.list` costs nothing and spends no
tokens). If A passed and B failed, the code is fine and the key needs attention
in AI Studio — which is the common case, and the one nobody guesses.

Run it with `uv run python -m backend.diagnose`, or double-click `check-ai`.
"""

from __future__ import annotations

import logging
import tempfile
import threading
from pathlib import Path
from typing import Any

from backend import config, db

BAR = "=" * 74

# Neither a real PNG nor pretending to be one — nothing decodes it, the path
# only needs bytes to carry through.
STUB_IMAGE = b"\x89PNG\r\n\x1a\n" + b"stub-image-bytes" * 48

# The stub returns the wrong phone number on purpose. Putting the operator's
# own wording back is the guarantee the poster feature rests on, so a check
# that never sees it get corrected has not checked the thing that matters.
REAL_PHONE = "9876543210"
ALTERED_PHONE = "9999999999"

STUB_LAYOUT = f"""```json
{{"blocks": [
  {{"id": "headline", "text": "ONAM SALE", "x": 10, "y": 12, "size": 72}},
  {{"id": "offer",    "text": "40% OFF",   "x": 10, "y": 40, "size": 48}},
  {{"id": "phone",    "text": "{ALTERED_PHONE}", "x": 10, "y": 80, "size": 24}}
]}}
```"""


# --- standing in for Google ------------------------------------------------


class _Inline:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.mime_type = "image/png"


class _Part:
    def __init__(self, data: bytes | None = None, text: str | None = None) -> None:
        self.inline_data = _Inline(data) if data else None
        self.text = text


class _Response:
    """Shaped like the SDK's response, because that shape is what we parse."""

    def __init__(self, parts: list[_Part], text: str | None = None) -> None:
        self.candidates = [
            type("C", (), {"content": type("X", (), {"parts": parts})()})()
        ]
        self.text = text


class _StubClient:
    def __init__(self, response: Any = None, raises: Exception | None = None) -> None:
        class _Models:
            def generate_content(self, **kwargs: Any) -> Any:
                if raises is not None:
                    raise raises
                return response

            def list(self) -> list[Any]:
                return []

        self.models = _Models()


# --- part A ----------------------------------------------------------------


def _check_machinery() -> list[tuple[bool, str]]:
    """Exercise every step of the call path with Google stubbed out."""
    from backend.features import ai, prompts

    prompts.seed_defaults()
    results: list[tuple[bool, str]] = []
    original = ai._client  # noqa: SLF001 - restored in the finally below

    try:
        # 1. An image comes back, is priced, and carries the watermark notice.
        ai._client = lambda: _StubClient(_Response([_Part(data=STUB_IMAGE)]))  # noqa: SLF001
        art = ai.generate_artwork(
            {
                "headline": "ONAM SALE",
                "offer": "40% OFF",
                "phone": REAL_PHONE,
                "subject": "festive marigold background",
                "occasion": "Onam",
                "idea": "",
                "style": "",
                "palette": "",
                "aspect": "",
            },
            batch=True,
        )
        results.append(
            (
                art.ok and art.image is not None,
                f"artwork: {len(art.image or b'')} bytes back, "
                f"Rs {art.cost_paise / 100:.2f} charged, "
                f"{len(art.warnings)} warning(s)",
            )
        )

        # 2. A fenced JSON layout parses, and the altered phone number is repaired.
        ai._client = lambda: _StubClient(  # noqa: SLF001
            _Response([_Part(text=STUB_LAYOUT)], text=STUB_LAYOUT)
        )
        lay = ai.plan_layout(
            {
                "headline": "ONAM SALE",
                "offer": "40% OFF",
                "phone": REAL_PHONE,
                "occasion": "Onam",
                "tone": "festive",
            }
        )
        blocks = (lay.layout or {}).get("blocks", [])
        phone = next((b for b in blocks if b.get("id") == "phone"), {})
        results.append(
            (lay.ok and len(blocks) == 3, f"layout: {len(blocks)} blocks parsed")
        )
        results.append(
            (
                phone.get("text") == REAL_PHONE and bool(lay.warnings),
                f"text guard: AI said {ALTERED_PHONE}, poster will print "
                f"{phone.get('text', '(nothing)')}",
            )
        )

        # 3. A refusal costs nothing and keeps the operator's work.
        ai._client = lambda: _StubClient(raises=RuntimeError("503 model overloaded"))  # noqa: SLF001
        bad = ai.plan_layout(
            {"headline": "ONAM SALE", "offer": "", "phone": "", "occasion": "", "tone": ""}
        )
        results.append(
            (
                not bad.ok and bad.cost_paise == 0,
                f"failure is free: {bad.cost_paise} paise charged",
            )
        )
    finally:
        ai._client = original  # noqa: SLF001

    summary = db.spend_summary()
    results.append(
        (
            summary["total_paise"] > 0,
            f"ledger: {summary['runs']} run(s), Rs {summary['total_paise'] / 100:.2f} recorded",
        )
    )
    return results


# --- part B ----------------------------------------------------------------


def _check_key(key: str | None) -> tuple[bool, str]:
    """One free call. Never generates, so it can never cost anything."""
    from backend.features import ai

    if not key:
        return False, "No API key saved. Add one in Settings."

    note = ai.key_shape_note(key)
    if note:
        print(f"  key shape : {note}\n")

    try:
        models = ai.available_models()
    except Exception as exc:  # noqa: BLE001 - report, never raise past here
        return False, ai._friendly(exc)  # noqa: SLF001
    names = ", ".join(m["name"] for m in models[:6])
    return True, f"Google accepted the key and offers {len(models)} models ({names}…)"


# --- the report ------------------------------------------------------------


def main() -> int:
    """Exit 0 when the AI features are usable, 1 when they are not."""
    # The real key lives in the shop's database; everything after this point
    # runs against a throwaway one, so a diagnostic can never leave stub runs
    # in the operator's spend history. Same rule as the test suite.
    # Part A provokes a failure on purpose, and `ai` logs it. To the operator
    # that stray warning looks like the fault they came here to find, so the
    # report speaks for itself and the log stays quiet.
    logging.getLogger("backend.features.ai").setLevel(logging.CRITICAL)

    db.init()
    key = db.get_api_key("GEMINI_API_KEY")

    config.DB_PATH = Path(tempfile.mkdtemp()) / "diagnose.db"
    db._local = threading.local()  # noqa: SLF001 - connections are cached per thread
    db.init()
    if key:
        db.set_api_key("GEMINI_API_KEY", key)

    print(BAR)
    print("A. THE MACHINERY   (Google stubbed out — no internet, no money)")
    print(BAR)
    try:
        machinery = _check_machinery()
    except ImportError:
        print("\n  FAILED: the AI extra is not installed.")
        print("  Fix it with:  uv sync --extra ai\n")
        return 1

    for ok, line in machinery:
        print(f"  {'PASS' if ok else 'FAIL'}  {line}")
    machinery_ok = all(ok for ok, _ in machinery)

    print()
    print(BAR)
    print("B. THE KEY         (one free call — lists models, generates nothing)")
    print(BAR)
    print()
    key_ok, message = _check_key(key)
    print(f"  {'PASS' if key_ok else 'FAIL'}  {message}")

    print()
    print(BAR)
    if machinery_ok and key_ok:
        print("  READY. The AI features will work.")
    elif machinery_ok:
        print("  The program is fine. The problem is the API key — see part B above.")
        print("  Nothing on this computer needs reinstalling.")
    else:
        print("  The install itself is broken — part A failed before any key was used.")
        print("  Fix that first; the key is not the problem.")
    print(BAR)
    return 0 if (machinery_ok and key_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
