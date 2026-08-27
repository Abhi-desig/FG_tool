"""Golden tests for Malayalam ⇄ ML-TTKarthika conversion.

A green run here proves the encoding is self-consistent. It does NOT prove the
glyphs are right — only pasting into CorelDRAW does that, which is why the
Phase 1 exit gate in ROADMAP.md requires both.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest

from backend.features.fonts import (
    PRE_BASE,
    MapError,
    ascii_to_unicode,
    load_map,
    map_report,
    unconvertible,
    unicode_to_ascii,
)

GOLDEN = Path(__file__).parent / "golden" / "malayalam_pairs.tsv"

# Letters where we knowingly differ from payyans, each pinned by its own test.
KNOWN_DIVERGENCE = frozenset({"ൌ"})  # see test_au_sign_splits_around_the_consonant


def _pairs() -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for line in GOLDEN.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        note = parts[2] if len(parts) > 2 else ""
        rows.append((parts[0], parts[1], note))
    return rows


PAIRS = _pairs()


def test_golden_corpus_is_big_enough() -> None:
    """ROADMAP.md Phase 1 gate: at least 25 golden strings."""
    assert len(PAIRS) >= 25, f"only {len(PAIRS)} pairs; the gate needs 25"


@pytest.mark.parametrize(("uni", "ascii_", "note"), PAIRS, ids=[p[0] for p in PAIRS])
def test_unicode_to_ascii(uni: str, ascii_: str, note: str) -> None:
    assert unicode_to_ascii(uni) == ascii_, note


@pytest.mark.parametrize(("uni", "ascii_", "note"), PAIRS, ids=[p[0] for p in PAIRS])
def test_round_trip(uni: str, ascii_: str, note: str) -> None:
    """ASCII must carry back to the same Unicode — no information lost."""
    assert ascii_to_unicode(ascii_) == unicodedata.normalize("NFC", uni), note


# --- the specific defects that motivated writing our own converter ---------


def test_rejects_glyph_outside_the_font_encoding() -> None:
    """`∂` (U+2202) has no byte in cp1252, so it cannot be a glyph.

    The map lists it as a second code for ന്ന. Selecting it — as a last-wins
    policy does — produces broken output for a very common conjunct.
    """
    assert unicode_to_ascii("ന്ന") == "¶"
    warnings = map_report()["warnings"]
    assert any("∂" in w for w in warnings), "the bad entry should be reported"


def test_prefers_visible_code_over_invisible_one() -> None:
    """ണ്ട lists a soft hyphen before `ï`. An invisible code will not survive
    a clipboard round-trip into CorelDRAW."""
    got = unicode_to_ascii("ണ്ട")
    assert got == "ï"
    assert unicodedata.category(got) != "Cf"


def test_longest_match_beats_piecewise() -> None:
    """സ്റ്റ is a single 5-codepoint ligature, not സ + ് + റ്റ."""
    assert unicode_to_ascii("സ്റ്റ") == "Ì"


# --- WhatsApp input reality -----------------------------------------------


def test_chillu_written_with_zwj() -> None:
    """WhatsApp often sends ന + virama + ZWJ rather than the atomic ൻ."""
    assert unicode_to_ascii("ഞാ" + "ന" + "്" + "‍") == unicode_to_ascii("ഞാൻ")


def test_zero_width_joiners_are_dropped() -> None:
    assert unicode_to_ascii("കേരളം‌") == "tIcfw"


def test_decomposed_vowel_is_normalised() -> None:
    """ോ may arrive as േ + ാ; NFC composes it before lookup."""
    assert unicode_to_ascii("ക" + "േ" + "ാ") == unicode_to_ascii("കോ")


# --- passthrough ----------------------------------------------------------


@pytest.mark.parametrize("text", ["", "Kerala 2026", "+91 9847 000 000", "A-1, M.G. Road"])
def test_non_malayalam_passes_through(text: str) -> None:
    assert unicode_to_ascii(text) == text


def test_mixed_script_keeps_latin_intact() -> None:
    assert unicode_to_ascii("കേരളം 2026") == "tIcfw 2026"


# --- map integrity --------------------------------------------------------


def test_pre_base_table_agrees_with_the_map() -> None:
    """load_map() validates this, so a bad map raises rather than misconverts."""
    u2a, _, _ = load_map()
    for sign, (pre, post) in PRE_BASE.items():
        assert u2a[sign] == pre + post


def test_every_ascii_code_fits_the_font() -> None:
    u2a, _, _ = load_map()
    for code in u2a.values():
        code.encode("cp1252")  # raises UnicodeEncodeError if unrepresentable


def test_missing_map_raises_cleanly() -> None:
    with pytest.raises((MapError, FileNotFoundError)):
        load_map("NoSuchFont")


# --- cross-check against payyans -----------------------------------------


def _letters_with_two_codes() -> set[str]:
    """Letters the map gives more than one ASCII code.

    These are the only places we intentionally diverge from payyans: it takes
    the last code, we take the first visible one (ADR-004). Derived from the map
    rather than hardcoded so this stays true if the map is updated.
    """
    seen: dict[str, str] = {}
    dupes: set[str] = set()
    for line in (
        Path(__file__).parents[1] / "data" / "maps" / "ML-TTKarthika.map"
    ).read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#") or "=" not in line:
            continue
        code, letter = line.split("=", 1)
        if letter in seen and seen[letter] != code:
            dupes.add(letter)
        seen.setdefault(letter, code)
    return dupes


def test_matches_payyans_wherever_duplicates_do_not_apply() -> None:
    """payyans is the battle-tested reference for the reordering rules.

    Dev-only — it is not a runtime dependency (ADR-004). The invariant: our
    reordering is identical to payyans everywhere its duplicate-code policy
    does not come into play. Divergence anywhere else is a bug in our logic.
    """
    payyans = pytest.importorskip("libindic.payyans", reason="payyans not installed")
    ref = payyans.Payyans()
    skip = _letters_with_two_codes() | KNOWN_DIVERGENCE

    compared = 0
    for uni, _ascii, note in PAIRS:
        if any(letter in uni for letter in skip):
            continue
        assert unicode_to_ascii(uni) == ref.Unicode2ASCII(uni, "ML-TTKarthika"), note
        compared += 1

    assert compared >= 20, f"only {compared} pairs cross-checked; the test is too weak"


def test_au_sign_splits_around_the_consonant() -> None:
    """The documented divergence from payyans, pinned so it stays deliberate.

    The map defines ``su=ൌ``, a two-part sign exactly parallel to ``sm=ൊ`` and
    ``tm=ോ``, so ``കൌ`` is ``s`` + ``I`` + ``u``. payyans emits only ``Iu``,
    which decodes back as ``കൗ`` — a different letter. We follow the map.

    ൌ is archaic (modern Malayalam uses ൗ), so the practical risk is low either
    way. Listed for the CorelDRAW check in ROADMAP.md.
    """
    assert unicode_to_ascii("കൌ") == "sIu"
    assert ascii_to_unicode("sIu") == "കൌ"
    # The modern spelling is unaffected and stays post-base.
    assert unicode_to_ascii("കൗ") == "Iu"


# --- what will not survive the print font (NEXT.md 3.4) ------------------
#
# WhatsApp is the primary input to this screen, so emoji arrive constantly. They
# passed straight through unflagged, and the helper text — "This looks like
# gibberish here — that is correct" — trained the operator to ignore exactly
# this. In CorelDRAW a 🎉 lands as a box, on a client's poster.


def test_emoji_are_flagged() -> None:
    found = unconvertible("ഓണം ആശംസകൾ 🎉🎉")
    assert len(found) == 1
    assert found[0]["character"] == "🎉"
    assert found[0]["count"] == 2, "a message with two of the same emoji is one problem"
    assert found[0]["codepoint"] == "U+1F389"
    # Named, so the operator can find it in a long message.
    assert "POPPER" in str(found[0]["name"])


def test_ordinary_shop_text_is_not_flagged() -> None:
    """No false alarms: Latin, digits, punctuation and ₹ all pass through fine."""
    assert unconvertible("ഓണം ആശംസകൾ — Focus Digitals, ₹500 (50% off) 9847012345") == []


def test_pure_malayalam_is_not_flagged() -> None:
    assert unconvertible("കേരളം ഗ്രാൻഡ് സെയിൽ") == []


def test_another_script_is_flagged() -> None:
    """A Hindi or Tamil word pasted in by mistake is the same class of problem."""
    found = unconvertible("ഓണം नमस्ते")
    assert found, "text the print font cannot represent was passed through silently"


def test_an_arrow_is_flagged() -> None:
    """Not only emoji. Any symbol the 8-bit font has no glyph for."""
    assert [f["character"] for f in unconvertible("ഓണം → വിഷു")] == ["→"]


def test_empty_text_is_not_a_problem() -> None:
    assert unconvertible("") == []


def test_the_flag_does_not_change_the_conversion() -> None:
    """Reporting only. Silently dropping a character would be worse."""
    text = "ഓണം 🎉"
    assert "🎉" in unicode_to_ascii(text)
