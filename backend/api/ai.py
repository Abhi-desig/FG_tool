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
from backend.features import ai, images, prompts

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
    return {"keys": db.list_api_keys()}


@router.delete("/settings/keys/{name}")
def delete_key(name: str) -> dict[str, object]:
    db.delete_api_key(name)
    return {"keys": db.list_api_keys()}


@router.post("/settings/keys/{name}/test")
def test_key(name: str) -> dict[str, object]:
    """The cheapest real call that proves a key works.

    Guessing whether a key is right costs an afternoon; this costs a fraction of
    a paisa and answers plainly.
    """
    if name != "GEMINI_API_KEY":
        raise HTTPException(400, f"No connection test for {name} yet.")
    if not db.get_api_key(name):
        raise HTTPException(404, "That key is not set.")

    try:
        client = ai._client()  # noqa: SLF001 - same package, deliberate
        client.models.generate_content(model=ai.LAYOUT_MODEL, contents="ping")
        db.record_key_test(name, True)
        return {"ok": True, "message": "Connected.", "keys": db.list_api_keys()}
    except Exception as exc:  # noqa: BLE001 - report, never raise past here
        db.record_key_test(name, False)
        return {
            "ok": False,
            "message": ai._friendly(exc),  # noqa: SLF001
            "keys": db.list_api_keys(),
        }


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
    instruction: Annotated[str, Form()],
    preserve: Annotated[str, Form()] = "",
    batch: Annotated[bool, Form()] = False,
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
            data, media, {"instruction": instruction, "preserve": preserve}, batch
        )
    )


class ArtworkIn(BaseModel):
    subject: str = Field(min_length=1, max_length=2000)
    style: str = ""
    palette: str = ""
    aspect: str = ""
    batch: bool = True


@router.post("/ai/artwork")
def artwork(body: ArtworkIn) -> dict[str, object]:
    return _result(ai.generate_artwork(body.model_dump(exclude={"batch"}), body.batch))


class LayoutPlanIn(BaseModel):
    headline: str = Field(min_length=1, max_length=500)
    offer: str = ""
    phone: str = ""
    occasion: str = ""
    tone: str = ""


@router.post("/ai/layout-plan")
def layout_plan(body: LayoutPlanIn) -> dict[str, object]:
    """Text only — the AI returns positions, never a picture containing words."""
    return _result(ai.plan_layout(body.model_dump()))
