"""API tests for Phase 5. No route here is allowed to leak a key or spend money."""

from __future__ import annotations

import base64
import io
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend import db
from backend.features import ai, prompts
from backend.main import app

client = TestClient(app)

SECRET = "AIza-this-must-never-appear-anywhere"


def png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), (30, 90, 160)).save(buffer, format="PNG")
    return buffer.getvalue()


class FakeInline:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.mime_type = "image/png"


class FakePart:
    def __init__(self, data: bytes | None = None, text: str | None = None) -> None:
        self.inline_data = FakeInline(data) if data else None
        self.text = text


class FakeResponse:
    def __init__(self, parts: list[FakePart], text: str | None = None) -> None:
        self.candidates = [
            type("C", (), {"content": type("X", (), {"parts": parts})()})()
        ]
        self.text = text


def fake_client(monkeypatch: pytest.MonkeyPatch, response: Any = None,
                raises: Exception | None = None) -> None:
    class Models:
        def generate_content(self, **kwargs: Any) -> Any:
            if raises:
                raise raises
            return response

    monkeypatch.setattr(ai, "_client", lambda: type("C", (), {"models": Models()})())


@pytest.fixture(autouse=True)
def a_key() -> None:
    db.set_api_key("GEMINI_API_KEY", SECRET)


# --- the key never crosses the boundary -----------------------------------


def test_no_route_returns_the_key() -> None:
    """Walk every GET the settings surface exposes and grep for the secret."""
    for path in (
        "/api/settings/keys",
        "/api/settings/prompts",
        "/api/ai/status",
        "/api/ai/spend",
        "/api/settings/preferences",
        "/api/settings/models",
    ):
        body = client.get(path).text
        assert SECRET not in body, f"{path} leaked the API key"


def test_keys_route_shows_only_a_hint() -> None:
    body = client.get("/api/settings/keys").json()
    gemini = next(k for k in body["keys"] if k["name"] == "GEMINI_API_KEY")
    assert gemini["is_set"] is True
    assert gemini["hint"] == "…here"
    assert "value" not in gemini and "ciphertext" not in gemini


def test_key_can_be_set_and_deleted() -> None:
    saved = client.put("/api/settings/keys/GEMINI_API_KEY", json={"value": "x" * 20})
    assert saved.status_code == 200
    body = client.delete("/api/settings/keys/GEMINI_API_KEY").json()
    gemini = next(k for k in body["keys"] if k["name"] == "GEMINI_API_KEY")
    assert gemini["is_set"] is False


def test_unknown_key_name_is_refused() -> None:
    r = client.put("/api/settings/keys/MY_BANK_PASSWORD", json={"value": "x" * 20})
    assert r.status_code == 400


def test_a_too_short_key_is_refused() -> None:
    assert client.put("/api/settings/keys/GEMINI_API_KEY", json={"value": "abc"}).status_code == 422


def test_encryption_key_location_is_reported_not_its_value() -> None:
    body = client.get("/api/settings/keys").json()
    assert "encryption_key_location" in body
    assert SECRET not in json.dumps(body)


# --- money is never spent by accident -------------------------------------


def test_estimate_is_free_and_states_the_cost() -> None:
    body = client.get(
        "/api/ai/estimate", params={"feature": "poster-artwork", "batch": True}
    ).json()
    assert body["cost_rupees"] == 6.0
    # The name itself is a setting, not a constant — pinning a literal here is
    # what let three models that never existed ship in the first place.
    assert body["model"] == ai.model_for("artwork")


def test_estimate_shows_batch_is_half_price() -> None:
    def quote(batch: bool) -> dict[str, Any]:
        return client.get(
            "/api/ai/estimate", params={"feature": "poster-artwork", "batch": batch}
        ).json()

    instant, batch = quote(False), quote(True)
    assert batch["cost_paise"] * 2 == pytest.approx(instant["cost_paise"], rel=0.05)


def test_unknown_feature_is_refused() -> None:
    assert client.get("/api/ai/estimate", params={"feature": "buy-me-a-car"}).status_code == 422


def test_status_reports_the_budget() -> None:
    body = client.get("/api/ai/status").json()
    assert body["budget"]["budget_rupees"] == 2000.0
    assert body["budget"]["is_estimate"] is True
    assert body["configured"] is True


# --- paid calls -----------------------------------------------------------


def test_artwork_returns_an_image_and_its_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client(monkeypatch, response=FakeResponse([FakePart(data=b"PNGBYTES")]))
    body = client.post("/api/ai/artwork", json={"subject": "a temple at dusk"}).json()
    assert body["ok"] is True
    assert base64.b64decode(body["image"]) == b"PNGBYTES"
    assert body["cost_rupees"] == 6.0
    assert any("SynthID" in w for w in body["warnings"])


