"""Phase 4 routes: canvas presets, background analysis, SVG export.

No AI here — ROADMAP.md Phase 4 is deliberately templates-only. What this does
establish is the **layout-plan schema**, which is the contract Phase 5's AI will
fill in. The AI will return one of these objects; it will never return pixels
containing text.
"""

from __future__ import annotations

import json
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field

from backend import config
from backend.features import images, posters

router = APIRouter(prefix="/api", tags=["posters"])

MAX_BLOCKS = 40


class TextBlockIn(BaseModel):
    """One text object. Fractions of the canvas, so it is size independent."""

    id: str = Field(min_length=1, max_length=64)
    text: str = Field(max_length=2000)
    x: float = Field(default=0.1, ge=0, le=1)
    y: float = Field(default=0.1, ge=0, le=1)
    width: float = Field(default=0.8, gt=0, le=1)
    size: Literal["small", "medium", "large", "huge"] = "medium"
    # Overrides `size` when set. Every field below is defaulted, so a poster
    # laid out by an older `frontend/dist` still validates unchanged.
    size_fraction: float | None = Field(
        default=None, ge=posters.MIN_SIZE_FRACTION, le=posters.MAX_SIZE_FRACTION
    )
    weight: Literal["regular", "bold"] = "regular"
    tracking: float = Field(
        default=0.0, ge=posters.MIN_TRACKING, le=posters.MAX_TRACKING
    )
    leading: float = Field(
        default=posters.LINE_HEIGHT, ge=posters.MIN_LEADING, le=posters.MAX_LEADING
    )
    colour: str = Field(default="#ffffff", pattern=r"^#[0-9a-fA-F]{6}$")
    align: Literal["left", "centre", "right"] = "centre"
    case: Literal["as-typed", "upper"] = "as-typed"
    mode: Literal["unicode", "ascii"] = "unicode"
    shadow: bool = True
    role: Literal["headline", "offer", "occasion", "phone", "free"] = "free"

    def to_block(self) -> posters.TextBlock:
        return posters.TextBlock(**self.model_dump())


class LayoutIn(BaseModel):
    canvas: str = "a4-portrait"
    blocks: list[TextBlockIn] = Field(default_factory=list, max_length=MAX_BLOCKS)
    background_colour: str = Field(default="#1b1b22", pattern=r"^#[0-9a-fA-F]{6}$")

    def to_layout(self) -> posters.Layout:
        return posters.Layout(
            canvas=self.canvas,
            blocks=[b.to_block() for b in self.blocks],
            background_colour=self.background_colour,
        )


def _resolve(layout: LayoutIn) -> posters.Layout:
    resolved = layout.to_layout()
    try:
        resolved.resolved_canvas()
    except posters.PosterError as exc:
        raise HTTPException(400, str(exc)) from exc
    return resolved


@router.get("/posters/presets")
def presets() -> dict[str, object]:
    return {
        "canvases": posters.describe_presets(),
        "sizes": posters.SIZE_SCALE,
        # Published so the style editor and the block inspector bound their
        # inputs from one source rather than each hardcoding its own numbers.
        "size_bounds": [posters.MIN_SIZE_FRACTION, posters.MAX_SIZE_FRACTION],
        "tracking_bounds": [posters.MIN_TRACKING, posters.MAX_TRACKING],
        "leading_bounds": [posters.MIN_LEADING, posters.MAX_LEADING],
        "default_leading": posters.LINE_HEIGHT,
        "fonts": {
            "unicode": posters.DEFAULT_FONT_UNICODE,
            "ascii": posters.DEFAULT_FONT_ASCII,
        },
    }


@router.post("/posters/analyse")
def analyse(file: Annotated[UploadFile, File()]) -> dict[str, object]:
    """Where is this picture quiet, and how bright is it there?

    Feeds both auto-placement and auto-colour.
    """
    data = file.file.read(config.MAX_UPLOAD_BYTES + 1)
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "That image is too large.")
    if not data:
        raise HTTPException(400, "The uploaded file was empty.")
    try:
        image = images.load(data)
    except images.ImageError as exc:
        raise HTTPException(400, str(exc)) from exc

    regions = posters.find_calm_regions(image, top=4)
    return {
        "width": image.width,
        "height": image.height,
        "calm_regions": [r.__dict__ for r in regions],
    }


class CopyIn(BaseModel):
    text: str = Field(max_length=8000)


@router.post("/posters/split-copy")
def split_copy(body: CopyIn) -> dict[str, object]:
    """Sort one pasted WhatsApp message into the lines a poster is made of.

    Offline and free. Every line comes back tagged with a guess, in the order it
    was pasted — nothing is dropped, and the designer shows the guesses so the
    operator can correct any of them.
    """
    return {"lines": posters.split_copy(body.text)}


