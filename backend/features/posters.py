"""Poster layout: the app draws the text, never the AI.

**Why SVG and not a rendered image.** The Phase 4 exit gate asks for text that is
sharp *and editable* — so the deliverable is vector. An SVG carries real `<text>`
elements that CorelDRAW opens as editable text objects. Nothing is baked into
pixels, so a typo is a text edit rather than a re-render.

**Why the backend never rasterises Malayalam.** Pillow here has no Raqm or
HarfBuzz (`PIL.features.check("raqm")` is False), which means no complex-script
shaping. Rendered on 2026-08-23, ``കേരളം`` came out with the ``േ`` sign *after*
``ക`` instead of before it, and ``ക്ക``/``സ്റ്റ``/``ന്ന`` came out as loose
letters with a visible chandrakkala instead of ligatures — every conjunct and
every pre-base vowel wrong. Producing that silently would be exactly the
expensive, visible error this shop cannot afford. So raster proofs are made by
the browser, which shapes correctly, and this module stays vector-only.

**Two ways to carry Malayalam into the SVG**, both correct:

* ``unicode`` — real Unicode text with a Unicode font. The consumer shapes it.
* ``ascii`` — Phase 1's ML-TTKarthika conversion. The shaping is *already done*
  by the converter, so the glyph order is the visual order and no shaping engine
  is involved anywhere. This is the shop's native CorelDRAW workflow.

Coordinates are fractions of the canvas (0–1) so a layout is resolution
independent — the same plan renders to an A3 brochure or a 6×4 ft flex banner.
That also makes it the contract the Phase 5 AI fills in.
"""

from __future__ import annotations

import base64
import html
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal

import numpy as np
from PIL import Image

from backend import config
from backend.features import fonts

MM_PER_INCH = 25.4
PRINT_DPI = 300

TextMode = Literal["unicode", "ascii"]
Weight = Literal["regular", "bold"]
Align = Literal["left", "centre", "right"]


class PosterError(ValueError):
    """The layout or canvas is not usable."""


@dataclass(frozen=True)
class Canvas:
    key: str
    label: str
    width_mm: float
    height_mm: float
    dpi: int = PRINT_DPI
    # Distance from the trim edge that text must stay inside. Large-format
    # printers grip and trim more than a desktop press does.
    safe_mm: float = 5.0

    @property
    def width_px(self) -> int:
        return round(self.width_mm / MM_PER_INCH * self.dpi)

    @property
    def height_px(self) -> int:
        return round(self.height_mm / MM_PER_INCH * self.dpi)

    @property
    def safe_fraction(self) -> tuple[float, float]:
        """Safe inset as a fraction of width and height."""
        return (self.safe_mm / self.width_mm, self.safe_mm / self.height_mm)


# Real sizes this shop prints. Flex banners are metres, brochures millimetres —
# both fall out of the same fractional layout.
CANVAS_PRESETS: tuple[Canvas, ...] = (
    Canvas("a4-portrait", "A4 portrait", 210, 297),
    Canvas("a4-landscape", "A4 landscape", 297, 210),
    Canvas("a3-portrait", "A3 portrait", 297, 420),
    Canvas("flex-6x4", "Flex banner 6×4 ft", 1828.8, 1219.2, dpi=72, safe_mm=50),
    Canvas("flex-8x4", "Flex banner 8×4 ft", 2438.4, 1219.2, dpi=72, safe_mm=50),
    Canvas("square-social", "Square post", 1080 / 300 * MM_PER_INCH, 1080 / 300 * MM_PER_INCH),
    Canvas("story", "Story / reel", 1080 / 300 * MM_PER_INCH, 1920 / 300 * MM_PER_INCH),
)

CANVASES = {c.key: c for c in CANVAS_PRESETS}

# Text size as a fraction of canvas height, so a "huge" headline is huge on any
# format. The AI in Phase 5 speaks in these words, not in points.
SIZE_SCALE: dict[str, float] = {
    "small": 0.035,
    "medium": 0.055,
    "large": 0.085,
    "huge": 0.135,
}

DEFAULT_FONT_UNICODE = "Noto Sans Malayalam"
DEFAULT_FONT_ASCII = fonts.DEFAULT_FONT  # ML-TTKarthika

# The woff2 the UI already ships. Embedded into the exported SVG so the
# deliverable does not depend on the font being installed — see `_font_face`.
UNICODE_FONT_FILE = config.ROOT / "frontend" / "public" / "fonts" / "NotoSansMalayalam.woff2"

