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

`poster-concept` is the one that shapes a poster now: it turns the operator's
copy into a visual idea, which is then dropped into whichever design they picked
from `data/poster_prompts/`. The designs themselves are files, not rows here —
they are the shop's own work and `features/posters.py` reads them (ADR-034).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from backend import db
from backend.templating import VARIABLE, render, variables_in

# Substitution moved to `backend.templating` when design styles started needing
# the same rules. Re-exported here so `prompts.render(...)` keeps working.
__all__ = ["VARIABLE", "render", "variables_in"]


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
        key="poster-concept",
        label="Poster visual idea",
        description=(
            "Reads the poster's copy and describes the picture it should be. "
            "Its answer fills {{concept}} in the chosen design. Skipped entirely "
            "when the operator supplies a reference image instead."
        ),
        variables=("main", "h1", "h2"),
        required=("main",),
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
    "poster-concept": (
        "You are deciding what a printed shop poster should look like, from its\n"
        "words alone.\n"
        "\n"
        "Headline: {{main}}\n"
        "Second line: {{h1}}\n"
        "Third line: {{h2}}\n"
        "\n"
        "Reply with two or three sentences describing the picture: the subject,\n"
        "the mood, the colours, the light, and where the poster should be left\n"
        "calm so the words stay readable.\n"
        "\n"
        "Rules:\n"
        "- Describe the picture only. Do not repeat the words back, do not\n"
        "  suggest wording, and do not mention fonts or type sizes.\n"
        "- This is for a printing and advertising shop in Kerala. Keep the\n"
        "  imagery plausible for that, not generic stock photography.\n"
        "- No prose around your answer, no heading, no code fence.\n"
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


def seed_defaults() -> None:
    """Insert the shipped templates once, without disturbing edits.

    Also clears out **shipped** templates for scopes that no longer exist. When
    `poster-layout`, `poster-copy` and `poster-artwork` were retired (ADR-034)
    their seeded rows stayed behind in every database that had already run,
    listed by `/settings/prompts` under a scope the screen has no dropdown entry
    for and no default left to restore.

    Only `is_default` rows go. A template the operator wrote themselves is
    theirs, even if the feature it was for is gone — deleting it to tidy up
    would be a poor trade, and it costs one row to keep.
    """
    with db.cursor() as cur:
        for scope, body in SEEDS.items():
            cur.execute(
                "INSERT INTO prompts(scope, name, body, is_default, is_active, updated_at) "
                "VALUES(?, 'Default', ?, 1, 1, ?) "
                "ON CONFLICT(scope, name) DO NOTHING",
                (scope, body, _now()),
            )
        placeholders = ",".join("?" * len(SCOPE_KEYS))
        cur.execute(
            f"DELETE FROM prompts WHERE is_default = 1 AND scope NOT IN ({placeholders})",
            tuple(SCOPE_KEYS),
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
