"""Phase 3 routes: clients, glossary, translate, export.

The shape enforces the exit gate: **nothing is written to a file until the
operator accepts.** `POST /translate` returns rows to review and never touches
the workbook; `POST /export` takes the reviewed rows — edits included — and only
then produces a new file.
"""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from openpyxl.utils import get_column_letter
from pydantic import BaseModel, Field, model_validator

from backend import config, db, jobs, textkey
from backend.features import (
    columns,
    dictionary,
    excel,
    glossary,
    translate,
    translit,
    verify,
)

router = APIRouter(prefix="/api", tags=["excel"])

# A run of letters, for telling a label apart from a number in a numbers column.
_LATIN_WORDS = re.compile(r"[A-Za-z]{2,}")

# A ceiling on the review grid, not on the translator: the model works through
# *unique* strings in batches, so a long sheet costs time, not memory. Member
# lists of 30k+ cells are real work the shop takes, so the ceiling is set above
# them rather than turning them away.
MAX_ROWS = 100_000

# A glossary this long is a data-entry accident, not a client's terminology.
MAX_TERMS = 2_000


def _read(upload: UploadFile) -> bytes:
    data = upload.file.read(config.MAX_UPLOAD_BYTES + 1)
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "That spreadsheet is too large.")
    if not data:
        raise HTTPException(400, "The uploaded file was empty.")
    return data


# --- clients ---------------------------------------------------------------


class ClientIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class TermIn(BaseModel):
    source_term: str = Field(min_length=1, max_length=300)
    target_term: str = Field(min_length=1, max_length=300)
    notes: str = ""


# --- envelope convention --------------------------------------------------
#
# Every other route in this app answers with a wrapped object. These five
# returned bare lists, and `PUT /glossary` took one (NEXT.md 3.12) — which also
# meant there was nowhere to add a field without a breaking change.
#
# Both shapes are served: the response is wrapped, and the request accepts a bare
# list as well as a wrapped one. The shipped `frontend/dist` on the shop PC may
# be older than this server, and a settings screen that stops working is not a
# fair price for tidiness.


@router.get("/clients")
def list_clients(include_archived: bool = False) -> dict[str, object]:
    rows = db.list_clients(include_archived)
    return {"clients": rows, "count": len(rows)}


