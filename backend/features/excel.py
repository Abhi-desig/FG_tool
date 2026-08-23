"""Reading and writing client spreadsheets without disturbing them.

**Formatting survives because we never rebuild the file.** openpyxl loads the
workbook, we assign `cell.value`, and everything else — fonts, fills, column
widths, merges, number formats, conditional rules — is carried through
untouched. Rebuilding a sheet from extracted values is what loses a client's
formatting, so we don't.

**The source file is never modified.** Extraction reads bytes; applying writes a
*new* workbook. Nothing lands on the original until the operator has accepted the
translations, which is a Phase 3 exit-gate requirement.

**Not everything is translatable.** Formulas, numbers, dates and booleans are
left exactly as they are; so is any string with no Latin letters, because it has
either already been translated or was never English.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

# A cell worth sending to the translator has at least one Latin letter. This
# skips numbers, codes, dates, and text already in Malayalam.
_HAS_LATIN = re.compile(r"[A-Za-z]")

MAX_CELL_CHARS = 5000


def is_formula(value: object) -> bool:
    """True if this cell holds a formula.

    openpyxl represents a formula as a plain `str` starting with "=", so an
    `isinstance(value, str)` check is *not* enough to decide a cell is safe to
    overwrite. Extraction and writing both go through here so they cannot drift
    apart — overwriting a client's `=C2*D2` with Malayalam would silently break
    their totals.
    """
    return isinstance(value, str) and value.startswith("=")


class ExcelError(ValueError):
    """The workbook is unreadable or not a spreadsheet."""


@dataclass
class Cell:
    """One translatable cell, addressed so it can be written back exactly."""

    sheet: str
    row: int
    column: int
    source: str

    @property
    def ref(self) -> str:
        return f"{get_column_letter(self.column)}{self.row}"

    @property
    def key(self) -> str:
        return f"{self.sheet}!{self.ref}"


@dataclass
class Extraction:
    cells: list[Cell] = field(default_factory=list)
    sheets: list[str] = field(default_factory=list)
    # Cells deliberately left alone, so the operator can see nothing was missed.
    skipped_formulas: int = 0
    skipped_non_text: int = 0
    total_cells: int = 0


def _open(data: bytes):
    """Load a workbook, keeping formulas as formulas."""
    try:
        # data_only=False keeps formulas; rich_text=False keeps values simple.
        return load_workbook(io.BytesIO(data), data_only=False)
    except Exception as exc:  # noqa: BLE001 - openpyxl raises several types
        raise ExcelError(
            "That file could not be read as a spreadsheet. Save it as .xlsx and try again."
        ) from exc


def extract(data: bytes) -> Extraction:
    """Find every cell worth translating, without changing anything."""
    workbook = _open(data)
    result = Extraction(sheets=list(workbook.sheetnames))

    for name in workbook.sheetnames:
        sheet: Worksheet = workbook[name]
        for row in sheet.iter_rows():
            for cell in row:
                value = cell.value
                if value is None:
                    continue
                result.total_cells += 1

                if is_formula(value):
                    result.skipped_formulas += 1
                    continue
                if isinstance(value, (int, float, bool, datetime, date, time)):
                    result.skipped_non_text += 1
                    continue
                if not isinstance(value, str):
                    result.skipped_non_text += 1
                    continue

                text = value.strip()
                if not text or not _HAS_LATIN.search(text):
                    result.skipped_non_text += 1
                    continue

                result.cells.append(
                    Cell(
                        sheet=name,
                        row=cell.row,
                        column=cell.column,
                        source=text[:MAX_CELL_CHARS],
                    )
                )

    workbook.close()
    return result


def apply(data: bytes, translations: dict[str, str]) -> bytes:
    """Write translations into a copy of the workbook.

    `translations` is keyed by `Cell.key` ("Sheet1!B4"). Anything not present is
    left alone, so a partially-reviewed sheet is a valid thing to export.
    """
    workbook = _open(data)

    for key, text in translations.items():
        if not text:
            continue
        sheet_name, _, ref = key.rpartition("!")
        if not sheet_name or sheet_name not in workbook.sheetnames:
            continue
        cell = workbook[sheet_name][ref]
        # Only ever replace plain text. A formula or a number is the client's
        # data and outranks any translation — see `is_formula` for why the
        # isinstance check alone is not sufficient.
        if is_formula(cell.value) or not (
            cell.value is None or isinstance(cell.value, str)
        ):
            continue
        cell.value = text

    buffer = io.BytesIO()
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()


def unique_sources(cells: list[Cell]) -> list[str]:
    """Distinct source strings, order preserved.

    A catalogue repeats itself heavily — the same header or unit appears in
    hundreds of rows. Translating each distinct string once is the difference
    between a minute and an hour on the shop PC.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for cell in cells:
        if cell.source not in seen:
            seen.add(cell.source)
            ordered.append(cell.source)
    return ordered
