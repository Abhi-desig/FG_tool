"""Phase 3 routes: clients, glossary, translate, export.

The shape enforces the exit gate: **nothing is written to a file until the
operator accepts.** `POST /translate` returns rows to review and never touches
the workbook; `POST /export` takes the reviewed rows — edits included — and only
then produces a new file.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator

from backend import config, db, jobs, textkey
from backend.features import excel, glossary, translate, verify

router = APIRouter(prefix="/api", tags=["excel"])

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

    from_memory = [s for s in unique if textkey.normalise(s) in memory]
    remembered = set(from_memory)
    from_glossary = [
        s for s in unique if s not in remembered and glossary.is_fully_covered(s, terms)
    ]
    covered = remembered | set(from_glossary)
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
        "to_translate": len(quotable),
        "engine": translate.available_engine(),
        # What the optional Claude check would cost on *this* sheet. Quoted here
        # rather than on a route of its own so the operator is not asked to
        # upload the same 33,000-row file twice to find out.
        "verify": verify.estimate(verify.checkable(quotable)),
    }


@router.post("/excel/translate")
def start_translate(
    file: Annotated[UploadFile, File()],
    client_id: Annotated[int | None, Form()] = None,
    engine: Annotated[str | None, Form()] = None,
    check_with_claude: Annotated[bool, Form()] = False,
    over_budget_ok: Annotated[bool, Form()] = False,
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
        )
        by_source = {r.source: r for r in rows}

        checked: dict[str, verify.Verdict] = {}
        review: dict[str, object] = {"requested": check_with_claude}
        # Rows that are exact by construction are never sent. A glossary term
        # is the client's own approved wording and a remembered cell is the
        # operator's own correction; paying a model to overwrite either is both
        # a waste and a way to lose an answer that was already right.
        to_check = [r for r in rows if not (r.glossary_only or r.from_memory)]
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

    try:
        written = excel.apply(source.read_bytes(), body.translations)
    except excel.ExcelError as exc:
        raise HTTPException(400, str(exc)) from exc

    out = source.with_name("translated.xlsx")
    out.write_bytes(written)

    remembered = 0
    if body.remember:
        rows = job.result.get("rows") or []
        pairs = _learn(rows, body.translations)
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
        headers={"X-Corrections-Remembered": str(remembered)},
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


@router.get("/settings/translation")
def translation_settings() -> dict[str, object]:
    return {
        "engines": translate.engine_status(),
        "active": translate.available_engine(),
        "hf_home": str(config.HF_HOME),
    }
