"""Design styles: the prompt structure, and what it does to the copy.

This is the class of bug worth testing — one that spends money and looks fine.
A style that drops the headline still returns a perfectly good picture of the
wrong thing, and the operator only finds out when a client does.
"""

from __future__ import annotations

import pytest

from backend import db
from backend.features import ai, styles


@pytest.fixture(autouse=True)
def seeded() -> None:
    styles.seed_defaults()


# --- the shipped set ------------------------------------------------------


def test_every_shipped_style_is_usable_as_written() -> None:
    for style in styles.list_styles():
        assert styles.validate(style["body"]) == [], style["key"]


def test_every_shipped_style_forbids_lettering() -> None:
    """The one rule the whole poster feature rests on.

    A model that draws the headline draws Malayalam badly, and a badly drawn
    Malayalam headline is exactly the expensive, visible error this shop cannot
    afford. Every style has to say so.
    """
    for style in styles.list_styles():
        assert "no lettering" in style["body"].lower(), style["key"]


def test_every_shipped_style_styles_the_headline() -> None:
    """Half a style is not a style — the look has to reach the words too."""
    for style in styles.list_styles():
        defaults = style["text_defaults"]
        assert "background_colour" in defaults, style["key"]
        assert defaults["headline"]["colour"].startswith("#"), style["key"]


# --- the copy reaches the prompt ------------------------------------------


def test_the_copy_is_substituted_into_the_prompt() -> None:
    style = styles.by_key("offer")
    prompt = styles.build_prompt(
        style,
        {"headline": "ഗ്രാൻഡ് സെയിൽ", "offer": "50% OFF", "occasion": "Onam"},
    )
    assert "ഗ്രാൻഡ് സെയിൽ" in prompt
    assert "50% OFF" in prompt
    assert "Onam" in prompt
    assert "{{" not in prompt, "an unfilled variable would waste a paid call"


def test_an_empty_field_leaves_no_bare_label() -> None:
    """A bare `Offer:` reads to the model as a field to invent something for."""
    prompt = styles.build_prompt(styles.by_key("festival"), {"headline": "Sale"})
    assert "Offer:" not in prompt
    assert "Occasion:" not in prompt
    assert "Extra instruction:" not in prompt


def test_the_operators_own_idea_is_carried_through() -> None:
    prompt = styles.build_prompt(
        styles.by_key("minimal"),
        {"headline": "Now open", "idea": "a single brass key on white marble"},
    )
    assert "a single brass key on white marble" in prompt


def test_the_style_supplies_its_palette_unless_overridden() -> None:
    festival = styles.by_key("festival")
    default = styles.build_prompt(festival, {"headline": "Sale"})
    assert festival["palette"] in default

    overridden = styles.build_prompt(
        festival, {"headline": "Sale", "palette": "black and silver only"}
    )
    assert "black and silver only" in overridden
    assert festival["palette"] not in overridden


def test_two_styles_produce_different_prompts_from_the_same_copy() -> None:
    """Otherwise picking a style is decoration, not a choice."""
    copy = {"headline": "Grand opening", "offer": "Free gift"}
    assert styles.build_prompt(styles.by_key("wedding"), copy) != styles.build_prompt(
        styles.by_key("event"), copy
    )


# --- editing is safe ------------------------------------------------------


def test_a_prompt_that_forgets_the_headline_is_refused_at_edit_time() -> None:
    problems = styles.validate("A nice picture. No lettering anywhere.")
    assert any("headline" in p for p in problems)


def test_a_prompt_that_forgets_to_forbid_lettering_is_flagged() -> None:
    problems = styles.validate("A picture about {{headline}}.")
    assert any("lettering" in p.lower() for p in problems)


def test_an_unknown_variable_is_named_rather_than_silently_dropped() -> None:
    problems = styles.validate("{{headline}} in {{colour_scheme}}. No lettering.")
    assert any("colour_scheme" in p for p in problems)


def test_a_shipped_style_can_always_be_put_back() -> None:
    style = styles.by_key("festival")
    styles.save_style(
        style["key"],
        style["name"],
        style["description"],
        "ruined {{headline}}",
        style["palette"],
        style["swatches"],
        style["text_defaults"],
        style["id"],
    )
    assert styles.by_key("festival")["body"] == "ruined {{headline}}"

    restored = styles.restore_default(style["id"])
    assert restored["body"] == styles.SEED_KEYS["festival"].body


def test_a_shipped_style_cannot_be_deleted() -> None:
    """Deleting the six looks the shop works from is not an undo away."""
    with pytest.raises(styles.StyleError):
        styles.delete_style(styles.by_key("festival")["id"])


def test_a_shops_own_style_can_be_added_and_removed() -> None:
    added = styles.save_style(
        "shop-house",
        "House look",
        "Ours",
        "About {{headline}}. No lettering.",
        "green",
        ["#0f0"],
        {},
    )
    assert styles.by_key("shop-house")["name"] == "House look"
    styles.delete_style(added["id"])
    with pytest.raises(db.NotFound):
        styles.by_key("shop-house")


def test_a_bad_key_is_refused() -> None:
    with pytest.raises(styles.StyleError):
        styles.save_style("Not A Key", "x", "", "{{headline}} no lettering", "", [], {})


# --- the AI module uses them ----------------------------------------------


def test_artwork_prompt_is_free_and_shows_what_would_be_sent() -> None:
    text = ai.artwork_prompt({"headline": "പുതിയ കട"}, style_key="festival")
    assert "പുതിയ കട" in text
    assert "marigold" in text.lower()


def test_artwork_without_a_style_still_uses_the_prompt_library() -> None:
    text = ai.artwork_prompt({"subject": "a temple at dusk"}, style_key=None)
    assert "a temple at dusk" in text


def test_a_style_poster_needs_a_headline_not_a_subject() -> None:
    result = ai.generate_artwork({"headline": "  "}, style_key="festival")
    assert result.ok is False
    assert "headline" in (result.error or "").lower()
    assert result.cost_paise == 0


def test_an_unknown_style_fails_before_any_spend() -> None:
    result = ai.generate_artwork({"headline": "Sale"}, style_key="no-such-style")
    assert result.ok is False
    assert result.cost_paise == 0