# How small auto-fit may shrink a block before it stops reading as the size the
# operator asked for. Below this the text wraps instead.
MIN_FIT_SCALE = 0.6

# Line spacing as a multiple of font size, for wrapped blocks.
LINE_HEIGHT = 1.25


@dataclass
class TextBlock:
    """One line or paragraph of text, positioned in canvas fractions."""

    id: str
    text: str
    x: float = 0.1
    y: float = 0.1
    width: float = 0.8
    size: str = "medium"
    weight: Weight = "regular"
    colour: str = "#ffffff"
    align: Align = "centre"
    # Malayalam only: how this text should be carried into the SVG.
    mode: TextMode = "unicode"
    # A dark rule behind light text, for photos too busy to read against.
    shadow: bool = True
    # What this line *is* on the poster. Nothing in this module reads it — it
    # exists so a design style knows which line is the headline when it applies
    # its colours, and so that survives a round trip through auto-placement.
    role: str = "free"

    def clamp(self) -> TextBlock:
        self.x = min(max(self.x, 0.0), 1.0)
        self.y = min(max(self.y, 0.0), 1.0)
        self.width = min(max(self.width, 0.02), 1.0)
        if self.size not in SIZE_SCALE:
            self.size = "medium"
        return self


@dataclass
class Layout:
    canvas: str = "a4-portrait"
    blocks: list[TextBlock] = field(default_factory=list)
    background_colour: str = "#1b1b22"

    def resolved_canvas(self) -> Canvas:
        try:
            return CANVASES[self.canvas]
        except KeyError as exc:
            raise PosterError(
                f"unknown canvas {self.canvas!r}; available: {', '.join(CANVASES)}"
            ) from exc


# --- background analysis --------------------------------------------------


def _luminance(pixels: np.ndarray) -> np.ndarray:
    """Perceived brightness, 0–255. Rec. 709 weights."""
    return (
        0.2126 * pixels[..., 0] + 0.7152 * pixels[..., 1] + 0.0722 * pixels[..., 2]
    )


def _small(image: Image.Image, side: int = 240) -> np.ndarray:
    """Analysis runs on a thumbnail; a 24 MP photo is pointless here."""
    thumb = image.convert("RGB").copy()
    thumb.thumbnail((side, side), Image.Resampling.BILINEAR)
    return np.asarray(thumb, dtype=np.float32)


def suggest_colour(image: Image.Image | None, block: TextBlock) -> str:
    """Light or dark text, decided by what is actually behind the box.

    DESIGN.md: colour must never be the only signal, and text must never be
    unreadable. A mid-grey background is the dangerous case — that is why the
    threshold sits at 140 rather than 128, biasing toward dark text on anything
    ambiguous, since dark-on-light survives a bad print better.
    """
    if image is None:
        return "#ffffff"

    pixels = _small(image)
    height, width = pixels.shape[:2]
    x0 = int(min(max(block.x, 0.0), 1.0) * width)
    x1 = int(min(max(block.x + block.width, 0.0), 1.0) * width)
    y0 = int(min(max(block.y, 0.0), 1.0) * height)
    # A band roughly the height of the text, not a single row.
    band = max(1, int(SIZE_SCALE.get(block.size, 0.055) * height))
    y1 = min(height, y0 + band)

    if x1 <= x0 or y1 <= y0:
        return "#ffffff"

    mean = float(_luminance(pixels[y0:y1, x0:x1]).mean())
    return "#ffffff" if mean < 140 else "#16161d"


@dataclass(frozen=True)
class CalmRegion:
    x: float
    y: float
    width: float
    height: float
    # Lower is calmer: how much the pixels vary in this region.
    busyness: float
    mean_luminance: float


def find_calm_regions(
    image: Image.Image, rows: int = 6, columns: int = 4, top: int = 3
) -> list[CalmRegion]:
    """Rank areas of the image by how quiet they are.

    Text goes where the picture is not doing anything. Busyness is the standard
    deviation of luminance in the cell — a flat sky scores low, a face or a
    crowd scores high.
    """
    pixels = _small(image)
    height, width = pixels.shape[:2]
    lum = _luminance(pixels)

    found: list[CalmRegion] = []
    for row in range(rows):
        for column in range(columns):
            y0, y1 = row * height // rows, (row + 1) * height // rows
            x0, x1 = column * width // columns, (column + 1) * width // columns
            patch = lum[y0:y1, x0:x1]
            if patch.size == 0:
                continue
            found.append(
                CalmRegion(
                    x=column / columns,
                    y=row / rows,
                    width=1 / columns,
                    height=1 / rows,
                    busyness=round(float(patch.std()), 2),
                    mean_luminance=round(float(patch.mean()), 1),
                )
            )

    found.sort(key=lambda r: r.busyness)
    return found[:top]


