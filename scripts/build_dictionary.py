"""Turn the raw Olam dataset into the file the app ships.

Run once, by hand, when the dataset is refreshed. The **output is committed**;
the shop PC is offline and has no way to fetch anything at run time.

    curl -sSL -o enml.tar.gz https://olam.in/files/enml.tar.gz
    tar xzf enml.tar.gz
    uv run python scripts/build_dictionary.py files/enml

Olam ships one row per *sense*, so a headword appears many times over:

    card    {n}     തടിച്ച കടലാസ്
    card    {n}     തപാൽകാർഡ്
    card    {v}     ചീകിമിനുപ്പിക്കുക

The app needs one row per headword, so the senses are folded together: the
first is the primary and the rest become the "Other meanings" the operator sees
in the exported spreadsheet. **Olam's own order is kept.** Reordering by part of
speech was considered and rejected — it would make this script's judgement, not
the dataset's, the thing the shop prints, and the print-trade words that
actually matter are handled exactly instead in `data/dictionary/trade-en-ml.tsv`.
"""

from __future__ import annotations

import gzip
import re
import sys
from pathlib import Path

# `scripts/` is not a package; the repo root has to be on the path for the
# `backend` import below. `qc.py` does the same thing for the same reason.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import textkey  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "data" / "dictionary" / "en-ml.tsv.gz"

# ODbL requires attribution to travel with the database. It travels here, in the
# file itself, so it cannot be separated from the data by a later refactor.
HEADER = (
    "# English → Malayalam, folded one row per headword.\n"
    "# Source: Olam (https://olam.in/p/open/enml) · Open Database License (ODbL) 1.0\n"
    "# Built by scripts/build_dictionary.py — do not hand-edit.\n"
    "# Columns: english <TAB> primary <TAB> other|meanings|pipe-separated\n"
)

# More than this and the "Other meanings" column stops being readable and starts
# being a wall. The operator is choosing between senses, not studying them.
MAX_ALTERNATIVES = 6

_MALAYALAM = re.compile(r"[ഀ-ൿ]")


def fold(source: Path) -> dict[str, tuple[str, list[str]]]:
    """Collapse Olam's one-row-per-sense into one row per headword."""
    entries: dict[str, tuple[str, list[str]]] = {}
    skipped = 0

    with source.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle):
            # Olam's first line is a column header, not data.
            if line_number == 0 and line.startswith("from_content"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 3:
                skipped += 1
                continue
            english, _types, malayalam = parts[0].strip(), parts[1], parts[2].strip()

            # A row whose target has no Malayalam in it teaches nothing, and a
            # row that maps a word to itself would replace English with English.
            if not english or not _MALAYALAM.search(malayalam):
                skipped += 1
                continue

            key = textkey.normalise(english)
            if not key:
                skipped += 1
                continue

            existing = entries.get(key)
            if existing is None:
                entries[key] = (malayalam, [])
            elif malayalam != existing[0] and malayalam not in existing[1]:
                existing[1].append(malayalam)

    print(f"{len(entries):,} headwords · {skipped:,} rows skipped")
    return entries


def write(entries: dict[str, tuple[str, list[str]]], destination: Path) -> int:
    """Write the folded dictionary, gzipped. Returns the byte size."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(destination, "wt", encoding="utf-8", newline="\n") as out:
        out.write(HEADER)
        for key in sorted(entries):
            primary, alternatives = entries[key]
            others = "|".join(alternatives[:MAX_ALTERNATIVES])
            out.write(f"{key}\t{primary}\t{others}\n")
    return destination.stat().st_size


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    source = Path(argv[1])
    if not source.is_file():
        print(f"No such file: {source}")
        return 1
    size = write(fold(source), OUTPUT)
    print(f"Wrote {OUTPUT.relative_to(ROOT)} — {size / 1_048_576:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
