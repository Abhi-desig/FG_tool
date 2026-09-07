"""The column classifier, and the routes it picks.

Every class here exists because a measured failure could not be fixed any other
way. `Vishnu Prasad` → വിഷ്ണുപുരാണം (the Vishnu Purana) is not a translation
error a better model would avoid: a name has no meaning to find, and a model
whose job is to find meaning will invent one. So the test that matters most is
that a name column is never classified as text.
"""

from __future__ import annotations

import pytest

from backend.features import columns, translit

NAMES = translit.known_names()
PLACES = translit.known_places()


def classify(header: str, values: list[str], words: frozenset[str] = frozenset()) -> str:
    cls, _why, _profile = columns.classify(
        header, values, len(set(values)), NAMES, PLACES, words
    )
    return cls


# --- the header decides, when it says anything ----------------------------


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("Name", "PERSON_NAME"),
        ("Guardian Name", "PERSON_NAME"),
        ("Father's Name", "PERSON_NAME"),
        ("House Name", "ADDRESS"),
        ("Address", "ADDRESS"),
        ("District", "ADDRESS"),
        ("Sl No", "CODE"),
        ("Phone Number", "CODE"),
        ("Account No", "CODE"),
        ("Date of Joining", "NUMERIC_DATE"),
        ("Salary (INR)", "NUMERIC_DATE"),
    ],
)
def test_a_heading_that_names_its_own_kind_is_believed(header: str, expected: str) -> None:
    """The heading is the operator's own label for what the column holds, which
    is exactly the information the values cannot supply — on a member list every
    column is unknown words in title case, occupations included."""
    assert classify(header, ["Anything", "At all"]) == expected


def test_a_product_column_is_never_taken_for_names() -> None:
    """Spelling a real word by sound is exactly as wrong as translating a name,
    so the cost of guessing runs both ways.

    Checked with an *empty* vocabulary on purpose. The first version of this
    passed only because the word library happened to know `Standee`; with the
    library unavailable the short-and-unknown rule claimed the column for
    PERSON_NAME and would have spelled the shop's product list out by sound.
    The heading now settles it either way.
    """
    assert classify("Product", ["Standee", "Flex banner", "Visiting card"]) != "PERSON_NAME"
    assert classify("Description", ["Zyzzyva", "Qwerty", "Asdf"]) == "FREE_TEXT"


def test_every_class_comes_with_a_reason() -> None:
    for header in ("Name", "Address", "Sl No", "Department", "Notes"):
        _cls, why, _ = columns.classify(header, ["a", "b"], 2, NAMES, PLACES, frozenset())
        assert why and why[0].islower() or why, f"{header} has no reason"


# --- categorical: the rule the spec's ratio could not express -------------


def test_a_short_repeated_vocabulary_is_categorical() -> None:
    """Department on a real sheet: ten values across thirty cells.

    The brief proposed `distinct/total < 0.05`. That can never fire here — the
    ratio is 0.33, and on any sheet under ~200 rows the floor is 1/n. Two
    absolute conditions replace it.
    """
    values = ["Sales", "Finance", "IT", "Marketing", "Legal"] * 6
    assert classify("Department", values) == "CATEGORICAL"


def test_a_column_of_distinct_values_is_not_categorical() -> None:
    """Designation is 29 distinct in 30 cells — a vocabulary, not a category."""
    values = [f"Officer {i}" for i in range(30)]
    assert classify("Designation", values) != "CATEGORICAL"


def test_the_ratio_the_brief_proposed_would_have_missed_it() -> None:
    """Kept as a test so the reasoning cannot be quietly reverted."""
    values = ["Sales", "Finance", "IT", "Marketing", "Legal"] * 6
    ratio = len(set(values)) / len(values)
    assert ratio > 0.05, "the 0.05 rule would not have fired"
    assert len(set(values)) <= columns.CATEGORICAL_MAX_DISTINCT
    assert len(set(values)) < len(values) * columns.CATEGORICAL_MAX_SHARE


# --- the values decide, when the header is silent -------------------------


def test_a_column_of_numbers_is_a_code_whatever_it_is_called() -> None:
    assert classify("Ref", ["9847012301", "9846023412", "9895034523"]) == "CODE"
    assert classify("", ["EMP-014", "EMP-015", "EMP-016"]) == "CODE"


