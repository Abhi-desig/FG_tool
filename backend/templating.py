"""`{{variable}}` substitution, shared by the prompt library and design styles.

Both need the same two operations — find the variables in a body, and fill them
in — and CLAUDE.md keeps feature modules from importing one another, so the rules
live here once rather than drifting apart in two copies.
"""

from __future__ import annotations

import re

VARIABLE = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}")

# A line that is nothing but a label once its value turned out empty — "Offer:"
# with no offer. Dropped rather than sent, because a bare label reads to the
# model as a field it should invent something for.
BARE_LABEL = re.compile(r"\s*[A-Z][A-Za-z ]{0,24}:\s*")


def variables_in(body: str) -> list[str]:
    """Distinct `{{variable}}` names, in order of first appearance."""
    seen: list[str] = []
    for match in VARIABLE.finditer(body):
        name = match.group(1)
        if name not in seen:
            seen.append(name)
    return seen


def render(body: str, values: dict[str, str]) -> str:
    """Substitute values, leaving nothing unresolved.

    An unfilled `{{variable}}` reaching a paid API call would waste money on a
    confused request, so a missing value becomes an empty string and the line it
    sits on is dropped if that leaves it bare.
    """

    def swap(match: re.Match[str]) -> str:
        return (values.get(match.group(1)) or "").strip()

    filled = VARIABLE.sub(swap, body)
    kept = [line for line in filled.splitlines() if not BARE_LABEL.fullmatch(line)]
    return "\n".join(kept).strip()
