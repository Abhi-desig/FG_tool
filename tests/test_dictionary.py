"""The bundled word library, and the precedence it sits inside.

The tests that matter here are the precedence ones. The library is the third of
four layers and the only one that is not the operator's own work, so every way
it could quietly overwrite their answer is a way the shop prints the wrong thing.
"""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from backend import textkey
from backend.features import dictionary, translate


def test_the_bundled_files_are_actually_shipped() -> None:
    """A clone with no dictionary would degrade silently, which is the point."""
    assert dictionary.BUNDLED.is_file(), "the Olam file is missing from data/"
    assert dictionary.TRADE.is_file(), "the trade overlay is missing from data/"
    assert dictionary.count() > 50_000


def test_attribution_travels_with_the_data() -> None:
    """ODbL requires it, and a licence note in a doc can be separated from the
    file it applies to. This one cannot."""
    with gzip.open(dictionary.BUNDLED, "rt", encoding="utf-8") as handle:
        header = "".join(handle.readline() for _ in range(4))
    assert "Olam" in header
    assert "ODbL" in header


def test_a_whole_cell_is_answered() -> None:
    entry = dictionary.lookup("Coconut oil")
    assert entry is not None
    assert entry.primary == "വെളിച്ചെണ്ണ"


def test_lookup_is_case_and_space_insensitive() -> None:
    """Keyed through `textkey.normalise`, the same as the corrections memory.

    If these two ever normalised differently, a cell the operator had already
    approved would stop matching and be quietly re-translated.
    """
    assert dictionary.lookup("  COCONUT   OIL ") == dictionary.lookup("coconut oil")


def test_the_trade_overlay_beats_olam() -> None:
    """Olam says `flex` means "to fold". On a price list that is a wrong quote."""
    entry = dictionary.lookup("flex")
    assert entry is not None
    assert entry.trade is True
    assert entry.primary == "ഫ്ലക്സ്"


def test_words_olam_has_never_heard_of_are_supplied() -> None:
    for word in ("standee", "matte", "visiting card", "vinyl", "sunboard"):
        entry = dictionary.lookup(word)
        assert entry is not None, f"{word} is missing from the trade overlay"
        assert entry.trade is True


def test_a_definition_is_not_offered_as_a_translation() -> None:
    """Olam explains as well as translates, and the two are indistinguishable
    until they print. `Product` came back as "an item manufactured in a
    factory" — true, and useless as a column header."""
    long_gloss = dictionary.Entry("gizmo", "ഒരു ചെറിയ യന്ത്ര ഉപകരണം ആയ വസ്തു ആണ്")
    assert dictionary._is_definition(long_gloss.source, long_gloss.primary)
    # And a real compound translation is not mistaken for one.
    assert not dictionary._is_definition("coconut oil", "വെളിച്ചെണ്ണ")


def test_answers_and_lookup_agree() -> None:
    """The router builds the translator's map from `answers`, but the quote and
    the tests read `lookup`. A disagreement would quote a cell as free and then
    charge for it."""
    answerable = dictionary.answers()
    for word in ("coconut oil", "standee", "product", "flex"):
        key = textkey.normalise(word)
        assert (key in answerable) is (dictionary.lookup(word) is not None)


def test_a_miss_returns_nothing_rather_than_guessing() -> None:
    assert dictionary.lookup("Focus Digitals") is None
    assert dictionary.lookup("") is None
    assert dictionary.lookup("വെളിച്ചെണ്ണ") is None


def test_phrase_terms_are_only_the_curated_ones() -> None:
    """Masking 59,000 general words into a sentence would emit uninflected roots
    in a row, and `glossary.mask` runs one regex per term per cell."""
    terms = dictionary.phrase_terms()
    assert 0 < len(terms) < 500
    sources = {source.casefold() for source, _ in terms}
    assert "matte" in sources
    assert "coconut oil" not in sources


def test_phrase_terms_are_longest_first() -> None:
    """So `visiting card` is masked as one term rather than losing to `card`."""
    lengths = [len(source) for source, _ in dictionary.phrase_terms()]
    assert lengths == sorted(lengths, reverse=True)


