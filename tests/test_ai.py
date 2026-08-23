"""Tests for Phase 5.

**No test here makes a real API call.** The Gemini client is faked, so the suite
never spends money and runs offline. What that leaves untested is exactly one
thing — whether Google's live responses match the shapes we parse — and that is
called out in ROADMAP.md rather than pretended away.

The guarantees worth testing are all local: a key never leaves the server, a
failure never loses work, costs are exact, and refused calls are not counted as
spend.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from backend import crypto, db
from backend.features import ai, prompts

# --- fakes ----------------------------------------------------------------


class FakePart:
    def __init__(self, data: bytes | None = None, text: str | None = None) -> None:
        self.inline_data = _Inline(data) if data else None
        self.text = text


class _Inline:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.mime_type = "image/png"


class FakeResponse:
    def __init__(self, parts: list[FakePart], text: str | None = None) -> None:
        self.candidates = [type("C", (), {"content": type("X", (), {"parts": parts})()})()]
        self.text = text


class FakeModels:
    def __init__(self, response: Any = None, raises: Exception | None = None) -> None:
        self._response = response
        self._raises = raises
        self.calls: list[dict[str, Any]] = []

    def generate_content(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self._raises:
            raise self._raises
        return self._response


class FakeClient:
    def __init__(self, response: Any = None, raises: Exception | None = None) -> None:
        self.models = FakeModels(response, raises)


@pytest.fixture(autouse=True)
def a_key() -> None:
    """A fake key, so the configured path is exercised without a real one."""
    db.set_api_key("GEMINI_API_KEY", "fake-key-for-tests-0000")


@pytest.fixture(autouse=True)
def seeded() -> None:
    prompts.seed_defaults()


def fake(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> FakeClient:
    client = FakeClient(**kwargs)
    monkeypatch.setattr(ai, "_client", lambda: client)
    return client


# --- keys never leave the server ------------------------------------------


def test_stored_key_is_encrypted_not_plaintext() -> None:
    db.set_api_key("GEMINI_API_KEY", "AIza-super-secret-value")
    with db.cursor() as cur:
        row = cur.execute(
            "SELECT ciphertext, hint FROM api_keys WHERE name = 'GEMINI_API_KEY'"
        ).fetchone()
    assert b"AIza-super-secret-value" not in row["ciphertext"]
    assert row["hint"] == "…alue"


def test_listing_keys_never_includes_the_key() -> None:
    db.set_api_key("GEMINI_API_KEY", "AIza-super-secret-value")
    blob = json.dumps(db.list_api_keys(), ensure_ascii=False)
    assert "AIza-super-secret-value" not in blob
    assert "…alue" in blob, "the hint is the only form allowed out"


def test_key_round_trips_for_server_side_use() -> None:
    db.set_api_key("GEMINI_API_KEY", "AIza-round-trip")
    assert db.get_api_key("GEMINI_API_KEY") == "AIza-round-trip"


def test_unknown_key_name_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown key"):
        db.set_api_key("MY_BANK_PASSWORD", "nope")


def test_blank_key_is_refused() -> None:
    with pytest.raises(ValueError, match="cannot be blank"):
        db.set_api_key("GEMINI_API_KEY", "   ")


def test_hint_reveals_only_four_characters() -> None:
    assert crypto.hint("abcdefghijklmnop") == "…mnop"


def test_replacing_a_key_clears_its_test_result() -> None:
    db.set_api_key("GEMINI_API_KEY", "first-key-value")
    db.record_key_test("GEMINI_API_KEY", True)
    db.set_api_key("GEMINI_API_KEY", "second-key-value")
    row = next(k for k in db.list_api_keys() if k["name"] == "GEMINI_API_KEY")
    assert row["last_test_ok"] is None, "a new key must be re-tested"


def test_deleting_a_key_leaves_nothing_behind() -> None:
    db.set_api_key("GEMINI_API_KEY", "to-be-deleted")
    db.delete_api_key("GEMINI_API_KEY")
    assert db.get_api_key("GEMINI_API_KEY") is None


# --- costs are exact ------------------------------------------------------


def test_batch_artwork_is_half_price() -> None:
    instant = ai.cost_of(ai.ARTWORK_MODEL, batch=False)
    batch = ai.cost_of(ai.ARTWORK_MODEL, batch=True)
    assert batch < instant
    assert batch == pytest.approx(instant / 2, rel=0.05)


def test_costs_are_whole_paise() -> None:
    """Currency in floats drifts; this total is compared to Google's console."""
    for value in ai.RATES_PAISE.values():
        assert isinstance(value, int)


def test_estimate_is_free_and_states_the_price() -> None:
    quote = ai.estimate("poster-artwork", batch=True)
    assert quote["cost_paise"] == 600
    assert quote["cost_rupees"] == 6.0
    assert quote["batch"] is True


def test_photo_edit_defaults_cheaper_than_artwork() -> None:
    assert ai.estimate("photo-edit", False)["cost_paise"] < ai.estimate(
        "poster-artwork", False
    )["cost_paise"]


# --- spend tracking -------------------------------------------------------


