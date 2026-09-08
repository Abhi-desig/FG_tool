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

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from backend import db
from backend.budget import MONTHLY_BUDGET_PAISE, OverBudget, budget_status, ensure_within
from backend.features import posters, prompts
from backend.posterspec import check as check_prompt

log = logging.getLogger(__name__)

# The three retired names — `poster-artwork`, `poster-layout`, `poster-copy` —
# stay valid here on purpose. They are gone from ROLES, but `ai_spend` holds real
# rows recorded under them and the Settings spend view still has to render the
# shop's own history (ADR-034).
Feature = Literal[
    "photo-edit",
    "poster",
    "poster-concept",
    "poster-artwork",
    "poster-layout",
    "poster-copy",
]

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
POSTER_MODEL = "gemini-2.5-flash-image"
CONCEPT_MODEL = "gemini-2.5-flash"


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
        key="poster",
        feature="poster",
        label="Poster",
        description="Draws the whole poster, words and all, from your design.",
        needs_image=True,
        default=POSTER_MODEL,
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
        key="concept",
        feature="poster-concept",
        label="Poster visual idea",
        description=(
            "Text only — reads the poster's words and describes the picture "
            "they should sit on. Costs almost nothing."
        ),
        needs_image=False,
        default=CONCEPT_MODEL,
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
    # Which of the styles the model chose, and the one line it gave for why.
    # Read out of the same response as the image so a poster can be explained
    # and reproduced later; empty when the model did not name a valid one
    # (ADR-037).
    style: str = ""
    style_reason: str = ""
    # Alternative sets of poster words, each in English and Malayalam.
    alternatives: list[dict[str, Any]] | None = None
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
    """Refuse a paid Gemini call once the month's budget is gone.

    `over_budget` existed and was consumed in exactly one place: the colour of a
    bar in the Settings screen. No paid route consulted it, so `/ai/artwork`,
    `/ai/photo-edit` and `/ai/layout-plan` would spend past ₹2,000 indefinitely
    (NEXT.md 1.1). The budget is now a limit rather than a decoration.

    The ceiling itself is in `backend/budget.py` — it is one budget across every
    paid feature, not one per provider.
    """
    ensure_within(override, provider="Google")


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


# --- posters ---------------------------------------------------------------
#
# Gemini draws the whole poster, words included, from one of the operator's own
# designs in `data/poster_prompts/`. ADR-034 records what that trades away: the
# words are no longer set by this app, so Malayalam in the image will be
# misspelled and nothing in the result is editable. Neither is checkable from
# here — both are said plainly in the UI instead.

# The aspect ratios Google's image models accept. Stated as a **parameter** and
# not only in the prompt text: a ratio mentioned in prose is advice, and 4:5
# drifts to square often enough to waste a call (ADR-036). An unlisted value is
# dropped rather than sent, because a rejected config fails the whole request.
ASPECT_RATIOS: tuple[str, ...] = (
    "1:1",
    "2:3",
    "3:2",
    "3:4",
    "4:3",
    "4:5",
    "5:4",
    "9:16",
    "16:9",
    "21:9",
)


def _image_config(aspect: str, want_text: bool = False) -> Any | None:
    """A request config for a drawing call, or None if there is nothing to ask for.

    `want_text` asks for a text part alongside the image. That is what carries
    the chosen style back on the auto-selection call — the image models take
    `response_modalities`, but **not** `response_schema`, so the style arrives
    as a line of text under a stated contract rather than as validated JSON.
    Parsing it is `posters.parse_choice`'s job, and an unparseable answer is
    reported as unknown rather than guessed at (ADR-037).
    """
    if aspect and aspect not in ASPECT_RATIOS:
        log.warning("Ignoring unsupported aspect ratio %r", aspect)
        aspect = ""
    if not aspect and not want_text:
        return None
    from google.genai import types

    return types.GenerateContentConfig(
        image_config=types.ImageConfig(aspect_ratio=aspect) if aspect else None,
        response_modalities=["TEXT", "IMAGE"] if want_text else None,
    )


