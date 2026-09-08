"""Poster routes: list the shop's designs, draw a poster, change it.

**This screen always leaves the machine.** Every other route in Phases 1–4 is
offline; this one is a Google call every time, and the UI says so on every
button. The old Phase 4 routes — canvas presets, layout-plan schema, SVG export —
are gone with the designer they served (ADR-034).

Three calls, in the order the operator meets them:

    GET  /api/posters/designs   what is in data/poster_prompts/
    POST /api/posters/generate  copy + design (+ reference image) -> a poster
    POST /api/posters/refine    that poster + a note -> a changed poster
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from backend import config
from backend.api.ai import _result
from backend.features import ai, images, posters

router = APIRouter(prefix="/api", tags=["posters"])

# Every field that reaches a prompt is bounded. Cost is recorded as a flat
# per-call rate whatever the token count, so an unbounded field grows the real
# Google bill while the budget meter does not move (NEXT.md 1.1).
MAX_COPY_CHARS = 4000
MAX_NOTE_CHARS = 2000


@dataclass(frozen=True)
class _Picture:
    """An uploaded image that has been decoded and found to be one."""

    data: bytes
    media_type: str
    width: int
    height: int

    def as_pair(self) -> tuple[bytes, str]:
        return self.data, self.media_type


def _image(upload: UploadFile, what: str) -> _Picture:
    """Read an uploaded picture and say what it is.

    Decoded through `images.load` rather than trusted by its extension: this
    goes straight to a paid API, and a file that is not an image would spend
    money finding that out. The real pixel size comes back with it, because
    changing a poster has to be able to ask for the shape it already has.
    """
    data = upload.file.read(config.MAX_UPLOAD_BYTES + 1)
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"That {what} is too large.")
    if not data:
        raise HTTPException(400, f"The uploaded {what} was empty.")
    try:
        loaded = images.load(data)
    except images.ImageError as exc:
        raise HTTPException(400, str(exc)) from exc
    width, height = loaded.size
    return _Picture(
        data=data,
        media_type=f"image/{(loaded.format or 'PNG').lower()}",
        width=width,
        height=height,
    )


@router.get("/posters/designs")
def list_designs() -> dict[str, object]:
    """The operator's own designs. Free, offline, and re-read on every call.

    An empty list is a normal answer, not an error: it means the folder is there
    and holds nothing yet. The screen says where to put a design rather than
    showing a failure.
    """
    found = posters.designs()
    return {
        "designs": [d.as_dict() for d in found],
        "count": len(found),
        # Shown in the empty state, so the operator knows where their files go.
        "folder": str(posters.DESIGNS_DIR),
        "tags": list(posters.TAGS),
        # The operator never picks a style — the model does. This says whether
        # the override is available, so the screen shows it to whoever started
        # the server with DEV_TOOLS and to nobody else (ADR-037).
        "dev_tools": config.DEV_TOOLS,
    }


@router.post("/posters/parse")
def parse_copy(
    # Named `words` in Python and `copy` on the wire: FastAPI builds a Pydantic
    # model from these parameters, and a field called `copy` shadows
    # `BaseModel.copy`. The wire name is the domain word and stays.
    words: Annotated[str, Form(alias="copy", max_length=MAX_COPY_CHARS)],
) -> dict[str, object]:
    """What the app read out of the pasted copy. Free, offline, no model.

    Its own route so the screen can show the parse back as the operator types,
    before anything is spent. Getting `h1` and `h2` the wrong way round is
    cheap to fix here and expensive to notice on a printed poster.
    """
    parsed = posters.parse_copy(words)
    return {
        "copy": parsed,
        "tags": list(posters.TAGS),
        "missing": [tag for tag in posters.TAGS if not parsed.get(tag)],
    }


@router.post("/posters/generate")
def generate(
    words: Annotated[str, Form(alias="copy", max_length=MAX_COPY_CHARS)],
    reference: Annotated[UploadFile | None, File()] = None,
    batch: Annotated[bool, Form()] = True,
    over_budget_ok: Annotated[bool, Form()] = False,
    force_style: Annotated[str, Form(max_length=80)] = "",
) -> dict[str, object]:
    """Draw the poster.

    The operator sends copy and nothing else — the style is the model's choice,
    made in the same call that draws (ADR-037).

    Two calls at most. Without a reference image, a cheap text call turns the
    copy into a visual idea first; with one, that step is skipped because a
    picture the operator chose is a better brief than anything written from the
    words alone.

    `force_style` is refused rather than ignored when `DEV_TOOLS` is off. A
    silently dropped override would look like the auto-selection agreeing with
    whoever pinned it, which is the one wrong answer here.
    """
    if force_style and not config.DEV_TOOLS:
        raise HTTPException(
            403,
            "Pinning a style is a developer control. Start the server with "
            "DEV_TOOLS=true to use it.",
        )

    parsed = posters.parse_copy(words)
    if not parsed.get("main"):
        raise HTTPException(
            400,
            "No headline found. Put `main:` in front of the poster's main line "
            "— or make it the first line of what you paste.",
        )

    picture = _image(reference, "reference image").as_pair() if reference is not None else None

    concept = ""
    concept_cost = 0
    concept_error: str | None = None
    if picture is None:
        idea = ai.poster_concept(parsed, over_budget_ok)
        concept_cost = idea.cost_paise
        if idea.ok and idea.text:
            concept = idea.text
        else:
            # Not fatal. The design's own prompt still describes a poster, so
            # losing the idea makes it plainer rather than impossible — and
            # failing the whole job here would charge for nothing.
            concept_error = idea.error

    result = ai.generate_poster(
        copy=parsed,
        concept=concept,
        reference=picture,
        batch=batch,
        over_budget_ok=over_budget_ok,
        force_style=force_style,
    )

    body = _result(result)
    body["copy"] = parsed
    body["concept"] = concept
    # Always present, empty included. The operator did not choose the style, so
    # this is the only account of why the poster looks the way it does — and a
    # key that disappears when the model failed to name one is a key nothing can
    # be logged or debugged against (ADR-037).
    body["style"] = result.style
    body["style_reason"] = result.style_reason
    # Both calls, added up. The operator is deciding whether to press it again,
    # and a figure that leaves out the first call is not that decision's number.
    body["cost_paise"] = int(body["cost_paise"]) + concept_cost
    body["cost_rupees"] = round(int(body["cost_paise"]) / 100, 2)
    if concept_error:
        body["warnings"] = [
            *list(body.get("warnings") or []),
            f"The visual idea could not be written ({concept_error}) — the "
            f"design's own description was used on its own.",
        ]
    return body


@router.post("/posters/refine")
def refine(
    poster: Annotated[UploadFile, File()],
    note: Annotated[str, Form(max_length=MAX_NOTE_CHARS)],
    batch: Annotated[bool, Form()] = False,
    over_budget_ok: Annotated[bool, Form()] = False,
) -> dict[str, object]:
    """Change the poster that was just made, keeping the rest of it.

    The poster comes back up from the browser rather than being held on the
    server. It is already in the page, the operator may have gone back to an
    earlier attempt, and a server-side "current poster" would be one more piece
    of state to get wrong.

    Its shape is measured here and asked for again explicitly. The design that
    made it is not part of this request — the operator may be three changes
    deep — so the poster's own pixels are the only honest source for the ratio,
    and sending none lets the model reshape it (ADR-036).
    """
    picture = _image(poster, "poster")
    return _result(
        ai.refine_poster(
            picture.data,
            picture.media_type,
            note,
            batch,
            over_budget_ok,
            aspect=ai.nearest_aspect(picture.width, picture.height),
        )
    )