def test_only_successful_calls_count_as_spend(monkeypatch: pytest.MonkeyPatch) -> None:
    """A refused request costs nothing, so counting it would disagree with Google."""
    before = ai.budget_status()["total_paise"]

    fake(monkeypatch, response=FakeResponse([FakePart(data=b"PNGDATA")]))
    ai.generate_artwork({"subject": "a temple at dusk"}, batch=True)
    after_success = ai.budget_status()
    assert after_success["total_paise"] == before + 600

    fake(monkeypatch, raises=RuntimeError("safety: blocked"))
    ai.generate_artwork({"subject": "something refused"}, batch=True)
    after_failure = ai.budget_status()
    assert after_failure["total_paise"] == after_success["total_paise"]
    assert after_failure["failed_runs"] >= 1


def test_budget_reports_against_two_thousand_rupees() -> None:
    status = ai.budget_status()
    assert status["budget_paise"] == 200_000
    assert status["budget_rupees"] == 2000.0
    assert status["is_estimate"] is True, "the UI must not present this as the truth"


def test_budget_fraction_never_exceeds_one(monkeypatch: pytest.MonkeyPatch) -> None:
    db.record_spend("poster-artwork", ai.ARTWORK_MODEL, 500_000, True, "ok")
    status = ai.budget_status()
    assert status["fraction_used"] == 1.0
    assert status["over_budget"] is True


# --- failure never loses work ---------------------------------------------


def test_missing_key_is_explained_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    db.delete_api_key("GEMINI_API_KEY")
    result = ai.generate_artwork({"subject": "anything"})
    assert result.ok is False
    assert "Settings" in (result.error or "")


def test_no_charge_recorded_when_the_key_is_missing() -> None:
    db.delete_api_key("GEMINI_API_KEY")
    before = ai.budget_status()["runs"] + ai.budget_status()["failed_runs"]
    ai.generate_artwork({"subject": "anything"})
    after = ai.budget_status()["runs"] + ai.budget_status()["failed_runs"]
    assert after == before, "a call that never left the machine is not a run"


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (RuntimeError("401 unauthorized: bad api key"), "rejected"),
        (RuntimeError("RESOURCE_EXHAUSTED: quota"), "quota"),
        (RuntimeError("blocked by safety filters"), "refused"),
        (RuntimeError("deadline exceeded"), "timed out"),
        (RuntimeError("failed to connect: dns"), "internet"),
    ],
)
def test_sdk_errors_become_plain_english(
    monkeypatch: pytest.MonkeyPatch, raised: Exception, expected: str
) -> None:
    fake(monkeypatch, raises=raised)
    result = ai.generate_artwork({"subject": "a temple"})
    assert result.ok is False
    assert expected in (result.error or "").lower()


def test_empty_response_is_not_charged(monkeypatch: pytest.MonkeyPatch) -> None:
    fake(monkeypatch, response=FakeResponse([FakePart(text="sorry")]))
    result = ai.generate_artwork({"subject": "a temple"})
    assert result.ok is False
    assert result.cost_paise == 0
    assert "no image" in (result.error or "").lower()


def test_missing_instruction_never_reaches_the_api(monkeypatch: pytest.MonkeyPatch) -> None:
    client = fake(monkeypatch, response=FakeResponse([FakePart(data=b"X")]))
    result = ai.edit_photo(b"fakeimage", "image/png", {"instruction": "  "})
    assert result.ok is False
    assert client.models.calls == [], "no request should have been sent"


# --- the poster guarantee -------------------------------------------------


def test_layout_plan_returns_data_not_pixels(monkeypatch: pytest.MonkeyPatch) -> None:
    """The constraint the whole poster feature rests on."""
    plan = {"blocks": [{"id": "headline", "text": "കേരളം സെയിൽ", "x": 0.1, "y": 0.1}]}
    fake(monkeypatch, response=FakeResponse([], text=json.dumps(plan)))
    result = ai.plan_layout({"headline": "കേരളം സെയിൽ"})
    assert result.ok is True
    assert result.image is None, "layout planning must never return an image"
    assert result.layout is not None
    assert result.layout["blocks"][0]["text"] == "കേരളം സെയിൽ"