@router.post("/clients")
def add_client(body: ClientIn) -> dict[str, object]:
    try:
        return db.add_client(body.name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/clients/{client_id}/archive")
def archive_client(client_id: int, archived: bool = True) -> dict[str, object]:
    try:
        return db.archive_client(client_id, archived)
    except db.NotFound as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/clients/{client_id}/glossary")
def get_glossary(client_id: int) -> dict[str, object]:
    try:
        rows = db.get_glossary(client_id)
    except db.NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    return _glossary_envelope(client_id, rows)


class GlossaryIn(BaseModel):
    """A glossary write. Accepts `{"terms": [...]}` or a bare `[...]`."""

    terms: list[TermIn] = Field(default_factory=list, max_length=MAX_TERMS)

    @model_validator(mode="before")
    @classmethod
    def _accept_a_bare_list(cls, data: object) -> object:
        if isinstance(data, list):
            return {"terms": data}
        return data


@router.put("/clients/{client_id}/glossary")
def put_glossary(client_id: int, body: GlossaryIn) -> dict[str, object]:
    """Add or correct terms. Correcting one fixes every later occurrence."""
    try:
        rows = db.upsert_terms(client_id, [t.model_dump() for t in body.terms])
    except db.NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    return _glossary_envelope(client_id, rows)


@router.delete("/clients/{client_id}/glossary/{term_id}")
def delete_term(client_id: int, term_id: int) -> dict[str, object]:
    try:
        rows = db.delete_term(client_id, term_id)
    except db.NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    return _glossary_envelope(client_id, rows)


def _glossary_envelope(
    client_id: int, rows: list[dict[str, object]]
) -> dict[str, object]:
    return {"client_id": client_id, "terms": rows, "count": len(rows)}


# --- translation -----------------------------------------------------------


def _client_context(client_id: int | None) -> tuple[list[tuple[str, str]], dict[str, str]]:
    """The glossary terms and remembered cells that apply to this sheet.

    Read here and handed down as plain data: `features/translate.py` may not
    import `db`, which is what keeps it unit-testable without a database.

    The corrections memory is consulted even with no client selected, because
    its shop-wide half applies to every sheet — a house name written by sound is
    right whoever the client is (ADR-029).
    """
    terms: list[tuple[str, str]] = []
    if client_id is not None:
        try:
            terms = glossary.compile_terms(db.get_glossary(client_id))
        except db.NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
    return terms, db.corrections_map(client_id)


def _classify(found: excel.Extraction) -> list[columns.Classified]:
    """Every column with its suggested class, ready for the operator to confirm.

    The profiling lexicons come from `features/translit.py` and
    `features/dictionary.py`. The classifier itself imports neither — it is
    handed frozensets, for the same reason every other feature module is handed
    plain data.
    """
    names = translit.known_names()
    places = translit.known_places()
    words = frozenset(dictionary.answers())

    out: list[columns.Classified] = []
    for column in excel.columns(found):
        values = [
            cell.source
            for cell in found.cells
            if cell.sheet == column.sheet
            and get_column_letter(cell.column) == column.letter
        ]
        # The heading is not one of the values it describes.
        body = values[1:] if values else []
        sample = body[: columns.SAMPLE_LIMIT]
        cls, why, profile = columns.classify(
            column.header, sample, len(set(body)), names, places, words
        )
        out.append(
            columns.Classified(
                key=f"{column.sheet}!{column.letter}",
                sheet=column.sheet,
                letter=column.letter,
                header=column.header,
                count=column.count,
                sample=column.sample,
                cls=cls,
                why=why,
                profile=profile,
            )
        )
    return out


def _parse_overrides(raw: str) -> dict[str, str]:
    """`Sheet!B=PERSON_NAME,Sheet!C=ADDRESS` from the form field.

    An unknown class name is ignored rather than raising: the operator's sheet
    is already uploaded, and refusing the whole job over one malformed pair
    would cost them the upload.
    """
    picked: dict[str, str] = {}
    for pair in raw.split(","):
        key, _, cls = pair.partition("=")
        key, cls = key.strip(), cls.strip().upper()
        if key and cls in columns.CLASSES:
            picked[key] = cls
    return picked


def _route(
    found: excel.Extraction,
    classified: list[columns.Classified],
    approved: frozenset[str] = frozenset(),
) -> tuple[dict[str, str], dict[str, translate.Resolved]]:
    """Turn column classes into a per-string class map and per-string answers.

    Everything but FREE_TEXT is answered here, before the translator is called,
    because only this layer may import `features/translit.py`. What goes down is
    the decision.

    A string appearing in two columns of different classes keeps the **stricter**
    one — the first non-FREE_TEXT class wins. Dedup is by distinct string, so one
    answer has to serve every cell holding it, and routing a name through the
    model because it also appears in a sentence column is the failure this whole
    change exists to stop.
    """
    by_column = {c.key: c.cls for c in classified}
    classes: dict[str, str] = {}
    resolved: dict[str, translate.Resolved] = {}

    for cell in found.cells:
        key = f"{cell.sheet}!{get_column_letter(cell.column)}"
        cls = by_column.get(key, "FREE_TEXT")
        existing = classes.get(cell.source)
        if existing and existing != "FREE_TEXT":
            continue
        classes[cell.source] = cls

    # The heading of a classified column is a word, not an instance of the thing
    # the column holds. `Name` must be translated; spelled by sound it read നമെ.
    headings = {c.header for c in classified if c.header}
    bodies = {
        cell.source
        for cell in found.cells
        if cell.row != min(
            other.row
            for other in found.cells
            if other.sheet == cell.sheet and other.column == cell.column
        )
    }
    for heading in headings & (headings - bodies):
        classes[heading] = "FREE_TEXT"

    # A residue token that is an ordinary English word is the signal that this
    # column is not what it was classified as — `Manager` in a name column means
    # the class is wrong, not that the lexicon is short. That is the genuinely
    # uncertain case, and the only one that refuses outright.
    vocabulary = frozenset(dictionary.answers())

    for source, cls in classes.items():
        if cls in ("PERSON_NAME", "ADDRESS"):
            text, unknown = (
                translit.person(source) if cls == "PERSON_NAME" else translit.address(source)
            )
            english = [t for t in unknown if textkey.normalise(t) in vocabulary]
            if english:
                resolved[source] = translate.Resolved(
                    text=text,
                    unresolved_reason=(
                        f"“{', '.join(english[:3])}” is an ordinary English word, "
                        f"so this column may not be "
                        f"{'names' if cls == 'PERSON_NAME' else 'addresses'} after "
                        f"all. Left in English — change the column's kind, or type "
                        f"this one by hand."
                    ),
                )
            elif unknown:
                resolved[source] = translate.Resolved(
                    text=text,
                    reading_note=(
                        f"“{', '.join(unknown[:3])}” was written by sound. Read the "
                        f"spelling once — correcting it here remembers it for good."
                    ),
                )
            else:
                resolved[source] = translate.Resolved(text=text)
        elif cls == "CATEGORICAL":
            # A short vocabulary repeated across the column. Approved once into
            # the client glossary and then correct on every future sheet — which
            # is worth far more than a machine translation of the same ten
            # words, thirty times, differently each sheet.
            #
            # An approved value is left out of `resolved` entirely so the
            # glossary path picks it up, exactly as it would any locked term.
            if textkey.normalise(source) not in approved:
                resolved[source] = translate.Resolved(
                    text="",
                    unresolved_reason=(
                        "This column repeats a short list of values. Approve them "
                        "once below and they are fixed on every sheet from now on "
                        "— they are never machine-translated."
                    ),
                )
        elif cls in ("CODE", "NUMERIC_DATE"):
            if _LATIN_WORDS.search(source):
                # A word in a numbers column is a label, not a number. Measured:
                # `Total` and `Average` sit in the salary column of a real sheet
                # and passed straight through in English, which is a wrong cell
                # the column class had made invisible. Left for the ordinary
                # routes to answer.
                classes[source] = "FREE_TEXT"
            else:
                # Untouched, and asserted untouched on export.
                resolved[source] = translate.Resolved(text=source)

    return classes, resolved


def _approval_lists(
    found: excel.Extraction,
    classified: list[columns.Classified],
    library: dict[str, str],
    approved: frozenset[str],
) -> list[dict[str, object]]:
    """Per categorical column, the distinct values and a suggested Malayalam.

    The suggestion comes from the word library only — never from the model. The
    whole point of this route is that these ten words are decided once by a
    person rather than guessed thirty times by a machine, so offering a machine
    guess as the default would give the column back to the thing it was taken
    from.
    """
    out: list[dict[str, object]] = []
    for column in classified:
        if column.cls != "CATEGORICAL":
            continue
        letter = column.letter
        values: list[str] = []
        for cell in found.cells:
            if cell.sheet != column.sheet or get_column_letter(cell.column) != letter:
                continue
            if cell.source == column.header or cell.source in values:
                continue
            values.append(cell.source)
        out.append(
            {
                "key": column.key,
                "header": column.header,
                "values": [
                    {
                        "source": value,
                        "suggested": library.get(textkey.normalise(value), ""),
                        "approved": textkey.normalise(value) in approved,
                    }
                    for value in sorted(values, key=str.casefold)
                ],
            }
        )
    return out


def _library() -> tuple[dict[str, str], list[tuple[str, str]]]:
    """The word library as the translator wants it: whole-cell map, plus phrases.

    Assembled here for the same reason `_client_context` is: `features/
    translate.py` may not import `db`, and it may not import another feature
    module either, so the router is the one place allowed to hold both halves.
    The operator's overrides go on last, so they win over both Olam and the
    trade overlay (ADR-032).
    """
    overrides = db.dictionary_overrides()
    entries = dictionary.answers()
    entries.update(overrides)
    phrases = [
        (source, overrides.get(textkey.normalise(source), target))
        for source, target in dictionary.phrase_terms()
    ]
    return entries, phrases


@router.post("/excel/inspect")
def inspect(
    file: Annotated[UploadFile, File()],
    client_id: Annotated[int | None, Form()] = None,
) -> dict[str, object]:
    """What is in this sheet, what would be translated, and what that costs.

    `client_id` is what makes the quote true. Without it the estimate counted
    every unique string, including the ones the glossary and the corrections
    memory cover entirely and that therefore never reach the model — an
    over-quote of roughly a third on a member list, on the one number the
    operator uses to decide whether to spend.
    """
    try:
        found = excel.extract(_read(file))
    except excel.ExcelError as exc:
        raise HTTPException(400, str(exc)) from exc

    unique = excel.unique_sources(found.cells)
    terms, memory = _client_context(client_id)
    library, _phrases = _library()
    classified = _classify(found)
    approved = frozenset(textkey.normalise(t[0]) for t in terms)
    classes, resolved = _route(found, classified, approved)

    from_memory = [s for s in unique if textkey.normalise(s) in memory]
    remembered = set(from_memory)
    from_glossary = [
        s for s in unique if s not in remembered and glossary.is_fully_covered(s, terms)
    ]
    covered = remembered | set(from_glossary)
    # Must mirror the precedence in `translate.translate_rows` exactly, or the
    # quote promises a saving the translation does not make. A cell the glossary
    # touched without covering still goes to the model, so it is not free here
    # either.
    from_dictionary = [
        s
        for s in unique
        if s not in covered
        and textkey.normalise(s) in library
        and not glossary.mask(s, terms).terms
    ]
    covered |= set(from_dictionary)
    # Names, addresses, codes and dates never reach the model, so they are not
    # quotable work either.
    covered |= {s for s in unique if s in resolved}
    quotable = [s for s in unique if s not in covered]

    return {
        "sheets": found.sheets,
        "total_cells": found.total_cells,
        "translatable": len(found.cells),
        "unique_strings": len(unique),
        "skipped_formulas": found.skipped_formulas,
        "skipped_non_text": found.skipped_non_text,
        # Already approved, so free and offline however the operator proceeds.
        "from_memory": len(from_memory),
        "from_glossary": len(from_glossary),
        "from_dictionary": len(from_dictionary),
        "to_translate": len(quotable),
        # Every column with text in it, its suggested class, and why. A
        # suggestion the operator confirms or overrides before pressing
        # Translate — guessing wrong is symmetrical, so this never decides for
        # them (ADR-035).
        "columns": [c.as_dict() for c in classified],
        "classes": list(columns.CLASSES),
        # How much of the sheet each route would take. The operator is deciding
        # whether the classes look right, and these are the numbers that say so.
        "routed": {
            cls: sum(1 for source in unique if classes.get(source) == cls)
            for cls in columns.CLASSES
        },
        "unresolved": sum(
            1 for answer in resolved.values() if answer.unresolved_reason
        ),
        # The values each categorical column repeats, with a suggestion from the
        # word library where there is one. Approved once, they become locked
        # glossary terms and this column is never machine-translated again.
        "categorical": _approval_lists(found, classified, library, approved),
        "engine": translate.available_engine(),
        # What the optional Claude check would cost on *this* sheet. Quoted here
        # rather than on a route of its own so the operator is not asked to
        # upload the same 33,000-row file twice to find out.
        "verify": verify.estimate(verify.checkable(quotable)),
    }


class ApprovalIn(BaseModel):
    """Confirmed values for one categorical column."""

    client_id: int
    pairs: list[TermIn] = Field(default_factory=list, max_length=MAX_TERMS)


@router.post("/excel/categorical/approve")
def approve_categorical(body: ApprovalIn) -> dict[str, object]:
    """Lock a categorical column's vocabulary into the client glossary.

    Deliberately the *same* store as any other locked term rather than a new
    one. An approved category value and a locked brand name are the same kind of
    thing — the operator's decision about wording — and a second table holding
    half of them would be a second place to look when one of them is wrong.
    """
    if not body.pairs:
        raise HTTPException(400, "Nothing to approve.")
    try:
        rows = db.upsert_terms(
            body.client_id, [t.model_dump() for t in body.pairs]
        )
    except db.NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    return _glossary_envelope(body.client_id, rows)


@router.post("/excel/translate")
def start_translate(
    file: Annotated[UploadFile, File()],
    client_id: Annotated[int | None, Form()] = None,
    engine: Annotated[str | None, Form()] = None,
    check_with_claude: Annotated[bool, Form()] = False,
    over_budget_ok: Annotated[bool, Form()] = False,
    # `Sheet!B=PERSON_NAME,Sheet!C=ADDRESS`. Absent means take the suggestions.
    column_classes: Annotated[str, Form()] = "",
    # Off by default: a second model and a second pass over every distinct
    # string, for a reading that raises no flag. See the note in translate.py.
    read_back: Annotated[bool, Form()] = False,
) -> dict[str, object]:
    """Translate a sheet and return a job whose result is the review grid.

    The workbook is *not* modified. The operator reviews, edits, then exports.

    `check_with_claude` adds a paid pass over the offline model's output — the
    only part of this feature that leaves the machine or costs money, and off
    unless asked for. See `features/verify.py` for why a sheet of names needs it.
    """
    data = _read(file)
    try:
        found = excel.extract(data)
    except excel.ExcelError as exc:
        raise HTTPException(400, str(exc)) from exc

    if not found.cells:
        raise HTTPException(400, "Nothing in that sheet needs translating.")
    if len(found.cells) > MAX_ROWS:
        raise HTTPException(
            413, f"That sheet has {len(found.cells)} translatable cells (limit {MAX_ROWS})."
        )

    terms, memory = _client_context(client_id)
    library, phrases = _library()
    # The operator's confirmed classes, over the suggestions. A form field
    # rather than JSON because the file has to come with it.
    classified = _classify(found)
    overrides = _parse_overrides(column_classes)
    if overrides:
        classified = [
            columns.Classified(
                key=c.key,
                sheet=c.sheet,
                letter=c.letter,
                header=c.header,
                count=c.count,
                sample=c.sample,
                cls=overrides.get(c.key, c.cls),
                why="chosen by you" if c.key in overrides else c.why,
                profile=c.profile,
            )
            for c in classified
        ]
    approved = frozenset(textkey.normalise(t[0]) for t in terms)
    classes, resolved = _route(found, classified, approved)

    unique = excel.unique_sources(found.cells)
    name = file.filename or "sheet.xlsx"

    def work(report: jobs.Reporter) -> dict[str, object]:
        report.step("Reading sheet…", 0.05)
        # The offline pass keeps the whole bar when nothing follows it, and gives
        # up the back half when the paid check does.
        rows = translate.translate_rows(
            unique,
            terms,
            engine,
            report,
            progress_to=0.5 if check_with_claude else 0.95,
            memory=memory,
            dictionary=library,
            phrases=phrases,
            resolved=resolved,
            classes=classes,
            read_back=read_back,
        )
        by_source = {r.source: r for r in rows}

        checked: dict[str, verify.Verdict] = {}
        review: dict[str, object] = {"requested": check_with_claude}
        # Rows that are exact by construction are never sent. A glossary term
        # is the client's own approved wording and a remembered cell is the
        # operator's own correction; paying a model to overwrite either is both
        # a waste and a way to lose an answer that was already right.
        to_check = [r for r in rows if not r.exact]
        if check_with_claude and to_check:
            outcome = verify.check_rows(
                [(r.source, r.translation) for r in to_check],
                report,
                over_budget_ok=over_budget_ok,
                progress_from=0.5,
                progress_to=0.95,
            )
            # Zipped against `to_check`, not `rows`. Getting this wrong writes
            # one member's name into another member's row — the failure
            # `features/verify.py` rule 1 exists to prevent, and invisible on a
            # 33,000-row sheet. `strict=True` is what makes a mistake here loud.
            checked = {
                row.source: verdict
                for row, verdict in zip(to_check, outcome.verdicts, strict=True)
            }
            review = {
                "requested": True,
                "skipped_exact": len(rows) - len(to_check),
                "model": outcome.model,
                "checked": outcome.checked,
                "corrected": outcome.corrected,
                "unchecked": outcome.unchecked,
                "cost_paise": outcome.cost_paise,
                "cost_rupees": round(outcome.cost_paise / 100, 2),
                "error": outcome.error,
                "warnings": outcome.warnings,
            }
        elif check_with_claude:
            # Everything on the sheet was already approved. Nothing to buy, and
            # saying so is better than a panel reporting zero of zero checked.
            review = {
                "requested": True,
                "skipped_exact": len(rows),
                "checked": 0,
                "corrected": 0,
                "unchecked": 0,
                "cost_paise": 0,
                "cost_rupees": 0.0,
                "error": None,
                "warnings": [],
            }

        report.step("Building review grid…", 0.97)
        # Save the upload so export does not need it sent twice.
        job_dir = config.WORK_DIR / report._job.id  # noqa: SLF001
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "source.xlsx").write_bytes(data)

        grid = []
        for cell in found.cells:
            row = by_source[cell.source]
            verdict = checked.get(cell.source)
            grid.append(
                {
                    "key": cell.key,
                    "sheet": cell.sheet,
                    "ref": cell.ref,
                    "source": cell.source,
                    "translation": verdict.translation if verdict else row.translation,
                    # What the offline model said, kept even when Claude replaced
                    # it. On a sheet of names a "correction" is usually a whole
                    # new line, and the operator has to be able to see both.
                    "offline_translation": row.translation,
                    "checked": bool(verdict and verdict.checked),
                    "verify_corrected": bool(verdict and verdict.corrected),
                    "verify_note": verdict.note if verdict else "",
                    "glossary_terms": row.glossary_terms,
                    "glossary_only": row.glossary_only,
                    "from_memory": row.from_memory,
                    "from_dictionary": row.from_dictionary,
                    "from_name": row.from_name,
                    "cls": row.cls,
                    "unresolved": row.unresolved,
                    "suggestion": row.suggestion,
                    "score": row.score,
                    "divergence": row.divergence,
                    "back_translation": row.back_translation,
                    "lost_terms": row.lost_terms,
                    "warnings": row.warnings,
                    "needs_attention": row.needs_attention,
                    # Split by certainty so the grid can rank a demonstrable
                    # error above a cell that merely cannot be vouched for.
                    "problems": row.problems,
                    "checks": row.checks,
                    "must_fix": row.must_fix,
                }
            )
        return {
            "file": "source.xlsx",
            "rows": grid,
            "unique_strings": len(unique),
            "needs_attention": sum(1 for r in grid if r["needs_attention"]),
            "must_fix": sum(1 for r in grid if r["must_fix"]),
            "from_glossary": sum(1 for r in grid if r["glossary_only"]),
            "from_memory": sum(1 for r in grid if r["from_memory"]),
            "from_dictionary": sum(1 for r in grid if r["from_dictionary"]),
            "from_name": sum(1 for r in grid if r["from_name"]),
            "unresolved": sum(1 for r in grid if r["unresolved"]),
            "by_class": {
                cls: sum(1 for r in grid if r["cls"] == cls)
                for cls in columns.CLASSES
            },
            "review": review,
            "verify_corrected": sum(1 for r in grid if r["verify_corrected"]),
            # NEXT.md 1.7: translating with the glossary off is the most likely
            # operator error and it produces exactly the failures in 1.6, so the
            # grid says so rather than leaving it to be noticed.
            "glossary_applied": bool(terms),
            "glossary_terms": len(terms),
        }

    job = jobs.submit("translate", f"Translate — {name}", work)
    return job.as_dict()


