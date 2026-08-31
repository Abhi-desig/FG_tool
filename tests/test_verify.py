"""Tests for the Claude check over the offline translation.

**No test here makes a real API call.** The Anthropic client is faked, so the
suite never spends money and runs offline — the same bargain as `test_ai.py`,
and it leaves the same one thing untested: whether Anthropic's live replies match
the shape we parse.

What is tested is the part that could quietly ruin a job. A member list is
33,000 rows the operator cannot read end to end, so a row filled in with another
member's name would ship. Every misalignment the model could hand back — a
missing row, a duplicated row, a row nobody asked for, a reply in the wrong
script, an empty correction — must leave the offline translation in place and
say so, never guess.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend import db, jobs
from backend.features import verify

# --- fakes ----------------------------------------------------------------


class FakeUsage:
    def __init__(self, input_tokens: int = 100, output_tokens: int = 50) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class FakeResponse:
    def __init__(self, parsed: Any, usage: FakeUsage | None = None) -> None:
        self.parsed_output = parsed
        self.usage = usage or FakeUsage()


class FakeMessages:
    """Answers each batch from a queue of replies, or raises."""

    def __init__(
        self, replies: list[Any] | None = None, raises: Exception | None = None
    ) -> None:
        self._replies = list(replies or [])
        self._raises = raises
        self.calls: list[dict[str, Any]] = []

    def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self._raises:
            raise self._raises
        return self._replies.pop(0)


class FakeClient:
    def __init__(self, **kwargs: Any) -> None:
        self.messages = FakeMessages(**kwargs)


@pytest.fixture(autouse=True)
def a_key() -> None:
    """A fake key, so the configured path is exercised without a real one."""
    db.set_api_key("ANTHROPIC_API_KEY", "sk-ant-fake-key-for-tests-0000")


def fake(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> FakeClient:
    client = FakeClient(**kwargs)
    monkeypatch.setattr(verify, "_client", lambda: client)
    return client


def batch(*rows: tuple[int, str, str]) -> verify.CheckedBatch:
    return verify.CheckedBatch(
        rows=[
            verify.CheckedRow(
                index=index,
                verdict="corrected" if text else "ok",
                malayalam=text,
                note=note,
            )
            for index, text, note in rows
        ]
    )


# --- the happy path -------------------------------------------------------


def test_a_correction_replaces_the_offline_translation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point: a fabricated house name gets written over."""
    reply = batch((0, "എലവുങ്കൽ വീട്, വടശ്ശേരിക്കര", ""))
    fake(monkeypatch, replies=[FakeResponse(reply)])

    result = verify.check_rows([("ELAVUNKAL VEEDU,VADASERIKARA", "യൂക്കാലിപ് റ്റസ്")])

    assert result.verdicts[0].translation == "എലവുങ്കൽ വീട്, വടശ്ശേരിക്കര"
    assert result.verdicts[0].corrected is True
    assert result.verdicts[0].checked is True
    assert result.corrected == 1
    assert result.error is None


