"""API tests for Phase 3: clients, glossary, translate, export."""

from __future__ import annotations

import io
import time

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font

from backend.features import translate
from backend.main import app

# base_url is a loopback address deliberately: `guard.LocalOnlyMiddleware`
# refuses any Host that is not this machine, and TestClient's default
# `http://testserver` is exactly the DNS-rebinding shape it exists to stop.
client = TestClient(app, base_url="http://127.0.0.1:8000")

HAS_ENGINE = translate.available_engine() is not None
needs_engine = pytest.mark.skipif(not HAS_ENGINE, reason="no engine downloaded")


def sheet() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Catalogue"
    ws["A1"] = "Product"
    ws["A1"].font = Font(bold=True)
    ws["A2"] = "Coconut oil"
    ws["A3"] = "Fresh mango pickle"
    ws["B2"] = 180.5
    ws["C2"] = "=B2*2"
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def upload() -> dict[str, tuple[str, bytes, str]]:
    return {
        "file": (
            "catalogue.xlsx",
            sheet(),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    }


def wait_for(job_id: str, timeout: float = 600.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["status"] not in {"queued", "running"}:
            return body
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} did not finish")


@pytest.fixture
def a_client() -> int:
    made = client.post("/api/clients", json={"name": f"Test {time.time_ns()}"}).json()
    return int(made["id"])


# --- clients and glossary -------------------------------------------------


def test_client_lifecycle(a_client: int) -> None:
    names = [c["id"] for c in client.get("/api/clients").json()]
    assert a_client in names
    client.post(f"/api/clients/{a_client}/archive")
    assert a_client not in [c["id"] for c in client.get("/api/clients").json()]
    assert a_client in [
        c["id"] for c in client.get("/api/clients?include_archived=true").json()
    ]


def test_client_name_is_required() -> None:
    assert client.post("/api/clients", json={"name": ""}).status_code == 422


def test_glossary_round_trip(a_client: int) -> None:
    rows = client.put(
        f"/api/clients/{a_client}/glossary",
        json=[{"source_term": "coconut oil", "target_term": "വെളിച്ചെണ്ണ"}],
    ).json()
    assert rows[0]["target_term"] == "വെളിച്ചെണ്ണ"

    term_id = rows[0]["id"]
    assert client.delete(f"/api/clients/{a_client}/glossary/{term_id}").json() == []


def test_correcting_a_term_updates_not_duplicates(a_client: int) -> None:
    """Exit gate: correcting once fixes every later occurrence."""
    client.put(
        f"/api/clients/{a_client}/glossary",
        json=[{"source_term": "pickle", "target_term": "wrong"}],
    )
    rows = client.put(
        f"/api/clients/{a_client}/glossary",
        json=[{"source_term": "pickle", "target_term": "അച്ചാർ"}],
    ).json()
    assert len(rows) == 1
    assert rows[0]["target_term"] == "അച്ചാർ"


def test_two_clients_do_not_share_terms() -> None:
    """Exit gate: client A's brand terms must not leak into client B."""
    a = client.post("/api/clients", json={"name": f"A {time.time_ns()}"}).json()["id"]
    b = client.post("/api/clients", json={"name": f"B {time.time_ns()}"}).json()["id"]
    client.put(
        f"/api/clients/{a}/glossary",
        json=[{"source_term": "Fresh", "target_term": "ആംബർ ഫ്രഷ്"}],
    )
    client.put(
        f"/api/clients/{b}/glossary",
        json=[{"source_term": "Fresh", "target_term": "പുതിയ"}],
    )
    assert client.get(f"/api/clients/{a}/glossary").json()[0]["target_term"] == "ആംബർ ഫ്രഷ്"
    assert client.get(f"/api/clients/{b}/glossary").json()[0]["target_term"] == "പുതിയ"


def test_glossary_for_unknown_client_is_404() -> None:
    assert client.get("/api/clients/999999/glossary").status_code == 404


# --- inspect --------------------------------------------------------------


def test_inspect_counts_what_matters() -> None:
    body = client.post("/api/excel/inspect", files=upload()).json()
    assert body["sheets"] == ["Catalogue"]
    assert body["translatable"] == 3  # Product, Coconut oil, Fresh mango pickle
    assert body["skipped_formulas"] == 1
    assert body["skipped_non_text"] == 1  # the number


def test_inspect_rejects_a_non_spreadsheet() -> None:
    r = client.post(
        "/api/excel/inspect", files={"file": ("x.xlsx", b"not a sheet", "application/x")}
    )
    assert r.status_code == 400


def test_inspect_rejects_empty_upload() -> None:
    r = client.post("/api/excel/inspect", files={"file": ("x.xlsx", b"", "application/x")})
    assert r.status_code == 400


def test_translation_settings_lists_engines() -> None:
    body = client.get("/api/settings/translation").json()
    assert {e["key"] for e in body["engines"]} == {
        "opus-mt-en-ml",
        "indictrans2-en-indic",
    }


# --- translate and export -------------------------------------------------


def test_export_before_translation_is_rejected() -> None:
    r = client.post(
        "/api/excel/export", json={"job_id": "nope", "translations": {"A!A1": "x"}}
    )
    assert r.status_code == 409


@needs_engine
def test_translate_returns_a_review_grid_without_writing_a_file(a_client: int) -> None:
    """Exit gate: nothing is written until the operator accepts."""
    client.put(
        f"/api/clients/{a_client}/glossary",
        json=[{"source_term": "coconut oil", "target_term": "വെളിച്ചെണ്ണ"}],
    )
    started = client.post(
        "/api/excel/translate", files=upload(), data={"client_id": str(a_client)}
    ).json()
    done = wait_for(started["id"])
    assert done["status"] == "done", done

    rows = {r["key"]: r for r in done["result"]["rows"]}
    assert set(rows) == {"Catalogue!A1", "Catalogue!A2", "Catalogue!A3"}
    assert rows["Catalogue!A2"]["glossary_only"] is True
    assert rows["Catalogue!A2"]["translation"] == "വെളിച്ചെണ്ണ"
    assert done["result"]["from_glossary"] >= 1
    # No file is offered by the translate step itself.
    assert "translated.xlsx" not in str(done["result"])


@needs_engine
def test_export_applies_operator_edits_and_keeps_formatting(a_client: int) -> None:
    started = client.post(
        "/api/excel/translate", files=upload(), data={"client_id": str(a_client)}
    ).json()
    done = wait_for(started["id"])
    assert done["status"] == "done", done

    # The operator overrides one row by hand — that edit must win.
    edits = {r["key"]: r["translation"] for r in done["result"]["rows"]}
    edits["Catalogue!A3"] = "എന്റെ സ്വന്തം വിവർത്തനം"

    got = client.post(
        "/api/excel/export", json={"job_id": started["id"], "translations": edits}
    )
    assert got.status_code == 200
    ws = load_workbook(io.BytesIO(got.content))["Catalogue"]
    assert ws["A3"].value == "എന്റെ സ്വന്തം വിവർത്തനം"
    assert ws["A1"].font.bold is True, "formatting must survive"
    assert ws["C2"].value == "=B2*2", "formulas must survive"
    assert ws["B2"].value == 180.5, "numbers must survive"


@needs_engine
def test_translating_without_a_client_still_works() -> None:
    """No glossary chosen is valid — it just means no forced terms."""
    started = client.post("/api/excel/translate", files=upload()).json()
    done = wait_for(started["id"])
    assert done["status"] == "done", done
    assert all(not r["glossary_only"] for r in done["result"]["rows"])


def test_translate_with_unknown_client_is_404() -> None:
    r = client.post(
        "/api/excel/translate", files=upload(), data={"client_id": "999999"}
    )
    assert r.status_code == 404


def test_translate_rejects_a_sheet_with_nothing_to_do() -> None:
    wb = Workbook()
    wb.active["A1"] = 42
    buffer = io.BytesIO()
    wb.save(buffer)
    r = client.post(
        "/api/excel/translate",
        files={"file": ("nums.xlsx", buffer.getvalue(), "application/x")},
    )
    assert r.status_code == 400
    assert "needs translating" in r.json()["detail"]
