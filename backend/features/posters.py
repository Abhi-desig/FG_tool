"""Poster designs: the shop's own prompt set, read from disk.

**What this module is now.** A reader for `data/poster_prompts/`, where each file
is one finished poster design written by the operator. It parses them, lists
them, and fills their placeholders from the poster's copy. Nothing here talks to
Google — `features/ai.py` owns every call that leaves the machine.

**What it used to be.** An SVG poster renderer, a layout engine and a text
fitter: the app composed the poster itself and drew every word as a real `<text>`
element, which is why Malayalam came out correctly spelled and why CorelDRAW
could still edit each line. That is gone. Gemini now draws the whole poster,
words included, from these prompts. The trade is recorded in ADR-034, along with
what it costs — the two things worth repeating here are that an image model
cannot spell Malayalam, and that the result is a raster nobody can edit.

**Why files rather than the database.** These are the operator's own designs,
written outside this app and dropped in. A folder is something they can edit in
any text editor, copy between machines, and back up by copying — none of which
is true of a row in `data.db`. The prompt *library* in Settings stays where it
is; it holds the app's own templates, which are a different thing.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from backend import config
from backend.templating import render, variables_in

log = logging.getLogger(__name__)

DESIGNS_DIR = config.DATA_DIR / "poster_prompts"

# What a design may refer to. `concept` is the visual idea — either written by
# the AI from the copy, or left empty when the operator supplied a reference
# image for it to work from instead.
VARIABLES = ("main", "h1", "h2", "concept")

# The tags the operator writes in front of their copy. Exactly three: this is a
# poster, not a form, and every extra field is one more thing to fill in before
# the interesting part.
TAGS = ("main", "h1", "h2")

# The header is optional: a design with nothing to say about itself opens on
# `---` with no blank line before it, which is what anyone writing one by hand
# does first. Requiring a newline ahead of the marker rejected exactly that.
_FRONT_MATTER = re.compile(r"\A(?:(.*?)\n)?---[ \t]*\n(.*)\Z", re.DOTALL)
# `main: Onam Sale` — a tag, then the line. Case-insensitive because the copy is
# pasted from WhatsApp and nobody is careful about it there.
_TAGGED = re.compile(rf"^\s*({'|'.join(TAGS)})\s*[:\-]\s*(.*)$", re.IGNORECASE)


class PosterError(ValueError):
    """A design file is unusable, or the copy is not."""


@dataclass(frozen=True)
class Design:
    """One poster design from the folder."""

    key: str
    name: str
    description: str
    body: str

    def as_dict(self) -> dict[str, object]:
        """What the screen needs. The body is deliberately not sent.

        A prompt is the shop's own work and the thing that makes its posters
        look the way they do. It has no business in a browser, where it would
        sit in the page source of a tool the operator shows clients.
        """
        return {"key": self.key, "name": self.name, "description": self.description}


def _parse(path: Path) -> Design | None:
    """One design file, or None with a warning if it is not one.

    A bad file never stops the others loading. The operator drops these in by
    hand, and one mistyped header must not empty the whole screen.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("Could not read poster design %s: %s", path.name, exc)
        return None

    match = _FRONT_MATTER.match(text)
    if match is None:
        log.warning(
            "Poster design %s has no `---` line separating its header from its "
            "prompt — skipped",
            path.name,
        )
        return None

    header, body = match.group(1) or "", match.group(2).strip()
    fields: dict[str, str] = {}
    for line in header.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip().casefold()] = value.strip()

    if not body:
        log.warning("Poster design %s has no prompt under its header — skipped", path.name)
        return None

    unknown = set(variables_in(body)) - set(VARIABLES)
    if unknown:
        # Not fatal: `render` empties an unknown placeholder rather than sending
        # `{{offer}}` to Google. Said out loud so a typo is findable.
        log.warning(
            "Poster design %s uses %s, which a poster does not provide. Available: %s",
            path.name,
            ", ".join(sorted(unknown)),
            ", ".join(VARIABLES),
        )

    return Design(
        key=path.stem,
        name=fields.get("name") or path.stem.replace("-", " ").replace("_", " ").title(),
        description=fields.get("description", ""),
        body=body,
    )


def designs() -> list[Design]:
    """Every usable design, by name.

    Read on each call rather than cached. The operator adds a design by dropping
    a file in the folder, and having to restart the server to see it is exactly
    the kind of thing that makes a tool feel broken. The folder holds a handful
    of small files; this costs nothing.
    """
    if not DESIGNS_DIR.is_dir():
        log.warning("No poster designs at %s", DESIGNS_DIR)
        return []
    found = [
        _parse(path)
        for path in sorted(DESIGNS_DIR.glob("*.md"))
        # The folder's own instructions are not a design. Named rather than
        # inferred, so a real design is never skipped for looking like docs.
        if path.name.casefold() != "readme.md"
    ]
    return sorted((d for d in found if d is not None), key=lambda d: d.name.casefold())


def design(key: str) -> Design:
    """One design by key, or `PosterError` naming what is available."""
    for candidate in designs():
        if candidate.key == key:
            return candidate
    known = ", ".join(d.key for d in designs()) or "none"
    raise PosterError(f"No poster design called “{key}”. Available: {known}.")


def parse_copy(text: str) -> dict[str, str]:
    """Pull `main`, `h1` and `h2` out of the operator's pasted copy.

    Tolerant on purpose. The copy arrives in a WhatsApp message and is pasted
    straight in, so the tag may be `main:`, `Main -` or `MAIN :`. An untagged
    first line becomes `main`, because that is what it always is.

    Later lines under one tag are joined onto it, so a two-line headline works
    without a second tag.
    """
    values: dict[str, str] = {}
    current: str | None = None

    for line in text.splitlines():
        tagged = _TAGGED.match(line)
        if tagged:
            current = tagged.group(1).casefold()
            values[current] = tagged.group(2).strip()
            continue
        stripped = line.strip()
        if not stripped:
            continue
        if current is None:
            # An untagged opening line is the headline. Anything else untagged
            # is dropped rather than guessed at.
            current = "main"
            values.setdefault(current, stripped)
            continue
        values[current] = f"{values.get(current, '')} {stripped}".strip()

    return {tag: values[tag] for tag in TAGS if values.get(tag)}


def prompt_for(design_key: str, copy: dict[str, str], concept: str = "") -> str:
    """The finished prompt for one poster.

    `render` empties any placeholder with no value and drops the line if that
    leaves it bare, so a design asking for `{{h2}}` on a poster that has none
    does not send Google a dangling label.
    """
    values = {tag: copy.get(tag, "") for tag in TAGS}
    values["concept"] = concept
    return render(design(design_key).body, values)


__all__ = [
    "DESIGNS_DIR",
    "TAGS",
    "VARIABLES",
    "Design",
    "PosterError",
    "design",
    "designs",
    "parse_copy",
    "prompt_for",
]