class ExportRequest(BaseModel):
    job_id: str
    # key → the operator's final text, edits included
    translations: dict[str, str]
    # Whether the operator's corrections are written to the memory. Default true
    # so a `frontend/dist` older than this server still learns; the checkbox
    # beside Export is what turns it off.
    remember: bool = True
    # Which client this sheet belongs to. None means the shop-wide memory.
    client_id: int | None = None


def _learn_by_class(
    job_rows: list[dict[str, object]], final: dict[str, str], client_id: int | None
) -> dict[str, int]:
    """Send each correction to the store its class belongs in (ADR-035).

    A corrected name goes to the name lexicon, a corrected address token to the
    gazetteer, a corrected category value to the client glossary. Only ordinary
    wording goes to the flat corrections memory.

    **Why not put everything in the corrections memory.** That memory matches a
    *whole cell*. Correcting `Kadavil House` there fixes that cell and teaches
    nothing about `Kadavil Veedu` in the next row — whereas one row in the
    gazetteer fixes every address that place ever appears in. The class is
    already known, so routing the correction costs nothing and compounds.
    """
    counts = {"names": 0, "places": 0, "glossary": 0}
    glossary_pairs: list[dict[str, str]] = []

    for row in job_rows:
        key = str(row.get("key", ""))
        if key not in final:
            continue
        text = (final[key] or "").strip()
        source = str(row.get("source") or "")
        offline = str(row.get("offline_translation") or "").strip()
        if not text or not source or text == offline:
            continue

        cls = str(row.get("cls") or "FREE_TEXT")
        if cls == "PERSON_NAME":
            # One token corrected at a time only. A two-word name corrected as a
            # whole says nothing about which half was wrong, and writing the
            # pair under the first token would teach the lexicon a falsehood.
            if len(source.split()) == 1 and translit.remember_name(source, text):
                counts["names"] += 1
        elif cls == "ADDRESS":
            if len(source.split()) == 1 and translit.remember_place(source, text):
                counts["places"] += 1
        elif cls == "CATEGORICAL" and client_id is not None:
            glossary_pairs.append({"source_term": source, "target_term": text})

    if glossary_pairs:
        try:
            db.upsert_terms(client_id, glossary_pairs)  # type: ignore[arg-type]
            counts["glossary"] = len(glossary_pairs)
        except db.NotFound:
            # The client was archived between translate and export. The flat
            # memory still catches it below; losing the export would be worse.
            counts["glossary"] = 0
    return counts