def place_in_calm_space(
    image: Image.Image, blocks: list[TextBlock], rows: int = 8
) -> list[TextBlock]:
    """Move blocks onto the quietest parts of the picture.

    Two rules make this useful rather than merely clever:

    * **One block per horizontal band.** Ranking individual grid cells put all
      three lines of a poster in the same band of a gradient sky — technically
      the calmest cells, and completely unusable because they overlapped. Blocks
      are assigned to *distinct* rows instead.
    * **Reading order is preserved.** A headline that jumps below the phone
      number is calmer and wrong.
    """
    if not blocks:
        return blocks

    pixels = _small(image)
    height, width = pixels.shape[:2]
    lum = _luminance(pixels)
    rows = max(rows, len(blocks))

    # Busyness and brightness per horizontal band, since a line of text spans
    # the width of the poster.
    bands: list[tuple[int, float, float]] = []
    for row in range(rows):
        y0, y1 = row * height // rows, (row + 1) * height // rows
        strip = lum[y0:y1, :]
        if strip.size == 0:
            continue
        bands.append((row, float(strip.std()), float(strip.mean())))

    if not bands:
        return blocks

    # Give each block its own zone of the canvas and let it find the calmest
    # band *within* that zone. Simply ranking all bands globally clusters the
    # lines wherever the ties fall — on a two-tone image four bands score an
    # identical 0.00 and the winner is arbitrary. Zoning keeps a poster looking
    # like a poster: something near the top, something near the bottom.
    zones = len(blocks)
    for index, block in enumerate(blocks):
        start = index * len(bands) // zones
        end = max(start + 1, (index + 1) * len(bands) // zones)
        row, _busyness, brightness = min(bands[start:end], key=lambda b: b[1])
        block.x = 0.08
        block.width = 0.84
        block.y = round(row / rows + (1 / rows) * 0.2, 4)
        block.colour = "#ffffff" if brightness < 140 else "#16161d"
    return blocks


# --- safe zone ------------------------------------------------------------


def text_extent(canvas: Canvas, block: TextBlock, fitted: Fitted | None = None) -> tuple[
    float, float, float, float
]:
    """Where the drawn text actually sits, as canvas fractions: x0, y0, x1, y1.

    The *box* is not the text. A centred block anchored at the middle of a narrow
    box still paints outward from that anchor, so a long line escapes a box that
    is itself comfortably inside the trim. Measuring the glyphs rather than the
    box is what makes this agree with `overflow_warnings`.
    """
    if fitted is None:
        fitted = fit_block(block, canvas)
    _, offset = _anchor(block.align)
    anchor_x = block.x + block.width * offset
    x0 = anchor_x - fitted.width * offset
    x1 = x0 + fitted.width
    return x0, block.y, x1, block.y + fitted.height


def outside_safe_zone(
    canvas: Canvas, block: TextBlock, fitted: Fitted | None = None
) -> bool:
    """True if trimming could cut this text off.

    Tests the rendered glyphs, not the box that nominally contains them — see
    `text_extent`. Previously a block whose text overflowed its box reported
    `false` here while `overflow_warnings` reported a violation: two checks that
    did not talk to each other.
    """
    inset_x, inset_y = canvas.safe_fraction
    x0, y0, x1, y1 = text_extent(canvas, block, fitted)
    return (
        x0 < inset_x
        or y0 < inset_y
        or x1 > 1 - inset_x
        or y1 > 1 - inset_y
    )


def safe_zone_report(
    layout: Layout, measured: dict[str, float] | None = None
) -> list[dict[str, object]]:
    canvas = layout.resolved_canvas()
    fits = fit_layout(layout, measured)
    rows: list[dict[str, object]] = []
    for block in layout.blocks:
        fitted = fits[block.id]
        x0, y0, x1, y1 = text_extent(canvas, block, fitted)
        rows.append(
            {
                "id": block.id,
                "outside_safe_zone": outside_safe_zone(canvas, block, fitted),
                "extent": {
                    "x0": round(x0, 4),
                    "y0": round(y0, 4),
                    "x1": round(x1, 4),
                    "y1": round(y1, 4),
                },
            }
        )
    return rows


# --- SVG ------------------------------------------------------------------


def _font_family(block: TextBlock) -> str:
    if block.mode == "ascii":
        return DEFAULT_FONT_ASCII
    return f"{DEFAULT_FONT_UNICODE}, sans-serif"


def _text_for(block: TextBlock) -> str:
    """The characters that actually go into the SVG.

    In ``ascii`` mode Phase 1 has already done the reordering and ligature
    selection, so the byte order is the visual order — no shaping engine is
    needed by anything downstream.
    """
    if block.mode != "ascii":
        return block.text
    return fonts.unicode_to_ascii(block.text)


def _anchor(align: Align) -> tuple[str, float]:
    """SVG text-anchor, and where in the box the anchor sits."""
    return {
        "left": ("start", 0.0),
        "centre": ("middle", 0.5),
        "right": ("end", 1.0),
    }[align]


@lru_cache(maxsize=1)
def _font_face() -> str:
    """An `@font-face` carrying the Unicode font, so the SVG stands alone.

    Without this the export names ``Noto Sans Malayalam`` and ships none of it:
    correct on a machine that happens to have the font installed, tofu or wrong
    metrics everywhere else. The whole woff2 is embedded rather than a subset —
    subsetting would need a font toolkit this project does not install, and 89 KB
    against an embedded background photo is not worth a dependency.

    Note for CorelDRAW: it honours the *text*, not the `@font-face`. The embedded
    font makes the file correct in browsers and modern viewers; on the shop PC the
    ``ascii`` export mode remains the route that needs no font resolution at all.
    """
    try:
        payload = UNICODE_FONT_FILE.read_bytes()
    except OSError:
        # Shipping an SVG with a broken font reference is still better than
        # failing the export the operator is waiting on.
        return ""
    encoded = base64.b64encode(payload).decode("ascii")
    return (
        "<defs><style type=\"text/css\">@font-face{"
        f"font-family:'{DEFAULT_FONT_UNICODE}';font-style:normal;font-weight:400 700;"
        f"src:url(data:font/woff2;base64,{encoded}) format('woff2');"
        "}</style></defs>"
    )


def render_svg(
    layout: Layout,
    background: bytes | None = None,
    background_media_type: str = "image/png",
    show_safe_zone: bool = False,
    measured: dict[str, float] | None = None,
    embed_font: bool = True,
) -> str:
    """Build an SVG with real, editable text.

    `background` is embedded as a data URI so the file is self-contained — one
    file to hand over, nothing to lose track of. The Unicode font is embedded for
    the same reason; see `_font_face`.

    Text is auto-fitted before it is drawn, so a headline too long for the canvas
    comes back shrunk or wrapped rather than running off both edges.
    """
    canvas = layout.resolved_canvas()
    width, height = canvas.width_px, canvas.height_px
    fits = fit_layout(layout, measured)

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="{canvas.width_mm}mm" height="{canvas.height_mm}mm" '
        f'viewBox="0 0 {width} {height}">',
        f"<title>{html.escape(canvas.label)} poster</title>",
    ]

    # Only when a block actually uses it — an ASCII-only poster is for CorelDRAW
    # and does not need 89 KB of Unicode font it will never consult.
    if embed_font and any(b.mode != "ascii" for b in layout.blocks):
        parts.append(_font_face())

    parts.append(
        f'<rect width="{width}" height="{height}" fill="{layout.background_colour}"/>'
    )

    if background:
        encoded = base64.b64encode(background).decode("ascii")
        parts.append(
            f'<image x="0" y="0" width="{width}" height="{height}" '
            f'preserveAspectRatio="xMidYMid slice" '
            f'xlink:href="data:{background_media_type};base64,{encoded}"/>'
        )

    if show_safe_zone:
        inset_x, inset_y = canvas.safe_fraction
        parts.append(
            f'<rect x="{inset_x * width:.1f}" y="{inset_y * height:.1f}" '
            f'width="{(1 - 2 * inset_x) * width:.1f}" '
            f'height="{(1 - 2 * inset_y) * height:.1f}" '
            f'fill="none" stroke="#b42318" stroke-width="{max(2, width // 400)}" '
            f'stroke-dasharray="{width // 80},{width // 120}"/>'
        )

    for block in layout.blocks:
        block.clamp()
        fitted = fits[block.id]
        font_px = fitted.font_px
        anchor, offset = _anchor(block.align)
        x = (block.x + block.width * offset) * width
        # SVG y is the baseline, so drop by roughly a cap height.
        y = block.y * height + font_px * 0.8
        weight = "700" if block.weight == "bold" else "400"
        common = (
            f'x="{x:.1f}" y="{y:.1f}" '
            f'font-family="{html.escape(_font_family(block))}" '
            f'font-size="{font_px:.1f}" font-weight="{weight}" '
            f'text-anchor="{anchor}"'
        )

        # One <tspan> per wrapped line. A single-line block emits its text
        # directly, so the common case stays the plain <text> CorelDRAW likes.
        if len(fitted.lines) == 1:
            body = html.escape(_line_text(block, fitted.lines[0]))
        else:
            body = "".join(
                f'<tspan x="{x:.1f}" '
                f'dy="{0 if i == 0 else font_px * LINE_HEIGHT:.1f}">'
                f"{html.escape(_line_text(block, line))}</tspan>"
                for i, line in enumerate(fitted.lines)
            )

        if block.shadow:
            # A soft dark pass under light text. Kept as a second <text> rather
            # than a filter so it stays editable and prints predictably.
            parts.append(
                f'<text {common} fill="#000000" fill-opacity="0.45" '
                f'stroke="#000000" stroke-opacity="0.45" '
                f'stroke-width="{max(1.0, font_px * 0.07):.1f}" '
                f'stroke-linejoin="round">{body}</text>'
            )
        parts.append(f'<text {common} fill="{block.colour}">{body}</text>')

    parts.append("</svg>")
    return "\n".join(parts)