def test_known_names_in_the_values_are_enough() -> None:
    assert classify("Signatory", ["Anjali Menon", "Rahul Nair", "Sneha Pillai"]) == (
        "PERSON_NAME"
    )


def test_known_places_in_the_values_are_enough() -> None:
    assert classify("Origin", ["Ernakulam", "Kottayam", "Thrissur"]) == "ADDRESS"


def test_ordinary_wording_is_free_text() -> None:
    words = frozenset({"delivery", "within", "three", "days", "please", "note", "the"})
    values = [
        "Delivery within three days please",
        "Please note the delivery date",
        "Three days from the order date",
    ]
    assert classify("Notes", values, words) == "FREE_TEXT"


def test_an_empty_column_is_free_text_rather_than_a_guess() -> None:
    assert classify("", []) == "FREE_TEXT"


# --- the routes -----------------------------------------------------------


def test_a_person_name_is_looked_up_not_derived() -> None:
    written, unknown = translit.person("Anjali Menon")
    assert written == "അഞ്ജലി മേനോൻ"
    assert unknown == [], "both tokens are in the lexicon"


def test_a_name_the_lexicon_does_not_hold_is_named_as_such() -> None:
    """Still written by sound — that is the right operation — but reported, so
    the caller can ask the operator to read the spelling once."""
    _written, unknown = translit.person("Zyzzyva Qwerty")
    assert unknown == ["Zyzzyva", "Qwerty"]


def test_initials_survive_exactly() -> None:
    written, unknown = translit.person("K.M. Nair")
    assert written == "കെ.എം. നായർ"
    assert unknown == [], "an initial is rendered exactly, not guessed"


def test_an_address_is_decomposed_gazetteer_first() -> None:
    written, _ = translit.address("Near Aluva Junction, Ernakulam Dist")
    # Gazetteer beats structure beats sound.
    assert "ആലുവ" in written and "എറണാകുളം" in written
    assert "ജംഗ്ഷൻ" in written and "ജില്ല" in written


def test_numbers_inside_an_address_are_untouched() -> None:
    written, _ = translit.address("Thoppil Veedu, Uthimoodu PO, 683101")
    assert "683101" in written, "a PIN code must survive byte-identical"


def test_the_assets_are_shipped() -> None:
    assert translit.EXCEPTIONS_PATH.is_file()
    assert translit.GAZETTEER_PATH.is_file()
    assert translit.STRUCTURAL_PATH.is_file()
    assert len(translit.known_names()) > 40
    assert len(translit.known_places()) > 40


def test_a_missing_asset_degrades_rather_than_crashes(monkeypatch, tmp_path) -> None:
    """The shop must still translate a sheet with an incomplete data folder."""
    monkeypatch.setattr(translit, "EXCEPTIONS_PATH", tmp_path / "nope.tsv")
    monkeypatch.setattr(translit, "GAZETTEER_PATH", tmp_path / "nope2.tsv")
    monkeypatch.setattr(translit, "STRUCTURAL_PATH", tmp_path / "nope3.tsv")
    translit.reset_cache()
    try:
        assert translit.known_names() == frozenset()
        # Rules alone still produce Malayalam, and every token is reported.
        written, unknown = translit.person("Anjali Menon")
        assert written and unknown == ["Anjali", "Menon"]
    finally:
        translit.reset_cache()


# --- corrections go where they compound (ADR-035) -------------------------


def test_a_corrected_name_is_written_to_the_lexicon(tmp_path, monkeypatch) -> None:
    """And to a *file*, not the database. These assets are plain text so the
    operator can read and copy them; a correction that lived only in `data.db`
    would be lost the first time they moved machines."""
    lexicon = tmp_path / "exceptions.tsv"
    lexicon.write_text("# header\nmenon\tമേനോൻ\n", encoding="utf-8")
    monkeypatch.setattr(translit, "EXCEPTIONS_PATH", lexicon)
    translit.reset_cache()
    try:
        assert translit.remember_name("Zyzzyva", "സിസീവ") is True
        # Visible immediately: the next sheet must not need a restart.
        written, unknown = translit.person("Zyzzyva Menon")
        assert written == "സിസീവ മേനോൻ"
        assert unknown == []
    finally:
        translit.reset_cache()


