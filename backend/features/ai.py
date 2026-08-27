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
from backend.features import prompts, styles

log = logging.getLogger(__name__)

Feature = Literal["photo-edit", "poster-artwork", "poster-layout"]

# --- which model does which job -------------------------------------------
#
# **Model names are settings, not constants.** Google renames and retires these
# on its own schedule, and a name that no longer exists fails as a 404 halfway
# through a job — which is precisely how 0.1.0 shipped calling three models that
# had never existed at all. So the names below are only *defaults*: the operator
# can pick from the live list in Settings, and testing the key repairs a stale
# choice automatically. Nothing here is load-bearing enough to need a code
# change when Google moves.

PHOTO_MODEL = "gemini-2.5-flash-image"
ARTWORK_MODEL = "gemini-2.5-flash-image"
LAYOUT_MODEL = "gemini-2.5-flash"


@dataclass(frozen=True)
class Role:
    """One job, and the models that can do it."""

    key: str
    feature: Feature
    label: str
    description: str
    # Whether this job needs a model that can return a picture.
    needs_image: bool
    default: str
    # Tried in order when the configured model has gone missing. Ordered by
    # running cost, not by quality — silently upgrading the shop to a dearer
    # model is a spending decision, and those are the operator's to make.
    fallbacks: tuple[str, ...]
    rate_paise: int
    batch_rate_paise: int | None = None


ROLES: tuple[Role, ...] = (
    Role(
        key="artwork",
        feature="poster-artwork",
        label="Poster artwork",
        description="Draws the picture behind the poster. Never any words in it.",
        needs_image=True,
        default=ARTWORK_MODEL,
        fallbacks=(
            "gemini-2.5-flash-image",
            "gemini-3-pro-image-preview",
            "gemini-2.0-flash-preview-image-generation",
        ),
        rate_paise=1150,  # ~₹11.50 instant
        batch_rate_paise=600,  # ~₹6 batch — half price
    ),
    Role(
        key="photo",
        feature="photo-edit",
        label="Photo editing",
        description="Changes a client photograph you upload.",
        needs_image=True,
        default=PHOTO_MODEL,
        fallbacks=("gemini-2.5-flash-image", "gemini-3-pro-image-preview"),
        rate_paise=400,  # ~₹4
    ),
    Role(
        key="layout",
        feature="poster-layout",
        label="Layout planning",
        description="Text only — asks where the words should go. Costs almost nothing.",
        needs_image=False,
        default=LAYOUT_MODEL,
        fallbacks=(
            "gemini-2.5-flash",
            "gemini-flash-latest",
            "gemini-2.5-flash-lite",
            "gemini-2.0-flash",
        ),
        rate_paise=5,  # negligible, but not free
    ),
)

ROLE_BY_KEY = {r.key: r for r in ROLES}
ROLE_BY_FEATURE = {r.feature: r for r in ROLES}
PREFERENCE_KEY = {r.key: f"ai_model_{r.key}" for r in ROLES}

# Converted at ~₹88/USD and rounded up. An **estimate** — the budget meter says
# so, and Google's console is the truth.
#
# Keyed by *role*, not by model, because the model is now the operator's choice
# and one model can do two jobs at very different sizes: the same image model
# both edits a photograph and paints a full poster background. Pricing the job
# rather than the model keeps the quote right whichever name Google is using
# this month.
RATES_PAISE: dict[str, int] = {}
for _role in ROLES:
    RATES_PAISE[_role.key] = _role.rate_paise
    if _role.batch_rate_paise is not None:
        RATES_PAISE[f"{_role.key}:batch"] = _role.batch_rate_paise

MONTHLY_BUDGET_PAISE = 200_000  # ₹2,000


class AiError(RuntimeError):
    """Something about the request itself is wrong, before any spend."""


class OverBudget(AiError):
    """The month's budget is used up and no override was given.

    Separate from `AiError` so the API layer can answer with a status the UI can
    act on — offering the override — rather than a generic refusal.
    """


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


