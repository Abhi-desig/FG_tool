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

# base_url is a loopback address deliberately: `guard.LocalOnlyMiddleware`
# refuses any Host that is not this machine, and TestClient's default
# `http://testserver` is exactly the DNS-rebinding shape it exists to stop.
client = TestClient(app, base_url="http://127.0.0.1:8000")

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
    # Derived, not hardcoded: what matters is that every scope the app declares
    # is described and seeded, which stays true as scopes are added.
    assert {s["key"] for s in body["scopes"]} == {s.key for s in prompts.SCOPES}
    assert {"poster-layout", "poster-artwork", "photo-edit"} <= {
        s["key"] for s in body["scopes"]
    }
    assert len(body["prompts"]) >= len(prompts.SCOPES)


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


# --- writing the poster's words (ADR-030) ----------------------------------


def _copy_body(**extra: object) -> dict[str, object]:
    return {"brief": "Onam sale, 50% off gold, Thrissur showroom", **extra}


def test_the_copy_prompt_route_is_free_and_states_the_cost() -> None:
    body = client.post("/api/ai/copy/prompt", json=_copy_body()).json()
    assert "Onam sale" in body["prompt"]
    assert body["estimate"]["cost_rupees"] > 0


def test_a_phone_number_never_reaches_the_prompt() -> None:
    """It is the field a wrong digit is most expensive on, and the model has no
    business writing it — so it is not in the request at all."""
    body = client.post(
        "/api/ai/copy/prompt", json=_copy_body(phone="9876543210")
    ).json()
    assert "9876543210" not in body["prompt"]


def test_the_operators_phone_is_put_back_into_every_alternative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_write(values, locked=None, over_budget_ok=False):
        return ai.AiResult(
            ok=True,
            feature="poster-copy",
            model="fake",
            batch=False,
            cost_paise=10,
            alternatives=[
                {
                    "id": "a",
                    "label": "A",
                    "blocks": [{"id": "headline", "text": "Onam Sale"}],
                    "blocks_ml": [{"id": "headline", "text": "ഓണം ഓഫർ"}],
                }
            ],
        )

    monkeypatch.setattr(ai, "write_copy", fake_write)
    body = client.post("/api/ai/copy", json=_copy_body(phone="9876543210")).json()
    for alt in body["alternatives"]:
        for key in ("blocks", "blocks_ml"):
            assert any(b["text"] == "9876543210" for b in alt[key]), key


def test_a_refused_copy_call_is_a_200_with_ok_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-023. A refusal must not lose the operator's work."""
    monkeypatch.setattr(
        ai,
        "write_copy",
        lambda *a, **k: ai.AiResult(
            ok=False,
            feature="poster-copy",
            model="fake",
            batch=False,
            error="nothing usable",
        ),
    )
    response = client.post("/api/ai/copy", json=_copy_body())
    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["error"] == "nothing usable"


def test_an_empty_brief_is_refused_by_validation() -> None:
    assert client.post("/api/ai/copy", json={"brief": ""}).status_code == 422


def test_a_brief_longer_than_the_bound_is_refused() -> None:
    """Cost is a flat per-call rate, so an unbounded field grows Google's bill
    while the budget meter does not move."""
    assert client.post("/api/ai/copy", json={"brief": "x" * 5000}).status_code == 422


def test_poster_copy_is_a_known_feature_for_the_estimate_route() -> None:
    body = client.get("/api/ai/estimate", params={"feature": "poster-copy"}).json()
    assert body["cost_paise"] > 0


def test_a_copy_model_can_be_chosen_in_settings() -> None:
    assert client.put("/api/ai/models", json={"copy": "gemini-2.5-pro"}).status_code == 200
    roles = {r["key"]: r for r in client.get("/api/ai/models").json()["roles"]}
    assert roles["copy"]["chosen"] == "gemini-2.5-pro"
    client.put("/api/ai/models", json={"copy": ""})