def _learn(job_rows: list[dict[str, object]], final: dict[str, str]) -> list[tuple[str, str]]:
    """The pairs worth remembering from one export.

    Computed here rather than in the browser on purpose. The grid seeds its
    edit map with *every* row, so a client-side diff that went wrong would write
    33,000 unreviewed machine translations into permanent memory on one click.
    The server has the offline reading to compare against and the browser does
    not.

    The rule, per row: remember it when the final text differs from what the
    offline model produced. That covers the operator typing a correction, and it
    covers them keeping a Claude correction through to export — which is the
    only way a paid correction ever becomes permanent. Untouched machine output
    is never remembered; the review grid exists precisely to distrust it.

    A row that was already filled from memory and left alone is *not* re-saved.
    It looks harmless — the value is identical — but re-saving re-stamps it with
    this job's id, so a later `forget-job` would delete a correction the
    operator made weeks ago on a different sheet.
    """
    pairs: list[tuple[str, str]] = []
    for row in job_rows:
        key = str(row.get("key", ""))
        if key not in final:
            continue
        text = (final[key] or "").strip()
        offline = str(row.get("offline_translation") or "").strip()
        source = str(row.get("source") or "")
        if not text or not source:
            continue
        if text == offline:
            continue
        pairs.append((source, text))
    return pairs


