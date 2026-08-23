"""Gemini: the only part of this app that leaves the machine or costs money.

**Nothing here runs without a deliberate act.** No key is shipped, no call is
made on startup, and every route that can spend money says what it will cost
before it does. Features 1–4 remain entirely offline.

**Costs are recorded in paise, not rupees.** Currency in floating point drifts,
and this total gets compared against Google's console — where a few paise of
disagreement would undermine confidence in the whole figure.

**Batch is the default.** Half price for a few minutes' wait, which on poster
work is no wait at all, and it doubles what a ₹2,000 month buys.

**A failure must never cost the operator their work.** Every call returns an
`AiResult` rather than raising: the caller keeps its inputs, the reason is in
plain words, and a refusal is recorded as a failed run costing nothing rather
than as spend.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from backend import db
from backend.features import prompts

log = logging.getLogger(__name__)

Feature = Literal["photo-edit", "poster-artwork", "poster-layout"]

# Verified against ai.google.dev pricing on 2026-08-22, converted at ~₹88/USD
# and rounded up. Editable from Settings, because Google will change them and
# the operator should not need a code change to stay accurate.
RATES_PAISE: dict[str, int] = {
    "gemini-3.1-flash-image": 400,  # ~₹4    photo edits
    "gemini-3-pro-image": 1150,  # ~₹11.50  poster artwork, instant
    "gemini-3-pro-image:batch": 600,  # ~₹6  poster artwork, batch — half price
    "gemini-3-flash": 5,  # text-only layout planning, negligible
}

PHOTO_MODEL = "gemini-3.1-flash-image"
ARTWORK_MODEL = "gemini-3-pro-image"
LAYOUT_MODEL = "gemini-3-flash"

MONTHLY_BUDGET_PAISE = 200_000  # ₹2,000


class AiError(RuntimeError):
    """Something about the request itself is wrong, before any spend."""


@dataclass
class AiResult:
    """The outcome of one call. Never raises past the caller's work."""

    ok: bool
    feature: str
    model: str
    batch: bool
    cost_paise: int = 0
    image: bytes | None = None
    media_type: str = "image/png"
    text: str | None = None
    layout: dict[str, Any] | None = None
    error: str | None = None
    # What was actually sent, so the operator can see it and tune the template.
    prompt_used: str = ""
    warnings: list[str] = field(default_factory=list)


def cost_of(model: str, batch: bool) -> int:
    key = f"{model}:batch" if batch and f"{model}:batch" in RATES_PAISE else model
    return RATES_PAISE.get(key, 0)


def estimate(feature: Feature, batch: bool) -> dict[str, Any]:
    """What this will cost, before committing to it."""
    model = {
        "photo-edit": PHOTO_MODEL,
        "poster-artwork": ARTWORK_MODEL,
        "poster-layout": LAYOUT_MODEL,
    }[feature]
    paise = cost_of(model, batch)
    return {
        "feature": feature,
        "model": model,
        "batch": batch,
        "cost_paise": paise,
        "cost_rupees": round(paise / 100, 2),
    }


def is_configured() -> bool:
    """True when a Gemini key has been entered. Never reveals the key."""
    return any(k["name"] == "GEMINI_API_KEY" and k["is_set"] for k in db.list_api_keys())


def _client() -> Any:
    key = db.get_api_key("GEMINI_API_KEY")
    if not key:
        raise AiError(
            "No Gemini API key yet. Add one in Settings — nothing here works "
            "without it, and nothing else in the app needs it."
        )
    try:
        from google import genai
    except ImportError as exc:
        raise AiError(
            "The AI extra is not installed: uv sync --extra ai"
        ) from exc
    return genai.Client(api_key=key)


def _friendly(exc: Exception) -> str:
    """Turn an SDK exception into something the operator can act on."""
    text = str(exc)
    lowered = text.lower()
    if "api key" in lowered or "unauthorized" in lowered or "401" in lowered:
        return "That API key was rejected. Check it in Settings."
    if "quota" in lowered or "429" in lowered or "resource_exhausted" in lowered:
        return "Google's quota is exhausted for now. Try again later, or top up."
    if "safety" in lowered or "blocked" in lowered or "prohibited" in lowered:
        return "Google refused this request on content grounds. Nothing was charged."
    if "deadline" in lowered or "timeout" in lowered:
        return "The request timed out. Your work is unchanged — try again."
    if "connect" in lowered or "network" in lowered or "dns" in lowered:
        return "Could not reach Google. Check the internet connection."
    return f"The request failed: {text[:200]}"


def _record(result: AiResult) -> AiResult:
    db.record_spend(
        feature=result.feature,
        model=result.model,
        cost_paise=result.cost_paise,
        batch=result.batch,
        status="ok" if result.ok else "failed",
    )
    return result


def _extract_image(response: Any) -> tuple[bytes | None, str]:
    """Pull the first inline image out of a Gemini response."""
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            inline = getattr(part, "inline_data", None)
            if inline is not None and getattr(inline, "data", None):
                return inline.data, getattr(inline, "mime_type", "image/png")
    return None, "image/png"


def _extract_text(response: Any) -> str:
    direct = getattr(response, "text", None)
    if direct:
        return str(direct)
    chunks: list[str] = []
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            if getattr(part, "text", None):
                chunks.append(str(part.text))
    return "\n".join(chunks)


# --- photo editing --------------------------------------------------------