def test_a_refused_call_is_200_with_ok_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exit gate: a failure must never lose the operator's work.

    An HTTP error would make the browser discard the form; a 200 with `ok:false`
    keeps everything on screen and explains what happened.
    """
    fake_client(monkeypatch, raises=RuntimeError("blocked by safety filters"))
    response = client.post("/api/ai/artwork", json={"subject": "something refused"})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "refused" in body["error"].lower()
    assert body["cost_rupees"] == 0


def test_photo_edit_sends_the_image_and_reports_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client(monkeypatch, response=FakeResponse([FakePart(data=b"EDITED")]))
    body = client.post(
        "/api/ai/photo-edit",
        files={"file": ("client.png", png(), "image/png")},
        data={"instruction": "remove the plastic chair"},
    ).json()
    assert body["ok"] is True
    assert body["cost_rupees"] == 4.0
    assert "remove the plastic chair" in body["prompt_used"]


def test_photo_edit_rejects_a_non_image() -> None:
    r = client.post(
        "/api/ai/photo-edit",
        files={"file": ("x.png", b"not an image", "image/png")},
        data={"instruction": "brighten"},
    )
    assert r.status_code == 400


def test_layout_plan_returns_data_never_an_image(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = {"blocks": [{"id": "headline", "text": "കേരളം സെയിൽ", "x": 0.1, "y": 0.1}]}
    fake_client(monkeypatch, response=FakeResponse([], text=json.dumps(plan)))
    body = client.post("/api/ai/layout-plan", json={"headline": "കേരളം സെയിൽ"}).json()
    assert body["ok"] is True
    assert "image" not in body
    assert body["layout"]["blocks"][0]["text"] == "കേരളം സെയിൽ"


def test_layout_plan_restores_wording_the_ai_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = {"blocks": [{"id": "phone", "text": "9847 999 999"}]}
    fake_client(monkeypatch, response=FakeResponse([], text=json.dumps(plan)))
    body = client.post(
        "/api/ai/layout-plan", json={"headline": "Sale", "phone": "9847 000 000"}
    ).json()
    assert body["layout"]["blocks"][0]["text"] == "9847 000 000"
    assert body["warnings"]


def test_every_paid_response_carries_the_running_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """So the UI can show the total moving without a second request."""
    fake_client(monkeypatch, response=FakeResponse([FakePart(data=b"X")]))
    body = client.post("/api/ai/artwork", json={"subject": "a temple"}).json()
    assert "budget" in body
    assert body["budget"]["budget_rupees"] == 2000.0


# --- prompt library -------------------------------------------------------


def test_prompts_are_seeded_and_described() -> None:
    body = client.get("/api/settings/prompts").json()
    assert {s["key"] for s in body["scopes"]} == {
        "poster-layout",
        "poster-artwork",
        "photo-edit",
    }
    assert len(body["prompts"]) >= 3


def test_validation_catches_an_unknown_variable() -> None:
    body = client.post(
        "/api/settings/prompts/validate",
        json={"scope": "photo-edit", "name": "x", "body": "Do {{nonsense}}"},
    ).json()
    assert body["problems"]


def test_saving_and_restoring_a_default() -> None:
    listing = client.get("/api/settings/prompts", params={"scope": "photo-edit"}).json()
    default = next(p for p in listing["prompts"] if p["is_default"])

    client.put(
        "/api/settings/prompts",
        json={
            "scope": "photo-edit",
            "name": default["name"],
            "body": "ruined",
            "prompt_id": default["id"],
        },
    )
    restored = client.post(
        f"/api/settings/prompts/{default['id']}/restore-default"
    ).json()
    assert restored["prompt"]["body"] == prompts.SEEDS["photo-edit"]


def test_version_history_is_available() -> None:
    saved = client.put(
        "/api/settings/prompts",
        json={"scope": "photo-edit", "name": "Versioned", "body": "Change: {{instruction}} one"},
    ).json()["prompt"]
    client.put(
        "/api/settings/prompts",
        json={
            "scope": "photo-edit",
            "name": "Versioned",
            "body": "Change: {{instruction}} two",
            "prompt_id": saved["id"],
        },
    )
    body = client.get(f"/api/settings/prompts/{saved['id']}/versions").json()
    assert len(body["versions"]) == 1


def test_the_shipped_default_cannot_be_deleted() -> None:
    listing = client.get("/api/settings/prompts", params={"scope": "photo-edit"}).json()
    default = next(p for p in listing["prompts"] if p["is_default"])
    assert client.delete(f"/api/settings/prompts/{default['id']}").status_code == 400


def test_unknown_prompt_is_404() -> None:
    assert client.get("/api/settings/prompts/999999/versions").status_code == 404