def model_for(role: Role | str) -> str:
    """The model this job should use — the operator's choice, or the default.

    Reads settings every call rather than caching, because changing the model in
    Settings has to take effect on the very next job. These are cheap SQLite
    reads on a single-operator machine.
    """
    spec = ROLE_BY_KEY[role] if isinstance(role, str) else role
    chosen = db.get_preferences().get(PREFERENCE_KEY[spec.key], "").strip()
    return chosen or spec.default


def cost_of(model_or_role: str, batch: bool = False) -> int:
    """What one call costs, in paise.

    Accepts a role key ("artwork") or a model name. A model that serves two
    roles — the same image model both edits photographs and paints poster
    backgrounds — is priced at the dearer of them, so a quote given by model
    name is never an under-quote.
    """
    if model_or_role in ROLE_BY_KEY:
        key = model_or_role
        batch_key = f"{key}:batch"
        if batch and batch_key in RATES_PAISE:
            return RATES_PAISE[batch_key]
        return RATES_PAISE.get(key, 0)

    matches = [r for r in ROLES if model_for(r) == model_or_role or r.default == model_or_role]
    if not matches:
        return 0
    return max(cost_of(r.key, batch) for r in matches)


def has_batch_discount(feature: Feature) -> bool:
    """Whether waiting actually saves anything on this job.

    Only artwork defines a batch rate. The module docstring says "Batch is the
    default. Half price for a few minutes' wait" — true for artwork, and for
    photo editing and layout planning a wait for nothing (NEXT.md 1.4). The UI
    hides the toggle where this is false rather than offering a saving that does
    not exist.
    """
    return ROLE_BY_FEATURE[feature].batch_rate_paise is not None


def estimate(feature: Feature, batch: bool) -> dict[str, Any]:
    """What this will cost, before committing to it."""
    role = ROLE_BY_FEATURE[feature]
    discount = has_batch_discount(feature)
    # Asking for batch where there is no batch rate is not an error, but it must
    # not be reported as a saving either.
    paise = cost_of(role.key, batch and discount)
    return {
        "feature": feature,
        "model": model_for(role),
        "batch": batch,
        "cost_paise": paise,
        "cost_rupees": round(paise / 100, 2),
        "batch_discount": discount,
        "instant_paise": cost_of(role.key, False),
        "batch_paise": cost_of(role.key, True) if discount else None,
    }


def check_budget(feature: Feature, batch: bool, override: bool = False) -> None:
    """Refuse a paid call once the month's budget is gone.

    `over_budget` existed and was consumed in exactly one place: the colour of a
    bar in the Settings screen. No paid route consulted it, so `/ai/artwork`,
    `/ai/photo-edit` and `/ai/layout-plan` would spend past ₹2,000 indefinitely
    (NEXT.md 1.1). The budget is now a limit rather than a decoration.

    `override` is the operator's explicit "spend anyway" — this is their shop and
    their money, so the ceiling must be passable. It just may not be passed by
    accident.
    """
    if override:
        return
    status = budget_status()
    if not status["over_budget"]:
        return
    raise OverBudget(
        f"This month's AI spending has reached ₹{status['spent_rupees']:.2f}, past "
        f"the ₹{status['budget_rupees']:.0f} budget. Nothing was sent and nothing "
        f"was charged. Check Google's console for the real figure — this total is "
        f"an estimate — then tick “spend past the budget” to continue."
    )


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


def key_shape_note(value: str) -> str | None:
    """Say so immediately when a pasted key is the wrong *kind* of credential.

    Google changed the format in 2026, and this function used to have it exactly
    backwards — it told the operator that a current key was an OAuth token and
    sent them off to make a deprecated one. The two formats now are:

    * `AQ.Ab…` — an **auth key**, bound to a service account and restricted to
      the Gemini API by default. This is what aistudio.google.com/apikey issues
      today, and the only kind it will issue for a new key.
    * `AIza…` — a **standard key**, the old format. Still accepted for now, but
      only while restricted to the Gemini API, and Google stops accepting them
      altogether in September 2026.

    Neither shape is refused: a key is stored whatever it looks like, because
    guessing at Google's format is what caused the original bug.
    """
    value = value.strip()
    if value.startswith("AQ."):
        return None
    if value.startswith("AIza"):
        return (
            "That is an old-style standard key. It still works today only if you "
            "have restricted it to the Gemini API, and Google stops accepting "
            "standard keys altogether in September 2026. Create a replacement at "
            "aistudio.google.com/apikey — new keys begin with AQ."
        )
    if value.startswith("ya29."):
        return (
            "That is a short-lived Google OAuth access token; it will stop working "
            "within the hour. Get a proper key from aistudio.google.com/apikey."
        )
    return (
        "That does not look like a Gemini API key. Keys from "
        "aistudio.google.com/apikey begin with AQ. — saved anyway, in case Google "
        "has changed the format again."
    )


