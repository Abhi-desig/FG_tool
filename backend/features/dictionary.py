"""The offline word library: English → Malayalam, bundled, free, on the machine.

**Why this exists.** The translation engine is 57M parameters and it fails
*confidently* on exactly the cells a print shop's spreadsheets are made of —
one- and two-word product names. Measured and recorded in `translate.py`:
``Standee`` came back as the Malayalam for "Saint Kitts and Nevis",
``300 gsm matte`` as "300 gsm mathematics". Nothing structural can see either
error. The only fix is to know the word.

**Two layers, and the small one wins.**

``en-ml.tsv.gz``
    59,027 headwords folded from the Olam dataset (ODbL 1.0 — see LICENSES.md).
    Broad, free, and wrong about this trade: it renders ``flex`` as മടക്കുക,
    "to fold".

``trade-en-ml.tsv``
    The shop's own vocabulary, hand-checked, ~90 lines, plain text so it can be
    read and corrected without a tool. Overrides Olam wherever the two disagree,
    and supplies the words Olam has never heard of at all.

A third layer — the operator's own overrides — lives in the database and is
merged on top by the router. This module never touches storage: it may not
import ``db``, and a feature module may not import another feature module, so
everything here is plain data that a caller assembles.

**What this is not.** It is not a translator. A dictionary holds root words; it
cannot inflect a Malayalam sentence, so it answers a cell only when the cell
*is* a headword, and otherwise merely locks its terms away from the model. That
boundary is ADR-032, and it is the difference between a claim of consistency and
a false claim of accuracy.
"""

from __future__ import annotations

import gzip
import logging
from dataclasses import dataclass, field

from backend import config, textkey

log = logging.getLogger(__name__)

BUNDLED = config.DATA_DIR / "dictionary" / "en-ml.tsv.gz"
TRADE = config.DATA_DIR / "dictionary" / "trade-en-ml.tsv"


@dataclass(frozen=True)
class Entry:
    """One headword and every sense the shop might want for it."""

    source: str
    primary: str
    alternatives: list[str] = field(default_factory=list)
    # True for the hand-checked trade overlay. Two things turn on it: these are
    # the only terms masked into a sentence (see `phrase_terms`), and the Excel
    # export marks them so the operator knows which rows were curated here and
    # which came from a general dictionary.
    trade: bool = False


# Loaded once and kept. ~59k small strings is a few MB — nothing like a model,
# so CLAUDE.md's "one model at a time, free it after use" does not apply here.
_CACHE: dict[str, Entry] | None = None


def _parse(line: str) -> tuple[str, Entry] | None:
    """One `english <TAB> primary <TAB> a|b|c` line, or None if it is not one."""
    if not line.strip() or line.lstrip().startswith("#"):
        return None
    parts = line.rstrip("\n").split("\t")
    if len(parts) < 2:
        return None
    source, primary = parts[0].strip(), parts[1].strip()
    if not source or not primary:
        return None
    others = parts[2].strip() if len(parts) > 2 else ""
    alternatives = [alt.strip() for alt in others.split("|") if alt.strip()]
    return textkey.normalise(source), Entry(source, primary, alternatives)


def load() -> dict[str, Entry]:
    """The bundled dictionary, keyed by `textkey.normalise`.

    Keyed the same way the corrections memory is, deliberately: if the two ever
    normalised differently, a cell the operator had already approved would stop
    matching and quietly get re-translated.

    A missing file is a warning, not a crash. The shop must still be able to
    translate — worse, but working — if the data directory is incomplete.
    """
    global _CACHE
    if _CACHE is not None:
        return _CACHE

    entries: dict[str, Entry] = {}

    if BUNDLED.is_file():
        with gzip.open(BUNDLED, "rt", encoding="utf-8") as handle:
            for line in handle:
                parsed = _parse(line)
                if parsed:
                    entries[parsed[0]] = parsed[1]
    else:
        log.warning("No bundled dictionary at %s — falling back to the model", BUNDLED)

    # Applied second, so it wins. This ordering is the whole point of the file.
    if TRADE.is_file():
        for line in TRADE.read_text(encoding="utf-8").splitlines():
            parsed = _parse(line)
            if parsed:
                key, entry = parsed
                entries[key] = Entry(
                    entry.source, entry.primary, entry.alternatives, trade=True
                )
    else:
        log.warning("No trade overlay at %s — print terms will be guessed", TRADE)

    _CACHE = entries
    return entries