def test_an_ok_verdict_keeps_what_the_offline_model_said(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reply = verify.CheckedBatch(
        rows=[verify.CheckedRow(index=0, verdict="ok", malayalam="")]
    )
    fake(monkeypatch, replies=[FakeResponse(reply)])

    result = verify.check_rows([("Sri. T.P ABRAHAM", "ശ്രീ. ടി. പി. അബ്രഹാം")])

    assert result.verdicts[0].translation == "ശ്രീ. ടി. പി. അബ്രഹാം"
    assert result.verdicts[0].corrected is False
    assert result.verdicts[0].checked is True


def test_rows_are_returned_in_the_order_they_were_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Order is the caller's contract — it zips these against its own rows."""
    # Deliberately answered back to front.
    reply = batch((2, "മൂന്ന്", ""), (0, "ഒന്ന്", ""), (1, "രണ്ട്", ""))
    fake(monkeypatch, replies=[FakeResponse(reply)])

    result = verify.check_rows(
        [("One name", "a"), ("Two name", "b"), ("Three name", "c")]
    )

    assert [v.translation for v in result.verdicts] == ["ഒന്ന്", "രണ്ട്", "മൂന്ന്"]


# --- misalignment: every one of these must fail closed --------------------


def test_a_row_the_model_never_returned_keeps_its_translation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dropped-row case. Flagged, never filled in from a neighbour."""
    reply = batch((0, "ഒന്ന്", ""))  # row 1 simply missing
    fake(monkeypatch, replies=[FakeResponse(reply)])

    result = verify.check_rows([("One name", "a"), ("Two name", "b")])

    assert result.verdicts[1].translation == "b"
    assert result.verdicts[1].checked is False
    assert result.unchecked == 1
    assert "did not return this row" in result.verdicts[1].note


def test_an_index_that_was_never_sent_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An invented row number must not land on any real row."""
    reply = batch((0, "ഒന്ന്", ""), (7, "എവിടെ നിന്ന്?", ""))
    fake(monkeypatch, replies=[FakeResponse(reply)])

    result = verify.check_rows([("One name", "a"), ("Two name", "b")])

    assert result.verdicts[0].translation == "ഒന്ന്"
    assert result.verdicts[1].translation == "b"
    assert result.verdicts[1].checked is False


def test_a_repeated_index_does_not_overwrite_the_first_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reply = batch((0, "ആദ്യത്തേത്", ""), (0, "രണ്ടാമത്തേത്", ""))
    fake(monkeypatch, replies=[FakeResponse(reply)])

    result = verify.check_rows([("One name", "a")])

    assert result.verdicts[0].translation == "ആദ്യത്തേത്"


def test_a_correction_with_no_malayalam_in_it_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The model answering in the wrong script must not reach the workbook."""
    reply = batch((0, "THOPPIL VEEDU", ""))
    fake(monkeypatch, replies=[FakeResponse(reply)])

    result = verify.check_rows([("THOPPIL VEEDU", "തോപ്പിൽ വീട്")])

    assert result.verdicts[0].translation == "തോപ്പിൽ വീട്"
    assert result.verdicts[0].corrected is False
    assert "no Malayalam" in result.verdicts[0].note


def test_an_empty_correction_never_blanks_a_cell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reply = verify.CheckedBatch(
        rows=[verify.CheckedRow(index=0, verdict="corrected", malayalam="   ")]
    )
    fake(monkeypatch, replies=[FakeResponse(reply)])

    result = verify.check_rows([("Sri. ABRAHAM", "ശ്രീ. അബ്രഹാം")])

    assert result.verdicts[0].translation == "ശ്രീ. അബ്രഹാം"
    assert result.verdicts[0].corrected is False


# --- a failure never costs the operator their work ------------------------


def test_a_failed_batch_leaves_every_row_as_it_was(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake(monkeypatch, raises=RuntimeError("connection reset by peer"))

    result = verify.check_rows([("One name", "a"), ("Two name", "b")])

    assert [v.translation for v in result.verdicts] == ["a", "b"]
    assert result.unchecked == 2
    assert result.warnings


def test_one_bad_batch_does_not_lose_the_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rate limit halfway through must not throw away the half that worked."""
    monkeypatch.setattr(verify, "BATCH", 1)
    client = fake(
        monkeypatch,
        replies=[FakeResponse(batch((0, "ഒന്ന്", ""))), FakeResponse(batch((1, "", "")))],
    )
    calls = {"n": 0}
    original = client.messages.parse

    def flaky(**kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("429 rate limit exceeded")
        return original(**kwargs)

    monkeypatch.setattr(client.messages, "parse", flaky)

    result = verify.check_rows([("One name", "a"), ("Two name", "b")])

    assert result.verdicts[0].translation == "ഒന്ന്"
    assert result.verdicts[1].translation == "b"
    assert result.checked == 1
    assert result.unchecked == 1


def test_a_reply_that_could_not_be_parsed_is_survivable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake(monkeypatch, replies=[FakeResponse(None)])

    result = verify.check_rows([("One name", "a")])

    assert result.verdicts[0].translation == "a"
    assert result.warnings


def test_no_key_means_no_call_and_no_lost_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db.delete_api_key("ANTHROPIC_API_KEY")

    result = verify.check_rows([("One name", "a")])

    assert result.verdicts[0].translation == "a"
    assert result.error is not None
    assert "Settings" in result.error
    assert result.cost_paise == 0


# --- money ----------------------------------------------------------------


def test_cost_comes_from_the_reported_token_counts() -> None:
    """Not from the estimate. A million in and a million out is exact."""
    assert verify.cost_of(1_000_000, 0) == verify.INPUT_PAISE_PER_MTOK
    assert verify.cost_of(0, 1_000_000) == verify.OUTPUT_PAISE_PER_MTOK


def test_spend_is_recorded_from_what_actually_happened(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake(
        monkeypatch,
        replies=[
            FakeResponse(
                batch((0, "ഒന്ന്", "")), usage=FakeUsage(1_000_000, 1_000_000)
            )
        ],
    )

    before = verify.budget.budget_status()["total_paise"]
    result = verify.check_rows([("One name", "a")])
    after = verify.budget.budget_status()["total_paise"]

    expected = verify.INPUT_PAISE_PER_MTOK + verify.OUTPUT_PAISE_PER_MTOK
    assert result.cost_paise == expected
    assert after - before == expected


def test_nothing_is_spent_or_recorded_when_over_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db.record_spend(
        feature="poster-artwork",
        model="test",
        cost_paise=verify.budget.MONTHLY_BUDGET_PAISE + 1,
        batch=False,
        status="ok",
    )
    client = fake(monkeypatch, replies=[FakeResponse(batch((0, "ഒന്ന്", "")))])

    result = verify.check_rows([("One name", "a")])

    assert client.messages.calls == []
    assert result.cost_paise == 0
    assert result.error is not None
    assert result.verdicts[0].translation == "a"


def test_the_operator_can_spend_past_the_budget_deliberately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db.record_spend(
        feature="poster-artwork",
        model="test",
        cost_paise=verify.budget.MONTHLY_BUDGET_PAISE + 1,
        batch=False,
        status="ok",
    )
    fake(monkeypatch, replies=[FakeResponse(batch((0, "ഒന്ന്", "")))])

    result = verify.check_rows([("One name", "a")], over_budget_ok=True)

    assert result.error is None
    assert result.verdicts[0].corrected is True


# --- what is worth paying to check ----------------------------------------


@pytest.mark.parametrize("source", ["A19", "23/07/2026", "1", "  ", "-", "50.00"])
def test_codes_and_numbers_are_never_sent(
    monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    """Thousands of these on a member list, and none of them can be mistranslated."""
    client = fake(monkeypatch, replies=[])

    result = verify.check_rows([(source, source)])

    assert client.messages.calls == []
    assert result.verdicts[0].translation == source
    assert result.verdicts[0].checked is True


@pytest.mark.parametrize(
    "source", ["Sri. T.P ABRAHAM", "THOPPIL VEEDU,UTHIMOODU P.O,", "Guardian"]
)
def test_names_and_words_are_sent(source: str) -> None:
    assert verify.checkable([source]) == 1


def test_the_quote_only_counts_rows_that_would_be_sent() -> None:
    sources = ["Sri. T.P ABRAHAM", "A19", "1", "THOPPIL VEEDU"]
    assert verify.checkable(sources) == 2

    quote = verify.estimate(verify.checkable(sources))
    assert quote["rows"] == 2
    assert quote["cost_paise"] > 0
    assert quote["is_estimate"] is True


def test_a_sheet_with_nothing_to_check_costs_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = fake(monkeypatch, replies=[])

    result = verify.check_rows([("1", "1"), ("A19", "A19")])

    assert client.messages.calls == []
    assert result.cost_paise == 0
    assert result.error is None


# --- the key never leaves the server --------------------------------------


def test_a_key_shaped_string_is_stripped_from_an_error() -> None:
    message = verify.friendly_error(
        RuntimeError("bad request with x-api-key: sk-ant-api03-ZZZZZZZZZZZZZZZZ")
    )
    assert "sk-ant-api03-ZZZZZZZZZZZZZZZZ" not in message
    assert "[redacted]" in message


def test_batching_covers_every_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """More rows than fit in one request must all come back."""
    monkeypatch.setattr(verify, "BATCH", 2)
    pairs = [(f"Name number {i}", f"m{i}") for i in range(5)]
    replies = [
        FakeResponse(batch(*[(i, f"മ{i}", "") for i in indexes]))
        for indexes in ([0, 1], [2, 3], [4])
    ]
    client = fake(monkeypatch, replies=replies)

    result = verify.check_rows(pairs)

    assert len(client.messages.calls) == 3
    assert [v.translation for v in result.verdicts] == [f"മ{i}" for i in range(5)]
    assert result.checked == 5


# --- cancelling, and the model setting -------------------------------------


class CancellingReporter:
    """A reporter that cancels partway, the way `jobs.Reporter` really does.

    Cancellation is not a flag this module polls — `jobs.Reporter.step` raises
    `Cancelled` from inside itself. That is why the bug existed: the raise came
    out of a call that looked like it only moved a progress bar.
    """

    def __init__(self, cancel_on_call: int = 2) -> None:
        self.cancel_on_call = cancel_on_call
        self.calls = 0

    def step(self, text: str, progress: float | None = None) -> None:
        self.calls += 1
        if self.calls >= self.cancel_on_call:
            raise jobs.Cancelled

    def check_cancelled(self) -> None:
        if self.calls >= self.cancel_on_call:
            raise jobs.Cancelled


def _many(count: int) -> list[tuple[str, str]]:
    return [(f"NAME {i}", f"തെറ്റ് {i}") for i in range(count)]


def test_cancelling_stops_the_paid_check_without_losing_the_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The defect this fixes, stated as the operator experiences it.

    Before, `Cancelled` escaped `check_rows`: the enclosing job ended with
    `result=None`, so the operator lost the entire offline translation — ten
    minutes of local work — *and* every correction they had already paid for,
    while the spend was still recorded. Cancel must cost the money already gone
    and nothing else.
    """
    pairs = _many(3 * verify.BATCH)
    reply = FakeResponse(batch(*[(i, "", "") for i in range(verify.BATCH)]))
    client = fake(monkeypatch, replies=[reply])
    reporter = CancellingReporter(cancel_on_call=2)

    outcome = verify.check_rows(pairs, reporter=reporter)

    assert len(outcome.verdicts) == len(pairs), "every row must come back"
    assert len(client.messages.calls) == 1, "no paid request after the cancel"
    assert any("Stopped at your request" in w for w in outcome.warnings)
    # The rows never reached keep exactly what the offline model produced.
    for index in range(verify.BATCH, len(pairs)):
        assert outcome.verdicts[index].translation == pairs[index][1]
        assert not outcome.verdicts[index].checked


def test_cancelling_raises_nothing_past_the_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rule 2 of this module, and the precise thing that was broken."""
    fake(monkeypatch, replies=[FakeResponse(batch((0, "", "")))])
    verify.check_rows(_many(2 * verify.BATCH), reporter=CancellingReporter(cancel_on_call=1))


def test_spend_is_still_recorded_when_the_check_is_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The money is gone whether or not the job finished."""
    fake(
        monkeypatch,
        replies=[FakeResponse(batch(*[(i, "", "") for i in range(verify.BATCH)]))],
    )
    outcome = verify.check_rows(_many(3 * verify.BATCH), reporter=CancellingReporter(2))
    assert outcome.cost_paise > 0
    spent = db.connect().execute(
        "SELECT COALESCE(SUM(cost_paise), 0) AS n FROM ai_spend WHERE feature = ?",
        (verify.FEATURE,),
    ).fetchone()["n"]
    assert spent == outcome.cost_paise


def test_a_check_that_is_never_cancelled_says_nothing_about_stopping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The warning must not appear on a clean run."""
    fake(monkeypatch, replies=[FakeResponse(batch((0, "", "")))])
    outcome = verify.check_rows([("NAME", "പേര്")])
    assert not any("Stopped at your request" in w for w in outcome.warnings)


def test_the_malayalam_check_model_can_be_chosen_in_settings() -> None:
    """The setting `verify.friendly_error` has always told the operator to use.

    It read `verify_model` from preferences, but the key was not in
    `db.DEFAULTS` — so `set_preferences` refused it and `get_preferences`
    filtered it out, and the model was permanently the hardcoded default. A 404
    from a retired model name was unfixable from the screen that said to fix it.
    """
    db.set_preferences({"verify_model": "claude-opus-5"})
    assert verify.model_name() == "claude-opus-5"


def test_an_empty_model_setting_falls_back_to_the_default() -> None:
    db.set_preferences({"verify_model": ""})
    assert verify.model_name() == verify.DEFAULT_MODEL


def test_the_chosen_model_is_the_one_actually_called(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A setting nothing sends is the same bug in a different place."""
    db.set_preferences({"verify_model": "claude-opus-5"})
    client = fake(monkeypatch, replies=[FakeResponse(batch((0, "", "")))])
    try:
        verify.check_rows([("NAME", "പേര്")])
        assert client.messages.calls[0]["model"] == "claude-opus-5"
    finally:
        db.set_preferences({"verify_model": ""})
