"""Editable prompt templates.

SETTINGS.md: this is what makes poster and photo output tunable **without
touching code**. The operator finds a phrasing that works for their clients and
keeps it; nobody has to open an editor to change how the AI is asked.

Three things make it safe to edit:

* **Version history.** Every save keeps the previous body, so an experiment can
  never destroy a prompt that was working.
* **Restore default.** Seeded templates can always be put back exactly.
* **Variable checking.** A template referring to `{{colour}}` when its scope only
  provides `{{palette}}` is caught at save time, not halfway through a paid job.

The `poster-layout` default is the load-bearing one: it must return **JSON layout
data and never image text**, because that constraint is the whole reason this
shop can produce Malayalam posters at all (ROADMAP.md Phase 4).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from backend import db

VARIABLE = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}")


class PromptError(ValueError):
    """The template or its variables are not usable."""


@dataclass(frozen=True)
class Scope:
    key: str
    label: str
    description: str
    variables: tuple[str, ...]
    required: tuple[str, ...]


SCOPES: tuple[Scope, ...] = (
    Scope(
        key="poster-layout",
        label="Poster layout plan",
        description=(
            "Asks the AI where the words should go. Must return JSON only — the "
            "app draws every word itself, which is why Malayalam comes out right."
        ),
        variables=("occasion", "headline", "offer", "phone", "tone"),
        required=("headline",),
    ),
    Scope(
        key="poster-artwork",
        label="Poster background artwork",
        description="Generates the picture behind the text. No words in the image.",
        variables=("subject", "style", "palette", "aspect"),
        required=("subject",),
    ),
    Scope(
        key="photo-edit",
        label="Photo edit instruction",
        description="Tells the AI what to change in a client photograph.",
        variables=("instruction", "preserve"),
        required=("instruction",),
    ),
)

SCOPE_KEYS = {s.key: s for s in SCOPES}

# Shipped so the operator opens a working set, not an empty screen.
SEEDS: dict[str, str] = {
    "poster-layout": (
        "You are laying out a print poster for a shop in Kerala.\n"
        "\n"
        "Occasion: {{occasion}}\n"
        "Headline: {{headline}}\n"
        "Offer: {{offer}}\n"
        "Phone: {{phone}}\n"
        "Tone: {{tone}}\n"
        "\n"
        "Return ONLY a JSON object, no prose and no code fence, shaped like:\n"
        '{"blocks": [{"id": "headline", "text": "...", "x": 0.08, "y": 0.1,\n'
        '  "width": 0.84, "size": "large", "weight": "bold", "align": "centre"}]}\n'
        "\n"
        "Rules:\n"
        "- x, y and width are fractions of the canvas between 0 and 1.\n"
        "- size is one of: small, medium, large, huge.\n"
        "- Keep every block between 0.06 and 0.94 so trimming cannot cut it.\n"
        "- Order blocks top to bottom in reading order.\n"
        "- Copy the supplied text EXACTLY. Do not translate, correct or "
        "re-spell it, and never invent new wording.\n"
        "- Never describe an image and never place text into a picture. You are "
        "choosing positions only."
    ),
    "poster-artwork": (
        "A photographic background for a print poster.\n"
        "\n"
        "Subject: {{subject}}\n"
        "Style: {{style}}\n"
        "Colours: {{palette}}\n"
        "Aspect: {{aspect}}\n"
        "\n"
        "Leave calm, uncluttered space in the upper and lower thirds where text "
        "will be placed over it.\n"
        "Absolutely no lettering, no words, no numbers and no logos anywhere in "
        "the image."
    ),
    "photo-edit": (
        "Edit this photograph.\n"
        "\n"
        "Change: {{instruction}}\n"
        "Keep unchanged: {{preserve}}\n"
        "\n"
        "Keep the subject's face, proportions and skin tone exactly as they are "
        "unless the change explicitly asks otherwise. Match the existing lighting "
        "and perspective. Do not add any text to the image."
    ),
}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def variables_in(body: str) -> list[str]:
    """Distinct `{{variable}}` names, in order of first appearance."""
    seen: list[str] = []
    for match in VARIABLE.finditer(body):
        name = match.group(1)
        if name not in seen:
            seen.append(name)
    return seen


def validate(scope: str, body: str) -> list[str]:
    """Problems with a template, in words the operator can act on.

    Returned rather than raised: a warning list lets the UI show every issue at
    once instead of one per save.
    """
    spec = SCOPE_KEYS.get(scope)
    if spec is None:
        raise PromptError(f"unknown scope {scope!r}")

    problems: list[str] = []
    used = set(variables_in(body))
    unknown = used - set(spec.variables)
    if unknown:
        problems.append(
            f"{scope} does not provide {', '.join('{{' + u + '}}' for u in sorted(unknown))}. "
            f"Available: {', '.join('{{' + v + '}}' for v in spec.variables)}."
        )
    missing = set(spec.required) - used
    if missing:
        problems.append(
            f"Missing {', '.join('{{' + m + '}}' for m in sorted(missing))}, "
            f"which this prompt needs to be useful."
        )
    if not body.strip():
        problems.append("The prompt is empty.")
    return problems


def render(body: str, values: dict[str, str]) -> str:
    """Substitute values, leaving nothing unresolved.

    An unfilled `{{variable}}` reaching a paid API call would waste money on a
    confused request, so a missing value becomes an empty string and the line it
    sits on is dropped if that leaves it bare.
    """
    def swap(match: re.Match[str]) -> str:
        return (values.get(match.group(1)) or "").strip()

    filled = VARIABLE.sub(swap, body)
    # Drop "Label:" lines whose value turned out empty — they only confuse.
    kept = [
        line
        for line in filled.splitlines()
        if not re.fullmatch(r"\s*[A-Z][A-Za-z ]{0,24}:\s*", line)
    ]
    return "\n".join(kept).strip()


def seed_defaults() -> None:
    """Insert the shipped templates once, without disturbing edits."""
    with db.cursor() as cur:
        for scope, body in SEEDS.items():
            cur.execute(
                "INSERT INTO prompts(scope, name, body, is_default, is_active, updated_at) "
                "VALUES(?, 'Default', ?, 1, 1, ?) "
                "ON CONFLICT(scope, name) DO NOTHING",
                (scope, body, _now()),
            )


def describe_scopes() -> list[dict[str, Any]]:
    return [
        {
            "key": s.key,
            "label": s.label,
            "description": s.description,
            "variables": list(s.variables),
            "required": list(s.required),
        }
        for s in SCOPES
    ]


def list_prompts(scope: str | None = None) -> list[dict[str, Any]]:
    sql = (
        "SELECT id, scope, name, body, is_default, is_active, updated_at FROM prompts"
    )
    args: tuple[Any, ...] = ()
    if scope:
        sql += " WHERE scope = ?"
        args = (scope,)
    sql += " ORDER BY scope, is_default DESC, name"
    with db.cursor() as cur:
        return [
            dict(r) | {"is_default": bool(r["is_default"]), "is_active": bool(r["is_active"])}
            for r in cur.execute(sql, args)
        ]


def get_prompt(prompt_id: int) -> dict[str, Any]:
    with db.cursor() as cur:
        row = cur.execute(
            "SELECT id, scope, name, body, is_default, is_active, updated_at "
            "FROM prompts WHERE id = ?",
            (prompt_id,),
        ).fetchone()
    if row is None:
        raise db.NotFound(f"no prompt with id {prompt_id}")
    return dict(row) | {"is_default": bool(row["is_default"]), "is_active": bool(row["is_active"])}


def active_prompt(scope: str) -> dict[str, Any]:
    """The template a feature should actually use."""
    if scope not in SCOPE_KEYS:
        raise PromptError(f"unknown scope {scope!r}")
    seed_defaults()
    with db.cursor() as cur:
        row = cur.execute(
            "SELECT id, scope, name, body FROM prompts "
            "WHERE scope = ? ORDER BY is_active DESC, is_default DESC LIMIT 1",
            (scope,),
        ).fetchone()
    if row is None:
        raise PromptError(f"no prompt available for {scope!r}")
    return dict(row)


def save_prompt(
    scope: str, name: str, body: str, prompt_id: int | None = None
) -> dict[str, Any]:
    """Create or update, keeping the previous body as a version."""
    if scope not in SCOPE_KEYS:
        raise PromptError(f"unknown scope {scope!r}")
    name = name.strip() or "Untitled"

    with db.cursor() as cur:
        if prompt_id is not None:
            previous = cur.execute(
                "SELECT body FROM prompts WHERE id = ?", (prompt_id,)
            ).fetchone()
            if previous is None:
                raise db.NotFound(f"no prompt with id {prompt_id}")
            if previous["body"] != body:
                cur.execute(
                    "INSERT INTO prompt_versions(prompt_id, body, saved_at) VALUES(?, ?, ?)",
                    (prompt_id, previous["body"], _now()),
                )
            cur.execute(
                "UPDATE prompts SET name = ?, body = ?, updated_at = ? WHERE id = ?",
                (name, body, _now(), prompt_id),
            )
            new_id = prompt_id
        else:
            cur.execute(
                "INSERT INTO prompts(scope, name, body, is_default, is_active, updated_at) "
                "VALUES(?, ?, ?, 0, 0, ?) "
                "ON CONFLICT(scope, name) DO UPDATE SET body=excluded.body, "
                "  updated_at=excluded.updated_at",
                (scope, name, body, _now()),
            )
            new_id = cur.execute(
                "SELECT id FROM prompts WHERE scope = ? AND name = ?", (scope, name)
            ).fetchone()["id"]

    return get_prompt(new_id)


def set_active(prompt_id: int) -> dict[str, Any]:
    prompt = get_prompt(prompt_id)
    with db.cursor() as cur:
        cur.execute("UPDATE prompts SET is_active = 0 WHERE scope = ?", (prompt["scope"],))
        cur.execute("UPDATE prompts SET is_active = 1 WHERE id = ?", (prompt_id,))
    return get_prompt(prompt_id)


def restore_default(prompt_id: int) -> dict[str, Any]:
    prompt = get_prompt(prompt_id)
    seed = SEEDS.get(prompt["scope"])
    if seed is None or not prompt["is_default"]:
        raise PromptError("only a shipped default can be restored")
    return save_prompt(prompt["scope"], prompt["name"], seed, prompt_id)


def versions(prompt_id: int) -> list[dict[str, Any]]:
    with db.cursor() as cur:
        return [
            dict(r)
            for r in cur.execute(
                "SELECT id, body, saved_at FROM prompt_versions "
                "WHERE prompt_id = ? ORDER BY saved_at DESC, id DESC",
                (prompt_id,),
            )
        ]


def delete_prompt(prompt_id: int) -> None:
    prompt = get_prompt(prompt_id)
    if prompt["is_default"]:
        raise PromptError("the shipped default cannot be deleted, only edited or restored")
    with db.cursor() as cur:
        cur.execute("DELETE FROM prompts WHERE id = ?", (prompt_id,))