def reset_cache() -> None:
    """Drop the loaded dictionary. For tests that point at different files."""
    global _CACHE
    _CACHE = None


# A general dictionary explains words; a price list labels them. Olam does both,
# and the difference is invisible until it prints: `Product` comes back as
# ഫാക്ടറിയിൽ നിർമ്മിച്ച വസ്തു — "an item manufactured in a factory". True, and
# useless as a column header.
#
# A definition is recognisable by shape: it is far longer than the word it
# defines. Four words is the line. Below it sit the compounds a real translation
# needs (`coconut oil` → വെളിച്ചെണ്ണ, one word); above it sit explanations.
# Entries over the line are still shown in the exported spreadsheet, where the
# operator can read them and pick — they are just never used as the answer.
_DEFINITION_WORDS = 4


def _is_definition(source: str, primary: str) -> bool:
    """Whether this reads as an explanation rather than a translation."""
    return len(primary.split()) > max(_DEFINITION_WORDS, len(source.split()) * 2)


def lookup(text: str) -> Entry | None:
    """The entry for a whole cell that can stand as its translation, or None.

    Whole cell only. Looking words up inside a sentence and substituting them
    would emit uninflected roots in a row — readable as a word list, wrong as
    Malayalam, and it would print that way.

    Returns None for a definitional entry, so the cell falls through to the
    model rather than printing a dictionary's explanation of itself. The
    hand-checked trade overlay is exempt: it says what the shop says.
    """
    if not text or not textkey.has_latin(text):
        return None
    entry = load().get(textkey.normalise(text))
    if entry is None or entry.trade:
        return entry
    return None if _is_definition(text, entry.primary) else entry


def answers() -> dict[str, str]:
    """Every entry that can stand as a whole-cell translation, keyed for lookup.

    The bulk form of `lookup`, and it must stay in step with it: the router
    builds the translator's map from here, and if the two disagreed a cell would
    be quoted as free and then translated anyway, or worse, the reverse.
    """
    return {
        key: entry.primary
        for key, entry in load().items()
        if entry.trade or not _is_definition(entry.source, entry.primary)
    }


def phrase_terms() -> list[tuple[str, str]]:
    """Terms safe to lock away from the model inside a sentence.

    **Only the trade overlay**, for two reasons. Correctness: a general
    dictionary's root form dropped into the middle of a Malayalam sentence is
    wrong, whereas ``ഫ്ലക്സ്`` for ``flex`` is right wherever it lands, because
    it is a loanword the shop already says out loud. Speed: `glossary.mask` runs
    one regex per term per cell, so handing it 59,027 terms would turn a
    33,000-cell sheet into an afternoon.

    Longest-first, so ``visiting card`` wins over ``card``.
    """
    terms = [
        (entry.source, entry.primary)
        for entry in load().values()
        if entry.trade
    ]
    return sorted(terms, key=lambda pair: len(pair[0]), reverse=True)


def search(query: str, limit: int = 50, offset: int = 0) -> tuple[list[Entry], int]:
    """Entries whose English contains `query`. Returns the page and the total.

    Exact matches first, then by length, so searching "card" puts ``card`` above
    ``cardiovascular surgery``.
    """
    entries = load()
    needle = textkey.normalise(query)
    if not needle:
        matched = [entries[key] for key in sorted(entries)]
    else:
        matched = sorted(
            (entry for key, entry in entries.items() if needle in key),
            key=lambda e: (textkey.normalise(e.source) != needle, len(e.source), e.source),
        )
    return matched[offset : offset + limit], len(matched)


def count() -> int:
    """How many headwords are bundled, for the Excel tab's panel."""
    return len(load())


__all__ = [
    "BUNDLED",
    "TRADE",
    "Entry",
    "answers",
    "count",
    "load",
    "lookup",
    "phrase_terms",
    "reset_cache",
    "search",
]