def test_writing_the_same_correction_twice_changes_nothing(tmp_path, monkeypatch) -> None:
    lexicon = tmp_path / "exceptions.tsv"
    lexicon.write_text("# header\n", encoding="utf-8")
    monkeypatch.setattr(translit, "EXCEPTIONS_PATH", lexicon)
    translit.reset_cache()
    try:
        assert translit.remember_name("Zyzzyva", "സിസീവ") is True
        assert translit.remember_name("Zyzzyva", "സിസീവ") is False
        assert lexicon.read_text(encoding="utf-8").count("zyzzyva") == 1
    finally:
        translit.reset_cache()


def test_a_corrected_place_goes_to_the_gazetteer(tmp_path, monkeypatch) -> None:
    """One gazetteer row fixes every address that place appears in, which a
    whole-cell correction in the flat memory cannot do."""
    gazetteer = tmp_path / "gazetteer.tsv"
    gazetteer.write_text("# header\n", encoding="utf-8")
    monkeypatch.setattr(translit, "GAZETTEER_PATH", gazetteer)
    translit.reset_cache()
    try:
        assert translit.remember_place("Kadavil", "കടവിൽ") is True
        # Both addresses, from one correction.
        assert "കടവിൽ" in translit.address("Kadavil House")[0]
        assert "കടവിൽ" in translit.address("Kadavil Veedu, Aluva")[0]
    finally:
        translit.reset_cache()


def test_a_correction_cannot_corrupt_the_tab_separated_file(tmp_path, monkeypatch) -> None:
    """The files are tab-separated, so a stray tab would shift every column.

    The two sides are handled differently and deliberately. A tab in the
    *source* is whitespace and gets normalised away with the rest of it, exactly
    as `textkey.normalise` treats a double space. A tab in the *Malayalam* is
    not whitespace we may quietly rewrite — it is the operator's own text — so
    the write is refused instead.
    """
    lexicon = tmp_path / "exceptions.tsv"
    lexicon.write_text("# header\n", encoding="utf-8")
    monkeypatch.setattr(translit, "EXCEPTIONS_PATH", lexicon)
    translit.reset_cache()
    try:
        assert translit.remember_name("Bad\tName", "x") is True
        assert "bad name\tx" in lexicon.read_text(encoding="utf-8")

        assert translit.remember_name("Name", "bad\tvalue") is False
        assert translit.remember_name("", "x") is False
        # Whatever happened, every data row still has exactly two fields.
        for line in lexicon.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#"):
                assert len(line.split("\t")) == 2, line
    finally:
        translit.reset_cache()


# --- the round-trip check, and why it is not a flag ----------------------


def test_the_round_trip_reading_never_raises_a_flag_on_its_own() -> None:
    """Pinned, because the obvious thing to do with it is wrong.

    Measured against twelve cells whose correctness was already established, a
    similarity threshold on the back-translation caught **0 of 3 real errors**
    and flagged **1 of 9 correct cells**. The signal is inverted and
    structurally so: a wrong translation is usually near the source in meaning
    (`legal advisor` → "legal auditor" reads back close to the original), while
    a correct translation of an idiom can round-trip through a second 57M model
    into nonsense.
    """
    from backend.features.translate import Row

    # A correct translation whose reading came back as garbage. Must stay quiet.
    correct = Row(
        source="Price per kilogram",
        translation="ഒരു കിലോഗ്രാം വില",
        back_translation="The Friday's Eve — Ancient of Israel",
        divergence=0.05,
    )
    assert correct.warnings == [], "a bad reading of a good cell must not flag"

    # A wrong translation whose reading came back close. The other signals may
    # catch it; the reading must not be what decides.
    wrong = Row(
        source="Legal Advisor",
        translation="നിയമപരമായ ആഡിറ്റർ",
        back_translation="The legalator",
        divergence=0.05,
    )
    assert not any("Read back" in note for note in wrong.warnings)


def test_the_reading_is_off_unless_asked_for() -> None:
    """A second model load and a second pass over every distinct string is real
    time on a 33,000-cell sheet, for information that raises no flag."""
    import inspect

    from backend.features import translate

    signature = inspect.signature(translate.translate_rows)
    assert signature.parameters["read_back"].default is False