# --- pasted copy ----------------------------------------------------------
#
# Work arrives as one WhatsApp message, not as four labelled fields. Typing it
# back out line by line is the slowest part of the job and the one where a
# phone number gets a digit wrong, so the operator pastes the lot and this
# guesses which line is which.
#
# **Nothing is ever dropped.** A line this cannot place becomes an extra line on
# the poster rather than disappearing — losing a client's wording silently is
# exactly the expensive, invisible error the shop cannot afford. The guesses are
# shown and every one is correctable.

# A run of digits long enough to be a phone number, however it is spaced.
_PHONE_RUN = re.compile(r"\+?\d[\d\s\-().]{7,}\d")

# Strong price signals. `%` and a currency mark are worth more than any word.
_MONEY = re.compile(r"[%₹]|(?<![A-Za-z])(?:rs|inr)\.?\s*\d", re.IGNORECASE)

_OFFER_WORDS = (
    "off",
    "offer",
    "discount",
    "free",
    "upto",
    "up to",
    "flat",
    "combo",
    "buy 1",
    "buy one",
    "ഓഫർ",
    "കിഴിവ്",
    "സൗജന്യ",
)

# Deliberately not "sale" — GRAND SALE is a headline, not an occasion, and
# tagging it as one pushes the real headline down a slot.
_OCCASION_WORDS = (
    "onam",
    "vishu",
    "diwali",
    "deepavali",
    "christmas",
    "xmas",
    "new year",
    "eid",
    "ramadan",
    "ramzan",
    "bakrid",
    "navratri",
    "pooja",
    "puja",
    "wedding",
    "anniversary",
    "birthday",
    "inauguration",
    "grand opening",
    "ഓണം",
    "വിഷു",
    "ദീപാവലി",
    "ക്രിസ്മസ്",
    "പെരുന്നാൾ",
    "വിവാഹ",
)

