"""How a source cell is turned into a lookup key, in one place.

Two modules need the identical answer and may not reach each other. `db.py`
writes `corrections.source_norm` when a correction is learned; `features/
translate.py` normalises the next sheet's cells to look it up. If those two ever
disagreed by a character the corrections memory would silently stop matching —
it would not raise, it would just quietly translate a cell the operator had
already approved. CLAUDE.md permits a shared helper for exactly this, and
`excel.is_formula` is the existing precedent: "Extraction and writing both go
through here so they cannot drift apart."

Deliberately dependency-free, so both a feature module and the database layer
can import it without either importing the other.
"""

from __future__ import annotations

import re

# A cell worth sending to the translator has at least one Latin letter. This
# skips numbers, codes, dates, and text already in Malayalam.
_LATIN = re.compile(r"[A-Za-z]")


def normalise(text: str) -> str:
    """The form a correction is matched on.

    Whitespace is collapsed and case is folded because Indian office
    spreadsheets deliver the same name as `ELAVUNKAL  VEEDU` one year and
    `Elavunkal Veedu` the next — a memory that missed on that would fail at
    precisely the moment it was supposed to help.

    The cost is that `Bill` (a name) and `bill` (an invoice) collapse into one
    entry. Acceptable for a whole cell, and visible and reversible in Settings.
    """
    return " ".join(text.split()).casefold()


def has_latin(text: str) -> bool:
    """Whether this cell is English enough to be worth translating at all."""
    return bool(_LATIN.search(text))