def _friendly(exc: Exception) -> str:
    """Turn an SDK exception into something the operator can act on."""
    text = str(exc)
    lowered = text.lower()
    # Both of these are checked before the generic key case. Google returns the
    # same "invalid authentication credentials" text for either, which reads as a
    # mistyped key when the real problem is the key's standing with Google — and
    # no amount of re-pasting it will help.
    if "api_key_service_blocked" in lowered:
        return (
            "Google recognises this key but is blocking it from the Gemini API. "
            "That is set on Google's side, not here: open "
            "aistudio.google.com/apikey, check the key is still listed and "
            "restricted to the Gemini API, and that its project still has "
            "billing enabled. If it is missing or flagged, create a new key. "
            "Nothing was charged."
        )
    # The SDK always authenticates with the `x-goog-api-key` header, so this is
    # the branch the operator actually reaches when a key has been revoked or
    # blocked — Google only says so plainly on the header we do not use. Hence
    # the checks below rather than a bare "wrong credential type".
    if "access_token_type_unsupported" in lowered or "expected oauth 2" in lowered:
        return (
            "Google will not accept this key. Open aistudio.google.com/apikey and "
            "check three things: the key is still listed there (Google withdraws "
            "keys it finds published anywhere), it is restricted to the Gemini "
            "API, and its project still has billing on. If it is missing or "
            "flagged, make a new key — re-pasting a withdrawn one will not help. "
            "Nothing was charged."
        )
    if "is not found for api version" in lowered or "is not supported for" in lowered:
        missing = re.search(r"models/([\w.\-]+)", text)
        name = missing.group(1) if missing else "that model"
        return (
            f"Google has no model called {name} any more. Open Settings → AI "
            "models, press Refresh, and pick one from the list. Nothing was "
            "charged."
        )
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
    return f"The request failed: {_redact(text)[:200]}"


# Anything key-shaped. Both Gemini formats, plus a bearer token and a generic
# long opaque credential, since the SDK's exception text is not ours to predict.
_SECRET_SHAPES = re.compile(
    r"""
      AQ\.[A-Za-z0-9_\-]{8,}          # current Gemini auth key
    | AIza[A-Za-z0-9_\-]{10,}         # old-style standard key
    | ya29\.[A-Za-z0-9_\-.]{10,}      # OAuth access token
    | (?i:bearer)\s+[A-Za-z0-9_\-.=]{12,}
    | (?i:(?:api[_-]?key|key|token|authorization)["'\s:=]+)[A-Za-z0-9_\-.=]{12,}
    """,
    re.VERBOSE,
)


def _redact(text: str) -> str:
    """Strip anything key-shaped out of an SDK exception before it is shown.

    `_friendly`'s fallback echoes the raw exception to the UI, which made it the
    one path in the app where a credential could reach the screen — against
    SECURITY.md §2, which says a key is never logged and never in an error
    message (NEXT.md 3.17).

    The SDK usually does not include the key. "Usually" is not the standard for
    the only thing here with direct monetary value, and Google is free to change
    its error text whenever it likes.
    """
    return _SECRET_SHAPES.sub("[redacted]", text)


def friendly_error(exc: Exception) -> str:
    """Turn an SDK exception into something the operator can act on.

    The public name. `api/ai.py` reached across the module boundary for
    `_friendly` twice (NEXT.md 3.16); this is the same function without the
    `noqa: SLF001` at each call site.
    """
    return _friendly(exc)


def _record(result: AiResult) -> AiResult:
    db.record_spend(
        feature=result.feature,
        model=result.model,
        cost_paise=result.cost_paise,
        batch=result.batch,
        status="ok" if result.ok else "failed",
    )
    return result


