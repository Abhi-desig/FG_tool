"""Tests for the poster layout engine.

The exit gate is "text sharp and **editable** — not rasterised", so the SVG
assertions are the important ones: real `<text>` elements carrying real
characters, well-formed, with nothing converted to paths.
"""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET

import pytest
from PIL import Image, ImageDraw

from backend.features import posters

SVG_NS = "{http://www.w3.org/2000/svg}"


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
    canvas = posters.CANVASES["a4-portrait"]
    assert posters.outside_safe_zone(canvas, block(x=0.001, y=0.5)) is True
    assert posters.outside_safe_zone(canvas, block(x=0.2, y=0.5, width=0.6)) is False


def test_a_wide_box_overflowing_the_right_edge_is_flagged() -> None:
    canvas = posters.CANVASES["a4-portrait"]
    assert posters.outside_safe_zone(canvas, block(x=0.5, y=0.5, width=0.6)) is True


def test_flex_banners_need_a_bigger_margin() -> None:
    """Large-format printers grip and trim far more than a desktop press."""
    a4 = posters.CANVASES["a4-portrait"]
    flex = posters.CANVASES["flex-6x4"]
    assert flex.safe_mm > a4.safe_mm


def test_safe_zone_report_covers_every_block() -> None:
    layout = posters.Layout(blocks=[block(id="a"), block(id="b", x=0.0)])
    report = posters.safe_zone_report(layout)
    assert {r["id"] for r in report} == {"a", "b"}
    assert [r["outside_safe_zone"] for r in report] == [False, True]


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


def test_svg_is_well_formed_xml() -> None:
    root = parse(posters.render_svg(sample_layout()))
    assert root.tag == f"{SVG_NS}svg"


def test_svg_carries_real_text_not_paths() -> None:
    """The whole exit gate: editable text objects, nothing rasterised or traced."""
    root = parse(posters.render_svg(sample_layout()))
    texts = [t.text for t in root.iter(f"{SVG_NS}text")]
    assert "കേരളം ഗ്രാൻഡ് സെയിൽ" in texts
    assert "50% OFF" in texts
    assert "9847 000 000" in texts
    assert not list(root.iter(f"{SVG_NS}path")), "text must not be converted to outlines"


def test_svg_malayalam_survives_verbatim() -> None:
    """Unicode mode must not mangle a single codepoint."""
    original = "കേരളം ഗ്രാൻഡ് സെയിൽ"
    root = parse(posters.render_svg(sample_layout()))
    assert any(t.text == original for t in root.iter(f"{SVG_NS}text"))


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