def test_ai_altering_the_operators_words_is_undone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A changed phone number or headline is not acceptable, ever."""
    plan = {
        "blocks": [
            {"id": "headline", "text": "GRAND SALE!!!", "x": 0.1, "y": 0.1},
            {"id": "phone", "text": "9847 000 111", "x": 0.1, "y": 0.8},
        ]
    }
    fake(monkeypatch, response=FakeResponse([], text=json.dumps(plan)))
    result = ai.plan_layout({"headline": "Grand Sale", "phone": "9847 000 000"})

    texts = {b["id"]: b["text"] for b in result.layout["blocks"]}
    assert texts["headline"] == "Grand Sale"
    assert texts["phone"] == "9847 000 000"
    assert len(result.warnings) >= 2


def test_layout_survives_a_code_fence(monkeypatch: pytest.MonkeyPatch) -> None:
    wrapped = '```json\n{"blocks": [{"id": "headline", "text": "Sale"}]}\n```'
    fake(monkeypatch, response=FakeResponse([], text=wrapped))
    result = ai.plan_layout({"headline": "Sale"})
    assert result.ok is True


@pytest.mark.parametrize(
    "text", ["not json at all", "{}", '{"blocks": "nope"}', "", "{oops"]
)
def test_a_bad_layout_plan_is_an_error_not_a_guess(
    monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    """Silently dropping a phone number is worse than a visible failure."""
    fake(monkeypatch, response=FakeResponse([], text=text))
    result = ai.plan_layout({"headline": "Sale"})
    assert result.ok is False


def test_synthid_watermark_is_disclosed(monkeypatch: pytest.MonkeyPatch) -> None:
    """PRD.md lists this as something to tell a client about."""
    fake(monkeypatch, response=FakeResponse([FakePart(data=b"PNG")]))
    result = ai.generate_artwork({"subject": "a temple"})
    assert any("SynthID" in w for w in result.warnings)


# --- prompt library -------------------------------------------------------


def test_defaults_are_seeded_for_every_scope() -> None:
    scopes = {p["scope"] for p in prompts.list_prompts()}
    assert scopes == {"poster-layout", "poster-artwork", "photo-edit"}


def test_seeding_twice_does_not_duplicate() -> None:
    prompts.seed_defaults()
    prompts.seed_defaults()
    layout = [p for p in prompts.list_prompts("poster-layout") if p["is_default"]]
    assert len(layout) == 1


def test_the_layout_default_forbids_text_in_images() -> None:
    """The one prompt that must not drift — it is the Malayalam guarantee."""
    body = prompts.SEEDS["poster-layout"]
    assert "JSON" in body
    assert "never place text into a picture" in body.lower()
    assert "exactly" in body.lower(), "the AI must be told not to reword copy"


def test_artwork_default_forbids_lettering() -> None:
    assert "no lettering" in prompts.SEEDS["poster-artwork"].lower()


def test_unknown_variable_is_caught_at_edit_time() -> None:
    problems = prompts.validate("photo-edit", "Change {{instruction}} to {{colour}}")
    assert problems
    assert "colour" in problems[0]


def test_missing_required_variable_is_caught() -> None:
    problems = prompts.validate("poster-layout", "Make something nice")
    assert any("headline" in p for p in problems)


def test_a_good_template_has_no_problems() -> None:
    assert prompts.validate("photo-edit", "Change: {{instruction}}") == []


def test_rendering_fills_variables() -> None:
    out = prompts.render("Change: {{instruction}}", {"instruction": "remove the chair"})
    assert out == "Change: remove the chair"


def test_rendering_drops_lines_left_empty() -> None:
    """An unfilled label reaching a paid call wastes money on a confused request."""
    out = prompts.render(
        "Change: {{instruction}}\nKeep unchanged: {{preserve}}",
        {"instruction": "brighten it", "preserve": ""},
    )
    assert "Keep unchanged" not in out
    assert "brighten it" in out


def test_editing_keeps_the_previous_version() -> None:
    saved = prompts.save_prompt("photo-edit", "Mine", "Change: {{instruction}} v1")
    prompts.save_prompt("photo-edit", "Mine", "Change: {{instruction}} v2", saved["id"])
    history = prompts.versions(saved["id"])
    assert len(history) == 1
    assert "v1" in history[0]["body"]


def test_saving_the_same_body_twice_adds_no_version() -> None:
    saved = prompts.save_prompt("photo-edit", "Same", "Change: {{instruction}}")
    prompts.save_prompt("photo-edit", "Same", "Change: {{instruction}}", saved["id"])
    assert prompts.versions(saved["id"]) == []


def test_restore_default_puts_the_shipped_text_back() -> None:
    default = next(p for p in prompts.list_prompts("photo-edit") if p["is_default"])
    prompts.save_prompt("photo-edit", default["name"], "ruined", default["id"])
    restored = prompts.restore_default(default["id"])
    assert restored["body"] == prompts.SEEDS["photo-edit"]


def test_the_shipped_default_cannot_be_deleted() -> None:
    default = next(p for p in prompts.list_prompts("photo-edit") if p["is_default"])
    with pytest.raises(prompts.PromptError, match="cannot be deleted"):
        prompts.delete_prompt(default["id"])


def test_activating_a_prompt_deactivates_its_siblings() -> None:
    mine = prompts.save_prompt("photo-edit", "Active one", "Change: {{instruction}}")
    prompts.set_active(mine["id"])
    active = [p for p in prompts.list_prompts("photo-edit") if p["is_active"]]
    assert [p["id"] for p in active] == [mine["id"]]


def test_the_active_prompt_is_what_a_feature_uses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mine = prompts.save_prompt("photo-edit", "Custom", "MY OWN: {{instruction}}")
    prompts.set_active(mine["id"])
    fake(monkeypatch, response=FakeResponse([FakePart(data=b"PNG")]))
    result = ai.edit_photo(b"img", "image/png", {"instruction": "brighten"})
    assert result.prompt_used.startswith("MY OWN: brighten")


def test_unknown_scope_is_refused() -> None:
    with pytest.raises(prompts.PromptError, match="unknown scope"):
        prompts.validate("make-me-rich", "anything")