# --- which models actually exist ------------------------------------------


def _short(name: str) -> str:
    """`models/gemini-2.5-flash` → `gemini-2.5-flash`."""
    return name.split("/", 1)[-1]


def available_models() -> list[dict[str, Any]]:
    """Ask Google what this key can call. Free, and the only honest answer.

    Guessing a model name is what broke this feature once already. The list is
    fetched rather than hardcoded so the Settings screen shows what is really
    there on the day the operator looks.
    """
    client = _client()
    found: list[dict[str, Any]] = []
    for model in client.models.list():
        actions = {str(a) for a in (getattr(model, "supported_actions", None) or [])}
        # An empty action list means the SDK did not report them; assume usable
        # rather than hiding a model the operator can see in Google's console.
        if actions and "generateContent" not in actions:
            continue
        name = _short(str(getattr(model, "name", "")))
        if not name:
            continue
        found.append(
            {
                "name": name,
                "label": str(getattr(model, "display_name", "") or name),
                "description": str(getattr(model, "description", "") or ""),
                # A heuristic, and the picker says so: Google does not declare
                # "returns pictures" in the model list, and the name is the only
                # signal that survives a rename.
                "image_output": "image" in name,
                "input_token_limit": getattr(model, "input_token_limit", None),
            }
        )
    found.sort(key=lambda m: m["name"])
    return found


