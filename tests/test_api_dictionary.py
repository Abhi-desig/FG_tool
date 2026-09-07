"""API tests for the word library panel: search, export, import, override.

The round trip is the risky part. Exporting 59,000 rows and loading them back
must write *nothing*, or one click would copy the whole bundled dictionary into
the database and freeze it at today's version — with the operator's real
corrections buried somewhere inside it.
"""

from __future__ import annotations

import io
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

from backend import db
from backend.features import dictionary
from backend.main import app

client = TestClient(app, base_url="http://127.0.0.1:8000")


@pytest.fixture(autouse=True)
def _clean_overrides() -> Iterator[None]:
    """Each test starts *and ends* with the bundled library and nothing on top.

    Cleaning up afterwards as well is not tidiness. Overrides are shop-wide by
    design, so one left behind by the last test in this file changed what
    `test_api_excel.py` saw a sheet translate to — which is exactly the bug this
    fixture existed to prevent, escaping one file to the next.
    """
    def wipe() -> None:
        for row in db.list_dictionary_overrides():
            db.delete_dictionary_override(int(row["id"]))

    wipe()
    yield
    wipe()


def pairs_sheet(rows: list[tuple[str, str]]) -> dict[str, tuple[str, bytes, str]]:
    wb = Workbook()
    ws = wb.active
    ws.append(["English", "Malayalam"])
    for source, target in rows:
        ws.append([source, target])
    buffer = io.BytesIO()
    wb.save(buffer)
    return {
        "file": (
            "words.xlsx",
            buffer.getvalue(),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    }


def test_search_returns_a_page_and_a_total() -> None:
    body = client.get("/api/dictionary?q=card").json()
    assert body["total"] > 1
    assert len(body["rows"]) <= body["page"]
    assert body["bundled"] > 50_000
    assert body["rows"][0]["source"].casefold() == "card"


def test_search_marks_where_each_row_came_from() -> None:
    trade = client.get("/api/dictionary?q=standee").json()["rows"][0]
    assert trade["origin"] == "trade"
    general = client.get("/api/dictionary?q=coconut oil").json()["rows"][0]
    assert general["origin"] == "olam"


def test_an_override_is_shown_in_place_with_what_it_replaced() -> None:
    client.put(
        "/api/dictionary/override",
        json={"source_term": "standee", "target_term": "സ്റ്റാൻഡീ"},
    )
    row = client.get("/api/dictionary?q=standee").json()["rows"][0]
    assert row["target"] == "സ്റ്റാൻഡീ"
    assert row["origin"] == "yours"
    # The operator must be able to see what the book said, not have to remember.
    assert row["bundled"] == "സ്റ്റാൻഡി"


def test_an_override_is_removable_and_the_bundled_answer_returns() -> None:
    client.put(
        "/api/dictionary/override",
        json={"source_term": "standee", "target_term": "സ്റ്റാൻഡീ"},
    )
    override_id = int(db.list_dictionary_overrides()[0]["id"])
    client.delete(f"/api/dictionary/override/{override_id}")

    row = client.get("/api/dictionary?q=standee").json()["rows"][0]
    assert row["origin"] == "trade"
    assert row["target"] == "സ്റ്റാൻഡി"


def test_the_export_carries_the_pair_in_the_first_two_columns() -> None:
    """So `read_pairs` can load an edited copy straight back."""
    response = client.get("/api/dictionary/export")
    assert response.status_code == 200

    book = load_workbook(io.BytesIO(response.content))
    sheet = book[book.sheetnames[0]]
    assert [c.value for c in sheet[1]] == [
        "English",
        "Malayalam",
        "Other meanings",
        "Where from",
    ]
    assert sheet.max_row > 50_000


def test_a_round_trip_with_no_edits_writes_nothing() -> None:
    """The whole reason imports are stored as a diff.

    Without this, exporting and re-importing would copy 59,000 bundled rows into
    the database, and a later refresh of the dataset would bring nothing.
    """
    entries = sorted(dictionary.load().values(), key=lambda e: e.source)[:200]
    body = client.post(
        "/api/dictionary/import",
        files=pairs_sheet([(e.source, e.primary) for e in entries]),
    ).json()

    assert body["rows"] == 200
    assert body["unchanged"] == 200
    assert body["added"] == 0
    assert body["updated"] == 0
    assert db.list_dictionary_overrides() == []


def test_an_edited_row_is_stored_and_a_new_word_is_added() -> None:
    body = client.post(
        "/api/dictionary/import",
        files=pairs_sheet(
            [
                ("standee", "സ്റ്റാൻഡീ"),  # changed
                ("Focus Digitals", "ഫോക്കസ് ഡിജിറ്റൽസ്"),  # not in the library
            ]
        ),
    ).json()

    assert body["added"] == 2
    assert body["unchanged"] == 0
    stored = {r["source"]: r["target"] for r in db.list_dictionary_overrides()}
    assert stored["standee"] == "സ്റ്റാൻഡീ"
    assert stored["Focus Digitals"] == "ഫോക്കസ് ഡിജിറ്റൽസ്"


def test_a_reversed_sheet_is_refused_rather_than_loaded() -> None:
    """A reversed library does not sit there looking wrong — it fills the next
    sheet's cells with English."""
    response = client.post(
        "/api/dictionary/import",
        files=pairs_sheet([("സ്റ്റാൻഡി", "standee"), ("ബ്രോഷർ", "brochure")]),
    )
    assert response.status_code == 400
    assert "wrong way round" in response.json()["detail"]
    assert db.list_dictionary_overrides() == []


def test_an_empty_sheet_is_refused() -> None:
    response = client.post("/api/dictionary/import", files=pairs_sheet([]))
    assert response.status_code == 400


def test_an_override_changes_what_a_sheet_translates_to() -> None:
    """The end of the chain: the panel is only worth having if it reaches the
    translator."""
    client.put(
        "/api/dictionary/override",
        json={"source_term": "standee", "target_term": "സ്റ്റാൻഡീ"},
    )
    wb = Workbook()
    ws = wb.active
    ws.title = "Prices"
    ws["A1"] = "Standee"
    buffer = io.BytesIO()
    wb.save(buffer)

    body = client.post(
        "/api/excel/inspect",
        files={
            "file": (
                "prices.xlsx",
                buffer.getvalue(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    ).json()
    # Still free and still offline; only the wording changed.
    assert body["from_dictionary"] == 1
    assert body["to_translate"] == 0
