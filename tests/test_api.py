"""API tests for the Phase 1 endpoint."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend import guard
from backend.main import MAX_INPUT_CHARS, app

# base_url is a loopback address deliberately: `guard.LocalOnlyMiddleware`
# refuses any Host that is not this machine, and TestClient's default
# `http://testserver` is exactly the DNS-rebinding shape it exists to stop.
client = TestClient(app, base_url="http://127.0.0.1:8000")


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


# --- what actually holds the line (NEXT.md 2.5) ---------------------------
#
# SECURITY.md §1 treated the 127.0.0.1 bind as the whole answer. It stops the
# shop's Wi-Fi; it does nothing about a page already open in the operator's own
# browser. A multipart POST is a CORS *simple* request — no preflight, no
# permission asked — so any site could fire one at this port. It cannot read the
# reply, but the Google spend and the CPU burn happen anyway.

# A client that does NOT rewrite Host, so the guard sees what a rebound DNS name
# would look like.
rebound = TestClient(app, base_url="http://evil.example.com:8000")


def test_a_non_loopback_host_is_refused() -> None:
    """This is what closes DNS rebinding.

    A hostile domain resolving to 127.0.0.1 is same-origin as far as the browser
    is concerned; the Host header is the only thing that gives it away.
    """
    response = rebound.get("/api/health")
    assert response.status_code == 421
    assert "localhost" in response.json()["detail"]


def test_loopback_hosts_are_accepted() -> None:
    for host in ("127.0.0.1:8000", "localhost:8000", "127.0.0.1"):
        response = client.get("/api/health", headers={"host": host})
        assert response.status_code == 200, host


def test_a_cross_site_post_is_refused() -> None:
    """The measured attack: any open page could start a paid job."""
    response = client.post(
        "/api/fonts/convert",
        json={"text": " കേരളം", "direction": "to_ascii"},
        headers={"sec-fetch-site": "cross-site", "origin": "https://evil.example.com"},
    )
    assert response.status_code == 403
    assert "another website" in response.json()["detail"]


def test_a_foreign_origin_is_refused_even_without_sec_fetch_site() -> None:
    """An older browser, or one that strips the hint. Origin still gives it away."""
    response = client.post(
        "/api/fonts/convert",
        json={"text": " കേരളം", "direction": "to_ascii"},
        headers={"origin": "https://evil.example.com"},
    )
    assert response.status_code == 403


def test_the_apps_own_pages_are_accepted() -> None:
    response = client.post(
        "/api/fonts/convert",
        json={"text": " കേരളം", "direction": "to_ascii"},
        headers={
            "sec-fetch-site": "same-origin",
            "origin": "http://127.0.0.1:8000",
        },
    )
    assert response.status_code == 200


def test_a_get_is_never_blocked_on_origin() -> None:
    """Reads change nothing, and the same-origin policy already stops the reply
    being read. Blocking them would only break the diagnostic script."""
    response = client.get(
        "/api/health", headers={"sec-fetch-site": "cross-site"}
    )
    assert response.status_code == 200


def test_a_request_with_no_browser_headers_is_allowed() -> None:
    """curl, check-ai.command, and the test client. All local, all deliberate."""
    response = client.post(
        "/api/fonts/convert", json={"text": "ക", "direction": "to_ascii"}
    )
    assert response.status_code == 200


def test_every_state_changing_method_is_guarded() -> None:
    """Not just POST — the check must not be one method wide."""
    assert {"POST", "PUT", "PATCH", "DELETE"} <= guard.UNSAFE_METHODS
    response = client.put(
        "/api/settings/preferences",
        json={"default_dpi": "300"},
        headers={"sec-fetch-site": "cross-site"},
    )
    assert response.status_code == 403


# --- a Pydantic body on every route (NEXT.md 3.14) ------------------------


def test_preferences_still_accepts_the_bare_map_the_ui_sends() -> None:
    """A `dist` bundle older than this server must keep working."""
    response = client.put("/api/settings/preferences", json={"default_dpi": "300"})
    assert response.status_code == 200
    assert response.json()["default_dpi"] == "300"


def test_preferences_also_accepts_the_wrapped_form() -> None:
    response = client.put(
        "/api/settings/preferences", json={"updates": {"default_dpi": "300"}}
    )
    assert response.status_code == 200


def test_an_absurd_preference_value_is_refused() -> None:
    """Bounded, which it was not — CLAUDE.md and SECURITY.md §4 both ask for it."""
    response = client.put(
        "/api/settings/preferences", json={"default_dpi": "3" * 5000}
    )
    assert response.status_code == 422


# --- a base install can start (NEXT.md 2.6) ------------------------------


def test_features_endpoint_reports_what_is_installed() -> None:
    """So the UI can say "run uv sync --extra images" rather than 404 on
    every button of a screen it should not have shown."""
    body = client.get("/api/features").json()
    assert "fonts" in body["available"], "the font converter is always available"
    assert isinstance(body["unavailable"], dict)
