"""Tests for the print-size calculator.

PRD.md: "A false YES is a failure; a false NO is merely cautious." The tests
that matter most are the ones asserting we never approve something that would
print badly — see `test_never_approves_*`.
"""

from __future__ import annotations

import pytest

from backend.features import printsize
from backend.features.printsize import (
    ARCMINUTE_CONSTANT,
    BY_KEY,
    PRINT_CLASSES,
    Assessment,
    PrintSizeError,
    Unit,
    Verdict,
    assess,
    best_use_for,
    crop_fraction,
    effective_dpi,
    from_inches,
    print_class,
    to_inches,
)

# --- unit conversion ------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "unit", "inches"),
    [
        (1, Unit.INCH, 1.0),
        (25.4, Unit.MM, 1.0),
        (2.54, Unit.CM, 1.0),
        (1, Unit.FEET, 12.0),
        (6, Unit.FEET, 72.0),
    ],
)
def test_to_inches(value: float, unit: Unit, inches: float) -> None:
    assert to_inches(value, unit) == pytest.approx(inches)


@pytest.mark.parametrize("unit", list(Unit))
def test_conversion_round_trips(unit: Unit) -> None:
    assert from_inches(to_inches(37.5, unit), unit) == pytest.approx(37.5)


def test_unknown_unit_is_rejected() -> None:
    with pytest.raises(PrintSizeError):
        to_inches(1, "furlongs")


# --- effective DPI --------------------------------------------------------


def test_effective_dpi_is_simple_division_when_aspect_matches() -> None:
    # 3000 px across 10 inches = 300 dpi
    assert effective_dpi(3000, 2000, 10, 20 / 3) == pytest.approx(300)


def test_effective_dpi_uses_the_limiting_axis() -> None:
    """Covering a mismatched aspect means the worse axis sets the density."""
    # 1000/10 = 100 dpi wide, 1000/5 = 200 dpi tall → prints at 100.
    assert effective_dpi(1000, 1000, 10, 5) == pytest.approx(100)


@pytest.mark.parametrize(("w", "h"), [(0, 100), (100, 0), (-5, 100)])
def test_non_positive_pixels_rejected(w: int, h: int) -> None:
    with pytest.raises(PrintSizeError):
        effective_dpi(w, h, 10, 10)


@pytest.mark.parametrize(("tw", "th"), [(0, 10), (10, 0), (-1, 10)])
def test_non_positive_target_rejected(tw: float, th: float) -> None:
    with pytest.raises(PrintSizeError):
        effective_dpi(100, 100, tw, th)


# --- crop -----------------------------------------------------------------


def test_matching_aspect_crops_nothing() -> None:
    assert crop_fraction(2000, 1000, 6, 3) == 0.0


def test_mismatched_aspect_reports_crop() -> None:
    # Square image into a 2:1 banner loses half its area.
    assert crop_fraction(1000, 1000, 2, 1) == pytest.approx(0.5)


# --- the headline scenarios from the plan ---------------------------------


def test_whatsapp_logo_fails_as_a3_brochure() -> None:
    """PRD.md's stated limit: an 800px logo is not an A3 brochure."""
    a3_w, a3_h = 297, 420  # mm
    result = assess(800, 800, a3_w, a3_h, Unit.MM, "brochure")
    assert result.verdict is Verdict.TOO_SMALL
    assert "Not enough" in result.headline


def test_same_logo_works_as_a_small_flex_banner() -> None:
    """The same file, a different job — this is the feature's whole point."""
    result = assess(800, 800, 1, 1, Unit.FEET, "flex")
    assert result.verdict is Verdict.GOOD
    assert "Good for" in result.headline


def test_800px_logo_is_not_a_large_banner_either() -> None:
    """Corrects an over-optimistic claim in the source plan.

    PRD.md says an 800px logo "may be fine as a large flex banner". It is not:
    at 3×3 ft it prints at 22 DPI, under the 30 DPI flex minimum. It tops out
    around 1.3 ft of flex, and only reaches 3 ft as a distant hoarding.

    Being told this before starting the job is exactly what the feature is for.
    """
    flex = assess(800, 800, 3, 3, Unit.FEET, "flex")
    assert flex.verdict is Verdict.TOO_SMALL
    assert flex.max_w == pytest.approx(1.33, abs=0.01)

    hoarding = assess(800, 800, 3, 3, Unit.FEET, "hoarding")
    assert hoarding.verdict is Verdict.CAUTION


