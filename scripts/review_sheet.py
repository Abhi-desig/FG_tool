"""Every bundled Malayalam term, as one spreadsheet for a reader to check.

**Why this exists.** The word library, the name lexicon and the address assets
contain Malayalam that was written during development by someone who does not
read the language. It prints on client work. ADR-032 and ADR-035 both say so,
and this is how that debt gets paid down: one pass, English beside Malayalam,
with a column to correct in.

    uv run python scripts/review_sheet.py

Writes `MALAYALAM_REVIEW.xlsx` in the repo root — gitignored, because it is
generated from the assets and the assets are the truth. Corrections go back into
the `.tsv` files, not into this sheet.
"""

from __future__ import annotations

import pathlib

from openpyxl import Workbook
from openpyxl.styles import Font

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "MALAYALAM_REVIEW.xlsx"

# Every asset holding hand-written Malayalam, and what to call its group so a
# reader can take them a section at a time rather than all at once.
SOURCES: tuple[tuple[str, str], ...] = (
    ("data/dictionary/trade-en-ml.tsv", "print trade and office words"),
    ("data/names/exceptions.tsv", "people's names"),
    ("data/places/gazetteer.tsv", "places"),
    ("data/places/structural.tsv", "parts of an address"),
)

# The shop's own Malayalam font, so the column is readable in Excel rather than
# a row of boxes.
MALAYALAM_FONT = "Nirmala UI"


def rows() -> list[tuple[str, str, str, str]]:
    """(file, group, english, malayalam) for every term, in file order."""
    out: list[tuple[str, str, str, str]] = []
    for relative, group in SOURCES:
        path = ROOT / relative
        if not path.is_file():
            print(f"  missing: {relative}")
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 2 or not parts[0].strip() or not parts[1].strip():
                continue
            out.append((path.name, group, parts[0].strip(), parts[1].strip()))
    return out


def main() -> int:
    terms = rows()
    if not terms:
        print("No terms found — are the data files there?")
        return 1

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Review"
    sheet.append(
        ["File", "Group", "English", "Malayalam (as written)", "Correct it here", "OK?"]
    )
    for cell in sheet[1]:
        cell.font = Font(bold=True)

    for index, (name, group, english, malayalam) in enumerate(terms):
        sheet.append([name, group, english, malayalam, "", ""])
        # Columns D and E hold Malayalam: the current value and the correction.
        for column in (4, 5):
            sheet.cell(row=index + 2, column=column).font = Font(name=MALAYALAM_FONT)

    sheet.freeze_panes = "A2"
    for column, width in (
        ("A", 22), ("B", 28), ("C", 26), ("D", 30), ("E", 30), ("F", 8)
    ):
        sheet.column_dimensions[column].width = width

    workbook.save(OUTPUT)
    workbook.close()

    by_group: dict[str, int] = {}
    for _name, group, _english, _malayalam in terms:
        by_group[group] = by_group.get(group, 0) + 1
    print(f"{len(terms)} terms -> {OUTPUT.relative_to(ROOT)}")
    for group, count in by_group.items():
        print(f"  {count:4}  {group}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
