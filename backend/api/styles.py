"""Poster design styles: the shop's saved looks, and the prompts behind them.

These routes are free and offline — nothing here calls Google. A style only
becomes a paid thing when the poster designer sends the assembled prompt to
`/api/ai/artwork`, and even then `/api/ai/artwork/prompt` shows the operator
what that will be first.

Editing is safe by construction, the same way the prompt library is: a shipped
style can always be restored exactly, and only a style the shop added itself can
be deleted.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend import db
from backend.features import styles

router = APIRouter(prefix="/api", tags=["styles"])

MAX_SWATCHES = 6


class StyleIn(BaseModel):
    key: str = Field(min_length=2, max_length=40)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=400)
    body: str = Field(min_length=1, max_length=20_000)
    palette: str = Field(default="", max_length=400)
    swatches: list[str] = Field(
        default_factory=list,
        max_length=MAX_SWATCHES,
    )
    text_defaults: dict[str, Any] = Field(default_factory=dict)
    style_id: int | None = None


@router.get("/styles")
def list_styles() -> dict[str, object]:
    return {
        "styles": styles.list_styles(),
        "variables": list(styles.VARIABLES),
        "required": list(styles.REQUIRED),
    }


@router.post("/styles/validate")
def validate_style(body: StyleIn) -> dict[str, object]:
    """Catch a broken style at edit time, not halfway through a paid job."""
    return {"problems": styles.validate(body.body)}


@router.put("/styles")
def save_style(body: StyleIn) -> dict[str, object]:
    try:
        saved = styles.save_style(
            body.key,
            body.name,
            body.description,
            body.body,
            body.palette,
            body.swatches,
            body.text_defaults,
            body.style_id,
        )
    except styles.StyleError as exc:
        raise HTTPException(400, str(exc)) from exc
    except db.NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"style": saved, "problems": styles.validate(body.body)}


@router.post("/styles/{style_id}/restore-default")
def restore_style(style_id: int) -> dict[str, object]:
    try:
        return {"style": styles.restore_default(style_id)}
    except styles.StyleError as exc:
        raise HTTPException(400, str(exc)) from exc
    except db.NotFound as exc:
        raise HTTPException(404, str(exc)) from exc


@router.delete("/styles/{style_id}")
def delete_style(style_id: int) -> dict[str, object]:
    try:
        styles.delete_style(style_id)
    except styles.StyleError as exc:
        raise HTTPException(400, str(exc)) from exc
    except db.NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"styles": styles.list_styles()}
