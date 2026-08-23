"""API tests for the Phase 1 endpoint."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.main import MAX_INPUT_CHARS, app

client = TestClient(app)


def test_convert_to_ascii() -> None:
    r = client.post("/api/fonts/convert", json={"text": "കേരളം"})
    assert r.status_code == 200
    body = r.json()
    assert body["result"] == "tIcfw"
    assert body["direction"] == "to_ascii"
    assert body["chars_in"] == 5


def test_convert_to_unicode() -> None:
    r = client.post(
        "/api/fonts/convert", json={"text": "tIcfw", "direction": "to_unicode"}
    )
    assert r.status_code == 200
    assert r.json()["result"] == "കേരളം"


def test_empty_input_is_allowed() -> None:
    r = client.post("/api/fonts/convert", json={"text": ""})
    assert r.status_code == 200
    assert r.json()["result"] == ""


def test_oversized_input_is_rejected() -> None:
    r = client.post("/api/fonts/convert", json={"text": "ക" * (MAX_INPUT_CHARS + 1)})
    assert r.status_code == 422


def test_unknown_direction_is_rejected() -> None:
    r = client.post("/api/fonts/convert", json={"text": "ക", "direction": "sideways"})
    assert r.status_code == 422


def test_unknown_font_returns_400_not_500() -> None:
    r = client.post("/api/fonts/convert", json={"text": "ക", "font": "NoSuchFont"})
    assert r.status_code == 400


@pytest.mark.parametrize(
    "font",
    [
        "../../../../etc/passwd",
        "../../pyproject",
        "/etc/hosts",
        "ML-TTKarthika/../../../secret",
        "",
    ],
)
def test_font_name_cannot_address_an_arbitrary_path(font: str) -> None:
    """SECURITY.md: selection is by key from a fixed registry, never by path."""
    r = client.post("/api/fonts/convert", json={"text": "ക", "font": font})
    assert r.status_code == 400
    assert "unknown font" in r.json()["detail"]


def test_only_registered_fonts_load() -> None:
    from backend.features.fonts import available_fonts

    assert available_fonts() == ("ML-TTKarthika",)


def test_info_reports_the_map() -> None:
    body = client.get("/api/fonts/info").json()
    assert body["font"] == "ML-TTKarthika"
    assert body["unicode_entries"] > 100
    assert body["longest_match"] == 5


def test_health() -> None:
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["phase"] == 5