MAX_COPY_LINES = 40


def _looks_like_phone(line: str) -> bool:
    """Ten to thirteen digits in one run. An Indian mobile, or one with +91."""
    match = _PHONE_RUN.search(line)
    if match is None:
        return False
    digits = sum(character.isdigit() for character in match.group())
    return 10 <= digits <= 13


def _offer_strength(line: str) -> int:
    """How much this line looks like the price, 2 (certain) down to 0 (not).

    Ranked rather than a yes/no because the word alone is weak evidence: in
    *"ഓണം ഓഫർ / Flat 50% OFF on all sarees"* both lines contain a word for
    offer, and taking the first one put the festival name in the price slot and
    left the actual price as a spare line. A figure beats a word.
    """
    if _MONEY.search(line):
        return 2
    lowered = line.lower()
    if any(word in lowered for word in _OFFER_WORDS):
        # "Onam offer" is the occasion wearing the word, not the price.
        return 0 if _looks_like_occasion(line) else 1
    return 0


def _looks_like_occasion(line: str) -> bool:
    lowered = line.lower()
    return any(word in lowered for word in _OCCASION_WORDS)


def split_copy(text: str) -> list[dict[str, str]]:
    """Sort one pasted message into the lines a poster is made of.

    Returns every line in the order it was pasted, each tagged with the role it
    was guessed to be — `headline`, `offer`, `occasion`, `phone`, or `free` for
    anything left over. Only the first candidate takes a role; a second phone
    number stays as an extra line rather than replacing the first.
    """
    lines = [stripped for raw in text.splitlines() if (stripped := raw.strip())]
    if not lines:
        return []
    lines = lines[:MAX_COPY_LINES]

    roles: list[str] = ["free"] * len(lines)

    def free_indexes() -> list[int]:
        return [i for i, role in enumerate(roles) if role == "free"]

    # 1. The phone number, by shape. Position cannot mislead it.
    for index in free_indexes():
        if _looks_like_phone(lines[index]):
            roles[index] = "phone"
            break

    # 2. The price. Strongest evidence wins, earliest line breaking a tie.
    scored = [(index, _offer_strength(lines[index])) for index in free_indexes()]
    best = max(scored, key=lambda pair: pair[1], default=(0, 0))
    if best[1] > 0:
        roles[best[0]] = "offer"

    # 3. The occasion. A short festival name on the first remaining line is a
    #    kicker above the headline — the commonest layout in this shop's work.
    #    Anywhere else it is the headline that leads, because that is how people
    #    write a message, so the rest of the lines are only scanned afterwards.
    remaining = free_indexes()
    first = remaining[0] if remaining else None
    if (
        first is not None
        and len(remaining) > 1
        and _looks_like_occasion(lines[first])
        and len(lines[first].split()) <= 2
    ):
        roles[first] = "occasion"
    else:
        for index in remaining[1:]:
            if _looks_like_occasion(lines[index]):
                roles[index] = "occasion"
                break

    # 4. The headline is whatever still leads.
    remaining = free_indexes()
    if remaining:
        roles[remaining[0]] = "headline"

    return [{"text": line, "role": role} for line, role in zip(lines, roles, strict=True)]