def test_ordinary_shop_job_is_not_flagged() -> None:
    """A 4000px photo at 6×4 ft is routine work and must read as GOOD.

    Crying wolf on everyday jobs is how the tool stops being consulted.
    """
    result = assess(4000, 2667, 6, 4, Unit.FEET, "flex")
    assert result.verdict is Verdict.GOOD
    assert result.effective_dpi >= 55


def test_verdict_boundaries_are_exact() -> None:
    """At exactly good_dpi it is GOOD; one pixel under, CAUTION."""
    # flex: good 50, min 30. Over 10 inches that is 500 px and 300 px.
    assert assess(500, 500, 10, 10, Unit.INCH, "flex").verdict is Verdict.GOOD
    assert assess(499, 499, 10, 10, Unit.INCH, "flex").verdict is Verdict.CAUTION
    assert assess(300, 300, 10, 10, Unit.INCH, "flex").verdict is Verdict.CAUTION
    assert assess(299, 299, 10, 10, Unit.INCH, "flex").verdict is Verdict.TOO_SMALL


# --- the safety property --------------------------------------------------


@pytest.mark.parametrize("cls", [c.key for c in PRINT_CLASSES])
def test_never_approves_below_required_dpi(cls: str) -> None:
    """The core guarantee: GOOD is never returned under the class threshold."""
    threshold = BY_KEY[cls].good_dpi
    for px in range(50, 3000, 50):
        result = assess(px, px, 10, 10, Unit.INCH, cls)
        if result.verdict is Verdict.GOOD:
            assert result.effective_dpi >= threshold, (
                f"{cls}: approved {px}px at {result.effective_dpi} DPI, "
                f"below the {threshold} DPI bar"
            )


@pytest.mark.parametrize("cls", [c.key for c in PRINT_CLASSES])
def test_verdict_is_monotonic_in_resolution(cls: str) -> None:
    """More pixels must never produce a worse verdict."""
    order = {Verdict.TOO_SMALL: 0, Verdict.CAUTION: 1, Verdict.GOOD: 2}
    previous = -1
    for px in range(100, 6000, 100):
        rank = order[assess(px, px, 10, 10, Unit.INCH, cls).verdict]
        assert rank >= previous, f"{cls}: verdict got worse at {px}px"
        previous = rank


def test_thresholds_respect_the_arcminute_rule() -> None:
    """Each class must ask for at least what the viewing distance demands.

    Guards against someone loosening a threshold below what the eye resolves.
    """
    approx_distance_inches = {"brochure": 18, "poster": 48, "flex": 120, "hoarding": 360}
    for cls in PRINT_CLASSES:
        needed = ARCMINUTE_CONSTANT / approx_distance_inches[cls.key]
        assert cls.good_dpi >= needed * 0.85, (
            f"{cls.key}: good_dpi {cls.good_dpi} is well under the ~{needed:.0f} "
            f"the arcminute rule implies"
        )


def test_classes_are_ordered_most_demanding_first() -> None:
    dpis = [c.good_dpi for c in PRINT_CLASSES]
    assert dpis == sorted(dpis, reverse=True)
    for cls in PRINT_CLASSES:
        assert cls.min_dpi < cls.good_dpi


# --- max size and upscale advice ------------------------------------------


def test_max_size_is_the_size_that_just_passes() -> None:
    result = assess(3000, 3000, 1, 1, Unit.INCH, "brochure")
    # 3000 px at 300 dpi = 10 inches
    assert result.max_w == pytest.approx(10.0)
    at_max = assess(3000, 3000, result.max_w, result.max_h, Unit.INCH, "brochure")
    assert at_max.verdict is Verdict.GOOD


def test_upscale_target_reaches_the_requirement() -> None:
    result = assess(800, 800, 10, 10, Unit.INCH, "brochure")
    assert result.upscale_to == (3000, 3000)
    upscaled = assess(*result.upscale_to, 10, 10, Unit.INCH, "brochure")
    assert upscaled.verdict is Verdict.GOOD


