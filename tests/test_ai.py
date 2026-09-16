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
import os
from hashlib import sha256
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
    instant = ai.cost_of(ai.POSTER_MODEL, batch=False)
    batch = ai.cost_of(ai.POSTER_MODEL, batch=True)
    assert batch < instant
    assert batch == pytest.approx(instant / 2, rel=0.05)


def test_costs_are_whole_paise() -> None:
    """Currency in floats drifts; this total is compared to Google's console."""
    for value in ai.RATES_PAISE.values():
        assert isinstance(value, int)


def test_estimate_is_free_and_states_the_price() -> None:
    quote = ai.estimate("poster", batch=True)
    assert quote["cost_paise"] == 600
    assert quote["cost_rupees"] == 6.0
    assert quote["batch"] is True


def test_photo_edit_defaults_cheaper_than_artwork() -> None:
    assert ai.estimate("photo-edit", False)["cost_paise"] < ai.estimate(
        "poster", False
    )["cost_paise"]


# --- spend tracking -------------------------------------------------------


def test_only_successful_calls_count_as_spend(monkeypatch: pytest.MonkeyPatch) -> None:
    """A refused request costs nothing, so counting it would disagree with Google."""
    before = ai.budget_status()["total_paise"]

    fake(monkeypatch, response=FakeResponse([FakePart(data=b"PNGDATA")]))
    ai.generate_poster({"main": "Onam Sale"}, batch=True)
    after_success = ai.budget_status()
    assert after_success["total_paise"] == before + 600

    fake(monkeypatch, raises=RuntimeError("safety: blocked"))
    ai.generate_poster({"main": "Onam Sale"}, batch=True)
    after_failure = ai.budget_status()
    assert after_failure["total_paise"] == after_success["total_paise"]
    assert after_failure["failed_runs"] >= 1


def test_budget_reports_against_two_thousand_rupees() -> None:
    status = ai.budget_status()
    assert status["budget_paise"] == 200_000
    assert status["budget_rupees"] == 2000.0
    assert status["is_estimate"] is True, "the UI must not present this as the truth"


def test_budget_fraction_never_exceeds_one(monkeypatch: pytest.MonkeyPatch) -> None:
    db.record_spend("poster", ai.POSTER_MODEL, 500_000, True, "ok")
    status = ai.budget_status()
    assert status["fraction_used"] == 1.0
    assert status["over_budget"] is True


# --- failure never loses work ---------------------------------------------


def test_missing_key_is_explained_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    db.delete_api_key("GEMINI_API_KEY")
    result = ai.generate_poster({"main": "Onam Sale"})
    assert result.ok is False
    assert "Settings" in (result.error or "")


def test_no_charge_recorded_when_the_key_is_missing() -> None:
    db.delete_api_key("GEMINI_API_KEY")
    before = ai.budget_status()["runs"] + ai.budget_status()["failed_runs"]
    ai.generate_poster({"main": "Onam Sale"})
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
    result = ai.generate_poster({"main": "Onam Sale"})
    assert result.ok is False
    assert expected in (result.error or "").lower()


def test_empty_response_is_not_charged(monkeypatch: pytest.MonkeyPatch) -> None:
    fake(monkeypatch, response=FakeResponse([FakePart(text="sorry")]))
    result = ai.generate_poster({"main": "Onam Sale"})
    assert result.ok is False
    assert result.cost_paise == 0
    assert "no poster" in (result.error or "").lower()


def test_missing_instruction_never_reaches_the_api(monkeypatch: pytest.MonkeyPatch) -> None:
    client = fake(monkeypatch, response=FakeResponse([FakePart(data=b"X")]))
    result = ai.edit_photo(b"fakeimage", "image/png", {"instruction": "  "})
    assert result.ok is False
    assert client.models.calls == [], "no request should have been sent"


# --- honest disclosure ----------------------------------------------------