def describe_presets() -> list[dict[str, object]]:
    return [
        {
            "key": c.key,
            "label": c.label,
            "width_mm": c.width_mm,
            "height_mm": c.height_mm,
            "width_px": c.width_px,
            "height_px": c.height_px,
            "dpi": c.dpi,
            "safe_mm": c.safe_mm,
            "aspect": round(c.width_mm / c.height_mm, 4),
        }
        for c in CANVAS_PRESETS
    ]


# --- fitting --------------------------------------------------------------
#
# A Malayalam shop name overflows the page at every size that reads as a
# headline. Measured in the browser on 2026-08-26 with the real woff2 loaded,
# A4 portrait: ``ഫോക്കസ് ഡിജിറ്റൽസ്`` at "huge" renders 5730 px wide on a
# 2480 px canvas — 231%. So the text is *fitted* before it is drawn, and a
# warning is the last resort rather than the only defence.

# Average advance per character, in ems, by Unicode general category. This is an
# estimate and is documented as one: the backend has no shaping engine (see the
# module docstring), so it cannot know a conjunct's real width. Weights are
# anchored to the five browser measurements in `tests/golden/poster_widths.tsv`,
# where this lands within about ±20%.
#
# The browser is authoritative. `PosterDesigner` measures each block with the
# real font and sends the fitted size back, so these numbers decide only the
# server-side warning and the fallback used when no measurement was supplied.
_ADVANCE_EM: dict[str, float] = {
    "Lo": 1.00,  # Malayalam base letters — wide, and wider than Latin
    "Lu": 0.62,
    "Ll": 0.52,
    "Mc": 0.55,  # spacing marks: ി ോ ം all take advance
    "Mn": 0.00,  # nonspacing: the virama ് sits on the consonant
    "Nd": 0.60,
    "Zs": 0.30,
    "Po": 0.30,
    "Pd": 0.35,
}
_DEFAULT_ADVANCE_EM = 0.55

