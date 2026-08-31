"""Tests for the poster layout engine.

The exit gate is "text sharp and **editable** — not rasterised", so the SVG
assertions are the important ones: real `<text>` elements carrying real
characters, well-formed, with nothing converted to paths.
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from backend.features import fonts, posters

SVG_NS = "{http://www.w3.org/2000/svg}"

PYTHON_SOURCE = Path(posters.__file__).read_text(encoding="utf-8")


def block(**kwargs) -> posters.TextBlock:
    base = {"id": "b1", "text": "GRAND SALE"}
    return posters.TextBlock(**(base | kwargs))


def photo(dark_top: bool = True, size: tuple[int, int] = (400, 600)) -> Image.Image:
    """Half dark, half light — so auto-colour has a right answer."""
    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    top = (20, 20, 30) if dark_top else (240, 240, 245)
    bottom = (240, 240, 245) if dark_top else (20, 20, 30)
    draw.rectangle((0, 0, size[0], size[1] // 2), fill=top)
    draw.rectangle((0, size[1] // 2, size[0], size[1]), fill=bottom)
    return img


def busy_photo() -> Image.Image:
    """A calm top half and a visually noisy bottom half."""
    img = Image.new("RGB", (400, 600), (128, 128, 128))
    draw = ImageDraw.Draw(img)
    for i in range(300, 600, 6):
        draw.line((0, i, 400, i), fill=(255, 255, 255) if i % 12 else (0, 0, 0), width=3)
    return img


# --- canvas geometry ------------------------------------------------------


def test_a4_at_300dpi_is_the_right_pixel_size() -> None:
    a4 = posters.CANVASES["a4-portrait"]
    assert (a4.width_px, a4.height_px) == (2480, 3508)


def test_flex_banner_is_metres_not_millimetres() -> None:
    """A 6×4 ft banner really is 1.83 m wide; a unit slip here is expensive."""
    flex = posters.CANVASES["flex-6x4"]
    assert flex.width_mm == pytest.approx(1828.8)
    assert flex.dpi == 72, "flex prints low-DPI because it is viewed from distance"


def test_every_preset_reports_consistent_geometry() -> None:
    for preset in posters.describe_presets():
        assert preset["width_px"] > 0 and preset["height_px"] > 0
        assert preset["aspect"] == pytest.approx(
            preset["width_mm"] / preset["height_mm"], rel=1e-3
        )


def test_unknown_canvas_is_rejected() -> None:
    with pytest.raises(posters.PosterError, match="unknown canvas"):
        posters.Layout(canvas="billboard-on-the-moon").resolved_canvas()


# --- safe zone ------------------------------------------------------------


def test_text_near_the_edge_is_flagged() -> None:
    """Left-aligned text starting at the trim is unsafe; centred text is not.

    The check measures the glyphs, not the box (NEXT.md 3.8). A *box* pinned to
    x=0 whose text is centred paints nowhere near the edge, so flagging it would
    be a false alarm — and false alarms are what get real warnings ignored.
    """
    canvas = posters.CANVASES["a4-portrait"]
    assert posters.outside_safe_zone(canvas, block(x=0.001, y=0.5, align="left")) is True
    assert posters.outside_safe_zone(canvas, block(x=0.2, y=0.5, width=0.6)) is False


def test_a_centred_box_at_the_edge_is_judged_on_its_text() -> None:
    """The box touches the trim; the short centred line inside it does not."""
    canvas = posters.CANVASES["a4-portrait"]
    assert posters.outside_safe_zone(canvas, block(x=0.0, y=0.5, align="centre")) is False


def test_text_running_below_the_bottom_trim_is_flagged() -> None:
    """Wrapping adds lines, and those lines have to go somewhere."""
    canvas = posters.CANVASES["a4-portrait"]
    tall = block(id="t", text="കേരളം ഗ്രാൻഡ് സെയിൽ", y=0.95, size="large")
    assert posters.outside_safe_zone(canvas, tall) is True


def test_a_wide_box_overflowing_the_right_edge_is_flagged() -> None:
    canvas = posters.CANVASES["a4-portrait"]
    assert posters.outside_safe_zone(canvas, block(x=0.5, y=0.5, width=0.6)) is True


def test_flex_banners_need_a_bigger_margin() -> None:
    """Large-format printers grip and trim far more than a desktop press."""
    a4 = posters.CANVASES["a4-portrait"]
    flex = posters.CANVASES["flex-6x4"]
    assert flex.safe_mm > a4.safe_mm


def test_safe_zone_report_covers_every_block() -> None:
    layout = posters.Layout(
        blocks=[block(id="a"), block(id="b", x=0.0, align="left")]
    )
    report = posters.safe_zone_report(layout)
    assert {r["id"] for r in report} == {"a", "b"}
    assert [r["outside_safe_zone"] for r in report] == [False, True]


def test_safe_zone_report_shows_where_the_text_actually_sits() -> None:
    """The extent is reported, so a warning can be explained rather than asserted."""
    layout = posters.Layout(blocks=[block(id="a")])
    extent = posters.safe_zone_report(layout)[0]["extent"]
    assert extent["x0"] < extent["x1"]
    assert extent["y0"] < extent["y1"]


# --- auto colour ----------------------------------------------------------


def test_light_text_on_a_dark_background() -> None:
    assert posters.suggest_colour(photo(dark_top=True), block(y=0.1)) == "#ffffff"


def test_dark_text_on_a_light_background() -> None:
    assert posters.suggest_colour(photo(dark_top=True), block(y=0.8)) == "#16161d"


def test_colour_flips_with_the_background() -> None:
    top = block(y=0.1)
    assert posters.suggest_colour(photo(dark_top=True), top) != posters.suggest_colour(
        photo(dark_top=False), top
    )


def test_no_background_defaults_to_white() -> None:
    assert posters.suggest_colour(None, block()) == "#ffffff"


def test_mid_grey_biases_to_dark_text() -> None:
    """The dangerous case. Dark-on-light survives a bad print better."""
    grey = Image.new("RGB", (200, 200), (150, 150, 150))
    assert posters.suggest_colour(grey, block(y=0.4)) == "#16161d"


# --- calm regions ---------------------------------------------------------


def test_calm_regions_prefer_the_quiet_half() -> None:
    regions = posters.find_calm_regions(busy_photo(), top=3)
    assert regions, "should always find something"
    assert all(r.y < 0.5 for r in regions), "the striped bottom half is not calm"


def test_calm_regions_are_sorted_by_busyness() -> None:
    regions = posters.find_calm_regions(busy_photo(), top=5)
    assert regions == sorted(regions, key=lambda r: r.busyness)


def test_auto_placement_keeps_reading_order() -> None:
    """A headline must not end up below the phone number."""
    blocks = [block(id="headline"), block(id="offer"), block(id="phone")]
    placed = posters.place_in_calm_space(busy_photo(), blocks)
    ys = [b.y for b in placed]
    assert ys == sorted(ys), f"blocks reordered vertically: {ys}"


def test_auto_placement_never_stacks_blocks_on_top_of_each_other() -> None:
    """Ranking single grid cells put all three lines in one band of a gradient.

    They were technically the calmest cells and completely unusable, because
    the text overlapped. Every block must land in its own horizontal band.
    """
    gradient = Image.new("RGB", (400, 600))
    draw = ImageDraw.Draw(gradient)
    for y in range(600):
        shade = int(16 + y * (239 - 16) / 600)
        draw.line((0, y, 400, y), fill=(shade, shade, shade + 8))

    blocks = [block(id=f"b{i}") for i in range(3)]
    ys = [b.y for b in posters.place_in_calm_space(gradient, blocks)]
    assert len(set(ys)) == len(ys), f"blocks share a position: {ys}"
    # And they are genuinely apart, not merely unequal.
    gaps = [b - a for a, b in zip(ys, ys[1:], strict=False)]
    assert all(g >= 0.08 for g in gaps), f"blocks too close together: {ys}"


def test_auto_placement_colours_each_block_for_where_it_landed() -> None:
    """Colour must follow the block's final position, not a global average.

    Deliberately not asserting that two blocks get *different* colours: on an
    image whose calm areas are all dark, all-white text is the right answer.
    The invariant is agreement with the background actually behind each line.
    """
    image = photo(dark_top=True)
    placed = posters.place_in_calm_space(image, [block(id="a"), block(id="b")])
    for placed_block in placed:
        assert placed_block.colour == posters.suggest_colour(image, placed_block), (
            f"{placed_block.id} at y={placed_block.y} got {placed_block.colour}"
        )


def test_auto_placement_spans_light_and_dark_when_both_are_calm() -> None:
    """Dark top, light bottom, noise only in the middle → one line in each."""
    image = Image.new("RGB", (400, 600))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 400, 220), fill=(18, 18, 26))
    draw.rectangle((0, 380, 400, 600), fill=(242, 242, 246))
    for y in range(220, 380, 7):
        draw.line((0, y, 400, y), fill=(255, 210, 60) if y % 14 else (10, 10, 10), width=4)

    placed = posters.place_in_calm_space(image, [block(id="a"), block(id="b")])
    assert {b.colour for b in placed} == {"#ffffff", "#16161d"}


def test_auto_placement_on_no_blocks_is_harmless() -> None:
    assert posters.place_in_calm_space(busy_photo(), []) == []


# --- SVG: the exit gate ---------------------------------------------------


def sample_layout() -> posters.Layout:
    return posters.Layout(
        canvas="a4-portrait",
        blocks=[
            posters.TextBlock(id="h", text="കേരളം ഗ്രാൻഡ് സെയിൽ", y=0.1, size="large"),
            posters.TextBlock(id="o", text="50% OFF", y=0.4, size="huge"),
            posters.TextBlock(id="p", text="9847 000 000", y=0.85, size="small"),
        ],
    )


def parse(markup: str) -> ET.Element:
    return ET.fromstring(markup)


def svg_texts(root: ET.Element) -> list[str]:
    """The words in each `<text>`, whether or not auto-fit wrapped it.

    A line too long for the canvas comes out as several `<tspan>`s (NEXT.md 0.2)
    rather than one line running off both edges. The gate is about the text being
    real and editable, not about it being on one line, so these are joined back
    up before comparing.
    """
    out: list[str] = []
    for element in root.iter(f"{SVG_NS}text"):
        spans = element.findall(f"{SVG_NS}tspan")
        if spans:
            out.append(" ".join((s.text or "") for s in spans))
        elif element.text:
            out.append(element.text)
    return out


def test_svg_is_well_formed_xml() -> None:
    root = parse(posters.render_svg(sample_layout()))
    assert root.tag == f"{SVG_NS}svg"


def test_svg_carries_real_text_not_paths() -> None:
    """The whole exit gate: editable text objects, nothing rasterised or traced."""
    root = parse(posters.render_svg(sample_layout()))
    texts = svg_texts(root)
    assert "കേരളം ഗ്രാൻഡ് സെയിൽ" in texts
    assert "50% OFF" in texts
    assert "9847 000 000" in texts
    assert not list(root.iter(f"{SVG_NS}path")), "text must not be converted to outlines"


def test_svg_malayalam_survives_verbatim() -> None:
    """Unicode mode must not mangle a single codepoint."""
    original = "കേരളം ഗ്രാൻഡ് സെയിൽ"
    root = parse(posters.render_svg(sample_layout()))
    assert original in svg_texts(root)


def test_svg_dimensions_are_physical_for_print() -> None:
    root = parse(posters.render_svg(sample_layout()))
    assert root.get("width") == "210mm"
    assert root.get("height") == "297mm"
    assert root.get("viewBox") == "0 0 2480 3508"


def test_ascii_mode_uses_the_phase_one_converter() -> None:
    """Shaping already done, so no shaping engine is needed downstream."""
    layout = posters.Layout(
        blocks=[posters.TextBlock(id="a", text="കേരളം", mode="ascii", shadow=False)]
    )
    root = parse(posters.render_svg(layout))
    node = next(root.iter(f"{SVG_NS}text"))
    assert node.text == "tIcfw"
    assert node.get("font-family") == "ML-TTKarthika"


def test_unicode_mode_asks_for_a_unicode_font() -> None:
    layout = posters.Layout(
        blocks=[posters.TextBlock(id="a", text="കേരളം", shadow=False)]
    )
    node = next(parse(posters.render_svg(layout)).iter(f"{SVG_NS}text"))
    assert "Noto Sans Malayalam" in (node.get("font-family") or "")


def test_shadow_adds_a_second_text_element_not_a_raster() -> None:
    layout = posters.Layout(blocks=[posters.TextBlock(id="a", text="HI", shadow=True)])
    root = parse(posters.render_svg(layout))
    assert len(list(root.iter(f"{SVG_NS}text"))) == 2


def test_special_characters_do_not_break_the_svg() -> None:
    """Client copy contains ampersands and angle brackets."""
    layout = posters.Layout(
        blocks=[posters.TextBlock(id="a", text='Tom & Jerry <b> "quotes"', shadow=False)]
    )
    node = next(parse(posters.render_svg(layout)).iter(f"{SVG_NS}text"))
    assert node.text == 'Tom & Jerry <b> "quotes"'


def test_background_is_embedded_so_the_file_is_self_contained() -> None:
    buffer = io.BytesIO()
    photo().save(buffer, format="PNG")
    markup = posters.render_svg(sample_layout(), background=buffer.getvalue())
    assert "data:image/png;base64," in markup
    parse(markup)  # still well formed with the payload inside


def test_safe_zone_overlay_is_opt_in() -> None:
    assert "stroke-dasharray" not in posters.render_svg(sample_layout())
    assert "stroke-dasharray" in posters.render_svg(sample_layout(), show_safe_zone=True)


def test_alignment_sets_the_svg_anchor() -> None:
    for align, anchor in (("left", "start"), ("centre", "middle"), ("right", "end")):
        layout = posters.Layout(
            blocks=[posters.TextBlock(id="a", text="X", align=align, shadow=False)]
        )
        node = next(parse(posters.render_svg(layout)).iter(f"{SVG_NS}text"))
        assert node.get("text-anchor") == anchor


def test_text_scales_with_the_canvas_not_with_points() -> None:
    """The same layout must look right on A4 and on a 6 ft banner."""
    def size_of(canvas: str) -> float:
        layout = posters.Layout(
            canvas=canvas,
            blocks=[posters.TextBlock(id="a", text="X", size="huge", shadow=False)],
        )
        node = next(parse(posters.render_svg(layout)).iter(f"{SVG_NS}text"))
        return float(node.get("font-size") or 0)

    a4 = size_of("a4-portrait")
    flex = size_of("flex-6x4")
    # Tolerance covers the `:.1f` rounding in the serialised attribute; the
    # underlying arithmetic is exactly SIZE_SCALE × canvas height.
    assert a4 / posters.CANVASES["a4-portrait"].height_px == pytest.approx(
        flex / posters.CANVASES["flex-6x4"].height_px, rel=1e-3
    )
    assert a4 / posters.CANVASES["a4-portrait"].height_px == pytest.approx(
        posters.SIZE_SCALE["huge"], rel=1e-3
    )


# --- overflow -------------------------------------------------------------


def test_obvious_overflow_is_warned_about() -> None:
    layout = posters.Layout(
        blocks=[posters.TextBlock(id="a", text="X" * 200, width=0.2, size="huge")]
    )
    assert posters.overflow_warnings(layout)


def test_short_text_is_not_warned_about() -> None:
    layout = posters.Layout(
        blocks=[posters.TextBlock(id="a", text="SALE", width=0.8, size="medium")]
    )
    assert posters.overflow_warnings(layout) == []


# --- the architectural guarantee -----------------------------------------


def test_the_module_never_rasterises() -> None:
    """Pillow here cannot shape Malayalam, so posters must stay vector.

    Verified 2026-08-23: rendering ``കേരളം`` through Pillow put the ``േ`` sign
    after ``ക`` and produced no conjunct ligatures. This test guards the
    decision, so a future raster shortcut fails loudly instead of shipping
    broken Malayalam.
    """
    from PIL import features

    source = (posters.__file__).replace(".py", ".py")
    text = open(source, encoding="utf-8").read()
    assert "ImageDraw" not in text, "posters.py must not draw text with Pillow"
    if not features.check("raqm"):
        assert "def render_png" not in text, (
            "no raster path may exist while Pillow cannot shape Malayalam"
        )


# --- pasted copy ----------------------------------------------------------
#
# The operator pastes one WhatsApp message and the app decides which line is
# which. A wrong guess is visible and correctable; a *dropped* line is neither,
# and it reaches the printer.


def _roles(text: str) -> dict[str, str]:
    return {row["role"]: row["text"] for row in posters.split_copy(text)}


def test_no_line_is_ever_dropped() -> None:
    """The one guarantee that matters — a lost line reaches the printer."""
    pasted = "Line one\nLine two\nLine three\n50% off\n9847 000 000\nExtra note"
    out = posters.split_copy(pasted)
    assert [row["text"] for row in out] == [
        "Line one",
        "Line two",
        "Line three",
        "50% off",
        "9847 000 000",
        "Extra note",
    ]


def test_a_typical_whatsapp_message_is_sorted() -> None:
    roles = _roles("ഓണം\nഗ്രാൻഡ് സെയിൽ\n50% OFF\n9847 000 000")
    assert roles["occasion"] == "ഓണം"
    assert roles["headline"] == "ഗ്രാൻഡ് സെയിൽ"
    assert roles["offer"] == "50% OFF"
    assert roles["phone"] == "9847 000 000"


def test_a_phone_number_is_found_wherever_it_sits() -> None:
    for pasted in (
        "Call +91 98470 12345\nGRAND SALE",
        "GRAND SALE\nPh: 9847000000",
        "GRAND SALE\n9847-000-000\nFlat 20% off",
    ):
        assert "phone" in _roles(pasted), pasted


def test_a_year_is_not_mistaken_for_a_phone_number() -> None:
    assert _roles("Since 1998\nGRAND SALE").get("phone") is None


def test_the_first_line_leads_unless_it_names_a_festival() -> None:
    assert _roles("GRAND SALE\nSarees and more")["headline"] == "GRAND SALE"
    assert _roles("Onam\nGRAND SALE")["occasion"] == "Onam"


def test_grand_sale_is_a_headline_not_an_occasion() -> None:
    """"Sale" is deliberately not an occasion word; tagging it pushes the real
    headline down a slot and the poster comes out with the wrong big line."""
    assert _roles("GRAND SALE\n50% off")["headline"] == "GRAND SALE"


def test_only_the_first_candidate_takes_a_role() -> None:
    out = posters.split_copy("Shop now\n9847 000 000\n9847 111 111")
    phones = [row for row in out if row["role"] == "phone"]
    assert len(phones) == 1
    assert out[2]["role"] == "free", "the second number stays as an extra line"


def test_a_single_line_becomes_the_headline() -> None:
    assert _roles("Just this")["headline"] == "Just this"


def test_blank_lines_and_padding_are_ignored() -> None:
    out = posters.split_copy("\n\n  GRAND SALE  \n\n   \n 50% off \n")
    assert [row["text"] for row in out] == ["GRAND SALE", "50% off"]


def test_empty_input_is_not_an_error() -> None:
    assert posters.split_copy("   \n\n ") == []


def test_a_runaway_paste_is_capped() -> None:
    out = posters.split_copy("\n".join(f"line {i}" for i in range(200)))
    assert len(out) == posters.MAX_COPY_LINES


def test_a_figure_beats_the_word_offer() -> None:
    """"ഓണം ഓഫർ" is the festival wearing the word, not the price.

    Taking the first line containing a word for "offer" put the festival name in
    the price slot and left the actual price as a spare line.
    """
    roles = _roles(
        "ഓണം ഓഫർ\nഗ്രാൻഡ് സെയിൽ\nFlat 50% OFF on all sarees\n9847 000 000"
    )
    assert roles["offer"] == "Flat 50% OFF on all sarees"
    assert roles["occasion"] == "ഓണം ഓഫർ"
    assert roles["headline"] == "ഗ്രാൻഡ് സെയിൽ"


def test_a_word_still_wins_when_there_is_no_figure() -> None:
    assert _roles("GRAND SALE\nFree gift inside")["offer"] == "Free gift inside"


def test_a_long_first_line_naming_a_festival_is_the_headline() -> None:
    """A kicker is two words. "Onam Mega Sale" is the big line, not a label."""
    assert _roles("Onam Mega Sale\nBig discounts inside")["headline"] == "Onam Mega Sale"


# --- golden layout widths -------------------------------------------------
#
# NEXT.md 0.2: a Malayalam headline did not fit the canvas at any size that
# reads as a headline, and the only defence was a warning rendered *below* the
# export buttons. These lock the fix. The fixture is real browser measurement —
# see tests/golden/poster_widths.tsv for how it was taken and why nothing else
# can stand in for it.

GOLDEN_WIDTHS = Path(__file__).parent / "golden" / "poster_widths.tsv"


def _golden_widths() -> list[tuple[str, str, str, float, str]]:
    rows: list[tuple[str, str, str, float, str]] = []
    for line in GOLDEN_WIDTHS.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        assert len(parts) >= 4, f"malformed golden row: {line!r}"
        note = parts[4] if len(parts) > 4 else ""
        rows.append((parts[0], parts[1], parts[2], float(parts[3]), note))
    return rows


WIDTH_ROWS = _golden_widths()


def test_golden_width_fixture_is_present() -> None:
    """The measurements are the only ground truth; losing them loses the test."""
    assert len(WIDTH_ROWS) >= 5, f"only {len(WIDTH_ROWS)} measured rows"


@pytest.mark.parametrize(
    ("text", "size", "canvas_key", "rendered_px", "note"),
    WIDTH_ROWS,
    ids=[f"{r[0][:14]}-{r[1]}" for r in WIDTH_ROWS],
)
def test_measured_text_is_fitted_inside_the_safe_box(
    text: str, size: str, canvas_key: str, rendered_px: float, note: str
) -> None:
    """Given the real measurement, auto-fit must bring the text inside the trim.

    This is the assertion that 0.2 was missing: the shop's own name at the
    default headline size rendered 231% of the canvas width, and the SVG carried
    it off both edges with no wrapping and no auto-shrink.
    """
    canvas = posters.CANVASES[canvas_key]
    blk = block(id="g1", text=text, size=size, width=0.84)
    requested_px = posters.SIZE_SCALE[size] * canvas.height_px
    measured_em = rendered_px / requested_px

    fitted = posters.fit_block(blk, canvas, measured_em=measured_em)

    inset_x, _ = canvas.safe_fraction
    limit = min(blk.width, 1 - 2 * inset_x)
    assert fitted.width <= limit + 1e-6, (
        f"{note}: fitted to {fitted.width:.3f} of the canvas, box allows {limit:.3f}"
    )
    assert not fitted.overflows, f"{note}: text would be trimmed off the sheet"
    assert not fitted.over_box, f"{note}: auto-fit gave up on ordinary shop copy"
    assert fitted.font_px > 0


def test_the_shop_name_actually_gets_shrunk_or_wrapped() -> None:
    """The headline row must be visibly adjusted, not quietly passed through."""
    canvas = posters.CANVASES["a4-portrait"]
    text, size, _, rendered_px, _ = WIDTH_ROWS[0]
    blk = block(id="g1", text=text, size=size, width=0.84)
    measured_em = rendered_px / (posters.SIZE_SCALE[size] * canvas.height_px)

    fitted = posters.fit_block(blk, canvas, measured_em=measured_em)
    assert fitted.shrunk or fitted.wrapped
    # 231% of the page cannot be rescued by shrinking alone without dropping
    # below the legible floor, so this one must wrap.
    assert fitted.wrapped, "a headline at 231% of the page should wrap, not just shrink"


@pytest.mark.parametrize(
    ("text", "size", "canvas_key", "rendered_px", "note"),
    WIDTH_ROWS,
    ids=[f"{r[0][:14]}-{r[1]}" for r in WIDTH_ROWS],
)
def test_estimate_never_calls_overflowing_text_safe(
    text: str, size: str, canvas_key: str, rendered_px: float, note: str
) -> None:
    """The backend's own estimate may over-warn. It may never under-warn.

    The estimator has no shaping engine and is documented as approximate. The
    direction of its error is what matters: over-warning costs a glance,
    under-warning costs a reprint.
    """
    canvas = posters.CANVASES[canvas_key]
    blk = block(id="g1", text=text, size=size, width=0.84)
    estimated_px = posters.estimate_text_width(blk, canvas) * canvas.width_px

    inset_x, _ = canvas.safe_fraction
    safe_px = (1 - 2 * inset_x) * canvas.width_px
    if rendered_px > safe_px:
        assert estimated_px * posters._ESTIMATE_MARGIN > safe_px, (
            f"{note}: really {rendered_px:.0f}px, estimated {estimated_px:.0f}px — "
            f"would have been reported as fitting a {safe_px:.0f}px box"
        )


def test_svg_wraps_a_long_headline_into_tspans() -> None:
    """An overlong line comes out as several lines, not one that runs off."""
    text = WIDTH_ROWS[0][0]
    layout = posters.Layout(
        canvas="a4-portrait",
        blocks=[block(id="h", text=text, size="huge", width=0.84)],
    )
    canvas = layout.resolved_canvas()
    measured = {"h": WIDTH_ROWS[0][3] / (posters.SIZE_SCALE["huge"] * canvas.height_px)}

    root = ET.fromstring(posters.render_svg(layout, measured=measured, embed_font=False))
    texts = root.findall(f".//{SVG_NS}text")
    assert texts, "no <text> element in the export"
    spans = texts[-1].findall(f"{SVG_NS}tspan")
    assert len(spans) > 1, "the headline was not wrapped in the exported SVG"
    # Nothing may be lost in the process — that is the rule the whole module
    # is built on.
    joined = " ".join((s.text or "") for s in spans)
    assert joined.split() == text.split()


def test_export_embeds_the_unicode_font() -> None:
    """NEXT.md 1.5: the SVG named a font it did not ship."""
    layout = posters.Layout(blocks=[block(id="h", text="ഓണം ആശംസകൾ", mode="unicode")])
    markup = posters.render_svg(layout)
    assert "@font-face" in markup
    assert "font/woff2;base64," in markup


def test_ascii_only_export_skips_the_font_payload() -> None:
    """ML-TTKarthika posters do not need 89 KB of a font they never consult."""
    layout = posters.Layout(blocks=[block(id="h", text="ഓണം", mode="ascii")])
    assert "@font-face" not in posters.render_svg(layout)


def test_safe_zone_and_overflow_agree() -> None:
    """NEXT.md 3.8: two checks that did not talk to each other.

    A centred block in a narrow box, with text far too wide for it, used to
    report `outside_safe_zone: false` while the overflow check reported a
    violation. The safe-zone test now measures the glyphs, not the box.
    """
    canvas = posters.CANVASES["a4-portrait"]
    # A narrow box in the middle of the page: the box is safe, the text is not.
    blk = block(id="w", text="X" * 400, size="huge", x=0.45, width=0.1, align="centre")
    fitted = posters.fit_block(blk, canvas)
    assert fitted.overflows, "fixture is wrong — this text should not be fittable"
    assert posters.outside_safe_zone(canvas, blk, fitted), (
        "text running past the trim was reported as inside the safe zone"
    )


# --- the two copies of the fitter ------------------------------------------
#
# `frontend/src/lib/textFit.ts` measures in the browser and `posters.fit_block`
# applies the same shrink-then-wrap rule to the measurement. The constants are
# duplicated across the two, and textFit.ts's own docstring says they "must stay
# in step — that pairing is what makes the preview, the PNG proof and the
# exported SVG agree on where the text breaks". Until now nothing checked it, so
# the promise was worth exactly as much as somebody remembering it. Drift here
# does not raise: it makes the preview quietly lie about the printed output.

TEXTFIT = Path(__file__).resolve().parent.parent / "frontend" / "src" / "lib" / "textFit.ts"


def _ts_number(source: str, name: str) -> float:
    """Read `const NAME = 1.25` out of the TypeScript."""
    found = re.search(rf"\b{name}\s*=\s*([0-9.]+)", source)
    assert found, f"textFit.ts no longer declares {name} — the mirror is broken"
    return float(found.group(1))


def _ts_size_scale(source: str) -> dict[str, float]:
    body = re.search(r"SIZE_SCALE[^{]*\{(.*?)\}", source, re.DOTALL)
    assert body, "textFit.ts no longer declares SIZE_SCALE"
    return {k: float(v) for k, v in re.findall(r"(\w+)\s*:\s*([0-9.]+)", body.group(1))}


def test_both_copies_of_the_fitter_use_the_same_constants() -> None:
    source = TEXTFIT.read_text(encoding="utf-8")
    assert _ts_size_scale(source) == posters.SIZE_SCALE
    assert _ts_number(source, "MIN_FIT_SCALE") == posters.MIN_FIT_SCALE
    assert _ts_number(source, "LINE_HEIGHT") == posters.LINE_HEIGHT


def test_both_copies_of_the_fitter_take_the_same_number_of_wrap_steps() -> None:
    """`for step in range(0, 41)` against `WRAP_STEPS = 40`.

    Off by one here and the two pick different font sizes at the boundary, so a
    block wraps in the preview and not in the SVG, or the reverse.
    """
    python_steps = re.search(r"for step in range\(0,\s*(\d+)\)", PYTHON_SOURCE)
    assert python_steps, "fit_block no longer walks a fixed step range"
    ts_steps = _ts_number(TEXTFIT.read_text(encoding="utf-8"), "WRAP_STEPS")
    assert int(python_steps.group(1)) == int(ts_steps) + 1


# --- typography -------------------------------------------------------------
#
# Five new per-block fields. Every one of them has to reach four renderers that
# do not share code — the DOM preview, the SVG export, the PNG proof and the
# duplicated fitter — so these test the rules that keep them agreeing.


def test_a_block_with_no_typography_renders_exactly_as_before() -> None:
    """The defaults are the contract. Every poster the shop has already made
    predates these fields, and none of them may move by a pixel."""
    canvas = posters.CANVASES["a4-portrait"]
    plain = posters.TextBlock(id="h", text="GRAND SALE", size="large")
    assert plain.tracking == 0.0
    assert plain.leading == posters.LINE_HEIGHT
    assert plain.case == "as-typed"
    assert plain.size_fraction is None

    # The requested size still comes from the bucket; whatever the fitter then
    # does to it is the behaviour that already shipped.
    assert posters.size_fraction(plain) == posters.SIZE_SCALE["large"]
    fitted = posters.fit_block(plain, canvas)
    assert fitted.height == pytest.approx(
        fitted.font_px * posters.LINE_HEIGHT / canvas.height_px
    )
    assert fitted.lines == ["GRAND SALE"]
    assert 'letter-spacing' not in posters.render_svg(posters.Layout(blocks=[plain]))


def test_a_size_fraction_overrides_the_bucket() -> None:
    canvas = posters.CANVASES["a4-portrait"]
    blk = block(id="h", text="X", size="small", size_fraction=0.2)
    assert posters.size_fraction(blk) == 0.2
    assert posters.fit_block(blk, canvas).font_px == 0.2 * canvas.height_px


def test_an_unprintable_size_fraction_falls_back_to_the_bucket() -> None:
    """Clamping would silently pick a size the operator never chose."""
    blk = block(id="h", size="large", size_fraction=9.0).clamp()
    assert blk.size_fraction is None
    assert posters.size_fraction(blk) == posters.SIZE_SCALE["large"]


def test_tracking_widens_a_line_by_exactly_its_character_count() -> None:
    """Arithmetic, never measured — that is the only way the browser's fitter
    and this one can agree without a shaping engine.

    The full count, not count − 1: CSS and SVG both add letter spacing after
    every character, the last one included.
    """
    text = "SALE"
    natural = posters._advance_em(text)
    assert posters._advance_em(text, 0.1) == pytest.approx(natural + 0.1 * len(text))


def test_tracking_reaches_the_exported_svg_only_when_it_is_set() -> None:
    plain = posters.render_svg(posters.Layout(blocks=[block(id="h")]))
    spaced = posters.render_svg(posters.Layout(blocks=[block(id="h", tracking=0.08)]))
    assert "letter-spacing" not in plain
    assert "letter-spacing" in spaced


def test_uppercase_is_applied_before_the_ascii_conversion() -> None:
    """The one ordering that puts silent garbage on a client's poster.

    In `ascii` mode the string is ML-TTKarthika byte codes, where `.upper()`
    selects entirely different glyphs. Malayalam is unicameral, so casing it is
    a no-op — which is what makes doing it first safe.
    """
    malayalam = "കേരളം"
    upper = posters.TextBlock(id="h", text=malayalam, mode="ascii", case="upper")
    plain = posters.TextBlock(id="h", text=malayalam, mode="ascii", case="as-typed")
    assert posters._text_for(upper) == posters._text_for(plain)
    # And the ASCII is not itself upper-cased on the way out.
    assert posters._text_for(upper) == fonts.unicode_to_ascii(malayalam)


def test_uppercase_reaches_latin_text_in_the_svg() -> None:
    svg = posters.render_svg(
        posters.Layout(blocks=[block(id="h", text="grand sale", case="upper")])
    )
    assert "GRAND SALE" in svg
    assert ">grand sale<" not in svg


def test_a_wrapped_upper_case_block_is_not_cased_twice() -> None:
    """`fit_block` cases before wrapping; `_line_text` must not case again."""
    canvas = posters.CANVASES["a4-portrait"]
    blk = block(id="h", text="grand opening sale today", size="huge", width=0.3,
                case="upper")
    fitted = posters.fit_block(blk, canvas)
    assert fitted.lines, "fixture produced no lines"
    for line in fitted.lines:
        assert line == line.upper()


def test_leading_changes_how_far_down_the_page_a_block_reaches() -> None:
    """`Fitted.height` feeds `text_extent` and so the safe-zone check: raising
    leading on a bottom-anchored line can newly push a poster past the trim.
    That is correct behaviour — the text really does reach further — and it is
    pinned here because it will read as a regression."""
    canvas = posters.CANVASES["a4-portrait"]
    tight = posters.fit_block(
        block(id="h", text="one two three four five six", size="huge", width=0.3,
              leading=1.0),
        canvas,
    )
    loose = posters.fit_block(
        block(id="h", text="one two three four five six", size="huge", width=0.3,
              leading=2.0),
        canvas,
    )
    assert len(tight.lines) == len(loose.lines), "fixture must wrap the same way"
    assert loose.height > tight.height


def test_leading_reaches_the_tspans_of_a_wrapped_block() -> None:
    canvas = posters.CANVASES["a4-portrait"]
    blk = block(id="h", text="one two three four five six", size="huge", width=0.3,
                leading=2.0)
    fitted = posters.fit_block(blk, canvas)
    assert len(fitted.lines) > 1, "fixture must wrap"
    svg = posters.render_svg(posters.Layout(blocks=[blk]))
    assert f'dy="{fitted.font_px * 2.0:.1f}"' in svg


def test_a_supplied_measurement_is_not_tracked_a_second_time() -> None:
    """The wire contract: `measured` is the width that will be *drawn*, letter
    spacing already included. Adding tracking to it again would double-count and
    wrap text the browser had shown fitting."""
    canvas = posters.CANVASES["a4-portrait"]
    blk = block(id="h", text="SALE", size="small", tracking=0.3)
    with_measure = posters.fit_block(blk, canvas, measured_em=2.0)
    assert with_measure.width == pytest.approx(
        2.0 * with_measure.font_px / canvas.width_px
    )


def test_the_embedded_font_is_found_where_a_packaged_install_puts_it() -> None:
    """A packaged install ships only `frontend/dist` — there is no source tree
    on the shop PC. Resolving the woff2 from `frontend/public` alone made
    `_font_face` return "" and the exported SVG carry no embedded font, which is
    invisible until somebody opens the file in CorelDRAW (NEXT.md 1.5).
    """
    assert posters.UNICODE_FONT_FILE.exists(), posters.UNICODE_FONT_FILE
    assert posters.UNICODE_FONT_FILE.stat().st_size > 1000
    # `dist` is the shipped one, so it must be preferred over `public`.
    assert posters._FONT_CANDIDATES[0].parts[-3] == "dist"


def test_a_unicode_poster_carries_its_font_inside_the_svg() -> None:
    svg = posters.render_svg(posters.Layout(blocks=[block(id="h", text="കേരളം")]))
    assert "@font-face" in svg
    assert "base64" in svg, "the font was referenced but not embedded"
