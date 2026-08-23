"""Translation tests.

Model-backed cases are skipped when the weights are not downloaded, so the suite
stays runnable on a fresh checkout. What is always tested: routing between the
glossary path and the model path, and the flags the review grid depends on.
"""

from __future__ import annotations

import pytest

from backend import models
from backend.features import glossary, translate

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
    """One word cannot be too short, so no false alarm on headers."""
    assert translate.Row(source="Product", translation="നിർമ്മാണം").warnings == []


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
