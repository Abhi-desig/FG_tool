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
from pydantic import BaseModel, Field

from backend import config, db, jobs
from backend.features import excel, glossary, translate

router = APIRouter(prefix="/api", tags=["excel"])

MAX_ROWS = 20_000


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


@router.get("/clients")
def list_clients(include_archived: bool = False) -> list[dict[str, object]]:
    return db.list_clients(include_archived)


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
def get_glossary(client_id: int) -> list[dict[str, object]]:
    try:
        return db.get_glossary(client_id)
    except db.NotFound as exc:
        raise HTTPException(404, str(exc)) from exc


@router.put("/clients/{client_id}/glossary")
def put_glossary(client_id: int, terms: list[TermIn]) -> list[dict[str, object]]:
    """Add or correct terms. Correcting one fixes every later occurrence."""
    try:
        return db.upsert_terms(client_id, [t.model_dump() for t in terms])
    except db.NotFound as exc:
        raise HTTPException(404, str(exc)) from exc


@router.delete("/clients/{client_id}/glossary/{term_id}")
def delete_term(client_id: int, term_id: int) -> list[dict[str, object]]:
    try:
        return db.delete_term(client_id, term_id)
    except db.NotFound as exc:
        raise HTTPException(404, str(exc)) from exc


# --- translation -----------------------------------------------------------


@router.post("/excel/inspect")
def inspect(file: Annotated[UploadFile, File()]) -> dict[str, object]:
    """What is in this sheet, and what would be translated."""
    try:
        found = excel.extract(_read(file))
    except excel.ExcelError as exc:
        raise HTTPException(400, str(exc)) from exc
    unique = excel.unique_sources(found.cells)
    return {
        "sheets": found.sheets,
        "total_cells": found.total_cells,
        "translatable": len(found.cells),
        "unique_strings": len(unique),
        "skipped_formulas": found.skipped_formulas,
        "skipped_non_text": found.skipped_non_text,
        "engine": translate.available_engine(),
    }


@router.post("/excel/translate")
def start_translate(
    file: Annotated[UploadFile, File()],
    client_id: Annotated[int | None, Form()] = None,
    engine: Annotated[str | None, Form()] = None,
) -> dict[str, object]:
    """Translate a sheet and return a job whose result is the review grid.

    The workbook is *not* modified. The operator reviews, edits, then exports.
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

    terms: list[tuple[str, str]] = []
    if client_id is not None:
        try:
            terms = glossary.compile_terms(db.get_glossary(client_id))
        except db.NotFound as exc:
            raise HTTPException(404, str(exc)) from exc

    unique = excel.unique_sources(found.cells)
    name = file.filename or "sheet.xlsx"

    def work(report: jobs.Reporter) -> dict[str, object]:
        report.step("Reading sheet…", 0.05)
        rows = translate.translate_rows(unique, terms, engine, report)
        by_source = {r.source: r for r in rows}

        report.step("Building review grid…", 0.97)
        # Save the upload so export does not need it sent twice.
        job_dir = config.WORK_DIR / report._job.id  # noqa: SLF001
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "source.xlsx").write_bytes(data)

        grid = []
        for cell in found.cells:
            row = by_source[cell.source]
            grid.append(
                {
                    "key": cell.key,
                    "sheet": cell.sheet,
                    "ref": cell.ref,
                    "source": cell.source,
                    "translation": row.translation,
                    "glossary_terms": row.glossary_terms,
                    "glossary_only": row.glossary_only,
                    "lost_terms": row.lost_terms,
                    "warnings": row.warnings,
                    "needs_attention": row.needs_attention,
                }
            )
        return {
            "file": "source.xlsx",
            "rows": grid,
            "unique_strings": len(unique),
            "needs_attention": sum(1 for r in grid if r["needs_attention"]),
            "from_glossary": sum(1 for r in grid if r["glossary_only"]),
        }

    job = jobs.submit("translate", f"Translate — {name}", work)
    return job.as_dict()


class ExportRequest(BaseModel):
    job_id: str
    # key → the operator's final text, edits included
    translations: dict[str, str]


@router.post("/excel/export")
def export(body: ExportRequest) -> FileResponse:
    """Write the accepted translations into a new workbook.

    This is the only point at which a file is produced — the exit-gate promise
    that nothing is written until the operator accepts.
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
    return FileResponse(
        out,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="translated.xlsx",
    )


@router.get("/settings/translation")
def translation_settings() -> dict[str, object]:
    return {
        "engines": translate.engine_status(),
        "active": translate.available_engine(),
        "hf_home": str(config.HF_HOME),
    }
