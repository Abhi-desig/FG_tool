"""Phase 5 routes: API keys, prompt library, spend, and the paid calls.

Two rules are enforced by the shape of this module rather than by discipline:

* **A key never leaves the server.** No route returns one, and there is no route
  that could — `db.list_api_keys()` returns hints, and nothing else reads the
  plaintext except the Gemini client.
* **Nothing spends money by accident.** `/ai/estimate` is free and says what a
  call will cost; the paid routes each report `cost_paise` back so the running
  total is built from what actually happened.
"""

from __future__ import annotations

import base64
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from backend import config, crypto, db
from backend.features import ai, images, prompts, styles

router = APIRouter(prefix="/api", tags=["ai"])


# --- API keys --------------------------------------------------------------


class KeyIn(BaseModel):
    value: str = Field(min_length=8, max_length=500)


@router.get("/settings/keys")
def list_keys() -> dict[str, object]:
    """Hints only. There is deliberately no route that returns a key."""
    return {
        "keys": db.list_api_keys(),
        "encryption_key_location": crypto.key_location(),
        "configured": ai.is_configured(),
    }


@router.put("/settings/keys/{name}")
def put_key(name: str, body: KeyIn) -> dict[str, object]:
    try:
        db.set_api_key(name, body.value)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    # Stored either way — refusing a key on its shape would be wrong the day
    # Google changes the format — but a credential of the obviously wrong kind
    # is named now rather than after a confusing authentication failure.
    warning = ai.key_shape_note(body.value) if name == "GEMINI_API_KEY" else None
    return {"keys": db.list_api_keys(), "warning": warning}


@router.delete("/settings/keys/{name}")
def delete_key(name: str) -> dict[str, object]:
    db.delete_api_key(name)
    return {"keys": db.list_api_keys()}


@router.post("/settings/keys/{name}/test")
def test_key(name: str) -> dict[str, object]:
    """Prove the key works, and repair any model name Google has retired.

    Listing models is free and answers both questions at once: whether the
    credential is accepted, and whether the models this app is configured to
    call still exist. Fixing a stale name here — while the operator is already
    asking "does this work" — is what stops it surfacing as a 404 in the middle
    of a paid job, which is how this broke the first time.
    """
    if name != "GEMINI_API_KEY":
        raise HTTPException(400, f"No connection test for {name} yet.")
    if not db.get_api_key(name):
        raise HTTPException(404, "That key is not set.")

    try:
        resolved = ai.resolve_models()
    except Exception as exc:  # noqa: BLE001 - report, never raise past here
        db.record_key_test(name, False)
        return {
            "ok": False,
            "message": ai.friendly_error(exc),
            "keys": db.list_api_keys(),
            "models": ai.model_settings(),
        }

    db.record_key_test(name, True)
    notes = list(resolved["notes"])
    return {
        "ok": True,
        "message": " ".join(["Connected.", *notes]),
        "notes": notes,
        "keys": db.list_api_keys(),
        "models": resolved,
    }


# --- which model does which job --------------------------------------------


class ModelChoiceIn(BaseModel):
    """Empty string means "go back to the shipped default"."""

    artwork: str | None = Field(default=None, max_length=120)
    photo: str | None = Field(default=None, max_length=120)
    layout: str | None = Field(default=None, max_length=120)


@router.get("/ai/models")
def ai_models(refresh: bool = False) -> dict[str, object]:
    """The chosen models, and — with `refresh` — what Google actually offers.

    The live list needs a working key, so a failure here is reported as data
    rather than an error: the picker still shows what is configured.
    """
    if not refresh:
        return ai.model_settings()
    try:
        return ai.model_settings(ai.available_models()) | {"error": None}
    except Exception as exc:  # noqa: BLE001 - report, never raise past here
        return ai.model_settings() | {"error": ai.friendly_error(exc)}