def test_search_puts_the_exact_match_first() -> None:
    rows, total = dictionary.search("card", limit=5)
    assert total > 1
    assert rows[0].source.casefold() == "card"


def test_search_pages_without_repeating() -> None:
    first, total = dictionary.search("card", limit=3, offset=0)
    second, _ = dictionary.search("card", limit=3, offset=3)
    assert total >= 6
    assert {e.source for e in first}.isdisjoint({e.source for e in second})


# --- precedence -----------------------------------------------------------
#
# memory > glossary > dictionary > model. Each layer outranks the next because
# it is more specifically the operator's own answer.


def test_the_corrections_memory_beats_the_dictionary() -> None:
    rows = translate.translate_rows(
        ["Coconut oil"],
        terms=[],
        memory={textkey.normalise("Coconut oil"): "തേങ്ങാ എണ്ണ"},
        dictionary=dictionary.answers(),
    )
    assert rows[0].translation == "തേങ്ങാ എണ്ണ"
    assert rows[0].from_memory is True
    assert rows[0].from_dictionary is False


def test_the_client_glossary_beats_the_dictionary() -> None:
    rows = translate.translate_rows(
        ["Coconut oil"],
        terms=[("Coconut oil", "നാളികേര എണ്ണ")],
        dictionary=dictionary.answers(),
    )
    assert rows[0].translation == "നാളികേര എണ്ണ"
    assert rows[0].glossary_only is True
    assert rows[0].from_dictionary is False


def test_a_partly_glossed_cell_is_not_hijacked_by_the_dictionary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The subtle one, and the reason the dictionary check is conditional.

    The glossary matched *inside* this cell without covering it. If the whole
    cell were then answered from the dictionary, the client's approved word for
    "oil" would be silently replaced by a general dictionary's compound — the
    one thing a locked term exists to prevent. It must go to the model instead,
    with that term masked.
    """
    monkeypatch.setattr(
        translate, "_run_engine", lambda *args, **kwargs: ["X0X എണ്ണമയമുള്ള"]
    )
    rows = translate.translate_rows(
        ["Coconut oil"],
        terms=[("oil", "എണ്ണ")],
        dictionary={textkey.normalise("Coconut oil"): "വെളിച്ചെണ്ണ"},
    )
    assert rows[0].from_dictionary is False
    assert "വെളിച്ചെണ്ണ" not in rows[0].translation
    assert "എണ്ണ" in rows[0].translation


def test_the_dictionary_beats_the_model() -> None:
    rows = translate.translate_rows(
        ["Standee"], terms=[], dictionary=dictionary.answers()
    )
    assert rows[0].translation == "സ്റ്റാൻഡി"
    assert rows[0].from_dictionary is True
    # Exact by construction, so no heuristic may second-guess it.
    assert rows[0].warnings == []
    assert rows[0].exact is True


def test_a_dictionary_row_is_never_sent_to_the_paid_check() -> None:
    """`exact` is what the router filters on before spending money."""
    row = translate.Row(source="Standee", translation="സ്റ്റാൻഡി", from_dictionary=True)
    assert row.exact is True


def test_an_override_map_wins_over_the_bundled_answer() -> None:
    """What the router hands down after merging `dictionary_overrides`."""
    merged = dictionary.answers()
    merged[textkey.normalise("Standee")] = "സ്റ്റാൻഡീ"
    rows = translate.translate_rows(["Standee"], terms=[], dictionary=merged)
    assert rows[0].translation == "സ്റ്റാൻഡീ"


def test_a_missing_data_directory_degrades_rather_than_crashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shop must still be able to translate — worse, but working."""
    monkeypatch.setattr(dictionary, "BUNDLED", Path("/nonexistent/en-ml.tsv.gz"))
    monkeypatch.setattr(dictionary, "TRADE", Path("/nonexistent/trade.tsv"))
    dictionary.reset_cache()
    try:
        assert dictionary.load() == {}
        assert dictionary.lookup("standee") is None
    finally:
        dictionary.reset_cache()