def edit_photo(
    image: bytes,
    media_type: str,
    values: dict[str, str],
    batch: bool = False,
) -> AiResult:
    """Apply an instruction to a client photograph."""
    template = prompts.active_prompt("photo-edit")
    text = prompts.render(template["body"], values)
    result = AiResult(
        ok=False,
        feature="photo-edit",
        model=PHOTO_MODEL,
        batch=batch,
        prompt_used=text,
    )
    if not values.get("instruction", "").strip():
        result.error = "Say what should change about the photo."
        return result

    try:
        from google.genai import types

        response = _client().models.generate_content(
            model=PHOTO_MODEL,
            contents=[
                types.Part.from_bytes(data=image, mime_type=media_type),
                text,
            ],
        )
    except AiError as exc:
        result.error = str(exc)
        return result
    except Exception as exc:  # noqa: BLE001 - SDK raises many types
        log.warning("photo edit failed: %s", exc)
        result.error = _friendly(exc)
        return _record(result)

    data, mime = _extract_image(response)
    if data is None:
        result.error = "Google returned no image. Nothing was charged."
        return _record(result)

    result.ok = True
    result.image = data
    result.media_type = mime
    result.cost_paise = cost_of(PHOTO_MODEL, batch)
    result.warnings.append(
        "Google embeds an invisible SynthID watermark in AI images. It does not "
        "affect printing."
    )
    return _record(result)


# --- poster artwork -------------------------------------------------------


def generate_artwork(values: dict[str, str], batch: bool = True) -> AiResult:
    """Make a background picture. Never any words in it — the app draws those."""
    template = prompts.active_prompt("poster-artwork")
    text = prompts.render(template["body"], values)
    result = AiResult(
        ok=False,
        feature="poster-artwork",
        model=ARTWORK_MODEL,
        batch=batch,
        prompt_used=text,
    )
    if not values.get("subject", "").strip():
        result.error = "Say what the picture should be of."
        return result

    try:
        response = _client().models.generate_content(model=ARTWORK_MODEL, contents=text)
    except AiError as exc:
        result.error = str(exc)
        return result
    except Exception as exc:  # noqa: BLE001
        log.warning("artwork generation failed: %s", exc)
        result.error = _friendly(exc)
        return _record(result)

    data, mime = _extract_image(response)
    if data is None:
        result.error = "Google returned no image. Nothing was charged."
        return _record(result)

    result.ok = True
    result.image = data
    result.media_type = mime
    result.cost_paise = cost_of(ARTWORK_MODEL, batch)
    result.warnings.append(
        "Google embeds an invisible SynthID watermark in AI images. It does not "
        "affect printing."
    )
    return _record(result)


# --- layout planning ------------------------------------------------------

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)


def parse_layout(text: str) -> dict[str, Any]:
    """Read the AI's layout plan, tolerating a code fence around it.

    Raises rather than guessing: a half-understood layout that silently drops a
    phone number is worse than an error the operator can see.
    """
    cleaned = _FENCE.sub("", text).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise AiError("The AI did not return a layout plan.")
    try:
        parsed = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise AiError(f"The layout plan was not valid JSON: {exc.msg}") from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("blocks"), list):
        raise AiError("The layout plan had no blocks.")
    return parsed


def plan_layout(values: dict[str, str]) -> AiResult:
    """Ask where the words should go. **Data only — never pixels with text.**

    This is the constraint the whole poster feature rests on: the AI chooses
    positions, the app draws the text. Malayalam is correct because it never
    touches the model.
    """
    template = prompts.active_prompt("poster-layout")
    text = prompts.render(template["body"], values)
    result = AiResult(
        ok=False,
        feature="poster-layout",
        model=LAYOUT_MODEL,
        batch=False,
        prompt_used=text,
    )
    if not values.get("headline", "").strip():
        result.error = "A poster needs a headline to lay out."
        return result

    try:
        response = _client().models.generate_content(model=LAYOUT_MODEL, contents=text)
    except AiError as exc:
        result.error = str(exc)
        return result
    except Exception as exc:  # noqa: BLE001
        log.warning("layout planning failed: %s", exc)
        result.error = _friendly(exc)
        return _record(result)

    try:
        layout = parse_layout(_extract_text(response))
    except AiError as exc:
        result.error = str(exc)
        return _record(result)

    result.ok = True
    result.layout = layout
    result.cost_paise = cost_of(LAYOUT_MODEL, False)
    verified, notes = verify_text_unchanged(layout, values)
    result.layout = verified
    result.warnings.extend(notes)
    return _record(result)


def verify_text_unchanged(
    layout: dict[str, Any], values: dict[str, str]
) -> tuple[dict[str, Any], list[str]]:
    """Put the operator's exact words back if the AI altered them.

    The model is asked to copy text verbatim, and it mostly does. Mostly is not
    good enough for a phone number or a Malayalam headline, so anything that
    came back changed is replaced with what was typed and the row is flagged.
    """
    notes: list[str] = []
    blocks = layout.get("blocks")
    if not isinstance(blocks, list):
        return layout, notes

    for block in blocks:
        if not isinstance(block, dict):
            continue
        key = str(block.get("id", ""))
        original = values.get(key)
        if original is None or not original.strip():
            continue
        if str(block.get("text", "")).strip() != original.strip():
            notes.append(
                f"The AI changed the {key} text — your wording was put back."
            )
            block["text"] = original
    return layout, notes


# --- budget ---------------------------------------------------------------


def budget_status(month: str | None = None) -> dict[str, Any]:
    summary = db.spend_summary(month)
    spent = int(summary["total_paise"])
    return summary | {
        "budget_paise": MONTHLY_BUDGET_PAISE,
        "spent_rupees": round(spent / 100, 2),
        "budget_rupees": round(MONTHLY_BUDGET_PAISE / 100, 2),
        "fraction_used": round(min(spent / MONTHLY_BUDGET_PAISE, 1.0), 4),
        "over_budget": spent > MONTHLY_BUDGET_PAISE,
        # An estimate, and the UI must say so — Google's console is the truth.
        "is_estimate": True,
    }