@router.put("/ai/models")
def put_ai_models(body: ModelChoiceIn) -> dict[str, object]:
    updates = {
        ai.PREFERENCE_KEY[role]: (value or "").strip()
        for role, value in body.model_dump().items()
        if value is not None
    }
    if not updates:
        return ai.model_settings()
    try:
        db.set_preferences(updates)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return ai.model_settings()


# --- prompt library --------------------------------------------------------


class PromptIn(BaseModel):
    scope: str
    name: str = Field(min_length=1, max_length=120)
    body: str = Field(max_length=20_000)
    prompt_id: int | None = None


@router.get("/settings/prompts")
def get_prompts(scope: str | None = None) -> dict[str, object]:
    prompts.seed_defaults()
    return {"scopes": prompts.describe_scopes(), "prompts": prompts.list_prompts(scope)}


@router.post("/settings/prompts/validate")
def validate_prompt(body: PromptIn) -> dict[str, object]:
    """Catch a bad variable at edit time, not halfway through a paid job."""
    try:
        return {
            "problems": prompts.validate(body.scope, body.body),
            "variables": prompts.variables_in(body.body),
        }
    except prompts.PromptError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("/settings/prompts")
def save_prompt(body: PromptIn) -> dict[str, object]:
    try:
        saved = prompts.save_prompt(body.scope, body.name, body.body, body.prompt_id)
    except prompts.PromptError as exc:
        raise HTTPException(400, str(exc)) from exc
    except db.NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"prompt": saved, "problems": prompts.validate(body.scope, body.body)}


@router.post("/settings/prompts/{prompt_id}/activate")
def activate_prompt(prompt_id: int) -> dict[str, object]:
    try:
        return {"prompt": prompts.set_active(prompt_id)}
    except db.NotFound as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/settings/prompts/{prompt_id}/restore-default")
def restore_prompt(prompt_id: int) -> dict[str, object]:
    try:
        return {"prompt": prompts.restore_default(prompt_id)}
    except prompts.PromptError as exc:
        raise HTTPException(400, str(exc)) from exc
    except db.NotFound as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/settings/prompts/{prompt_id}/versions")
def prompt_versions(prompt_id: int) -> dict[str, object]:
    try:
        prompts.get_prompt(prompt_id)
    except db.NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"versions": prompts.versions(prompt_id)}


@router.delete("/settings/prompts/{prompt_id}")
def delete_prompt(prompt_id: int) -> dict[str, object]:
    try:
        prompts.delete_prompt(prompt_id)
    except prompts.PromptError as exc:
        raise HTTPException(400, str(exc)) from exc
    except db.NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"prompts": prompts.list_prompts()}


# --- money -----------------------------------------------------------------


@router.get("/ai/status")
def ai_status() -> dict[str, object]:
    return {
        "configured": ai.is_configured(),
        "budget": ai.budget_status(),
        "rates_paise": ai.RATES_PAISE,
    }


@router.get("/ai/estimate")
def ai_estimate(
    feature: Literal["photo-edit", "poster-artwork", "poster-layout"],
    batch: bool = True,
) -> dict[str, object]:
    """Free. Says what a call will cost before the operator commits."""
    return ai.estimate(feature, batch)


@router.get("/ai/spend")
def ai_spend(month: str | None = None) -> dict[str, object]:
    return ai.budget_status(month)


# --- paid calls ------------------------------------------------------------


def _result(result: ai.AiResult) -> dict[str, object]:
    """One shape for every AI response, success or failure.

    A failure is a 200 with `ok: false`, not an HTTP error — the caller keeps its
    work and shows the reason, which is the exit-gate requirement that a refused
    call never loses anything.
    """
    body: dict[str, object] = {
        "ok": result.ok,
        "feature": result.feature,
        "model": result.model,
        "batch": result.batch,
        "cost_paise": result.cost_paise,
        "cost_rupees": round(result.cost_paise / 100, 2),
        "error": result.error,
        "warnings": result.warnings,
        "prompt_used": result.prompt_used,
        "budget": ai.budget_status(),
    }
    if result.image is not None:
        body["image"] = base64.b64encode(result.image).decode("ascii")
        body["media_type"] = result.media_type
    if result.layout is not None:
        body["layout"] = result.layout
    return body


