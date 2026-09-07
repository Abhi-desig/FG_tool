"""Translation tests.

Model-backed cases are skipped when the weights are not downloaded, so the suite
stays runnable on a fresh checkout. What is always tested: routing between the
glossary path and the model path, and the flags the review grid depends on.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend import models, textkey
from backend.features import dictionary, glossary, translate

HAS_ENGINE = translate.available_engine() is not None
needs_engine = pytest.mark.skipif(HAS_ENGINE is False, reason="no engine downloaded")


def terms(pairs: dict[str, str]) -> list[tuple[str, str]]:
    return glossary.compile_terms(
        [{"source_term": k, "target_term": v} for k, v in pairs.items()]
    )


# --- registry and licensing ----------------------------------------------


def test_both_engines_are_registered() -> None:
    keys = {e["key"] for e in translate.engine_status()}
    assert keys == {"opus-mt-en-ml", "indictrans2-en-indic"}


def test_default_engine_is_the_open_one() -> None:
    default = [e for e in translate.engine_status() if e["default"]]
    assert len(default) == 1
    assert default[0]["key"] == "opus-mt-en-ml"
    assert default[0]["gated"] is False


def test_gated_engine_is_flagged_as_gated() -> None:
    it2 = next(e for e in translate.engine_status() if e["key"] == "indictrans2-en-indic")
    assert it2["gated"] is True
    assert "gated" in str(it2["notes"]).lower()


def test_every_engine_licence_permits_commercial_use() -> None:
    """LICENSES.md: the shop sells this work, so non-commercial is banned.

    This is why NLLB-200 is absent despite being the popular choice — it is
    cc-by-nc-4.0.
    """
    allowed = {"MIT", "Apache-2.0", "BSD-3-Clause"}
    for engine in translate.engine_status():
        assert engine["licence"] in allowed, engine


def test_non_commercial_models_are_not_reachable() -> None:
    for banned in ("facebook/nllb-200-distilled-600M", "nllb-200"):
        assert banned not in models.REGISTRY


# --- routing between glossary and model ----------------------------------


def test_no_sources_is_no_work() -> None:
    assert translate.translate_rows([], []) == []


def test_fully_covered_rows_never_touch_the_model() -> None:
    """A catalogue of product names should mostly bypass the model entirely."""
    t = terms({"coconut oil": "വെളിച്ചെണ്ണ", "cardamom": "ഏലക്കായ"})
    rows = translate.translate_rows(["Coconut oil", "Cardamom"], t, engine="does-not-exist")
    assert all(r.glossary_only for r in rows)
    assert [r.translation for r in rows] == ["വെളിച്ചെണ്ണ", "ഏലക്കായ"]


def test_glossary_only_rows_record_which_terms_matched() -> None:
    t = terms({"coconut oil": "വെളിച്ചെണ്ണ"})
    row = translate.translate_rows(["Coconut oil"], t, engine="does-not-exist")[0]
    assert row.glossary_terms == ["coconut oil"]
    assert row.needs_attention is False


def test_empty_translation_needs_attention() -> None:
    assert translate.Row(source="x", translation="").needs_attention is True


def test_lost_term_needs_attention() -> None:
    row = translate.Row(source="x", translation="ok", lost_terms=["ബ്രാൻഡ്"])
    assert row.needs_attention is True


def test_indictrans2_gets_language_tags_and_marian_does_not() -> None:
    assert translate._prepare("indictrans2-en-indic", "hello") == "eng_Latn mal_Mlym hello"
    assert translate._prepare("opus-mt-en-ml", "hello") == "hello"


def test_language_tags_are_stripped_from_output() -> None:
    assert translate._strip_tags("mal_Mlym വെളിച്ചെണ്ണ") == "വെളിച്ചെണ്ണ"


# --- model-backed ---------------------------------------------------------


@needs_engine
def test_real_translation_produces_malayalam() -> None:
    rows = translate.translate_rows(["Price per kilogram"], [])
    assert len(rows) == 1
    assert any("ഀ" <= ch <= "ൿ" for ch in rows[0].translation), rows[0]


@needs_engine
def test_glossary_beats_the_model_on_a_word_it_gets_wrong() -> None:
    """The headline case: the model drops "coconut"; the glossary must not.

    Measured 2026-08-22 — bare, this model returns "oil, 1 litre bottle".
    """
    plain = translate.translate_rows(["Coconut oil, 1 litre bottle"], [])[0]
    t = terms({"coconut oil": "വെളിച്ചെണ്ണ"})
    guarded = translate.translate_rows(["Coconut oil, 1 litre bottle"], t)[0]

    assert "വെളിച്ചെണ്ണ" in guarded.translation, (
        f"glossary term missing. plain={plain.translation!r} "
        f"guarded={guarded.translation!r}"
    )
    assert guarded.glossary_terms == ["coconut oil"]


@needs_engine
def test_mixed_batch_routes_each_row_correctly() -> None:
    t = terms({"cardamom": "ഏലക്കായ"})
    rows = translate.translate_rows(
        ["Cardamom", "Delivery within three days", "Cardamom 100 g pack"], t
    )
    assert rows[0].glossary_only is True
    assert rows[1].glossary_only is False
    assert rows[2].glossary_only is False
    assert "ഏലക്കായ" in rows[2].translation or rows[2].lost_terms


@needs_engine
def test_repeated_strings_are_translated_once() -> None:
    """Order is preserved even though duplicates share one model call."""
    rows = translate.translate_rows(["Total", "Total", "Unit"], [])
    assert rows[0].translation == rows[1].translation
    assert len(rows) == 3


# --- review warnings ------------------------------------------------------


def test_untranslated_english_is_flagged() -> None:
    row = translate.Row(source="Black pepper", translation="Black pepper")
    assert row.needs_attention
    assert "Still in English" in row.warnings[0]


def test_dropped_word_is_flagged() -> None:
    """Real measured omissions: "500 g jar" came back as just "500 ഗ്രാം"."""
    for source, target in [
        ("500 g jar", "500 ഗ്രാം"),
        ("Total amount payable", "മൊത്തം തുക"),
    ]:
        row = translate.Row(source=source, translation=target)
        assert row.needs_attention, f"{source} -> {target} should be flagged"
        assert "shorter" in row.warnings[0]


def test_good_translation_is_not_flagged() -> None:
    row = translate.Row(
        source="Price per kilogram", translation="ഒരു കിലോഗ്രാം വില"
    )
    assert row.needs_attention is False
    assert row.warnings == []


def test_single_words_are_not_judged_by_length() -> None:
    """One word cannot be too short — the length check must not fire on it."""
    row = translate.Row(source="Product", translation="നിർമ്മാണം")
    assert not any("shorter" in note for note in row.warnings)


def test_a_single_word_cell_is_answered_rather_than_queried() -> None:
    """NEXT.md 1.6 asked for a warning here. ADR-032 supplies an answer instead.

    "Product" really did come back as നിർമ്മാണം — "manufacturing" — with no
    signal at all, and a word-count heuristic structurally cannot fire on one
    word. The old fix was to raise a *check* saying nothing could vouch for it
    and asking the operator to add a glossary entry. On a price list of nothing
    but one-word product names that fired on almost every row.

    The library knows the word, so the cell never reaches the model at all.
    """
    entry = dictionary.lookup("Product")
    assert entry is not None
    assert entry.primary != "നിർമ്മാണം"

    row = translate.Row(
        source="Product", translation=entry.primary, from_dictionary=True
    )
    assert row.warnings == []
    assert row.needs_attention is False


def test_the_grid_no_longer_asks_the_operator_to_teach_it_a_word() -> None:
    """The nag is gone even for a cell the library has never heard of.

    A short cell the model guessed at is still not vouched for by anything —
    that much is unchanged and true. What changed is that saying so on every
    such row bought nothing and cost the operator's attention, which is the one
    thing the review grid needs.
    """
    row = translate.Row(source="Zyzzyva", translation="സിസീവ")
    assert not any("glossary entry" in note for note in row.warnings)
    assert not any("approved term" in note for note in row.warnings)


def test_glossary_rows_are_never_flagged_for_length() -> None:
    """Exact by construction — a short glossary term is not a dropped word."""
    row = translate.Row(
        source="Extra virgin coconut oil",
        translation="വെളിച്ചെണ്ണ",
        glossary_only=True,
    )
    assert row.needs_attention is False


def test_word_count_handles_malayalam_conjuncts() -> None:
    """`\\w+` splits at the virama and inflated the count — see _word_count."""
    assert translate._word_count("500 ഗ്രാം") == 2
    assert translate._word_count("മൂന്ന് ദിവസത്തിനുള്ളിൽ വിടുതൽ") == 3


# --- golden review-grid flags ---------------------------------------------
#
# NEXT.md 1.6 and 4.2. The measured failure was not that the model is weak —
# that is known and documented — but that the grid reported these rows as
# `needs_attention: false`. These pin the detectors against the real output.

GOLDEN_FLAGS = Path(__file__).parent / "golden" / "translate_flags.tsv"


def _flag_rows() -> list[tuple[str, str, str, str]]:
    rows: list[tuple[str, str, str, str]] = []
    for line in GOLDEN_FLAGS.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        assert len(parts) >= 3, f"malformed golden row: {line!r}"
        note = parts[3] if len(parts) > 3 else ""
        assert parts[2] in {
            "problem",
            "check",
            "clean",
            "library",
            "composed",
            "gap",
        }, parts[2]
        rows.append((parts[0], parts[1], parts[2], note))
    return rows


FLAG_ROWS = _flag_rows()


def test_flag_fixture_covers_the_measured_failures() -> None:
    assert len(FLAG_ROWS) >= 12, f"only {len(FLAG_ROWS)} measured rows"
    assert any(r[2] == "clean" for r in FLAG_ROWS), "no negative cases"


@pytest.mark.parametrize(
    ("source", "output", "expected", "note"),
    FLAG_ROWS,
    ids=[f"{r[0][:18]}-{r[2]}" for r in FLAG_ROWS],
)
def test_golden_review_flags(source: str, output: str, expected: str, note: str) -> None:
    """Every measured mistranslation must be dealt with, in the right way.

    "Dealt with" is no longer a synonym for "flagged". Since ADR-032 the better
    outcome for most of these is that the bad output in column two can no longer
    be produced at all, so those rows assert the word library, not the warning.
    """
    row = translate.Row(source=source, translation=output)

    if expected == "library":
        entry = dictionary.lookup(source)
        assert entry is not None, f"{note}: the word library must answer this cell"
        assert entry.primary != output, f"{note}: the library repeats the bad output"
        # Exact by construction, so it must also be immune to the heuristics.
        answered = translate.Row(
            source=source, translation=entry.primary, from_dictionary=True
        )
        assert answered.warnings == [], f"{note}: flagged its own correct answer"
    elif expected == "composed":
        built = translate.compose(source, dictionary.phrase_terms())
        assert built is not None, f"{note}: the trade terms do not cover this cell"
        assert built != output, f"{note}: composition repeats the bad output"
        # No English may survive, and no placeholder wreckage either — the two
        # ways a half-built cell used to reach the client's spreadsheet.
        assert not re.search(r"[A-Za-z]", built.replace("x", ""))
        assert "X" not in built
        composed = translate.Row(
            source=source, translation=built, from_dictionary=True
        )
        assert composed.warnings == [], f"{note}: flagged its own correct answer"
    elif expected == "gap":
        # Written down deliberately. Nothing here catches it, and pretending
        # otherwise with an assertion that passes for the wrong reason is worse
        # than an accepted gap that is named.
        assert dictionary.lookup(source) is None, (
            f"{note}: the library covers this now — reclassify the golden row"
        )
        assert row.warnings == [], f"{note}: something fires after all — reclassify"
    elif expected == "problem":
        assert row.problems, f"{note}: not flagged as a problem"
        assert row.must_fix is True
    elif expected == "check":
        assert row.checks, f"{note}: nothing surfaced for a cell nothing can verify"
        assert row.needs_attention is True
    else:
        assert row.warnings == [], f"{note}: false alarm — {row.warnings}"
        assert row.needs_attention is False


def test_unit_change_is_a_problem_not_a_check() -> None:
    """feet -> metre on a price list is a quotation for the wrong banner."""
    row = translate.Row(source="6x4 feet", translation="6x4 മീറ്റ")
    assert any("unit changed" in note.lower() for note in row.problems)


def test_a_dropped_number_is_flagged() -> None:
    """Digits are prices and sizes; they must survive exactly."""
    row = translate.Row(source="Banner 6x4 at 250 each", translation="ബാനർ 6x4")
    assert any("250" in note for note in row.problems)


def test_a_cell_of_trade_terms_is_built_not_translated() -> None:
    """`300 gsm matte` -> "300 gsm mathematics" has no structural signal at all.

    It is now prevented rather than flagged: every word in the cell is a trade
    term, so the cell is composed from them and never reaches the model.
    """
    built = translate.compose("300 gsm matte", dictionary.phrase_terms())
    assert built == "300 ജിഎസ്എം മാറ്റ്"


def test_a_composed_cell_never_carries_placeholder_debris() -> None:
    """The regression that made composition necessary, kept as a test.

    Trade terms were first handled by masking, the way glossary terms are.
    Measured on a real price list on 2026-09-05, that wrote marker wreckage
    straight into the output: `Flex banner` masked to `X1X X0X` — a cell that
    was *entirely* markers — and came back as ഫ്ലക്സ് 0X ബാനർ. `6x4 feet`
    came back as `6x4X അടി`, and `Total amount payable` carried എക്സ്, which is
    the placeholder's own X transliterated into Malayalam.
    """
    phrases = dictionary.phrase_terms()
    for cell in ("Flex banner", "6x4 feet", "Vinyl sticker", "Art card 300 gsm"):
        built = translate.compose(cell, phrases)
        assert built is not None, f"{cell} should be composed outright"
        assert "X" not in built, f"{cell} carries placeholder debris: {built}"
        assert "എക്സ്" not in built, f"{cell} carries a transliterated marker"
        assert "  " not in built, f"{cell} has a doubled space: {built}"


def test_composition_keeps_a_size_intact() -> None:
    """`6x4` is a quotation, not a word. The `x` must survive untouched."""
    assert translate.compose("6x4 feet", dictionary.phrase_terms()) == "6x4 അടി"


def test_a_partly_covered_cell_is_left_whole_for_the_model() -> None:
    """Refusing is the point. A half-built cell would be worse than none.

    `per` and `payable` are not trade terms, so these two go to the model
    exactly as the operator's client wrote them — not marked up, not part
    Malayalam.
    """
    phrases = dictionary.phrase_terms()
    assert translate.compose("Price per kilogram", phrases) is None
    assert translate.compose("Total amount payable", phrases) is None
    assert translate.compose("Delivery in three days", phrases) is None


def test_trade_terms_covered_by_the_glossary_are_not_flagged() -> None:
    row = translate.Row(
        source="Standee 6x4 feet",
        translation="സ്റ്റാൻഡി 6x4 അടി",
        glossary_terms=["Standee"],
    )
    assert row.warnings == []


# --- glossary debris (NEXT.md 0.3) ---------------------------------------


def test_mangled_placeholder_debris_never_reaches_the_output() -> None:
    """The exact failure that reached a client's spreadsheet.

    Source `Roll-up standee with stand, 2x6 ft`, glossary `Standee →
    സ്റ്റാൻഡി`. The 57M model exploded `X0X` into `X-px X X X`, dropping the
    digit, so `_PLACEHOLDER_RE` matched nothing and cell B6 was exported as:

        X-px X X X നിലവിലുളള റോൾ x 2x6 x സ്റ്റാൻഡി
    """
    t = terms({"Standee": "സ്റ്റാൻഡി"})
    source = "Roll-up standee with stand, 2x6 ft"
    masked = glossary.mask(source, t)
    mangled = "X-px X X X നിലവിലുളള റോൾ x 2x6 x"

    restored = glossary.restore(mangled, masked, source=source)

    assert "X-px" not in restored.text
    assert "X X X" not in restored.text
    assert restored.debris, "debris was removed but not reported"
    assert "സ്റ്റാൻഡി" in restored.text, "the approved term must still be present"


def test_the_size_in_the_cell_survives_debris_removal() -> None:
    """`2x6` is a size on a price list. Losing it is worse than the debris.

    A first version of the sweep matched case-insensitively and turned `2x6`
    into `6`.
    """
    t = terms({"Standee": "സ്റ്റാൻഡി"})
    source = "Roll-up standee with stand, 2x6 ft"
    masked = glossary.mask(source, t)
    restored = glossary.restore("X-px X X X റോൾ x 2x6 x", masked, source=source)
    assert "2x6" in restored.text


def test_a_client_part_number_shaped_like_a_placeholder_is_kept() -> None:
    """The rule that made the debris survivable is still honoured.

    An `X..X` token that *is* in the source is a client's part number and
    deleting it would be far worse than keeping a stray marker.
    """
    t = terms({"Standee": "സ്റ്റാൻഡി"})
    source = "Cable X0X standee"
    masked = glossary.mask(source, t)
    # The model returned our marker (X1X) and left the real part number alone.
    restored = glossary.restore("X1X കേബിൾ X0X", masked, source=source)
    assert "X0X" in restored.text, "a client part number was deleted"
    assert restored.debris == []


def test_the_row_says_the_term_could_not_be_placed() -> None:
    """The old grid said "much shorter than the English" — the wrong diagnosis."""
    row = translate.Row(
        source="Roll-up standee with stand, 2x6 ft",
        translation="നിലവിലുളള റോൾ x 2x6 x സ്റ്റാൻഡി",
        lost_terms=["സ്റ്റാൻഡി"],
        debris=["X-px", "X X X"],
        term_appended=True,
    )
    joined = " ".join(row.problems)
    assert "could not be placed" in joined
    assert "end of the cell" in joined, "an appended term must say it was appended"


def test_restore_without_a_source_leaves_everything_alone() -> None:
    """No source means no way to tell debris from a part number. Touch nothing."""
    masked = glossary.Masked(text="X0X", terms={0: "സ്റ്റാൻഡി"}, source="")
    restored = glossary.restore("X X X", masked, source="")
    assert restored.debris == []


# --- the corrections memory ------------------------------------------------
#
# ADR-029. These need neither a database nor a model: `translate_rows` is handed
# the memory as plain data, which is the whole reason the lookup lives there
# rather than in the router.


def _memory(*pairs: tuple[str, str]) -> dict[str, str]:
    return {textkey.normalise(source): target for source, target in pairs}


def test_a_remembered_cell_never_reaches_the_model(monkeypatch) -> None:
    """The point of the feature. If the model runs, the operator is paying
    again — in time, and in the paid check — for an answer they already gave."""
    monkeypatch.setattr(
        translate, "_run_engine", lambda *a, **k: pytest.fail("the model was called")
    )
    rows = translate.translate_rows(
        ["ELAVUNKAL VEEDU"], [], memory=_memory(("ELAVUNKAL VEEDU", "എളവുങ്കൽ വീട്"))
    )
    assert rows[0].translation == "എളവുങ്കൽ വീട്"
    assert rows[0].from_memory


def test_a_remembered_cell_is_matched_despite_case_and_spacing() -> None:
    """The same name arrives shouted one year and title-cased the next."""
    memory = _memory(("ELAVUNKAL  VEEDU,VADASERIKARA", "എളവുങ്കൽ"))
    rows = translate.translate_rows(["Elavunkal Veedu,Vadaserikara"], [], memory=memory)
    assert rows[0].from_memory
    assert rows[0].translation == "എളവുങ്കൽ"


def test_memory_beats_the_glossary_on_the_same_cell(monkeypatch) -> None:
    """A whole cell the operator approved outranks re-deriving it from phrases."""
    monkeypatch.setattr(translate, "_run_engine", lambda *a, **k: pytest.fail("no model"))
    rows = translate.translate_rows(
        ["coconut oil"],
        [("coconut oil", "വെളിച്ചെണ്ണ")],
        memory=_memory(("coconut oil", "REMEMBERED")),
    )
    assert rows[0].translation == "REMEMBERED"
    assert rows[0].from_memory and not rows[0].glossary_only


def test_a_remembered_row_is_not_flagged_as_an_unverifiable_short_cell() -> None:
    """NEXT.md 1.6 flags one-word cells because nothing can vouch for them.

    A remembered cell is the one cell on the sheet that *is* vouched for, so
    flagging it would be exactly backwards — and on a member list it would bury
    the rows that genuinely need an eye.
    """
    rows = translate.translate_rows(
        ["Standee"], [], memory=_memory(("Standee", "സ്റ്റാൻഡി"))
    )
    assert rows[0].problems == []
    assert rows[0].checks == []
    assert rows[0].warnings == []


def test_an_empty_memory_changes_nothing(monkeypatch) -> None:
    """The default path must be byte-identical to before the feature existed."""
    monkeypatch.setattr(translate, "_run_engine", lambda e, texts, *a, **k: list(texts))
    without = translate.translate_rows(["Some product"], [])
    with_empty = translate.translate_rows(["Some product"], [], memory={})
    assert without[0].translation == with_empty[0].translation
    assert not with_empty[0].from_memory


def test_a_blank_remembered_value_is_ignored(monkeypatch) -> None:
    """An empty target must not blank a cell — it falls through to the model."""
    monkeypatch.setattr(translate, "_run_engine", lambda e, texts, *a, **k: ["ഉൽപ്പന്നം"])
    rows = translate.translate_rows(["Product"], [], memory={"product": ""})
    assert not rows[0].from_memory
    assert rows[0].translation == "ഉൽപ്പന്നം"


def test_only_the_remembered_rows_skip_the_model(monkeypatch) -> None:
    """A mixed sheet is the real case: the model must still see the rest, and
    every row must come back in its original position."""
    seen: list[list[str]] = []

    def fake(engine, texts, *a, **k):
        seen.append(list(texts))
        return ["ഉൽപ്പന്നം"] * len(texts)

    monkeypatch.setattr(translate, "_run_engine", fake)
    rows = translate.translate_rows(
        ["Known", "Unknown", "Known too"],
        [],
        memory=_memory(("Known", "അറിയാം"), ("Known too", "ഇതും")),
    )
    assert seen == [["Unknown"]]
    assert [r.translation for r in rows] == ["അറിയാം", "ഉൽപ്പന്നം", "ഇതും"]
    assert [r.from_memory for r in rows] == [True, False, True]