@router.post("/excel/export")
def export(body: ExportRequest) -> FileResponse:
    """Write the accepted translations into a new workbook, and learn from them.

    This is the only point at which a file is produced — the exit-gate promise
    that nothing is written until the operator accepts. Learning is tied to that
    same moment: if the export fails, nothing is remembered.
    """
    job = jobs.registry.get(body.job_id)
    if job is None or job.status != "done" or not job.result:
        raise HTTPException(409, "That translation job is not finished.")

    source = (config.WORK_DIR / body.job_id / "source.xlsx").resolve()
    if not source.is_relative_to(config.WORK_DIR.resolve()) or not source.exists():
        raise HTTPException(404, "The uploaded sheet is no longer available.")

    # The invariant, checked rather than trusted: a CODE or NUMERIC cell is
    # passed through untouched, so if one differs here something upstream has
    # rewritten an account number or a phone number. Refuse the export — a
    # wrong digit in a client's spreadsheet is not recoverable by review.
    altered = [
        row["key"]
        for row in (job.result.get("rows") or [])
        if row.get("cls") in ("CODE", "NUMERIC_DATE")
        and body.translations.get(str(row["key"]), row["source"]) != row["source"]
    ]
    if altered:
        raise HTTPException(
            409,
            f"{len(altered)} number or code cell(s) would be changed "
            f"({', '.join(altered[:4])}). Those must pass through untouched — "
            f"nothing has been written.",
        )

    try:
        written = excel.apply(source.read_bytes(), body.translations)
    except excel.ExcelError as exc:
        raise HTTPException(400, str(exc)) from exc

    out = source.with_name("translated.xlsx")
    out.write_bytes(written)

    remembered = 0
    routed = {"names": 0, "places": 0, "glossary": 0}
    if body.remember:
        rows = job.result.get("rows") or []
        # By class first, so a corrected place lands where it compounds. What is
        # left over is ordinary wording, and that goes to the flat memory.
        routed = _learn_by_class(rows, body.translations, body.client_id)
        pairs = [
            pair
            for pair in _learn(rows, body.translations)
            if not any(
                str(r.get("source")) == pair[0]
                and str(r.get("cls") or "FREE_TEXT")
                in ("PERSON_NAME", "ADDRESS", "CATEGORICAL")
                for r in rows
            )
        ]
        if pairs:
            try:
                counts = db.upsert_corrections(
                    body.client_id, pairs, learned_from=body.job_id
                )
                remembered = counts["added"] + counts["updated"]
            except db.NotFound:
                # The client was archived between translating and exporting.
                # The operator asked for a file; they get the file.
                remembered = 0

    return FileResponse(
        out,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="translated.xlsx",
        # Read by `exportSheet`, which already uses raw fetch. A second round
        # trip to report this could fail and leave the learning unexplained.
        headers={
            "X-Corrections-Remembered": str(remembered),
            # Where the class-aware half went. Separate headers because these
            # land in different stores and the operator's undo differs: the
            # flat memory is undone by job id, a lexicon row by editing the file.
            "X-Names-Learned": str(routed["names"]),
            "X-Places-Learned": str(routed["places"]),
            "X-Terms-Locked": str(routed["glossary"]),
        },
    )



