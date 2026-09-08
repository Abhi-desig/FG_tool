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

Two scopes shape a poster, and between them they hold the poster engine's own
rules (v1.1, ADR-036) so the operator can tune them here rather than in code:

* **`poster-concept`** turns the copy into a visual idea, under the engine's
  specificity budget — a named material, a stated light direction, a camera
  position, a named highlight, one imperfection, qualified colours — and states
  the reserved zone three ways, because an area a prompt does not claim is one
  the image model fills. Its answer fills `{{concept}}` in whichever design was
  picked from `data/poster_prompts/`.
* **`poster-edit`** is the freeze clause behind "Change this": the full
  pixel-for-pixel inventory that keeps a one-line change from becoming a redraw.

The designs themselves are files, not rows here — they are the shop's own work
and `features/posters.py` reads them (ADR-034). The rules that can be *checked*
rather than merely asked for live in `features/posterspec.py`, which reads the
finished prompt before any money is spent.

**A shipped default the operator has never edited is updated in place** when its
seed here improves; anything they have edited is left alone. See
`seed_defaults`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
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
        key="poster-edit",
        label="Poster change instruction",
        description=(
            "Wraps your note in the freeze clause that stops Gemini redrawing "
            "the whole poster when you asked it to change one thing. Used by "
            "“Change this” under a finished poster."
        ),
        variables=("note",),
        required=("note",),
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
        "You are an art director deciding what a printed shop poster should look\n"
        "like, from its words alone. This is for a printing and advertising shop\n"
        "in Kerala.\n"
        "\n"
        "Headline: {{main}}\n"
        "Second line: {{h1}}\n"
        "Third line: {{h2}}\n"
        "\n"
        "Write one paragraph — four to six sentences — describing the picture the\n"
        "words will sit on. An image model reads this, so every sentence must\n"
        "name something it can draw.\n"
        "\n"
        "The paragraph must contain all six of these:\n"
        "- One named material with its finish — weathered red clay tile, brushed\n"
        "  stainless, powder-coated white steel. Not \"metal\", not \"wood\".\n"
        "- One time of day, with the light direction and quality stated: late\n"
        "  golden-hour light raking in from the left, flat overcast light from\n"
        "  above.\n"
        "- One camera position and distance: straight-on at eye level from about\n"
        "  twelve metres, low three-quarter view from two metres.\n"
        "- One named specular highlight on a named surface.\n"
        "- One small authentic imperfection — a slightly uneven tile course, a\n"
        "  worn step edge, one strand of a garland hanging lower.\n"
        "- Colours named with a qualifier: warm gold, deep navy, dusty green.\n"
        "  Never a bare colour name.\n"
        "\n"
        "Then state the reserved zone, in this order and all three ways, because\n"
        "an area you do not claim is one the image model fills — usually over the\n"
        "type:\n"
        "1. As geometry, with a percentage: \"the upper 45% of the frame is held\n"
        "   empty\".\n"
        "2. As positive content, described as a subject in its own right: \"one\n"
        "   continuous field of deep blue sky, evenly graded and slightly deeper\n"
        "   toward the top, smooth and free of detail\".\n"
        "3. As a short fence naming what may not enter it: \"nothing enters this\n"
        "   area — no clouds, no birds, no wires, no palm fronds, no roofline\".\n"
        "Never write only \"empty space\" or \"negative space\".\n"
        "\n"
        "Never use these words. Each one tells an image model nothing and takes\n"
        "the place of something that would: beautiful, stunning, vibrant,\n"
        "dynamic, eye-catching, modern-looking, high quality, professional,\n"
        "masterpiece, 4k, ultra HD, award-winning, breathtaking, perfect.\n"
        "\n"
        "Describe the picture only. Do not repeat the poster's words back, do not\n"
        "suggest wording, and do not mention fonts, type sizes or layout. No\n"
        "heading, no code fence, no prose around your answer.\n"
    ),
    "poster-edit": (
        "Using the provided image, change only {{note}}.\n"
        "\n"
        "Keep every other part of the image exactly as it is, pixel for pixel:\n"
        "the same framing, crop and aspect ratio; the same camera angle, height\n"
        "and focal length; the same composition and the position of every object;\n"
        "the same subject identity, face, skin tone, hair, expression and pose;\n"
        "the same clothing, materials and surface texture; the same light\n"
        "direction, intensity, colour temperature and shadow shape; the same\n"
        "colour grade, contrast, saturation and grain; the same background\n"
        "including all out-of-focus areas; and every text element unchanged in\n"
        "wording, spelling, font, weight, size, colour and position.\n"
        "\n"
        "If the change forces a secondary effect — a colour swap altering a\n"
        "reflection, a removed object revealing background — allow only the\n"
        "minimum local consequence.\n"
        "\n"
        "Do not regenerate or reinterpret the poster. Do not improve, re-balance,\n"
        "re-grade, re-crop, re-light or re-typeset it. Do not add or remove\n"
        "anything that is not named above, and do not add any word, number,\n"
        "price, date or phone number that is not already on the poster. This is a\n"
        "targeted local edit to an existing poster, not a new image.\n"
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


# Bodies this app shipped in an earlier version, by scope, as SHA-256 of the
# exact stored text. A default still holding one of these has never been written
# by the operator, whatever its version history says, so an update may replace
# it — see `seed_defaults`.
#
# Hashes rather than the retired prose: the point is to recognise old text, not
# to keep a growing museum of it in the source. To retire a seed, hash the body
# being replaced and add it here:
#
#     python -c "import hashlib,pathlib;print(hashlib.sha256(
#         pathlib.Path('old.txt').read_bytes()).hexdigest())"
SUPERSEDED_SEEDS: dict[str, frozenset[str]] = {
    # The pre-v1.1 concept prompt: "two or three sentences describing the
    # picture", with no specificity budget and no reserved zone (ADR-036).
    "poster-concept": frozenset(
        {"83e447f9744e86dc3c59dc16c4770914314d3693af76dedb3c027ae34935a605"}
    ),
}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _is_shipped_text(scope: str, body: str) -> bool:
    """Whether this body is one the app wrote, rather than one the operator did."""
    if body == SEEDS.get(scope):
        return True
    digest = sha256(body.encode("utf-8")).hexdigest()
    return digest in SUPERSEDED_SEEDS.get(scope, frozenset())


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
            # An improved shipped prompt must reach a database that already ran,
            # or it only ever helps a fresh install — and "delete data.db" is
            # not an upgrade path for a machine with nobody to run it.
            # `INSERT ... DO NOTHING` above cannot do it, so the refresh is
            # separate and narrow.
            #
            # Two ways to know a body is the app's and not the operator's, and
            # both are needed. No rows in `prompt_versions` means it has never
            # been saved over at all. But a version history is not proof of
            # authorship either: restoring a default, or a round trip through
            # the editor that changed nothing, files a version while leaving
            # shipped text in place — so a body that still matches something
            # this app has shipped is ours to replace whatever its history says.
            #
            # Anything else is the operator's wording and is never touched.
            row = cur.execute(
                "SELECT id, body FROM prompts "
                "WHERE scope = ? AND name = 'Default' AND is_default = 1",
                (scope,),
            ).fetchone()
            if row is None or row["body"] == body:
                continue
            edited = cur.execute(
                "SELECT 1 FROM prompt_versions WHERE prompt_id = ? LIMIT 1",
                (row["id"],),
            ).fetchone()
            if edited and not _is_shipped_text(scope, row["body"]):
                continue
            cur.execute(
                "UPDATE prompts SET body = ?, updated_at = ? WHERE id = ?",
                (body, _now(), row["id"]),
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
