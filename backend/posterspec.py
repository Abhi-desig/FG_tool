"""The poster engine's own rules, checked offline before anything is spent.

**Why this module exists.** The prompt engine (v1.1, pinned in the prompt
library) ends with a list headed *REJECT AND REBUILD IF* — over thirty words of
poster text, a banned adjective, an undeclared palette, a reserved zone given
without a percentage, supplied wording altered. Those are stated as
instructions to the model that writes the prompt, which means they hold only as
well as that model felt like following them. Most of them are mechanically
checkable, and checking them here costs nothing.

That matters because the alternative is finding out by paying. A poster call is
₹11.50 instant, and the failure it protects against is not an ugly poster — it
is a poster with the shop's phone number one digit wrong, printed, delivered
and paid for. ADR-030 used to guarantee that by setting the digits itself;
ADR-034 gave that up when Gemini took over the drawing. This is the part of it
that can be won back without giving the drawing back.

**What it will and will not claim.** Every check here is either exact or it is
absent. The rules that cannot be read off the text reliably — how many shapes a
prompt describes, whether two alignment axes are really two — are not guessed
at, because a validator that cries wolf is one the operator learns to click
past, and then the digit check goes unread with it. Where a rule is checkable
only in one direction, it is checked in that direction and says so.

Nothing here talks to Google, and nothing here blocks on judgement: one exact
failure refuses (the copy's digits went missing), everything else is a warning
the operator sees beside the poster.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Step 3.5 of the engine: words that mean nothing to an image model and crowd
# out the specifics that do. Matched on word boundaries and only outside quoted
# strings — the operator's own copy is sacred, and a shop calling its offer
# "Perfect Onam Deal" is not a prompt defect.
BANNED_WORDS: tuple[str, ...] = (
    "beautiful",
    "stunning",
    "vibrant",
    "dynamic",
    "eye-catching",
    "modern-looking",
    "high quality",
    "professional",
    "masterpiece",
    "4k",
    "ultra hd",
    "award-winning",
    "breathtaking",
    "perfect",
)

# Step 6: the typography cap. Above this, Gemini's lettering degrades — this is
# the single biggest lever on whether the words come out readable at all.
MAX_POSTER_WORDS = 30
MAX_POSTER_LINES = 7

# Global lock: four colours.
MAX_COLOURS = 4

# Straight double quotes, per the engine — every on-poster string is quoted
# verbatim, so the quotes are what makes the poster's own text findable in a
# wall of scene description.
_QUOTED = re.compile(r'"([^"\n]*)"')

# A run of digits. Phone numbers arrive spaced ("9656 00 3244"), so runs rather
# than whole numbers is the unit that survives the shop's own formatting.
_DIGITS = re.compile(r"\d+")

# "the upper 55% of the frame is held empty" — a percentage anywhere near a word
# about emptiness. Step 5 is explicit that an unspecified region gets filled.
_RESERVED = re.compile(
    r"\d{1,3}\s?%[^.]{0,80}?\b(?:empty|reserved|held|clear|free of)\b"
    r"|\b(?:empty|reserved|held|clear|free of)\b[^.]{0,80}?\d{1,3}\s?%",
    re.IGNORECASE,
)

# "4:5", "1:1", "16:9" — and the words that stand in for one.
_ASPECT = re.compile(
    r"\b\d{1,2}\s?:\s?\d{1,2}\b|\b(?:portrait|vertical|square|landscape)\b", re.IGNORECASE
)

# How far into the prompt the aspect ratio still counts as "the first clause".
_ASPECT_WINDOW = 240

# "Four colours only — deep navy, white, warm gold, grey." The declaration the
# engine asks for, which is also the only form a palette can be counted from.
_PALETTE = re.compile(
    r"\b(?:colours?|colors?)\s+(?:only|maximum|max)\b\s*[—–:-]\s*([^.]{0,200})",
    re.IGNORECASE,
)

_CLOSING_LOCK = re.compile(r"no watermarks?", re.IGNORECASE)


@dataclass
class Report:
    """What the rules say about one finished prompt.

    `refusals` is the short list that stops the call. `warnings` is everything
    else — shown, not enforced, because a prompt can break a style rule and
    still make the poster the operator wanted.
    """

    refusals: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.refusals

    def as_dict(self) -> dict[str, object]:
        return {"ok": self.ok, "refusals": list(self.refusals), "warnings": list(self.warnings)}


def poster_text(prompt: str) -> list[str]:
    """The strings the poster will actually have lettering for.

    Only quoted runs count. A design that describes its text instead of quoting
    it ("Headline: Onam Sale") gives nothing to check, which is itself worth
    saying — see `check`.
    """
    return [m.group(1).strip() for m in _QUOTED.finditer(prompt) if m.group(1).strip()]


def _digit_runs(text: str) -> set[str]:
    return set(_DIGITS.findall(text))


def _banned_outside_quotes(prompt: str) -> list[str]:
    """Banned words in the prose, ignoring anything the poster will letter."""
    prose = _QUOTED.sub(" ", prompt).casefold()
    found = []
    for word in BANNED_WORDS:
        # Word boundaries both ends, so "perfect" does not fire on "perfectly
        # even" — which is a legitimate way to describe a gradient.
        if re.search(rf"(?<![\w-]){re.escape(word)}(?![\w-])", prose):
            found.append(word)
    return found


def check(prompt: str, copy: dict[str, str] | None = None) -> Report:
    """Read one finished prompt against the engine's reject list.

    `copy` is the wording the operator supplied, narrowed by the caller to the
    tags the design actually uses. Passing the whole copy for a design that only
    sets a headline would report the phone number as missing when the design was
    never asked to draw it.
    """
    report = Report()
    body = prompt.strip()
    if not body:
        report.refusals.append("The prompt came out empty — there is nothing to send.")
        return report

    supplied = {tag: value for tag, value in (copy or {}).items() if value.strip()}
    quoted = poster_text(body)

    # --- the one exact failure, and the reason this module exists ----------
    #
    # Every digit run in the wording the design was given must survive into the
    # prompt. Checked one way only: a v1.1 prompt is full of digits that are not
    # copy — 7% margins, 1px strokes, twelve metres — so "no digit the operator
    # did not supply" is not a rule that can hold. This direction is exact.
    wanted = _digit_runs(" ".join(supplied.values()))
    if wanted:
        present = _digit_runs(body)
        lost = sorted(wanted - present, key=lambda d: (-len(d), d))
        if lost:
            report.refusals.append(
                "The prompt has lost numbers that are in your copy: "
                f"{', '.join(lost)}. A price or phone number the model never "
                "sees is one it will invent. Nothing was sent."
            )

    # Quoted wording must be the operator's, not a rewrite of it. Case is
    # allowed to differ — the engine sets headlines in capitals and the shop
    # writes them in title case — but the letters themselves may not.
    if supplied and quoted:
        haystack = " ".join(supplied.values()).casefold()
        haystack = re.sub(r"\s+", " ", haystack)
        invented = [text for text in quoted if re.sub(r"\s+", " ", text.casefold()) not in haystack]
        if invented:
            report.warnings.append(
                "The prompt quotes wording that is not in your copy: "
                + "; ".join(f"“{t}”" for t in invented[:4])
                + ". Check every word of it against what you pasted before printing."
            )

    if supplied and not quoted:
        report.warnings.append(
            "This design describes its text rather than quoting it, so the app "
            "cannot check the wording before spending. Read every word and digit "
            "on the finished poster against your copy."
        )

    # --- the typography cap ------------------------------------------------
    words = sum(len(text.split()) for text in quoted)
    if words > MAX_POSTER_WORDS:
        report.warnings.append(
            f"{words} words of poster text, over the {MAX_POSTER_WORDS}-word cap. "
            "Gemini's lettering gets worse the more of it there is — drop the "
            "small print, not the headline."
        )
    if len(quoted) > MAX_POSTER_LINES:
        report.warnings.append(
            f"{len(quoted)} lines of poster text, over the {MAX_POSTER_LINES}-line cap."
        )

    # --- the specificity budget -------------------------------------------
    banned = _banned_outside_quotes(body)
    if banned:
        report.warnings.append(
            "The prompt uses words that mean nothing to an image model: "
            + ", ".join(banned)
            + ". Each one is space that could have named a material, a light "
            "direction or a colour."
        )

    # --- the reserved zone -------------------------------------------------
    if not _RESERVED.search(body):
        report.warnings.append(
            "No reserved zone stated as a percentage. An area the prompt does "
            "not claim is one Gemini fills, and it usually fills it over the type."
        )

    # --- the global locks --------------------------------------------------
    if not _ASPECT.search(body[:_ASPECT_WINDOW]):
        report.warnings.append(
            "The prompt does not open with a shape or aspect ratio. Stated late, "
            "it drifts to square."
        )

    palette = _PALETTE.search(body)
    if palette is None:
        report.warnings.append(
            f"The palette is not declared. State it as a count — "
            f"“{MAX_COLOURS} colours only — …” — and name each one."
        )
    else:
        named = [part.strip() for part in re.split(r",| and ", palette.group(1)) if part.strip()]
        if len(named) > MAX_COLOURS:
            report.warnings.append(
                f"{len(named)} colours named, over the {MAX_COLOURS}-colour lock."
            )

    if not _CLOSING_LOCK.search(body):
        report.warnings.append(
            "The prompt does not close with the no-extra-text lock, so Gemini is "
            "free to add words of its own."
        )

    return report


__all__ = [
    "BANNED_WORDS",
    "MAX_COLOURS",
    "MAX_POSTER_LINES",
    "MAX_POSTER_WORDS",
    "Report",
    "check",
    "poster_text",
]
