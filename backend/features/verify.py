"""Claude checks the machine translation, word by word, before the operator sees it.

**Why this exists.** `opus-mt-en-ml` is a *translation* model: it has to find
meaning. Handed a co-operative bank's member list — which is names, guardian
names, house names and addresses, and so is almost entirely proper nouns — it
invents. Measured on a real sheet: `ELAVUNKAL VEEDU,VADASERIKARA` came back as
"യൂക്കാലിപ് റ്റസ്" (Eucalyptus), and `THOPPIL VEEDU,UTHIMOODU P.O,` came back
carrying a fabricated "retrieved on June 2, 2019" citation. Those are not
spelling errors; the output has no relationship to the input.

So the check is allowed to *replace* a row, not merely respell it, and the model
is told the rule the local one does not know: a person, house or place is
written by **sound** in Malayalam script, never translated for meaning.

**Three rules hold this module up.**

1. **A row is never silently replaced by the wrong row.** Rows go out numbered
   and must come back numbered. Anything whose number is missing, repeated or
   unrecognised keeps its original translation and is flagged unchecked — see
   `_apply`. Writing one member's name into another member's row is the
   data-destroying failure this feature could cause, and it would be invisible
   on a 33,000-row sheet.
2. **A failure never costs the operator their work.** Nothing here raises past
   the caller. A refused key, a dropped connection or a mangled reply leaves
   every row exactly as the local model produced it, flagged, with the reason in
   plain words.
3. **Spend is what actually happened**, taken from the API's own token counts
   rather than from an estimate, and recorded even if the operator cancels
   halfway.

This module deliberately does not import `translate.py`; it is handed plain
strings and hands plain strings back.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from backend import budget, db, jobs

log = logging.getLogger(__name__)

FEATURE = "excel-verify"

# The operator's choice, made deliberately: Sonnet 5 over Opus for cost, since a
# sheet of this size is thousands of short strings. A *setting*, not a constant —
# providers rename and retire models on their own schedule, and a name that no
# longer exists fails halfway through a paid job.
DEFAULT_MODEL = "claude-sonnet-5"
MODEL_PREFERENCE_KEY = "verify_model"

# Rows per request. Small enough that one bad reply loses little and the
# operator sees progress move; large enough that the instructions are not re-sent
# for every name on the sheet.
BATCH = 40

# Converted at ~₹88/USD, matching `features/ai.py`, and rounded up so a quote is
# never an under-quote. Sonnet 5 list price is $3/$15 per million tokens.
#
# These price the *quote*. What gets recorded as spend is computed from the token
# counts the API itself returns, so the running total does not inherit the
# estimate's error.
INPUT_PAISE_PER_MTOK = 26_400
OUTPUT_PAISE_PER_MTOK = 132_000

# Measured against the shape of this prompt: the instructions amortised over a
# batch, plus the source and the machine translation, come to roughly 55 input
# tokens a row, and a corrected Malayalam line plus its verdict to roughly 35
# output tokens. Only ever used before the fact, and the UI says it is an
# estimate.
EST_INPUT_TOKENS_PER_ROW = 55
EST_OUTPUT_TOKENS_PER_ROW = 35

# U+0D00–U+0D7F. A "correction" containing no Malayalam at all is not a
# correction — see `_apply`.
_MALAYALAM = re.compile(r"[ഀ-ൿ]")

# Anything Anthropic-key-shaped, kept out of any message that reaches the screen.
# SECURITY.md §2: a key is never logged and never in an error message.
_SECRET_SHAPES = re.compile(
    r"""
      sk-ant-[A-Za-z0-9_\-]{8,}
    | (?i:bearer)\s+[A-Za-z0-9_\-.=]{12,}
    | (?i:(?:api[_-]?key|key|token|authorization)["'\s:=]+)[A-Za-z0-9_\-.=]{12,}
    """,
    re.VERBOSE,
)


class Reporter(Protocol):
    """The part of `jobs.Reporter` this module uses.

    Typed structurally so the verifier can be tested without a job registry.
    """

    def step(self, text: str, progress: float | None = None) -> None: ...

    def check_cancelled(self) -> None: ...


# --- what comes back -------------------------------------------------------


class CheckedRow(BaseModel):
    """One row's verdict. `index` is load-bearing — see rule 1 above."""

    index: int
    verdict: Literal["ok", "corrected"]
    malayalam: str = Field(default="")
    note: str = Field(default="", max_length=200)


class CheckedBatch(BaseModel):
    rows: list[CheckedRow] = Field(default_factory=list)


@dataclass
class Verdict:
    """What the check did to one row."""

    translation: str
    checked: bool = False
    corrected: bool = False
    note: str = ""


@dataclass
class VerifyResult:
    """The outcome of checking a whole sheet. Never raises past the caller."""

    verdicts: list[Verdict]
    model: str
    cost_paise: int = 0
    checked: int = 0
    corrected: int = 0
    unchecked: int = 0
    error: str | None = None
    warnings: list[str] = field(default_factory=list)


def is_configured() -> bool:
    """True when an Anthropic key has been entered. Never reveals the key."""
    return any(k["name"] == "ANTHROPIC_API_KEY" and k["is_set"] for k in db.list_api_keys())


def model_name() -> str:
    """The model this job will use — the operator's choice, or the default."""
    chosen = db.get_preferences().get(MODEL_PREFERENCE_KEY, "").strip()
    return chosen or DEFAULT_MODEL


def checkable(sources: list[str]) -> int:
    """How many of these rows would actually be sent. Free, and quotes on it.

    A quote that counted the membership codes and serial numbers would be an
    over-quote by roughly a third on a member list.
    """
    return sum(1 for source in sources if _should_check(source))


def estimate(rows: int) -> dict[str, Any]:
    """What checking this sheet will cost, before committing to it. Free."""
    paise = (
        rows * EST_INPUT_TOKENS_PER_ROW * INPUT_PAISE_PER_MTOK
        + rows * EST_OUTPUT_TOKENS_PER_ROW * OUTPUT_PAISE_PER_MTOK
    ) // 1_000_000
    return {
        "rows": rows,
        "model": model_name(),
        "requests": (rows + BATCH - 1) // BATCH,
        "cost_paise": paise,
        "cost_rupees": round(paise / 100, 2),
        "configured": is_configured(),
        # Said out loud everywhere it is shown. The real figure comes from the
        # token counts afterwards, and Anthropic's console is the truth.
        "is_estimate": True,
    }


def cost_of(input_tokens: int, output_tokens: int) -> int:
    """Paise for one call, from the token counts the API reported."""
    return (
        input_tokens * INPUT_PAISE_PER_MTOK + output_tokens * OUTPUT_PAISE_PER_MTOK
    ) // 1_000_000


# --- the prompt ------------------------------------------------------------

SYSTEM = """\
You are checking English→Malayalam output from a small offline translation model, \
row by row, for a printing shop in Kerala. The operator will read every row you \
return, so be exact and never guess at a meaning you cannot see in the source.

The source rows come from Indian office spreadsheets — member lists, voter lists, \
address lists. Most cells are PROPER NOUNS: people's names, guardian names, house \
names ("VEEDU"), post offices, villages. The offline model translates them for \
meaning, which is wrong for a name, so it produces fabrications — it has rendered \
"ELAVUNKAL VEEDU" as the Malayalam for "Eucalyptus" and inserted invented citation \
text into addresses.

For each row decide which of these applies, and return the corrected Malayalam:

1. A person's name, house name, place, post office or initials — TRANSLITERATE it: \
write the same sounds in Malayalam script. Never translate its meaning. Keep the \
honorific (Sri./Smt.) transliterated, keep initials as initials, keep the order and \
the punctuation of the source. "VEEDU" is part of a house name; write it as ​വീട്.
2. Ordinary English words that genuinely carry meaning — column headings like \
"Name", "Address", "Guardian", or a line like "Voters list as on 23/07/2026" — \
TRANSLATE them properly.
3. Numbers, membership codes (A19), dates and punctuation — leave EXACTLY as they \
are, in the same digits and format as the source. Do not convert digits.

Return verdict "ok" only when the existing Malayalam is already correct and \
correctly spelled; return "corrected" with your replacement otherwise. Every row \
you were given must come back with its own index. Never merge rows, never reorder \
them, never invent a row that was not sent. `note` is at most a short phrase for \
the operator, in English, and only when something is genuinely worth their eye.\
"""


def _render(items: list[tuple[int, str, str]]) -> str:
    lines = []
    for index, source, current in items:
        lines.append(f"[{index}]\nEnglish: {source}\nMalayalam now: {current}")
    return "\n\n".join(lines)


# --- the call --------------------------------------------------------------


def _client() -> Any:
    key = db.get_api_key("ANTHROPIC_API_KEY")
    if not key:
        raise VerifyError(
            "No Anthropic API key yet. Add one in Settings — the Malayalam check "
            "is the only thing that uses it, and translation still works without it."
        )
    try:
        import anthropic
    except ImportError as exc:
        raise VerifyError("The AI extra is not installed: uv sync --extra ai") from exc
    return anthropic.Anthropic(api_key=key)


class VerifyError(RuntimeError):
    """Something about the request is wrong, before any spend."""


def _redact(text: str) -> str:
    return _SECRET_SHAPES.sub("[redacted]", text)


def friendly_error(exc: Exception) -> str:
    """Turn an SDK exception into something the operator can act on."""
    text = str(exc)
    lowered = text.lower()
    if "authentication" in lowered or "401" in lowered or "invalid x-api-key" in lowered:
        return (
            "Anthropic rejected that API key. Check it in Settings. Nothing was "
            "charged, and your translation is untouched."
        )
    if "permission" in lowered or "403" in lowered:
        return (
            "That key exists but is not allowed to make this call — check the "
            "workspace it belongs to at console.anthropic.com."
        )
    if "credit" in lowered or "billing" in lowered or "402" in lowered:
        return (
            "The Anthropic account is out of credit. Top it up at "
            "console.anthropic.com. Your translation is untouched."
        )
    if "rate" in lowered and "limit" in lowered or "429" in lowered:
        return "Anthropic's rate limit was hit. Wait a minute and check the rest again."
    if "not_found" in lowered or "404" in lowered:
        return (
            f"Anthropic has no model called {model_name()} any more. Set a current "
            "one in Settings. Nothing was charged."
        )
    if "timeout" in lowered or "timed out" in lowered:
        return "The check timed out. Your translation is unchanged — try again."
    if "connect" in lowered or "network" in lowered or "dns" in lowered:
        return "Could not reach Anthropic. Check the internet connection."
    return f"The check failed: {_redact(text)[:200]}"


def _apply(
    items: list[tuple[int, str, str]],
    returned: list[CheckedRow],
    verdicts: dict[int, Verdict],
) -> None:
    """Write one batch's verdicts back, refusing anything that does not line up.

    This is rule 1. Every guard here fails *closed* — the row keeps the offline
    model's translation and is left flagged for the operator — because on a
    33,000-row sheet a row quietly filled with another member's name would never
    be found.
    """
    current_by_index = {index: current for index, _, current in items}
    seen: set[int] = set()

    for row in returned:
        if row.index not in current_by_index:
            # A number we never sent. Not ours to write anywhere.
            continue
        if row.index in seen:
            # Sent once, answered twice. Neither answer is trustworthy now, so
            # the first is left in place rather than overwritten by the second.
            continue
        seen.add(row.index)

        current = current_by_index[row.index]
        text = (row.malayalam or "").strip()
        note = (row.note or "").strip()

        if row.verdict == "ok" or not text or text == current:
            verdicts[row.index] = Verdict(current, checked=True, note=note)
            continue
        if not _MALAYALAM.search(text):
            # A "correction" with no Malayalam in it. For a row whose source is
            # a bare number or code that is right, and `_should_check` has
            # already kept those out; anywhere else it is the model answering in
            # the wrong script, and the original is safer.
            verdicts[row.index] = Verdict(
                current,
                checked=True,
                note="The check returned no Malayalam for this row — left as it was.",
            )
            continue
        verdicts[row.index] = Verdict(text, checked=True, corrected=True, note=note)

    for index in current_by_index.keys() - seen:
        # Sent and never answered. Flagged, never guessed at.
        verdicts[index] = Verdict(
            current_by_index[index],
            checked=False,
            note="The check did not return this row — it is as the offline model left it.",
        )


def _should_check(source: str) -> bool:
    """Whether a row is worth paying to check.

    A cell that is only digits, punctuation and a membership code cannot be
    mistranslated in a way Claude would see, and there are thousands of them on
    a member list. Skipping them is the difference between a ₹1,600 sheet and a
    ₹1,100 one, and costs nothing in quality.
    """
    stripped = source.strip()
    if not stripped:
        return False
    return bool(re.search(r"[A-Za-zഀ-ൿ]{2,}", stripped))


def check_rows(
    pairs: list[tuple[str, str]],
    reporter: Reporter | None = None,
    over_budget_ok: bool = False,
    progress_from: float = 0.0,
    progress_to: float = 1.0,
) -> VerifyResult:
    """Check every (source, machine translation) pair. Never raises past here.

    Returns one `Verdict` per input pair, in the same order. On any failure the
    verdicts are the untouched machine translations, flagged unchecked.

    `progress_from`/`progress_to` are the slice of the enclosing job's bar this
    check owns. The check is the long pole on a large sheet — hundreds of
    requests — so a bar that sits still throughout reads as a hung job.
    """
    model = model_name()
    verdicts: dict[int, Verdict] = {
        i: Verdict(current, note="Not checked.") for i, (_, current) in enumerate(pairs)
    }
    result = VerifyResult(verdicts=[], model=model)

    def finish(error: str | None = None) -> VerifyResult:
        result.error = error
        result.verdicts = [verdicts[i] for i in range(len(pairs))]
        result.checked = sum(1 for v in result.verdicts if v.checked)
        result.corrected = sum(1 for v in result.verdicts if v.corrected)
        result.unchecked = len(result.verdicts) - result.checked
        return result

    try:
        budget.ensure_within(over_budget_ok, provider="Anthropic")
    except budget.OverBudget as exc:
        return finish(str(exc))

    todo = [
        (i, source, current)
        for i, (source, current) in enumerate(pairs)
        if _should_check(source)
    ]
    # Membered rather than scanned. On a 33,000-cell sheet the obvious
    # `all(index != i for ...)` is a 33,000 × 33,000 walk.
    checking = {index for index, _, _ in todo}
    for i, (_, current) in enumerate(pairs):
        if i not in checking:
            verdicts[i] = Verdict(
                current, checked=True, note="Numbers and codes — nothing to check."
            )
    if not todo:
        return finish()

    try:
        client = _client()
    except VerifyError as exc:
        return finish(str(exc))

    batches = [todo[start : start + BATCH] for start in range(0, len(todo), BATCH)]
    spent = 0
    stopped_after = -1

    def report(done: int) -> bool:
        """Move the bar, and say whether the operator has asked us to stop.

        Never raises — that is rule 2, and it was being broken here. `jobs
        .Reporter.step` calls `check_cancelled` from inside itself, so the bare
        `reporter.step(...)` this replaces let `Cancelled` escape `check_rows`
        entirely: every verdict built up so far was discarded, the enclosing job
        ended with `result=None`, and the operator lost the whole offline
        translation *and* the corrections they had already been charged for —
        while the `finally` below still recorded the spend.

        Cancel must cost the money already spent and nothing else.
        """
        if reporter is None:
            return False
        span = progress_to - progress_from
        try:
            reporter.step(
                f"Checking Malayalam with Claude — {done * BATCH:,} of {len(todo):,}…",
                progress_from + span * (done / len(batches)),
            )
        except jobs.Cancelled:
            return True
        return False

    try:
        for done, items in enumerate(batches):
            if report(done):
                stopped_after = done * BATCH
                break
            try:
                response = client.messages.parse(
                    model=model,
                    max_tokens=16000,
                    system=SYSTEM,
                    messages=[{"role": "user", "content": _render(items)}],
                    output_format=CheckedBatch,
                )
            except Exception as exc:  # noqa: BLE001 - SDK raises many types
                log.warning("verification batch failed: %s", exc)
                # One bad batch is not the whole sheet. Its rows stay flagged
                # unchecked and the rest carry on.
                result.warnings.append(friendly_error(exc))
                continue

            usage = getattr(response, "usage", None)
            spent += cost_of(
                int(getattr(usage, "input_tokens", 0) or 0),
                int(getattr(usage, "output_tokens", 0) or 0),
            )
            parsed = getattr(response, "parsed_output", None)
            if parsed is None:
                result.warnings.append(
                    "One batch came back in a shape this app could not read; "
                    "those rows are as the offline model left them."
                )
                continue
            _apply(items, parsed.rows, verdicts)
    finally:
        # Recorded even if the operator cancels mid-sheet: the money is gone
        # whether or not the job finished.
        result.cost_paise = spent
        if spent:
            db.record_spend(
                feature=FEATURE,
                model=model,
                cost_paise=spent,
                batch=False,
                status="ok",
            )

    if stopped_after >= 0:
        # Said plainly, and counted, so the results panel cannot read as a clean
        # pass over a sheet that was only half checked.
        result.warnings.append(
            f"Stopped at your request after {stopped_after:,} rows — the rest are "
            "as the offline model left them."
        )

    return finish()


__all__ = [
    "BATCH",
    "DEFAULT_MODEL",
    "CheckedBatch",
    "CheckedRow",
    "VerifyError",
    "VerifyResult",
    "Verdict",
    "check_rows",
    "cost_of",
    "estimate",
    "friendly_error",
    "is_configured",
    "model_name",
]