# The estimator's known error band. Applied so a block that will overflow is
# never reported as fitting — over-warning is recoverable, a poster printed with
# the shop's name running off both edges is not.
_ESTIMATE_MARGIN = 1.15


def _advance_em(text: str) -> float:
    """Estimated width of `text` in ems."""
    return sum(
        _ADVANCE_EM.get(unicodedata.category(character), _DEFAULT_ADVANCE_EM)
        for character in text
    )


def estimate_text_width(block: TextBlock, canvas: Canvas, font_px: float | None = None) -> float:
    """Rough fraction of canvas width this text will occupy.

    Deliberately approximate — the browser measures properly. This exists so the
    backend can warn about an obvious overflow without a shaping engine it does
    not have.
    """
    if font_px is None:
        font_px = SIZE_SCALE.get(block.size, 0.055) * canvas.height_px
    return _advance_em(_text_for(block)) * font_px / canvas.width_px


@dataclass(frozen=True)
class Fitted:
    """One block, sized and broken so it actually fits the page."""

    id: str
    # The size actually used, in pixels, and as a fraction of the requested size.
    font_px: float
    scale: float
    lines: list[str]
    shrunk: bool
    wrapped: bool
    # True when even the smallest allowed size, wrapped, still runs off the box.
    overflows: bool
    # Fraction of canvas width the widest line occupies.
    width: float
    # Fraction of canvas height the whole block occupies.
    height: float


def _wrap_to(text: str, limit_em: float) -> list[str]:
    """Break on spaces so no line exceeds `limit_em`.

    A single word wider than the limit is kept whole rather than split — a
    Malayalam word broken mid-conjunct is worse than one that overflows, and the
    caller reports the overflow either way.
    """
    words = text.split()
    if not words:
        return [text]

    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and _advance_em(candidate) > limit_em:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [text]


def fit_block(
    block: TextBlock,
    canvas: Canvas,
    measured_em: float | None = None,
) -> Fitted:
    """Shrink, then wrap, so the text stays inside its box.

    Order matters and was chosen deliberately: shrinking preserves the single
    strong line a headline wants, so it is tried first. Only when the text would
    have to drop below `MIN_FIT_SCALE` — small enough that it no longer reads as
    the size the operator picked — does it wrap instead.

    `measured_em` is the browser's real measurement of the text at 1 em. When
    supplied it replaces the estimate entirely, which is why the designer's
    on-screen fit and the exported SVG agree.
    """
    requested_px = SIZE_SCALE.get(block.size, 0.055) * canvas.height_px
    text = _text_for(block)
    total_em = measured_em if measured_em is not None else _advance_em(text)

    # The box, and the safe zone, whichever bites first. A block may not be
    # rescued by growing past the trim.
    inset_x, _ = canvas.safe_fraction
    limit_fraction = min(block.width, 1 - 2 * inset_x)
    limit_px = max(limit_fraction * canvas.width_px, 1.0)

    if total_em <= 0:
        return Fitted(
            id=block.id, font_px=requested_px, scale=1.0, lines=[block.text],
            shrunk=False, wrapped=False, overflows=False, width=0.0,
            height=requested_px * LINE_HEIGHT / canvas.height_px,
        )

    # 1. Fits as it is.
    if total_em * requested_px <= limit_px:
        return Fitted(
            id=block.id, font_px=requested_px, scale=1.0, lines=[block.text],
            shrunk=False, wrapped=False, overflows=False,
            width=total_em * requested_px / canvas.width_px,
            height=requested_px * LINE_HEIGHT / canvas.height_px,
        )

    # 2. Shrink on one line, if that keeps it legible.
    scale = limit_px / (total_em * requested_px)
    if scale >= MIN_FIT_SCALE:
        font_px = requested_px * scale
        return Fitted(
            id=block.id, font_px=font_px, scale=scale, lines=[block.text],
            shrunk=True, wrapped=False, overflows=False,
            width=total_em * font_px / canvas.width_px,
            height=font_px * LINE_HEIGHT / canvas.height_px,
        )

    # 3. Wrap. Take the largest size at or above the floor whose lines all fit,
    #    so a two-line headline stays as large as it can rather than jumping
    #    straight to the minimum.
    for step in range(0, 41):
        trial = 1.0 - step * (1.0 - MIN_FIT_SCALE) / 40
        font_px = requested_px * trial
        lines = _wrap_to(block.text, limit_px / font_px)
        widest = max(_advance_em(_line_text(block, line)) for line in lines)
        if widest * font_px <= limit_px:
            return Fitted(
                id=block.id, font_px=font_px, scale=trial, lines=lines,
                shrunk=trial < 1.0, wrapped=len(lines) > 1, overflows=False,
                width=widest * font_px / canvas.width_px,
                height=len(lines) * font_px * LINE_HEIGHT / canvas.height_px,
            )

    # 4. A single word wider than the box at the smallest allowed size.
    font_px = requested_px * MIN_FIT_SCALE
    lines = _wrap_to(block.text, limit_px / font_px)
    widest = max(_advance_em(_line_text(block, line)) for line in lines)
    return Fitted(
        id=block.id, font_px=font_px, scale=MIN_FIT_SCALE, lines=lines,
        shrunk=True, wrapped=len(lines) > 1, overflows=True,
        width=widest * font_px / canvas.width_px,
        height=len(lines) * font_px * LINE_HEIGHT / canvas.height_px,
    )


