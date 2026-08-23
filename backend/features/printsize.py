"""Print-size calculator — the shop's most trusted function.

Answers one question honestly: *will this image hold up at this size?*

Nothing here touches a model or the filesystem, which is why it is a separate
module from `images.py`: it is pure arithmetic and can be tested exhaustively.

The thresholds come from the arcminute rule of thumb — the human eye resolves
about one arcminute, so the pixel density you need is roughly
``3438 / viewing distance in inches``:

    18 in (held in the hand)   → ~190 PPI   → print industry uses 300 for halftone
    4 ft  (poster on a wall)   → ~72 PPI    → 150 is comfortable
    10 ft (flex banner)        → ~29 PPI    → 72 is generous
    30 ft (hoarding)           → ~10 PPI

This is why a flex banner and a brochure are not the same job, and why an image
that fails as an A3 brochure can be perfectly good as a 6×4 ft banner.

**A false YES is a failure. A false NO is merely cautious.** Verdicts are graded
against `good_dpi`, not `min_dpi`, so "good" means comfortably good.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

# Arcminute constant, kept visible so the thresholds below can be checked.
ARCMINUTE_CONSTANT = 3438

MM_PER_INCH = 25.4


class Unit(StrEnum):
    MM = "mm"
    CM = "cm"
    INCH = "inch"
    FEET = "feet"


_PER_INCH: dict[Unit, float] = {
    Unit.MM: MM_PER_INCH,
    Unit.CM: MM_PER_INCH / 10,
    Unit.INCH: 1.0,
    Unit.FEET: 1 / 12,
}


class Verdict(StrEnum):
    GOOD = "good"
    CAUTION = "caution"
    TOO_SMALL = "too_small"


@dataclass(frozen=True)
class PrintClass:
    """What the piece is for, which decides how much resolution it needs."""

    key: str
    label: str
    good_dpi: int
    min_dpi: int
    viewing: str


# Ordered loosest-last, so `best_use_for` can walk from most to least demanding.
PRINT_CLASSES: tuple[PrintClass, ...] = (
    PrintClass("brochure", "Brochure / leaflet", 300, 200, "held in the hand"),
    PrintClass("poster", "Poster / indoor print", 150, 100, "seen from about 1 m"),
    # 50/30 spans the 30–72 DPI range flex actually prints at. 50 matches the
    # arcminute rule at the near end of the stated viewing distance (≈2 m); 30
    # is the far end. Setting "good" at 72 called ordinary shop jobs borderline
    # — a tool that cries wolf on routine work stops being consulted.
    PrintClass("flex", "Flex banner", 50, 30, "seen from 2–6 m"),
    PrintClass("hoarding", "Hoarding / large board", 25, 15, "seen from 10 m or more"),
)

BY_KEY: dict[str, PrintClass] = {c.key: c for c in PRINT_CLASSES}
DEFAULT_CLASS = "flex"


class PrintSizeError(ValueError):
    """The requested size or print class does not make sense."""


def to_inches(value: float, unit: Unit | str) -> float:
    """Convert a length to inches."""
    try:
        factor = _PER_INCH[Unit(unit)]
    except ValueError as exc:
        raise PrintSizeError(f"unknown unit {unit!r}") from exc
    return value / factor


def from_inches(value: float, unit: Unit | str) -> float:
    return value * _PER_INCH[Unit(unit)]


def print_class(key: str) -> PrintClass:
    """Look up a print class by key. Selection is by key, never free-form."""
    try:
        return BY_KEY[key]
    except KeyError as exc:
        raise PrintSizeError(
            f"unknown print class {key!r}; available: {', '.join(BY_KEY)}"
        ) from exc


@dataclass(frozen=True)
class Assessment:
    """The answer, in the shape the UI needs to say it plainly."""

    verdict: Verdict
    headline: str
    detail: str
    effective_dpi: float
    required_dpi: int
    minimum_dpi: int
    print_class: str
    print_class_label: str
    target_w: float
    target_h: float
    unit: str
    pixels_w: int
    pixels_h: int
    # Largest size, in `unit`, that still reaches good_dpi for this class.
    max_w: float
    max_h: float
    # Fraction of the image cropped away to cover a different aspect ratio.
    crop_fraction: float
    # Pixel dimensions that would reach good_dpi at the requested size, or None
    # when the image is already big enough.
    upscale_to: tuple[int, int] | None


def effective_dpi(
    pixels_w: int, pixels_h: int, target_w_in: float, target_h_in: float
) -> float:
    """Printed pixel density when the image is scaled to cover the target.

    Uniform scaling to *cover* means the limiting axis sets the density: if one
    axis would land at 150 dpi and the other at 100, the piece prints at 100 and
    the surplus on the other axis is cropped.
    """
    if min(pixels_w, pixels_h) <= 0:
        raise PrintSizeError("image dimensions must be positive")
    if min(target_w_in, target_h_in) <= 0:
        raise PrintSizeError("target size must be greater than zero")
    return min(pixels_w / target_w_in, pixels_h / target_h_in)


def crop_fraction(
    pixels_w: int, pixels_h: int, target_w_in: float, target_h_in: float
) -> float:
    """Fraction of image area lost when covering a different aspect ratio."""
    image_aspect = pixels_w / pixels_h
    target_aspect = target_w_in / target_h_in
    if image_aspect == target_aspect:
        return 0.0
    ratio = min(image_aspect, target_aspect) / max(image_aspect, target_aspect)
    return 1.0 - ratio


def assess(
    pixels_w: int,
    pixels_h: int,
    target_w: float,
    target_h: float,
    unit: Unit | str = Unit.FEET,
    class_key: str = DEFAULT_CLASS,
) -> Assessment:
    """Decide whether this image can be printed at this size, and say why."""
    cls = print_class(class_key)
    unit = Unit(unit)

    target_w_in = to_inches(target_w, unit)
    target_h_in = to_inches(target_h, unit)

    dpi = effective_dpi(pixels_w, pixels_h, target_w_in, target_h_in)
    cropped = crop_fraction(pixels_w, pixels_h, target_w_in, target_h_in)

    max_w_in = pixels_w / cls.good_dpi
    max_h_in = pixels_h / cls.good_dpi

    size_text = f"{_trim(target_w)}×{_trim(target_h)} {unit.value}"

    if dpi >= cls.good_dpi:
        verdict = Verdict.GOOD
        headline = f"Good for {size_text} {cls.label.lower()}"
        detail = (
            f"Prints at {dpi:.0f} DPI, comfortably above the {cls.good_dpi} DPI "
            f"this needs when {cls.viewing}."
        )
        upscale_to = None
    elif dpi >= cls.min_dpi:
        verdict = Verdict.CAUTION
        headline = f"Borderline for {size_text} {cls.label.lower()}"
        detail = (
            f"Prints at {dpi:.0f} DPI. Usable when {cls.viewing}, but under the "
            f"{cls.good_dpi} DPI that looks clean. Upscaling may help, or print "
            f"no larger than {_trim(from_inches(max_w_in, unit))}×"
            f"{_trim(from_inches(max_h_in, unit))} {unit.value}."
        )
        upscale_to = _upscale_target(target_w_in, target_h_in, cls.good_dpi)
    else:
        verdict = Verdict.TOO_SMALL
        headline = f"Not enough for {size_text} {cls.label.lower()}"
        detail = (
            f"Prints at only {dpi:.0f} DPI, below the {cls.min_dpi} DPI minimum. "
            f"This image tops out at {_trim(from_inches(max_w_in, unit))}×"
            f"{_trim(from_inches(max_h_in, unit))} {unit.value} for this job."
        )
        upscale_to = _upscale_target(target_w_in, target_h_in, cls.good_dpi)

    return Assessment(
        verdict=verdict,
        headline=headline,
        detail=detail,
        effective_dpi=round(dpi, 1),
        required_dpi=cls.good_dpi,
        minimum_dpi=cls.min_dpi,
        print_class=cls.key,
        print_class_label=cls.label,
        target_w=target_w,
        target_h=target_h,
        unit=unit.value,
        pixels_w=pixels_w,
        pixels_h=pixels_h,
        max_w=round(from_inches(max_w_in, unit), 2),
        max_h=round(from_inches(max_h_in, unit), 2),
        crop_fraction=round(cropped, 3),
        upscale_to=upscale_to,
    )


def _upscale_target(
    target_w_in: float, target_h_in: float, dpi: int
) -> tuple[int, int]:
    """Pixels needed to hit `dpi` at this size — what to feed the upscaler."""
    return (round(target_w_in * dpi), round(target_h_in * dpi))


def best_use_for(
    pixels_w: int, pixels_h: int, unit: Unit | str = Unit.FEET
) -> list[dict[str, object]]:
    """Largest comfortable size for every print class.

    This is the question the operator actually has — "what *can* I do with this?"
    — so it is answered without being asked.
    """
    unit = Unit(unit)
    rows: list[dict[str, object]] = []
    for cls in PRINT_CLASSES:
        rows.append(
            {
                "print_class": cls.key,
                "label": cls.label,
                "viewing": cls.viewing,
                "required_dpi": cls.good_dpi,
                "max_w": round(from_inches(pixels_w / cls.good_dpi, unit), 2),
                "max_h": round(from_inches(pixels_h / cls.good_dpi, unit), 2),
            }
        )
    return rows


def _trim(value: float) -> str:
    """Format a number without a pointless trailing .0."""
    return f"{value:g}"