def test_synthid_watermark_is_disclosed(monkeypatch: pytest.MonkeyPatch) -> None:
    """PRD.md lists this as something to tell a client about."""
    fake(monkeypatch, response=FakeResponse([FakePart(data=b"PNG")]))
    result = ai.generate_poster({"main": "Onam Sale"})
    assert any("SynthID" in w for w in result.warnings)


# --- prompt library -------------------------------------------------------


def test_defaults_are_seeded_for_every_scope() -> None:
    scopes = {p["scope"] for p in prompts.list_prompts()}
    # Derived, not hardcoded: the point is that every declared scope has a
    # shipped default to restore to, which stays true as scopes are added.
    assert scopes == {s.key for s in prompts.SCOPES}
    assert "poster-concept" in scopes


def test_a_retired_scopes_shipped_template_is_cleared_out() -> None:
    """ADR-034 retired three poster scopes. Their seeded rows stayed behind in
    every database that had already run, listed under a scope the screen has no
    dropdown for and no default left to restore."""
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO prompts(scope, name, body, is_default, is_active, updated_at) "
            "VALUES('poster-layout', 'Default', 'old', 1, 1, '2026-01-01') "
            "ON CONFLICT(scope, name) DO NOTHING"
        )
    prompts.seed_defaults()
    scopes = {p["scope"] for p in prompts.list_prompts()}
    assert "poster-layout" not in scopes


def test_a_prompt_the_operator_wrote_survives_its_scope_being_retired() -> None:
    """It is theirs. Deleting it to tidy up would be a poor trade."""
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO prompts(scope, name, body, is_default, is_active, updated_at) "
            "VALUES('poster-layout', 'Mine', 'my own wording', 0, 0, '2026-01-01') "
            "ON CONFLICT(scope, name) DO NOTHING"
        )
    prompts.seed_defaults()
    kept = [p for p in prompts.list_prompts() if p["name"] == "Mine"]
    assert kept, "the operator's own template was deleted"


def test_seeding_twice_does_not_duplicate() -> None:
    prompts.seed_defaults()
    prompts.seed_defaults()
    layout = [p for p in prompts.list_prompts("poster-concept") if p["is_default"]]
    assert len(layout) == 1


def test_unknown_variable_is_caught_at_edit_time() -> None:
    problems = prompts.validate("photo-edit", "Change {{instruction}} to {{colour}}")
    assert problems
    assert "colour" in problems[0]


def test_missing_required_variable_is_caught() -> None:
    problems = prompts.validate("poster-concept", "Make something nice")
    assert any("main" in p for p in problems)


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


# --- the budget is a limit, not a decoration (NEXT.md 1.1) ----------------


def image_client(monkeypatch: pytest.MonkeyPatch, data: bytes | None = b"PNG") -> FakeClient:
    """A client whose reply carries an image, or a reply with no image at all."""
    return fake(monkeypatch, response=FakeResponse([FakePart(data=data)]))


def spend(paise: int) -> None:
    """Put a real amount into the ledger."""
    db.record_spend(
        feature="poster", model="m", cost_paise=paise, batch=False, status="ok"
    )


def test_over_budget_refuses_a_paid_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """`over_budget` was consumed only by the colour of a bar in Settings.

    No paid route consulted it, so /ai/artwork, /ai/photo-edit and
    /ai/layout-plan would spend past ₹2,000 indefinitely.
    """
    client = image_client(monkeypatch)
    spend(ai.MONTHLY_BUDGET_PAISE + 1)

    result = ai.generate_poster({"main": "Onam Sale"})
    assert result.ok is False
    assert "budget" in (result.error or "").lower()
    assert result.cost_paise == 0
    # And nothing was sent: a refusal must not cost anything.
    assert client.models.calls == []