def model_settings(live: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """What the Settings screen shows. Works with no key — just no live list."""
    names = {m["name"] for m in (live or [])}
    return {
        "roles": [
            {
                "key": role.key,
                "label": role.label,
                "description": role.description,
                "needs_image": role.needs_image,
                "chosen": model_for(role),
                "default": role.default,
                "is_default": model_for(role) == role.default,
                # Only meaningful once a list has been fetched; the UI does not
                # cry "missing" at a model it has simply never checked.
                "confirmed": (model_for(role) in names) if names else None,
                "cost_rupees": round(cost_of(role.key, batch=False) / 100, 2),
            }
            for role in ROLES
        ],
        "models": live or [],
    }


# Models that can be auto-selected when a configured name is retired and none of
# the role's own fallbacks are available either.
#
# An allowlist by *family*, so a name Google has not invented yet
# ("gemini-4-flash") is still eligible while an embedding, TTS, or vision-only
# model never is. Auto-selection is a last resort; anything outside this is the
# operator's deliberate choice in Settings, not ours.
_GENERATIVE_FAMILIES = ("gemini-",)

# Substrings that mean "not a text/image generator", whatever the family.
_NOT_GENERATIVE = (
    "embedding",
    "embed",
    "aqa",
    "tts",
    "text-to-speech",
    "imagen",  # a different API surface; generate_content does not drive it
    "veo",
    "live",
)


def _safe_last_resort(role: Role, live: list[dict[str, Any]]) -> str | None:
    """A model of the right shape that is plausibly able to do the job.

    See the call site: the previous rule could hand the layout role an embedding
    model. This refuses rather than guesses.
    """
    for model in live:
        name = str(model["name"])
        lowered = name.lower()
        if not any(lowered.startswith(f) for f in _GENERATIVE_FAMILIES):
            continue
        if any(bad in lowered for bad in _NOT_GENERATIVE):
            continue
        if bool(model["image_output"]) != role.needs_image:
            continue
        return name
    return None


def resolve_models() -> dict[str, Any]:
    """Repair any model choice Google no longer honours.

    Called when the key is tested, which is the moment the operator is already
    asking "does this work" — so a name that has been retired is fixed then and
    there rather than surfacing as a 404 in the middle of a paid job.
    """
    live = available_models()
    names = {m["name"] for m in live}
    updates: dict[str, str] = {}
    notes: list[str] = []

    for role in ROLES:
        current = model_for(role)
        if current in names:
            continue
        replacement = next(
            (candidate for candidate in role.fallbacks if candidate in names), None
        )
        if replacement is None:
            # Last resort — but only from a known-good family.
            #
            # This used to match on `image_output == role.needs_image`, where
            # `image_output` is just `"image" in name`. For the layout role that
            # meant "the first model alphabetically without 'image' in its name",
            # and because the `supported_actions` filter deliberately admits
            # models that report no actions at all, an embedding model could win
            # and then fail mid-job (NEXT.md 1.3). Leaving it unset and saying so
            # is better than picking something that cannot do the work.
            replacement = _safe_last_resort(role, live)
        if replacement is None:
            notes.append(
                f"{role.label}: {current} is gone and nothing here can replace it. "
                "Pick a model by hand in Settings."
            )
            continue
        updates[PREFERENCE_KEY[role.key]] = replacement
        notes.append(f"{role.label}: {current} is gone — now using {replacement}.")

    if updates:
        db.set_preferences(updates)
    return {"models": live, "changed": bool(updates), "notes": notes} | model_settings(live)


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
    over_budget_ok: bool = False,
) -> AiResult:
    """Apply an instruction to a client photograph."""
    template = prompts.active_prompt("photo-edit")
    text = prompts.render(template["body"], values)
    model = model_for("photo")
    result = AiResult(
        ok=False,
        feature="photo-edit",
        model=model,
        batch=batch,
        prompt_used=text,
    )
    if not values.get("instruction", "").strip():
        result.error = "Say what should change about the photo."
        return result

    # Before the request, so a refusal costs nothing and is not recorded as one.
    try:
        check_budget("photo-edit", batch, over_budget_ok)
    except OverBudget as exc:
        result.error = str(exc)
        return result

    try:
        from google.genai import types

        response = _client().models.generate_content(
            model=model,
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
        result.error = (
            "Google returned a reply but no image. This may still have been "
            "billed — the app cannot know. Check Google's console before "
            "assuming it was free."
        )
        return _record(result)

    result.ok = True
    result.image = data
    result.media_type = mime
    result.cost_paise = cost_of("photo", batch)
    result.warnings.append(
        "Google embeds an invisible SynthID watermark in AI images. It does not "
        "affect printing."
    )
    return _record(result)


# --- poster artwork -------------------------------------------------------


def artwork_prompt(values: dict[str, str], style_key: str | None = None) -> str:
    """The exact words that will be sent, without sending them.

    Free, so the designer can show the operator the finished prompt before
    anything is spent — the style owns the structure, the operator owns the
    copy, and neither should have to take the other on trust.
    """
    if style_key:
        return styles.build_prompt(styles.by_key(style_key), values)
    template = prompts.active_prompt("poster-artwork")
    return prompts.render(template["body"], values)


def generate_artwork(
    values: dict[str, str],
    batch: bool = True,
    style_key: str | None = None,
    over_budget_ok: bool = False,
) -> AiResult:
    """Make a background picture. Never any words in it — the app draws those.

    With a style, the picture is generated *from the poster's own copy* through
    that style's fixed prompt structure. Without one, this falls back to the
    prompt library's free-form `poster-artwork` template, which is still how the
    photo-edit screen and any older saved job reach it.
    """
    model = model_for("artwork")
    try:
        text = artwork_prompt(values, style_key)
    except (db.NotFound, styles.StyleError) as exc:
        return AiResult(
            ok=False,
            feature="poster-artwork",
            model=model,
            batch=batch,
            error=f"That design style is not usable: {exc}",
        )

    result = AiResult(
        ok=False,
        feature="poster-artwork",
        model=model,
        batch=batch,
        prompt_used=text,
    )
    # A style poster is described by its headline; a free-form one by a subject.
    if not (values.get("subject") or values.get("headline") or "").strip():
        result.error = (
            "Write the poster's headline first — the picture is made from it."
            if style_key
            else "Say what the picture should be of."
        )
        return result

    try:
        check_budget("poster-artwork", batch, over_budget_ok)
    except OverBudget as exc:
        result.error = str(exc)
        return result

    try:
        response = _client().models.generate_content(model=model, contents=text)
    except AiError as exc:
        result.error = str(exc)
        return result
    except Exception as exc:  # noqa: BLE001
        log.warning("artwork generation failed: %s", exc)
        result.error = _friendly(exc)
        return _record(result)

    data, mime = _extract_image(response)
    if data is None:
        result.error = (
            "Google returned a reply but no image. This may still have been "
            "billed — the app cannot know. Check Google's console before "
            "assuming it was free."
        )
        return _record(result)

    result.ok = True
    result.image = data
    result.media_type = mime
    result.cost_paise = cost_of("artwork", batch)
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


def plan_layout(values: dict[str, str], over_budget_ok: bool = False) -> AiResult:
    """Ask where the words should go. **Data only — never pixels with text.**

    This is the constraint the whole poster feature rests on: the AI chooses
    positions, the app draws the text. Malayalam is correct because it never
    touches the model.
    """
    template = prompts.active_prompt("poster-layout")
    text = prompts.render(template["body"], values)
    model = model_for("layout")
    result = AiResult(
        ok=False,
        feature="poster-layout",
        model=model,
        batch=False,
        prompt_used=text,
    )
    if not values.get("headline", "").strip():
        result.error = "A poster needs a headline to lay out."
        return result

    try:
        check_budget("poster-layout", False, over_budget_ok)
    except OverBudget as exc:
        result.error = str(exc)
        return result

    try:
        response = _client().models.generate_content(model=model, contents=text)
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
    result.cost_paise = cost_of("layout", False)
    verified, notes = verify_text_unchanged(layout, values)
    result.layout = verified
    result.warnings.extend(notes)
    return _record(result)


# Where a re-inserted block goes when the model dropped it entirely. Matches the
# designer's own opening layout, so a recovered line lands somewhere sensible
# rather than on top of another one.
#
# **This is also the list of fields that may become text on the poster**, and
# that is load-bearing. `plan_layout` is given `tone` too — a styling hint like
# "festive" — and a first version of the dropped-block repair re-inserted every
# non-empty input value, which would have printed the word "festive" on a
# client's poster. Caught by running the shipped self-check.
_FALLBACK_PLACEMENT: dict[str, dict[str, Any]] = {
    "occasion": {"y": 0.10, "size": "medium"},
    "headline": {"y": 0.22, "size": "large"},
    "offer": {"y": 0.45, "size": "huge"},
    "phone": {"y": 0.86, "size": "small"},
}


def verify_text_unchanged(
    layout: dict[str, Any], values: dict[str, str]
) -> tuple[dict[str, Any], list[str]]:
    """Put the operator's exact words back if the AI altered *or dropped* them.

    The model is asked to copy text verbatim, and it mostly does. Mostly is not
    good enough for a phone number or a Malayalam headline, so anything that came
    back changed is replaced with what was typed and the row is flagged.

    **Dropping is checked too, and used not to be.** This only ever repaired
    blocks that came back, so a layout that omitted the phone number — or
    returned it under a different `id` — passed silently: the exact failure the
    docstring claimed to prevent (NEXT.md 1.2). Every non-empty input field must
    now appear in the result, or it is re-inserted and said out loud.
    """
    notes: list[str] = []
    blocks = layout.get("blocks")
    if not isinstance(blocks, list):
        # No usable blocks at all: rebuild from what was typed rather than
        # handing back a layout with none of the operator's words in it.
        rebuilt = [
            {"id": key, "text": values[key].strip(), **placement}
            for key, placement in _FALLBACK_PLACEMENT.items()
            if values.get(key, "").strip()
        ]
        if rebuilt:
            notes.append(
                "The AI returned no usable blocks — your lines were laid out with "
                "the standard placement instead. Move them as you like."
            )
            return layout | {"blocks": rebuilt}, notes
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

    # Anything the model never returned. Re-inserted rather than lost: losing a
    # line of the client's wording is exactly the invisible error this shop
    # cannot afford, and it is the one this function exists to stop.
    #
    # Only the copy roles in `_FALLBACK_PLACEMENT` are eligible — see the note
    # there. A field that is not poster copy must never become text on a poster.
    returned = {
        str(b.get("id", "")) for b in blocks if isinstance(b, dict)
    }
    for key, placement in _FALLBACK_PLACEMENT.items():
        text = values.get(key, "")
        if not text or not text.strip() or key in returned:
            continue
        blocks.append({"id": key, "text": text.strip(), **placement})
        notes.append(
            f"The AI left out the {key} line — it was added back at the standard "
            f"position. Check where it sits."
        )

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