# --- corrections memory ----------------------------------------------------
#
# ADR-029. Lives here rather than in a router of its own: the client and
# glossary routes are already in this module, `main.py` mounts it behind the
# `translate` extra, and the corrections memory is useless without that extra
# anyway.

# A corrections file longer than this is a mistake, not a memory.
MAX_CORRECTIONS = 50_000


class CorrectionIn(BaseModel):
    source: str = Field(min_length=1, max_length=db.MAX_CORRECTION_CHARS)
    target: str = Field(min_length=1, max_length=db.MAX_CORRECTION_CHARS)


class CorrectionsIn(BaseModel):
    """A corrections write. Accepts `{"corrections": [...]}` or a bare list."""

    client_id: int | None = None
    corrections: list[CorrectionIn] = Field(
        default_factory=list, max_length=MAX_CORRECTIONS
    )

    @model_validator(mode="before")
    @classmethod
    def _accept_a_bare_list(cls, data: object) -> object:
        if isinstance(data, list):
            return {"corrections": data}
        return data


class ForgetJobIn(BaseModel):
    job_id: str = Field(min_length=1, max_length=64)


def _corrections_envelope(
    client_id: int | None, query: str = "", limit: int = 200, offset: int = 0
) -> dict[str, object]:
    rows = db.get_corrections(client_id, query, limit, offset)
    return {
        "client_id": client_id,
        "corrections": rows,
        "count": len(rows),
        "total": db.count_corrections(client_id, query),
    }