def _line_text(block: TextBlock, line: str) -> str:
    """One wrapped line, in the characters that will actually be drawn."""
    if block.mode != "ascii":
        return line
    return fonts.unicode_to_ascii(line)


def fit_layout(
    layout: Layout, measured: dict[str, float] | None = None
) -> dict[str, Fitted]:
    """Fit every block. Keyed by block id."""
    canvas = layout.resolved_canvas()
    measured = measured or {}
    return {
        block.id: fit_block(block.clamp(), canvas, measured.get(block.id))
        for block in layout.blocks
    }


def overflow_warnings(
    layout: Layout, measured: dict[str, float] | None = None
) -> list[dict[str, object]]:
    """Blocks that auto-fit could not rescue.

    Only genuine failures reach here. A block that was shrunk or wrapped to fit
    is reported by `fit_report` as an adjustment, not as a warning — the
    operator does not need to act on it.
    """
    fits = fit_layout(layout, measured)
    notes: list[dict[str, object]] = []
    for block in layout.blocks:
        fitted = fits[block.id]
        # Trust a real browser measurement exactly; pad an estimate by its known
        # error so a block that will overflow is never called safe.
        certain = measured is not None and block.id in measured
        effective = fitted.width if certain else fitted.width * _ESTIMATE_MARGIN
        if not fitted.overflows and effective <= block.width:
            continue
        notes.append(
            {
                "id": block.id,
                "message": (
                    f"“{block.text[:28]}” is too long for this size — it still "
                    f"runs past the edge at the smallest size that stays "
                    f"readable. Shorten it, or use a wider canvas."
                ),
                "estimated_width": round(effective, 3),
                "box_width": block.width,
                "measured": certain,
            }
        )
    return notes


def fit_report(
    layout: Layout, measured: dict[str, float] | None = None
) -> list[dict[str, object]]:
    """What auto-fit did to each block, so the operator is never surprised."""
    fits = fit_layout(layout, measured)
    report: list[dict[str, object]] = []
    for block in layout.blocks:
        fitted = fits[block.id]
        if not (fitted.shrunk or fitted.wrapped):
            continue
        if fitted.wrapped and fitted.shrunk:
            what = f"shrunk to {fitted.scale:.0%} and wrapped onto {len(fitted.lines)} lines"
        elif fitted.wrapped:
            what = f"wrapped onto {len(fitted.lines)} lines"
        else:
            what = f"shrunk to {fitted.scale:.0%} of the {block.size} size"
        report.append(
            {
                "id": block.id,
                "message": f"“{block.text[:28]}” was {what} to fit the page.",
                "scale": round(fitted.scale, 3),
                "lines": len(fitted.lines),
                "font_px": round(fitted.font_px, 1),
            }
        )
    return report


__all__ = [
    "CANVASES",
    "CANVAS_PRESETS",
    "PRINT_DPI",
    "SIZE_SCALE",
    "CalmRegion",
    "Canvas",
    "Fitted",
    "Layout",
    "PosterError",
    "TextBlock",
    "describe_presets",
    "estimate_text_width",
    "find_calm_regions",
    "fit_block",
    "fit_layout",
    "fit_report",
    "outside_safe_zone",
    "overflow_warnings",
    "place_in_calm_space",
    "render_svg",
    "safe_zone_report",
    "split_copy",
    "suggest_colour",
    "text_extent",
]