def nearest_aspect(width: int, height: int) -> str:
    """The supported ratio closest to an image's real shape.

    Used when changing a poster that already exists. The freeze clause promises
    the same crop and aspect ratio; sending no ratio at all leaves the model to
    pick one, which is how a portrait poster comes back square with its type cut
    off. Compared on the ratio itself rather than by name so a 1024×1280 poster
    resolves to 4:5 whatever the design said.
    """
    if width <= 0 or height <= 0:
        return ""
    actual = width / height
    return min(
        ASPECT_RATIOS,
        key=lambda r: abs(actual - (int(r.split(":")[0]) / int(r.split(":")[1]))),
    )


def poster_concept(copy: dict[str, str], over_budget_ok: bool = False) -> AiResult:
    """Turn the poster's words into a visual idea, in text.

    Runs only when the operator gave no reference image. A picture they chose
    themselves is a better brief than anything this can write, and paying for
    both would be paying twice to be told something less useful.
    """
    template = prompts.active_prompt("poster-concept")
    text = prompts.render(template["body"], dict(copy))
    model = model_for("concept")
    result = AiResult(
        ok=False,
        feature="poster-concept",
        model=model,
        batch=False,
        prompt_used=text,
    )
    if not (copy.get("main") or "").strip():
        result.error = "Write the poster's headline first — the idea comes from it."
        return result

    try:
        check_budget("poster-concept", False, over_budget_ok)
    except OverBudget as exc:
        result.error = str(exc)
        return result

    try:
        response = _client().models.generate_content(model=model, contents=text)
    except AiError as exc:
        result.error = str(exc)
        return result
    except Exception as exc:  # noqa: BLE001 - the SDK raises many types
        log.warning("poster concept failed: %s", exc)
        result.error = _friendly(exc)
        return _record(result)

    idea = _extract_text(response).strip()
    if not idea:
        result.error = "Google returned a reply with no visual idea in it."
        return _record(result)

    result.ok = True
    result.text = idea
    result.cost_paise = cost_of("concept", False)
    return _record(result)


def generate_poster(
    copy: dict[str, str],
    concept: str = "",
    reference: tuple[bytes, str] | None = None,
    batch: bool = True,
    over_budget_ok: bool = False,
    force_style: str = "",
) -> AiResult:
    """Choose a style and draw the poster, in one call.

    The operator supplies copy and nothing else. The model is shown every
    style's spec, picks the one that fits the words, names its choice in the
    reply, and draws — so there is one round trip and one charge, and the choice
    is a field rather than something to infer from the picture (ADR-037).

    `force_style` pins the choice instead, which is how all nine stay testable
    without hand-writing copy that triggers each one. A pinned style also gets
    its spec at full length, rather than compressed alongside eight others.
    """
    model = model_for("poster")
    result = AiResult(ok=False, feature="poster", model=model, batch=batch)

    if not (copy.get("main") or "").strip():
        result.error = "Write the poster's headline first — there is nothing to set."
        return result

    available = posters.designs()
    try:
        if force_style:
            pinned = posters.design(force_style)
            text = posters.one_style_prompt(pinned, copy, concept)
            aspect = pinned.aspect
        else:
            pinned = None
            text = posters.selection_prompt(copy, concept, available)
            aspect = posters.auto_aspect(available)
    except posters.PosterError as exc:
        result.error = str(exc)
        return result

    result.prompt_used = text

    # The engine's own reject list, read before a rupee is spent. Only the digit
    # check refuses; the rest is shown beside the poster (ADR-036).
    report = check_prompt(text, copy)
    result.warnings.extend(report.warnings)
    if not report.ok:
        result.error = " ".join(report.refusals)
        return result

    try:
        check_budget("poster", batch, over_budget_ok)
    except OverBudget as exc:
        result.error = str(exc)
        return result

    contents: list[Any] = [text]
    if reference is not None:
        from google.genai import types

        data, media_type = reference
        # Ahead of the prompt, matching `edit_photo`: the picture is the brief,
        # and the words that follow say what to do with it.
        contents = [types.Part.from_bytes(data=data, mime_type=media_type), text]

    drawn = _draw(
        result,
        model,
        contents,
        "poster",
        batch,
        _image_config(aspect, want_text=force_style == ""),
    )

    if pinned is not None:
        # Nothing was chosen, so nothing is reported as chosen. The style is
        # known because the operator pinned it, and saying the model picked it
        # would make the log lie.
        drawn.style = pinned.key
        drawn.style_reason = "pinned by the dev override"
    return drawn