@router.get("/corrections")
def list_corrections(
    client_id: int | None = None, q: str = "", limit: int = 200, offset: int = 0
) -> dict[str, object]:
    """Remembered cells for one scope. Always paginated — see `db.get_corrections`."""
    return _corrections_envelope(client_id, q, limit, offset)


@router.put("/corrections")
def put_corrections(body: CorrectionsIn) -> dict[str, object]:
    try:
        counts = db.upsert_corrections(
            body.client_id, [(c.source, c.target) for c in body.corrections]
        )
    except db.NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    return _corrections_envelope(body.client_id) | counts


@router.delete("/corrections/{correction_id}")
def remove_correction(correction_id: int, client_id: int | None = None) -> dict[str, object]:
    db.delete_correction(correction_id)
    return _corrections_envelope(client_id)


@router.post("/corrections/import")
def import_corrections(
    file: Annotated[UploadFile, File()],
    client_id: Annotated[int | None, Form()] = None,
) -> dict[str, object]:
    """Load an English → Malayalam sheet into the memory."""
    try:
        sheet = excel.read_pairs(_read(file), limit=MAX_CORRECTIONS)
    except excel.ExcelError as exc:
        raise HTTPException(400, str(exc)) from exc

    if sheet.looks_swapped:
        # Refused, not loaded. A reversed memory does not sit there looking
        # wrong — it silently fills the next sheet's cells with English.
        raise HTTPException(
            400,
            "This sheet looks the wrong way round: the Malayalam is in the first "
            "column. Put the English in the first column and the Malayalam in "
            "the second, then load it again.",
        )
    if not sheet.pairs:
        raise HTTPException(
            400,
            "No English and Malayalam pairs in that file. The first column is "
            "the English and the second is the Malayalam.",
        )

    try:
        counts = db.upsert_corrections(client_id, sheet.pairs)
    except db.NotFound as exc:
        raise HTTPException(404, str(exc)) from exc

    return {
        "client_id": client_id,
        "rows": len(sheet.pairs),
        "truncated": sheet.truncated,
        "blank_rows": sheet.skipped,
        "total": db.count_corrections(client_id),
        **counts,
    }


@router.get("/corrections/export")
def export_corrections(client_id: int | None = None) -> FileResponse:
    """The memory as a spreadsheet, so the shop can edit it in its own tool."""
    rows = db.get_corrections(client_id, limit=1000, offset=0)
    total = db.count_corrections(client_id)
    # One query per page rather than one enormous one; a shop with 40,000
    # entries must still get a file.
    while len(rows) < total:
        page = db.get_corrections(client_id, limit=1000, offset=len(rows))
        if not page:
            break
        rows.extend(page)

    scope = "All clients" if client_id is None else f"Client {client_id}"
    data = excel.write_pairs(
        [(r["source"], r["target"]) for r in rows],
        scope=scope,
        learned=[r["updated_at"][:10] for r in rows],
    )
    out = config.WORK_DIR / "corrections.xlsx"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return FileResponse(
        out,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="corrections.xlsx",
    )


@router.post("/corrections/forget-job")
def forget_job(body: ForgetJobIn) -> dict[str, object]:
    """Undo everything one export taught."""
    forgotten = db.forget_job(body.job_id)
    return {"forgotten": forgotten, "total": db.count_corrections(None)}


