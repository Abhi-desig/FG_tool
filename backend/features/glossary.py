"""Forced terminology: making the glossary a guarantee, not a suggestion.

The translation model is small and it *will* pick the wrong sense — measured on
2026-08-22, ``Coconut oil, 1 litre bottle`` came back as *oil, 1 litre bottle*
with "coconut" simply dropped, and ``bulk orders`` became ആജ്ഞകൾ, the military
kind of order. That is what this module exists to fix.

**How.** Glossary terms are lifted out of the sentence before translation and put
back afterwards, so the model never gets a chance to mistranslate them:

    "Coconut oil, 1 litre bottle"
      → mask     → "QQ0QQ, 1 litre bottle"
      → translate→ "QQ0QQ, 1 ലിറ്റർ കുപ്പി"
      → restore  → "വെളിച്ചെണ്ണ, 1 ലിറ്റർ കുപ്പി"

Post-translation find-and-replace would not work: by then the term has already
been mangled and there is nothing reliable left to search for.

**When it goes wrong.** A small model can drop or duplicate a placeholder. Every
substitution is verified, and a term the model lost is appended rather than
silently vanishing — the operator sees it flagged in the review grid.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Placeholders must survive sentencepiece tokenisation and machine translation.
# Chosen by measurement, not taste — six candidates were run through the real
# model across four sentence shapes on 2026-08-22:
#
#   X<n>X  5/6      Z<n>Z  3/6      X<nn>X 4/6
#   K<n>K  2/6      W<n>W  1/6      QQ<n>QQ  failed — transliterated to ക്യു
#
# Punctuation styles (⟦0⟧, <0>, {0}) all failed: MT re-spaces or drops them.
# `X<n>X` wins, but note the ceiling — **nothing survives every time**, which is
# why lost terms are detected and flagged rather than assumed impossible.
_PLACEHOLDER = "X{}X"
_PLACEHOLDER_RE = re.compile(r"X\s*(\d+)\s*X", re.IGNORECASE)

# A product code in the source could look like a placeholder. Start the counter
# above anything already present so a real "X1X" is never mistaken for ours.
_COLLISION_RE = re.compile(r"X(\d+)X", re.IGNORECASE)


@dataclass
class Masked:
    """A sentence with its glossary terms lifted out."""

    text: str
    # placeholder index → approved target term
    terms: dict[int, str] = field(default_factory=dict)
    # source terms that matched, for showing the operator why a row changed
    matched: list[str] = field(default_factory=list)
    # The text as it was *before* masking. Kept so `restore` can tell a mangled
    # placeholder from a client's part number — see `_debris`.
    source: str = ""


@dataclass
class Restored:
    text: str
    applied: list[str] = field(default_factory=list)
    # Terms the model lost. Appended rather than dropped, and flagged.
    lost: list[str] = field(default_factory=list)
    # Placeholder wreckage removed from the output: the shapes the model made of
    # our markers when it failed to carry them through. Reported so the row can
    # say what happened rather than merely that something is short.
    debris: list[str] = field(default_factory=list)
    # True when a lost term was appended to the end of the cell. The operator
    # must be told, because the term is in the wrong place.
    appended: bool = False


def compile_terms(terms: list[dict[str, str]]) -> list[tuple[str, str]]:
    """Order terms longest-first so 'coconut oil' wins over 'oil'."""
    pairs = [
        (t["source_term"].strip(), t["target_term"].strip())
        for t in terms
        if (t.get("source_term") or "").strip() and (t.get("target_term") or "").strip()
    ]
    return sorted(pairs, key=lambda p: len(p[0]), reverse=True)


def mask(text: str, terms: list[tuple[str, str]]) -> Masked:
    """Replace glossary terms with placeholders before translation."""
    if not text or not terms:
        return Masked(text=text)

    result = text
    mapping: dict[int, str] = {}
    matched: list[str] = []

    # Step past any "X<n>X" the source already contains, e.g. a product code.
    existing = [int(m.group(1)) for m in _COLLISION_RE.finditer(text)]
    next_index = max(existing) + 1 if existing else 0

    for source, target in terms:
        # Word boundaries so "oil" does not match inside "boiler". Terms with
        # non-word edges (e.g. "1L") fall back to a plain escaped search.
        pattern = re.compile(
            rf"(?<!\w){re.escape(source)}(?!\w)" if source[0].isalnum() else re.escape(source),
            re.IGNORECASE,
        )
        if not pattern.search(result):
            continue
        index = next_index
        next_index += 1
        mapping[index] = target
        matched.append(source)
        result = pattern.sub(_PLACEHOLDER.format(index), result)

    return Masked(text=result, terms=mapping, matched=matched, source=text)


# What a mangled placeholder looks like once the model has been at it. Measured
# on real output 2026-08-26: `Standee` masked to `X0X` came back from the 57M
# model as `X-px X X X` — the digit gone, the marker exploded into four tokens.
# `_PLACEHOLDER_RE` needs `X<digits>X`, so nothing matched and the wreckage was
# carried through to the client's spreadsheet.
#
# **Deliberately narrow, and case sensitive.** The placeholder is an uppercase
# `X`; a lowercase one is ordinary text. A first attempt at this matched
# case-insensitively and ate the `x` out of `2x6`, turning a size on a price list
# into "6" — far worse than the debris it was cleaning up. Tokens must be
# whole words, so nothing inside `2x6` can take part.
_DEBRIS_PATTERNS = (
    # `X-px`, `X-pt` — the marker fused with a unit the model invented.
    re.compile(r"(?<![^\W_])X-[a-z]{1,3}(?![^\W_])"),
    # `X X X` — the marker split into bare letters.
    re.compile(r"(?<![^\W_])X(?:\s+X)+(?![^\W_])"),
    # `X0X`, `X 0 X` — the marker intact in shape but not ours to restore.
    re.compile(r"(?<![^\W_])X\s*\d+\s*X(?![^\W_])"),
    # A single stranded uppercase `X` between spaces.
    re.compile(r"(?<=\s)X(?=\s|$)"),
)


def _debris(text: str, source: str) -> tuple[str, list[str]]:
    """Remove placeholder wreckage, keeping anything the source really contained.

    The old rule left every `X..X`-shaped token alone, on the grounds that it
    might be a client's part number. That is right in principle and the two cases
    are distinguishable: **a placeholder-shaped token not present in the source
    text is ours, and it is debris.** The source is available here, so the check
    costs nothing.

    Removal is one-directional and conservative: anything that appears in the
    source, in any case, is left exactly where it is.
    """
    found: list[str] = []
    source_tokens = {token.upper() for token in re.findall(r"[^\s]+", source)}

    def drop(match: re.Match[str]) -> str:
        token = match.group(0).strip()
        # `Cable X0X` — in the source all along. Never touch it.
        if token.upper() in source_tokens or token.upper() in source.upper():
            return match.group(0)
        found.append(token)
        return " "

    for pattern in _DEBRIS_PATTERNS:
        text = pattern.sub(drop, text)
    return text, found


def restore(translated: str, masked: Masked, source: str | None = None) -> Restored:
    """Put the approved terms back, and report any the model lost.

    `source` overrides `masked.source` for callers that hold the original text
    separately. One of the two must be present for debris removal to be safe —
    without it there is no way to tell our wreckage from a client's part number.
    """
    if not masked.terms:
        return Restored(text=translated)

    original = source if source is not None else masked.source
    applied: list[str] = []
    seen: set[int] = set()

    def swap(match: re.Match[str]) -> str:
        index = int(match.group(1))
        term = masked.terms.get(index)
        if term is None:
            # Not ours — either a product code that was in the source all along
            # ("Cable X0X") or one the model invented. Leave it exactly as it is:
            # deleting a client's part number would be far worse than keeping a
            # stray token.
            return match.group(0)
        seen.add(index)
        applied.append(term)
        return term

    text = _PLACEHOLDER_RE.sub(swap, translated)

    # Sweep up what the model made of the markers it failed to carry through.
    debris: list[str] = []
    if original:
        text, debris = _debris(text, original)

    # Anything the model swallowed. Appending is deliberately visible: a missing
    # brand name must not disappear quietly, and the grid marks the row.
    lost = [masked.terms[i] for i in masked.terms if i not in seen]
    appended = False
    if lost:
        text = f"{text.rstrip()} {' '.join(lost)}".strip()
        appended = True

    return Restored(
        text=_tidy(text),
        applied=applied,
        lost=lost,
        debris=debris,
        appended=appended,
    )


def _tidy(text: str) -> str:
    """Clean up spacing left behind by substitution."""
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    return text.strip()


def apply_glossary_only(text: str, terms: list[tuple[str, str]]) -> str:
    """Substitute terms without translating.

    Used when a cell is *entirely* a glossary term, which makes a model call
    pointless — and on a mediocre model, actively risky.
    """
    masked = mask(text, terms)
    return restore(masked.text, masked).text


def is_fully_covered(text: str, terms: list[tuple[str, str]]) -> bool:
    """True if the glossary alone accounts for the whole cell."""
    masked = mask(text, terms)
    if not masked.terms:
        return False
    residue = _PLACEHOLDER_RE.sub("", masked.text)
    return not re.search(r"[A-Za-z]", residue)