def test_no_upscale_advice_when_already_good() -> None:
    assert assess(4000, 4000, 5, 5, Unit.INCH, "brochure").upscale_to is None


# --- best_use_for ---------------------------------------------------------


def test_best_use_for_covers_every_class() -> None:
    rows = best_use_for(2000, 1500, Unit.FEET)
    assert [r["print_class"] for r in rows] == [c.key for c in PRINT_CLASSES]


def test_best_use_for_sizes_grow_as_requirements_relax() -> None:
    rows = best_use_for(2000, 1500, Unit.FEET)
    widths = [float(r["max_w"]) for r in rows]  # type: ignore[arg-type]
    assert widths == sorted(widths), "looser classes must allow larger prints"


def test_best_use_for_agrees_with_assess() -> None:
    for row in best_use_for(2400, 1800, Unit.INCH):
        result = assess(
            2400, 1800, float(row["max_w"]), float(row["max_h"]),  # type: ignore[arg-type]
            Unit.INCH, str(row["print_class"]),
        )
        assert result.verdict is Verdict.GOOD, row


# --- misc -----------------------------------------------------------------


def test_unknown_print_class_is_rejected() -> None:
    with pytest.raises(PrintSizeError):
        print_class("billboard-in-space")


def test_assessment_text_names_the_size_and_job() -> None:
    result = assess(4000, 2667, 6, 4, Unit.FEET, "flex")
    assert "6×4 feet" in result.headline
    assert "flex banner" in result.headline.lower()


def test_assessment_is_immutable() -> None:
    result = assess(1000, 1000, 1, 1, Unit.INCH, "flex")
    assert isinstance(result, Assessment)
    with pytest.raises((AttributeError, TypeError)):
        result.verdict = Verdict.GOOD  # type: ignore[misc]


# --- one number, one format (NEXT.md 3.1) --------------------------------


def test_the_prose_and_the_tile_agree_on_the_size() -> None:
    """`2×1.33333 feet` in the verdict beside `2×1.33 feet` in the tile.

    The same number in two formats on one screen, which reads as two different
    numbers to anyone not looking for it.
    """
    result = printsize.assess(4000, 2667, 2, 1.33333, "feet", "poster")
    assert "1.33333" not in result.headline
    assert "1.33" in result.headline
    # And the tile the operator reads next to it.
    assert result.max_w == round(result.max_w, 2)


def test_trim_still_drops_a_pointless_decimal() -> None:
    assert printsize._trim(6.0) == "6"  # noqa: SLF001
    assert printsize._trim(1.3333333) == "1.33"  # noqa: SLF001
    assert printsize._trim(0.5) == "0.5"  # noqa: SLF001


# --- say how much of the picture is lost (NEXT.md 3.7) -------------------


def test_heavy_cropping_is_stated_in_the_prose() -> None:
    """`crop_fraction: 0.8` was computed, returned, and never said out loud.

    The operator was not told that 80% of the picture would be cropped — a bigger
    surprise on a delivered print than any DPI figure.
    """
    result = printsize.assess(6000, 1000, 4, 4, "feet", "flex")
    assert result.crop_fraction > 0.5
    assert "cropped" in result.detail
    assert "%" in result.detail
    # And which edges go, because that decides whether it matters.
    assert "sides" in result.detail


def test_a_tall_image_on_a_wide_page_loses_top_and_bottom() -> None:
    result = printsize.assess(1000, 6000, 6, 4, "feet", "flex")
    assert "top and bottom" in result.detail


def test_a_matching_aspect_ratio_says_nothing_about_cropping() -> None:
    """No false alarm — the note must only appear when there is something to say."""
    result = printsize.assess(4000, 2667, 6, 4, "feet", "flex")
    assert result.crop_fraction < printsize.CROP_WORTH_SAYING
    assert "cropped" not in result.detail


def test_a_small_crop_is_not_worth_mentioning() -> None:
    """A 3% trim is not news, and saying so every time devalues saying it ever."""
    assert printsize._crop_note(0.03, 1.5, 1.55) is None  # noqa: SLF001
