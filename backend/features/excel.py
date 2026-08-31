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

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from backend.textkey import has_latin

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
                if not text or not has_latin(text):
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


# --- the corrections round trip --------------------------------------------
#
# ADR-029. The operator's own tool for a list of names is Excel, so the
# corrections memory is loaded and inspected as a two-column sheet rather than
# only one row at a time in a browser table.

# Header words that mean row 1 is a header, not data. Anything else and row 1 is
# treated as a pair, so a bare two-column sheet with no header works.
_HEADER_WORDS = {"english", "source", "english term", "source term", "term"}

_MALAYALAM = re.compile(r"[ഀ-ൿ]")


@dataclass
class PairSheet:
    """What a corrections .xlsx contained."""

    pairs: list[tuple[str, str]] = field(default_factory=list)
    # Rows with a blank side, counted rather than dropped silently.
    skipped: int = 0
    truncated: bool = False
    # Most of column A is Malayalam and most of column B is not: the operator
    # has handed us the sheet backwards.
    looks_swapped: bool = False


def _cell_text(value: object) -> str:
    """One cell as text, whatever openpyxl decided it was.

    A membership code like `19` arrives as an int and a date as a datetime;
    both are legitimate halves of a correction and neither is a str.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (datetime, date, time)):
        return str(value).strip()
    if isinstance(value, float) and value.is_integer():
        # openpyxl reads every number as a float; `19.0` is not a code anybody
        # typed, and writing it back would not match the cell it came from.
        return str(int(value))
    return str(value).strip()


def read_pairs(data: bytes, limit: int = 50_000) -> PairSheet:
    """Read English → Malayalam pairs from the first sheet's columns A and B.

    Only the first worksheet: a corrections file is a list, and guessing which
    of several sheets was meant is the kind of helpfulness that silently loads
    the wrong one.
    """
    workbook = _open(data)
    sheet = workbook[workbook.sheetnames[0]]

    result = PairSheet()
    english_malayalam = 0
    malayalam_english = 0

    for index, row in enumerate(sheet.iter_rows(min_col=1, max_col=2, values_only=True)):
        if len(result.pairs) >= limit:
            result.truncated = True
            break
        source = _cell_text(row[0] if len(row) > 0 else None)
        target = _cell_text(row[1] if len(row) > 1 else None)
        if index == 0 and source.casefold() in _HEADER_WORDS:
            continue
        if not source or not target:
            if source or target:
                result.skipped += 1
            continue
        # Which way round the sheet is, decided by counting rather than by the
        # first row — a header slipped through, or one stray English cell in the
        # Malayalam column, must not flip the verdict.
        if _MALAYALAM.search(source) and not _MALAYALAM.search(target):
            malayalam_english += 1
        elif _MALAYALAM.search(target) or has_latin(source):
            english_malayalam += 1
        result.pairs.append((source[:MAX_CELL_CHARS], target[:MAX_CELL_CHARS]))

    workbook.close()

    # Refused rather than loaded, because a reversed memory does not sit there
    # looking wrong — it silently fills cells with English on the next sheet.
    result.looks_swapped = malayalam_english > english_malayalam and malayalam_english > 0
    return result


def write_pairs(
    pairs: list[tuple[str, str]], scope: str, learned: list[str] | None = None
) -> bytes:
    """The memory as a spreadsheet the operator can edit and load back.

    Scope is written for the operator to read and is deliberately ignored on
    import — it comes from the form field instead, so a round trip cannot
    flatten a client's corrections into the shop-wide ones by accident.
    """
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Corrections"
    sheet.append(["English", "Malayalam", "Scope", "Learned"])
    for cell in sheet[1]:
        cell.font = Font(bold=True)

    learned = learned or []
    for index, (source, target) in enumerate(pairs):
        sheet.append([source, target, scope, learned[index] if index < len(learned) else ""])
        # The shop's own Malayalam font, so the column is readable in Excel
        # rather than a row of boxes.
        sheet.cell(row=index + 2, column=2).font = Font(name="Nirmala UI")

    sheet.freeze_panes = "A2"
    for column, width in (("A", 46), ("B", 46), ("C", 18), ("D", 16)):
        sheet.column_dimensions[column].width = width

    buffer = io.BytesIO()
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()