# --- word library ----------------------------------------------------------
#
# The bundled English → Malayalam dictionary, plus whatever the operator has
# corrected in it. Read-mostly: the data itself ships in `data/dictionary/` and
# only the diff lives in the database (ADR-032).


# A search page. Large enough to scan, small enough that 59,000 rows never
# cross the wire at once.
DICTIONARY_PAGE = 50


class DictionaryOverrideIn(BaseModel):
    source_term: str = Field(min_length=1, max_length=300)
    target_term: str = Field(min_length=1, max_length=300)


def _dictionary_rows(
    entries: list[dictionary.Entry], overrides: dict[str, str]
) -> list[dict[str, object]]:
    """Entries with the operator's own corrections shown in place."""
    rows: list[dict[str, object]] = []
    for entry in entries:
        key = textkey.normalise(entry.source)
        override = overrides.get(key)
        rows.append(
            {
                "source": entry.source,
                "target": override or entry.primary,
                "alternatives": entry.alternatives,
                # What the operator is looking at when they decide whether to
                # trust a row. "Yours" outranks the other two by construction.
                "origin": "yours" if override else ("trade" if entry.trade else "olam"),
                # Kept so the panel can show what it replaced, rather than
                # making the operator remember what the book used to say.
                "bundled": entry.primary if override else "",
            }
        )
    return rows


@router.get("/dictionary")
def search_dictionary(q: str = "", offset: int = 0) -> dict[str, object]:
    """Search the word library. `q` empty lists it from the beginning."""
    entries, total = dictionary.search(q, limit=DICTIONARY_PAGE, offset=max(0, offset))
    return {
        "rows": _dictionary_rows(entries, db.dictionary_overrides()),
        "total": total,
        "offset": max(0, offset),
        "page": DICTIONARY_PAGE,
        "bundled": dictionary.count(),
        "overrides": len(db.dictionary_overrides()),
    }


@router.get("/dictionary/export")
def export_dictionary() -> FileResponse:
    """The whole library as one spreadsheet, for editing in the shop's own tool."""
    overrides = db.dictionary_overrides()
    entries = sorted(dictionary.load().values(), key=lambda e: e.source.casefold())
    rows = [
        (
            str(row["source"]),
            str(row["target"]),
            " | ".join(row["alternatives"]),  # type: ignore[arg-type]
            {"yours": "Yours", "trade": "Print trade", "olam": "Olam"}[str(row["origin"])],
        )
        for row in _dictionary_rows(entries, overrides)
    ]
    out = config.WORK_DIR / "word-library.xlsx"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(excel.write_dictionary(rows))
    return FileResponse(
        out,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="word-library.xlsx",
    )


@router.post("/dictionary/import")
def import_dictionary(file: Annotated[UploadFile, File()]) -> dict[str, object]:
    """Load an edited library back, keeping only the rows that changed.

    Only the differences are stored. Re-importing the exported sheet unchanged
    must write nothing at all — otherwise one round trip would copy 59,000
    bundled rows into the database and freeze the library at today's version.
    """
    try:
        sheet = excel.read_pairs(_read(file), limit=MAX_CORRECTIONS)
    except excel.ExcelError as exc:
        raise HTTPException(400, str(exc)) from exc

    if sheet.looks_swapped:
        raise HTTPException(
            400,
            "This sheet looks the wrong way round: the Malayalam is in the first "
            "column. Put the English in the first column and the Malayalam in "
            "the second, then load it again.",
        )
    if not sheet.pairs:
        raise HTTPException(
            400,
            "No English and Malayalam pairs in that file. The first column is "
            "the English and the second is the Malayalam.",
        )

    bundled = dictionary.load()
    changed = [
        (source, target)
        for source, target in sheet.pairs
        if (entry := bundled.get(textkey.normalise(source))) is None
        or entry.primary != target
    ]
    counts = db.upsert_dictionary_overrides(changed)
    return {
        "rows": len(sheet.pairs),
        "unchanged": len(sheet.pairs) - len(changed),
        "truncated": sheet.truncated,
        "blank_rows": sheet.skipped,
        "overrides": len(db.dictionary_overrides()),
        **counts,
    }


@router.put("/dictionary/override")
def put_dictionary_override(body: DictionaryOverrideIn) -> dict[str, object]:
    """Correct one word without a spreadsheet round trip."""
    counts = db.upsert_dictionary_overrides([(body.source_term, body.target_term)])
    if not counts["added"] and not counts["updated"]:
        raise HTTPException(400, "That is not a word and a translation.")
    return {"overrides": db.list_dictionary_overrides(), **counts}


@router.delete("/dictionary/override/{override_id}")
def delete_dictionary_override(override_id: int) -> dict[str, object]:
    """Drop one correction, putting the bundled answer back in force."""
    db.delete_dictionary_override(override_id)
    return {"overrides": db.list_dictionary_overrides()}


@router.get("/settings/translation")
def translation_settings() -> dict[str, object]:
    return {
        "engines": translate.engine_status(),
        "active": translate.available_engine(),
        "hf_home": str(config.HF_HOME),
    }