class CheckRequest(BaseModel):
    layout: LayoutIn
    # The width each block will actually be **drawn** at, in ems, measured by
    # the browser with the font loaded and keyed by block id. The backend has no
    # shaping engine, so when these are supplied they replace its estimate
    # entirely.
    #
    # "Drawn", not "natural": the measurement includes the block's letter
    # spacing and text case, because the browser measures the exact string it
    # will render. Numerically identical for any poster made before those
    # existed (tracking 0, case as-typed), but `fit_block` must not add tracking
    # a second time — see the matching note in `frontend/src/lib/textFit.ts`.
    measured: dict[str, float] = Field(default_factory=dict, max_length=MAX_BLOCKS)


@router.post("/posters/check")
def check(body: CheckRequest) -> dict[str, object]:
    """Safe-zone and overflow warnings, without rendering anything."""
    layout = _resolve(body.layout)
    canvas = layout.resolved_canvas()
    measured = _clean_measured(body.measured)
    fits = posters.fit_layout(layout, measured)
    return {
        "canvas": {
            "key": canvas.key,
            "width_px": canvas.width_px,
            "height_px": canvas.height_px,
            "safe_fraction": canvas.safe_fraction,
        },
        "safe_zone": posters.safe_zone_report(layout, measured),
        "overflow": posters.overflow_warnings(layout, measured),
        # What auto-fit did. Not a warning — the operator does not have to act on
        # it — but it must be visible, or the poster silently differs from what
        # was typed.
        "fitted": posters.fit_report(layout, measured),
        "blocks": [
            {
                "id": f.id,
                "font_px": round(f.font_px, 1),
                "scale": round(f.scale, 3),
                "lines": f.lines,
                "over_box": f.over_box,
                "overflows": f.overflows,
            }
            for f in fits.values()
        ],
    }


def _clean_measured(measured: dict[str, float]) -> dict[str, float]:
    """Drop anything that is not a usable measurement.

    These arrive from the browser, so a stale or broken value must not be able
    to make a block fit that does not.
    """
    return {
        key: value
        for key, value in measured.items()
        if isinstance(value, int | float) and 0 < value < 10_000
    }


@router.post("/posters/svg")
async def svg(
    layout: Annotated[str, Form()],
    background: Annotated[UploadFile | None, File()] = None,
    safe_zone: Annotated[bool, Form()] = False,
    measured: Annotated[str | None, Form()] = None,
) -> Response:
    """The deliverable: vector, with real editable text.

    Multipart rather than JSON because the background image rides along, and the
    result is a self-contained file with the image embedded as a data URI.

    `measured` carries the browser's real text widths so the exported file is
    fitted exactly as the on-screen preview was — without it the export would
    fall back to the estimate and could differ from what the operator approved.
    """
    try:
        parsed = LayoutIn.model_validate_json(layout)
    except ValueError as exc:
        raise HTTPException(400, f"Bad layout: {exc}") from exc

    widths: dict[str, float] = {}
    if measured:
        try:
            widths = _clean_measured(json.loads(measured))
        except (ValueError, AttributeError) as exc:
            raise HTTPException(400, f"Bad measurements: {exc}") from exc

    resolved = _resolve(parsed)

    data: bytes | None = None
    media = "image/png"
    if background is not None:
        raw = background.file.read(config.MAX_UPLOAD_BYTES + 1)
        if len(raw) > config.MAX_UPLOAD_BYTES:
            raise HTTPException(413, "That background image is too large.")
        if raw:
            try:
                loaded = images.load(raw)
            except images.ImageError as exc:
                raise HTTPException(400, str(exc)) from exc
            media = f"image/{(loaded.format or 'PNG').lower()}"
            data = raw

    markup = posters.render_svg(
        resolved,
        background=data,
        background_media_type=media,
        show_safe_zone=safe_zone,
        measured=widths,
    )
    return Response(
        content=markup,
        media_type="image/svg+xml",
        headers={"Content-Disposition": 'attachment; filename="poster.svg"'},
    )


class AutoRequest(BaseModel):
    layout: LayoutIn


@router.post("/posters/auto")
async def auto(
    layout: Annotated[str, Form()],
    background: Annotated[UploadFile | None, File()] = None,
    place: Annotated[bool, Form()] = True,
) -> dict[str, object]:
    """Put the text where the picture is quiet, in a colour that can be read.

    Returns a layout rather than an image — the operator sees the suggestion in
    the editor and can drag it anywhere before committing to anything.
    """
    try:
        parsed = LayoutIn.model_validate_json(layout)
    except ValueError as exc:
        raise HTTPException(400, f"Bad layout: {exc}") from exc

    resolved = _resolve(parsed)
    if background is None:
        raise HTTPException(400, "Auto placement needs a background image.")

    raw = background.file.read(config.MAX_UPLOAD_BYTES + 1)
    try:
        image = images.load(raw)
    except images.ImageError as exc:
        raise HTTPException(400, str(exc)) from exc

    if place:
        posters.place_in_calm_space(image, resolved.blocks)
    for block in resolved.blocks:
        block.colour = posters.suggest_colour(image, block)

    return {
        "blocks": [b.__dict__ for b in resolved.blocks],
        "safe_zone": posters.safe_zone_report(resolved),
    }
