"""Writing names in Malayalam script by sound.

The cases here are real. `verify.py` records what a *translation* model did with
a co-operative bank's member list, and those exact strings are the fixture: a
house name came back as "Eucalyptus", and one row came back with a fabricated
citation attached. Anything this module produces for them is better, but it
still has to produce correctly-shaped Malayalam rather than merely different
nonsense — so these tests check the orthography, not just that something changed.
"""

from __future__ import annotations

import re

from backend.features import translit

MALAYALAM = re.compile(r"[ഀ-ൿ]")


# --- the measured failures ------------------------------------------------


def test_the_house_names_that_became_eucalyptus() -> None:
    """The rows `verify.py` was built for, done offline and for nothing."""
    assert translit.line("ELAVUNKAL VEEDU") == "എലവുങ്കൽ വീട്"
    assert translit.line("THOPPIL VEEDU") == "തൊപ്പിൽ വീട്"


def test_an_address_keeps_its_shape() -> None:
    """Punctuation and spacing carry meaning in an address, so they survive."""
    written = translit.line("THOPPIL VEEDU,UTHIMOODU P.O,")
    assert written == "തൊപ്പിൽ വീട്,ഉതിമൂദു പി.ഒ,"
    assert written.count(",") == 2


# --- orthography ----------------------------------------------------------


def test_a_word_ending_in_a_consonant_takes_its_chillu() -> None:
    """`Thoppil` ends in ൽ. A chandrakkala there is how a transliteration
    announces that a machine wrote it."""
    assert translit.word("Thoppil").endswith("ൽ")
    assert translit.word("Sunil").endswith("ൽ")
    assert translit.word("Kumar").endswith("ർ")
    assert translit.word("Krishnan").endswith("ൻ")


def test_a_word_ending_in_m_takes_the_anusvara() -> None:
    """Malayalam is full of these — Kottayam, Ernakulam, Malappuram."""
    assert translit.word("Kottayam") == "കൊട്ടയം"
    assert translit.word("Malappuram").endswith("ം")
    assert not translit.word("Malappuram").endswith("മ്")


def test_n_before_a_velar_is_the_velar_nasal() -> None:
    """Elavu**nk**al is എലവു**ങ്ക**ൽ, never ...ന്ക..."""
    assert "ങ്ക" in translit.word("Elavunkal")


def test_a_chillu_is_used_mid_word_too() -> None:
    """Ernakulam is എർ..., not എര്..."""
    assert translit.word("Ernakulam").startswith("എർ")


def test_an_initial_is_read_as_a_letter_not_spelled_out() -> None:
    """A member list is full of `K.M. Nair`. Spelling `P` phonetically gives
    പ്, which is not how anyone writes an initial."""
    assert translit.line("K.M. Nair") == "കെ.എം. നൈർ"
    assert translit.word("P") == "പി"


def test_a_vowel_opens_a_word_in_its_independent_form() -> None:
    assert translit.word("Ammu").startswith("അ")
    assert translit.word("Uthimoodu").startswith("ഉ")


def test_a_doubled_consonant_stays_doubled() -> None:
    assert "പ്പ" in translit.word("Thoppil")
    assert "മ്മ" in translit.word("Ammu")


def test_common_address_words_use_their_real_spelling() -> None:
    """`veedu` needs a retroflex that the letter `d` does not carry."""
    assert translit.word("Veedu") == "വീട്"
    assert translit.word("VEEDU") == "വീട്"


# --- what it must not do --------------------------------------------------


def test_everything_that_is_not_a_latin_word_is_left_alone() -> None:
    assert translit.line("42") == "42"
    assert translit.line("Ward 7, House 12/A") == "വർദ് 7, ഹൗസ് 12/എ"
    # Already Malayalam. Running this twice must not mangle it.
    assert translit.line("വീട്") == "വീട്"


def test_the_output_is_actually_malayalam() -> None:
    for name in ("Anil Kumar", "Radha Menon", "Alphonsa", "Vadaserikara"):
        written = translit.line(name)
        assert MALAYALAM.search(written), name
        assert not re.search(r"[A-Za-z]", written), f"{name} kept Latin: {written}"


def test_empty_input_is_empty_output() -> None:
    assert translit.word("") == ""
    assert translit.line("") == ""


def test_it_is_stable() -> None:
    """The same name must be written the same way every time it appears on a
    sheet — a member list repeats a house name across a family."""
    assert translit.line("Elavunkal Veedu") == translit.line("Elavunkal Veedu")
    assert translit.line("ELAVUNKAL VEEDU") == translit.line("elavunkal veedu")