@router.post("/ai/photo-edit")
def photo_edit(
    file: Annotated[UploadFile, File()],
    # Bounded, like every other field that reaches a prompt — see ArtworkIn.
    instruction: Annotated[str, Form(max_length=2000)],
    preserve: Annotated[str, Form(max_length=2000)] = "",
    batch: Annotated[bool, Form()] = False,
    over_budget_ok: Annotated[bool, Form()] = False,
) -> dict[str, object]:
    """Sends the client's photograph to Google. The UI must say so."""
    data = file.file.read(config.MAX_UPLOAD_BYTES + 1)
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "That image is too large.")
    if not data:
        raise HTTPException(400, "The uploaded file was empty.")
    try:
        loaded = images.load(data)
    except images.ImageError as exc:
        raise HTTPException(400, str(exc)) from exc

    media = f"image/{(loaded.format or 'PNG').lower()}"
    return _result(
        ai.edit_photo(
            data,
            media,
            {"instruction": instruction, "preserve": preserve},
            batch,
            over_budget_ok,
        )
    )


class ArtworkIn(BaseModel):
    """A picture request, described by the poster's own copy.

    `style_key` is what makes this different from a plain image prompt: with one,
    the shop's saved prompt structure for that look is filled in with the copy
    below, so the picture ends up about the message rather than about whatever
    the operator managed to describe in a hurry.
    """

    style_key: str | None = Field(default=None, max_length=40)
    headline: str = Field(default="", max_length=500)
    offer: str = Field(default="", max_length=500)
    occasion: str = Field(default="", max_length=200)
    phone: str = Field(default="", max_length=100)
    # The operator's own idea for the picture, when they have one.
    idea: str = Field(default="", max_length=2000)
    # Free-form fallback, and what the prompt-library template still uses.
    subject: str = Field(default="", max_length=2000)
    # Every field that reaches a prompt is bounded. Cost is recorded as a flat
    # per-call rate whatever the token count, so an unbounded field grows the
    # real Google bill while the budget meter does not move (NEXT.md 1.1).
    style: str = Field(default="", max_length=200)
    palette: str = Field(default="", max_length=200)
    aspect: str = Field(default="", max_length=100)
    batch: bool = True
    # The operator's explicit "spend past the budget". Never defaulted on.
    over_budget_ok: bool = False

    def values(self) -> dict[str, str]:
        return self.model_dump(exclude={"batch", "style_key", "over_budget_ok"})


@router.post("/ai/artwork/prompt")
def artwork_prompt(body: ArtworkIn) -> dict[str, object]:
    """Free. The exact words that would be sent, so nothing is hidden."""
    try:
        text = ai.artwork_prompt(body.values(), body.style_key)
    except db.NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except styles.StyleError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "prompt": text,
        "style_key": body.style_key,
        "estimate": ai.estimate("poster-artwork", body.batch),
    }


@router.post("/ai/artwork")
def artwork(body: ArtworkIn) -> dict[str, object]:
    return _result(
        ai.generate_artwork(
            body.values(), body.batch, body.style_key, body.over_budget_ok
        )
    )


class LayoutPlanIn(BaseModel):
    headline: str = Field(min_length=1, max_length=500)
    # Bounded for the same reason as ArtworkIn's — see the note there.
    offer: str = Field(default="", max_length=500)
    phone: str = Field(default="", max_length=100)
    occasion: str = Field(default="", max_length=200)
    tone: str = Field(default="", max_length=200)
    over_budget_ok: bool = False


@router.post("/ai/layout-plan")
def layout_plan(body: LayoutPlanIn) -> dict[str, object]:
    """Text only — the AI returns positions, never a picture containing words."""
    return _result(
        ai.plan_layout(
            body.model_dump(exclude={"over_budget_ok"}), body.over_budget_ok
        )
    )