def test_over_budget_refuses_every_paid_route(monkeypatch: pytest.MonkeyPatch) -> None:
    """All of them, not just the one that was easiest to check."""
    image_client(monkeypatch)
    spend(ai.MONTHLY_BUDGET_PAISE + 1)

    photo = ai.edit_photo(b"x", "image/png", {"instruction": "tidy it"})
    assert photo.ok is False and "budget" in (photo.error or "").lower()

    concept = ai.poster_concept({"main": "SALE"})
    assert concept.ok is False and "budget" in (concept.error or "").lower()

    changed = ai.refine_poster(b"x", "image/png", "darker")
    assert changed.ok is False and "budget" in (changed.error or "").lower()


def test_the_operator_can_deliberately_spend_past_the_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It is the shop's money. The ceiling must be passable — just not by accident."""
    image_client(monkeypatch)
    spend(ai.MONTHLY_BUDGET_PAISE + 1)

    result = ai.generate_poster({"main": "Onam Sale"}, over_budget_ok=True)
    assert result.ok is True


def test_inside_the_budget_nothing_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    image_client(monkeypatch)
    spend(100)
    assert ai.generate_poster({"main": "Onam Sale"}).ok is True


# --- auto-repair must not pick a nonsense model (NEXT.md 1.3) -------------


def test_last_resort_never_picks_an_embedding_model() -> None:
    """The old rule was "first model alphabetically without 'image' in its name".

    Combined with a `supported_actions` filter that deliberately admits models
    reporting no actions, an embedding model could win the layout role.
    """
    live = [
        {"name": "embedding-001", "image_output": False},
        {"name": "gemini-embedding-exp", "image_output": False},
        {"name": "gemini-2.5-flash", "image_output": False},
        {"name": "text-bison-001", "image_output": False},
    ]
    role = ai.ROLE_BY_KEY["concept"]
    assert ai._safe_last_resort(role, live) == "gemini-2.5-flash"


def test_last_resort_matches_the_shape_the_role_needs() -> None:
    live = [
        {"name": "gemini-2.5-flash", "image_output": False},
        {"name": "gemini-2.5-flash-image", "image_output": True},
    ]
    assert (
        ai._safe_last_resort(ai.ROLE_BY_KEY["poster"], live) == "gemini-2.5-flash-image"
    )


def test_last_resort_refuses_rather_than_guessing() -> None:
    """Nothing usable means say so, not pick the least-bad thing available."""
    live = [
        {"name": "embedding-001", "image_output": False},
        {"name": "gemini-2.5-tts", "image_output": False},
        {"name": "veo-3", "image_output": False},
    ]
    assert ai._safe_last_resort(ai.ROLE_BY_KEY["concept"], live) is None


# --- batch that saves nothing (NEXT.md 1.4) ------------------------------


def test_only_artwork_has_a_batch_discount() -> None:
    """The docstring promises "half price for a few minutes' wait".

    True for artwork. For photo editing and layout planning it was a wait for
    nothing, and the toggle was offered anyway.
    """
    assert ai.has_batch_discount("poster") is True
    assert ai.has_batch_discount("photo-edit") is False
    assert ai.has_batch_discount("poster-concept") is False


def test_the_estimate_says_whether_batch_saves_anything() -> None:
    artwork = ai.estimate("poster", batch=True)
    assert artwork["batch_discount"] is True
    assert artwork["cost_paise"] < artwork["instant_paise"]

    photo = ai.estimate("photo-edit", batch=True)
    assert photo["batch_discount"] is False
    assert photo["cost_paise"] == photo["instant_paise"]
    assert photo["batch_paise"] is None


# --- honest billing language ----------------------------------------------