def refine_poster(
    image: bytes,
    media_type: str,
    note: str,
    batch: bool = False,
    over_budget_ok: bool = False,
    aspect: str = "",
) -> AiResult:
    """Change the poster that was just made, keeping the rest of it.

    The previous poster goes back with the operator's note, so a design they
    nearly liked is adjusted rather than replaced. Not batched by default:
    this is the iterating step, and waiting minutes between attempts is what
    stops an operator iterating at all.

    The note is wrapped in the `poster-edit` template rather than a sentence
    written here. An image model asked to change one thing will happily redraw
    the scene around it, and what stops that is naming everything that must not
    move — framing, light, grade, every text element — which is long enough to
    be worth the operator being able to read and tune it in Settings (ADR-036).
    """
    model = model_for("poster")
    result = AiResult(ok=False, feature="poster", model=model, batch=batch)

    if not note.strip():
        result.error = "Say what should change about the poster."
        return result

    template = prompts.active_prompt("poster-edit")
    instruction = prompts.render(template["body"], {"note": note.strip()})
    result.prompt_used = instruction

    try:
        check_budget("poster", batch, over_budget_ok)
    except OverBudget as exc:
        result.error = str(exc)
        return result

    from google.genai import types

    contents = [types.Part.from_bytes(data=image, mime_type=media_type), instruction]
    return _draw(result, model, contents, "poster", batch, _image_config(aspect))


def _draw(
    result: AiResult,
    model: str,
    contents: list[Any],
    role: str,
    batch: bool,
    config: Any | None = None,
) -> AiResult:
    """Send a drawing request and unpack the picture.

    Shared by generating and refining because the failure handling is the part
    that matters and it must not drift between them: a refusal is a 200 with a
    plain reason (ADR-023), and a reply with no image is reported as possibly
    billed rather than assumed free.
    """
    try:
        # `config` is omitted rather than passed as None: a stub client in the
        # tests takes the same call, and an unexpected keyword is a failure that
        # would only show up against the real SDK.
        extra = {"config": config} if config is not None else {}
        response = _client().models.generate_content(
            model=model, contents=contents, **extra
        )
    except AiError as exc:
        result.error = str(exc)
        return result
    except Exception as exc:  # noqa: BLE001 - the SDK raises many types
        log.warning("poster generation failed: %s", exc)
        result.error = _friendly(exc)
        return _record(result)

    data, mime = _extract_image(response)
    if data is None:
        result.error = (
            "Google returned a reply but no poster. This may still have been "
            "billed — the app cannot know. Check Google's console before "
            "assuming it was free."
        )
        return _record(result)

    result.ok = True
    result.image = data
    result.media_type = mime
    result.cost_paise = cost_of(role, batch)
    result.warnings.append(
        "Google embeds an invisible SynthID watermark in AI images. It does not "
        "affect printing."
    )

    # The chosen style, out of the text part of this same response. Only looked
    # for when the caller asked for one — `refine_poster` sends no catalogue, so
    # a `STYLE:` line there would be the model echoing something invented.
    if role == "poster" and result.style == "" and _wants_text(config):
        said = _extract_text(response)
        result.style, result.style_reason = posters.parse_choice(said)
        if not result.style:
            # The poster is real and paid for; only the label is missing. Losing
            # the poster over an unparseable line would be the worse trade, so
            # this is a warning and the field stays empty rather than guessed.
            result.warnings.append(
                "The model drew the poster but did not say which style it chose, "
                "so this one cannot be reproduced from the log. What it replied: "
                f"{said.strip()[:200] or '(nothing)'}"
            )
    return _record(result)


def _wants_text(config: Any | None) -> bool:
    """Whether this request asked for a text part beside the image."""
    return bool(config is not None and getattr(config, "response_modalities", None))


# --- budget ---------------------------------------------------------------
#
# `MONTHLY_BUDGET_PAISE`, `OverBudget` and `budget_status` are re-exported from
# `backend/budget.py` so existing callers — the API layer and the Settings
# screen — keep working unchanged. The ceiling is shared with the Excel
# verifier; the shop has one AI budget, not one per provider.

__all__ = [
    "POSTER_MODEL",
    "MONTHLY_BUDGET_PAISE",
    "AiError",
    "AiResult",
    "OverBudget",
    "budget_status",
]