def test_a_reply_with_no_image_does_not_claim_it_was_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The app cannot know. Google answered, so it may well have billed.

    Phase 5's gate wants the ledger to reconcile with Google's console to within
    a few rupees; asserting "nothing was charged" here drifts one way.
    """
    image_client(monkeypatch, data=None)
    result = ai.generate_poster({"main": "Onam Sale"})
    assert result.ok is False
    error = (result.error or "").lower()
    assert "nothing was charged" not in error
    assert "may still have been" in error and "console" in error


# --- a key must never reach the screen (NEXT.md 3.17) --------------------


def test_a_raw_sdk_exception_is_redacted_before_it_is_shown() -> None:
    """`_friendly`'s fallback echoed 200 chars of the raw exception to the UI.

    That made it the one path in the app where a credential could reach the
    screen, against SECURITY.md §2 — which says a key is never logged and never
    in an error message. The SDK usually does not include the key; "usually" is
    not the standard for the only thing here with direct monetary value.
    """
    leaked = "AQ.Ab8RN6JxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxYZ"
    message = ai.friendly_error(RuntimeError(f"internal error calling {leaked}"))
    assert leaked not in message
    assert "[redacted]" in message


@pytest.mark.parametrize(
    "secret",
    [
        "AQ.Ab8RN6Jabcdefghijklmnopqrstuvwxyz012345",
        "AIzaSyDabcdefghijklmnopqrstuvwxyz01234567",
        "ya29.a0AfB_byC1234567890abcdefghijklmnop",
        "Bearer abcdef1234567890ABCDEF",
        "api_key=abcdef1234567890ABCDEF",
    ],
)
def test_every_credential_shape_is_redacted(secret: str) -> None:
    message = ai.friendly_error(RuntimeError(f"boom: {secret} at line 4"))
    assert secret not in message, f"{secret} survived redaction"


def test_redaction_does_not_eat_the_useful_part_of_the_message() -> None:
    """An error nobody can read is not an improvement on one that leaks."""
    message = ai.friendly_error(RuntimeError("upstream returned 503 unavailable"))
    assert "503" in message
    assert "unavailable" in message


def test_friendly_error_is_the_public_name() -> None:
    """api/ai.py reached across the module boundary for `_friendly` twice."""
    assert ai.friendly_error(RuntimeError("deadline exceeded")) == ai._friendly(  # noqa: SLF001
        RuntimeError("deadline exceeded")
    )


def test_an_improved_shipped_prompt_reaches_a_database_that_already_ran() -> None:
    """Otherwise a better default only ever helps a fresh install.

    The poster engine's rules live in these seeds (ADR-036). Pinning them is
    pointless if the shop's own `data.db`, seeded months ago, keeps the old
    wording forever — and "delete data.db" is not an upgrade path for a machine
    with nobody to run it.
    """
    with db.cursor() as cur:
        cur.execute(
            "UPDATE prompts SET body = 'stale shipped wording' "
            "WHERE scope = 'poster-concept' AND is_default = 1"
        )
    prompts.seed_defaults()
    assert prompts.active_prompt("poster-concept")["body"] == prompts.SEEDS["poster-concept"]


def test_a_default_the_operator_edited_is_never_overwritten_by_an_update() -> None:
    """Their wording is theirs, even in the row marked as the shipped default.

    `save_prompt` files the previous body under `prompt_versions` on every
    change, so an edit leaves a trace — and that trace is what tells the seeder
    to keep its hands off.
    """
    default = next(
        p for p in prompts.list_prompts("poster-concept") if p["is_default"]
    )
    prompts.save_prompt(
        "poster-concept", default["name"], "my own wording {{main}}", default["id"]
    )

    prompts.seed_defaults()

    assert prompts.get_prompt(default["id"])["body"] == "my own wording {{main}}"


def test_the_poster_change_template_is_editable_like_any_other() -> None:
    """The freeze clause is long, and the operator has to be able to tune it."""
    assert "poster-edit" in prompts.SCOPE_KEYS
    body = prompts.active_prompt("poster-edit")["body"]
    assert "{{note}}" in body
    assert prompts.validate("poster-edit", body) == []


def test_a_restored_default_still_picks_up_a_later_improvement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A version history is not proof the operator wrote anything.

    This is the case that actually happened. Pressing "Restore default", or a
    round trip through the editor that changed nothing, files a row in
    `prompt_versions` while leaving shipped text in place. Treating that as an
    edit froze the shop's own database on the pre-v1.1 concept prompt, and the
    only way out was a button nobody knew to press.

    So a body that still matches something this app shipped is the app's to
    replace, whatever its history says. The retired text is recognised by hash
    rather than kept in the source, so this test registers its own.
    """
    retired = "an older shipped wording {{main}}"
    monkeypatch.setitem(
        prompts.SUPERSEDED_SEEDS,
        "poster-concept",
        frozenset({sha256(retired.encode("utf-8")).hexdigest()}),
    )

    default = next(p for p in prompts.list_prompts("poster-concept") if p["is_default"])
    prompts.save_prompt("poster-concept", default["name"], retired, default["id"])
    assert prompts.versions(default["id"]), "the setup did not create a version history"

    prompts.seed_defaults()

    assert prompts.get_prompt(default["id"])["body"] == prompts.SEEDS["poster-concept"]


def test_the_hash_recorded_for_the_retired_concept_prompt_is_a_real_one() -> None:
    """A typo'd hash matches nothing and fails silently forever.

    It cannot be checked against the text — that text is deliberately gone —
    but it can be checked for being a SHA-256 at all, which catches a truncated
    or mistyped paste.
    """
    for scope, digests in prompts.SUPERSEDED_SEEDS.items():
        assert scope in prompts.SCOPE_KEYS, f"{scope} is not a live scope"
        for digest in digests:
            assert len(digest) == 64 and set(digest) <= set("0123456789abcdef")


# --- the Hugging Face token (gated models) ---------------------------------


def test_the_hugging_face_token_is_stored_like_any_other_key() -> None:
    """Encrypted at rest, and only its last four characters ever come back out.

    It is not a paid key, but it is a live credential on the operator's Hugging
    Face account, and "it only unlocks a model repo" is not a reason to hold it
    more loosely than the ones that cost money.
    """
    db.set_api_key("HF_TOKEN", "hf_thisisnotarealtokenatall")
    with db.cursor() as cur:
        row = cur.execute(
            "SELECT ciphertext, hint FROM api_keys WHERE name = 'HF_TOKEN'"
        ).fetchone()

    assert b"hf_thisisnotarealtokenatall" not in row["ciphertext"]
    assert row["hint"] == "…tall"
    assert json.dumps(db.list_api_keys()).count("hf_thisisnotarealtokenatall") == 0
    assert db.get_api_key("HF_TOKEN") == "hf_thisisnotarealtokenatall"


def test_a_stored_token_reaches_transformers(monkeypatch: pytest.MonkeyPatch) -> None:
    """huggingface_hub reads HF_TOKEN from the environment and nowhere else.

    Before this, the only way to set it was hand-editing a `.env` beside the app
    — on a machine whose operator is not a programmer.
    """
    from backend import models as model_store

    monkeypatch.delenv("HF_TOKEN", raising=False)
    db.set_api_key("HF_TOKEN", "hf_from_the_settings_screen")

    model_store._use_stored_token()

    assert os.environ["HF_TOKEN"] == "hf_from_the_settings_screen"


def test_an_exported_token_is_not_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    """Somebody who exported HF_TOKEN for one job meant it.

    Silently preferring the database would be the harder failure to explain, and
    it is the one that happens at 9pm before a deadline.
    """
    from backend import models as model_store

    monkeypatch.setenv("HF_TOKEN", "hf_exported_for_this_run")
    db.set_api_key("HF_TOKEN", "hf_stored_in_settings")

    model_store._use_stored_token()

    assert os.environ["HF_TOKEN"] == "hf_exported_for_this_run"


def test_no_stored_token_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """The overwhelmingly common case: nobody has ever set one."""
    from backend import models as model_store

    monkeypatch.delenv("HF_TOKEN", raising=False)
    db.delete_api_key("HF_TOKEN")

    model_store._use_stored_token()  # must not raise

    assert "HF_TOKEN" not in os.environ
