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
from backend.templating import variables_in

log = logging.getLogger(__name__)

DESIGNS_DIR = config.DATA_DIR / "poster_prompts"

# A style file is a **style spec**, not a whole prompt: geometry, reserved zone,
# scene direction, type treatment, shapes, palette. It says nothing about the
# poster's words.
#
# That split is what makes one call able to choose. In auto mode the model is
# shown all nine specs at once and picks one, so a spec that carried the copy
# inside it would repeat the operator's words nine times over. The app states
# the copy once, and the spec describes only the look (ADR-037).
VARIABLES: tuple[str, ...] = ()

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
    # `aspect: 4:5` in the header. Sent to Google as a request parameter as well
    # as being written in the prompt, because a ratio stated only in prose
    # drifts — usually to square, which crops the type off a portrait poster
    # (ADR-036). Empty means the design does not say, and nothing is forced.
    aspect: str = ""
    # `when:` and `tone:` are what the model reads to choose. `when` is the
    # occasion and content load this style suits; `tone` is the shape of copy it
    # can carry, because a style built for seven short lines fails on one long
    # one. Both go into the selection prompt; neither reaches the drawing.
    when: str = ""
    tone: str = ""

    def as_dict(self) -> dict[str, object]:
        """What the screen needs. The body is deliberately not sent.

        A prompt is the shop's own work and the thing that makes its posters
        look the way they do. It has no business in a browser, where it would
        sit in the page source of a tool the operator shows clients.
        """
        return {
            "key": self.key,
            "name": self.name,
            "description": self.description,
            "aspect": self.aspect,
        }


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

    leftover = set(variables_in(body))
    if leftover:
        # Not fatal, but always a mistake now. A style file is a spec, and the
        # app writes the copy — so a `{{main}}` left in one would reach Google
        # verbatim as those six characters. Loud rather than silent, because a
        # style carrying its own placeholder is a style whose poster comes back
        # with a literal `{{main}}` printed on it.
        log.warning(
            "Poster style %s still contains %s. Style files describe the look "
            "only; the app writes the words. Remove the placeholder.",
            path.name,
            ", ".join("{{" + name + "}}" for name in sorted(leftover)),
        )

    return Design(
        key=path.stem,
        name=fields.get("name") or path.stem.replace("-", " ").replace("_", " ").title(),
        description=fields.get("description", ""),
        body=body,
        aspect=fields.get("aspect", "").replace(" ", ""),
        when=fields.get("when", ""),
        tone=fields.get("tone", ""),
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
    # By key, not by name. The library is numbered — `01-quiet-premium` through
    # `09-announcement-stack` — and sorting by title put 09 first because
    # "Announcement" beats "Quiet". The number is the operator's mental index
    # and the order the catalogue is read in, so it wins.
    return sorted((d for d in found if d is not None), key=lambda d: d.key)


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


# What every poster gets, whichever style is used. These are the global locks
# from the engine (ADR-036): the app states them rather than trusting nine style
# files to each remember them.
_LOCKS = (
    "Every string in quotes above is drawn exactly as written — same wording, "
    "same spelling, same spacing, same digits — and no other words appear "
    "anywhere in the image. All lettering sharp, correctly spelled, evenly "
    "tracked and sitting on a clean baseline. Clean neo-grotesque sans-serif, "
    "two weights maximum. Nothing within 8% of any edge. Do not invent any "
    "word, number, price, date or phone number that is not written above. Do "
    "not add a logo. No watermarks, no extra text, no additional words "
    "anywhere in the image."
)

# `STYLE: 04-offer-block` on its own line, then `WHY: …`. Tolerant of the
# decoration a model reaches for — bold markers, a leading bullet, a full stop —
# because the alternative to parsing it loosely is losing the field entirely.
_CHOICE = re.compile(r"^\W*STYLE\W*[:\-]\W*([0-9a-z][0-9a-z_-]*)", re.IGNORECASE | re.MULTILINE)
_REASON = re.compile(r"^\W*WHY\W*[:\-]\s*(.+)$", re.IGNORECASE | re.MULTILINE)


def _copy_block(copy: dict[str, str]) -> str:
    """The operator's words, quoted, once.

    Quoted because that is what `posterspec` reads to check the wording against
    what was pasted, and what tells the model which runs are lettering rather
    than description.
    """
    lines = [f'{tag}: "{copy[tag]}"' for tag in TAGS if copy.get(tag)]
    return "\n".join(lines)


def _brief(copy: dict[str, str], concept: str, aspect: str) -> str:
    """The half of the prompt that does not depend on which style is chosen."""
    shape = f"A {aspect} " if aspect else "A vertical "
    parts = [
        f"{shape}print poster for a shop in Kerala, ready to send to press.",
        "",
        "THE WORDS ON THE POSTER — draw exactly these, and nothing else:",
        _copy_block(copy),
    ]
    if concept.strip():
        parts += ["", "THE PICTURE:", concept.strip()]
    return "\n".join(parts)


def one_style_prompt(design: Design, copy: dict[str, str], concept: str = "") -> str:
    """The prompt for a poster in one known style.

    Used by the dev override, and by any later path that already knows which
    style it wants. The style's own spec arrives at full length here, which is
    the fidelity the nine-style selection prompt has to compress.
    """
    return "\n\n".join(
        [
            _brief(copy, concept, design.aspect),
            f"THE STYLE — {design.name}. Follow this exactly:",
            design.body.strip(),
            _LOCKS,
        ]
    )


def selection_prompt(
    copy: dict[str, str], concept: str = "", available: list[Design] | None = None
) -> str:
    """One prompt that both chooses a style and draws the poster.

    The model is shown every style's `when` and `tone` alongside its spec and
    asked to name its choice before drawing. The choice comes back as a line of
    text in the same response as the image, which is what makes it loggable
    rather than guessed at from the picture.

    The naming contract is stated twice — once as the required shape, once as
    the list of permitted keys — because a choice that cannot be parsed is a
    poster nobody can explain later.
    """
    styles = available if available is not None else designs()
    if not styles:
        raise PosterError(
            f"There are no poster styles in {DESIGNS_DIR}. Put one .md file per "
            "style in that folder."
        )

    catalogue = []
    for style in styles:
        entry = [f"[{style.key}] {style.name}"]
        if style.when:
            entry.append(f"  Use when: {style.when}")
        if style.tone:
            entry.append(f"  Copy it suits: {style.tone}")
        entry.append(f"  Spec: {' '.join(style.body.split())}")
        catalogue.append("\n".join(entry))

    keys = ", ".join(s.key for s in styles)
    return "\n\n".join(
        [
            _brief(copy, concept, auto_aspect(styles)),
            (
                f"CHOOSE ONE OF THESE {len(styles)} STYLES. They differ in look, in "
                "layout, and in the shape of copy they can carry — judge the words "
                "above against each style's `Use when` and `Copy it suits`, and pick "
                "the single best fit."
            ),
            "\n\n".join(catalogue),
            (
                "ANSWER IN THIS SHAPE. Two lines of text first, then the image:\n"
                "STYLE: <the key of the style you chose>\n"
                "WHY: <one short sentence naming what in the copy decided it>\n"
                f"The key must be exactly one of: {keys}.\n"
                "Then draw the poster in that style, following its spec exactly."
            ),
            _LOCKS,
        ]
    )


def auto_aspect(available: list[Design] | None = None) -> str:
    """The shape to request when the style is not known until the reply.

    A ratio has to be sent with the request, but in auto mode the choice has not
    been made yet — so it can only be forced when every style agrees on it. If
    they disagree, nothing is forced and each style's own prompt text is left to
    argue for its shape. Silent disagreement would reshape whichever styles lost.
    """
    shapes = {s.aspect for s in (available if available is not None else designs())}
    shapes.discard("")
    if len(shapes) == 1:
        return next(iter(shapes))
    if shapes:
        log.warning(
            "Poster styles disagree on aspect ratio (%s), so none is forced in "
            "auto mode. Give every style the same `aspect:` to pin the shape.",
            ", ".join(sorted(shapes)),
        )
    return ""


def parse_choice(text: str, available: list[Design] | None = None) -> tuple[str, str]:
    """The style the model says it chose, and why, or empty if it did not say.

    Validated against the folder rather than trusted: a key that is not a real
    style is worse than no key, because it would be logged and believed.
    """
    known = {s.key for s in (available if available is not None else designs())}
    match = _CHOICE.search(text or "")
    chosen = ""
    if match:
        candidate = match.group(1).casefold()
        if candidate in known:
            chosen = candidate
        else:
            log.warning("Model named a style that does not exist: %r", candidate)
    if not chosen:
        # No reason either. A "why" attached to a style that does not exist
        # would read on screen as the explanation for a real choice.
        return "", ""
    reason = _REASON.search(text or "")
    return chosen, (reason.group(1).strip() if reason else "")


__all__ = [
    "DESIGNS_DIR",
    "TAGS",
    "VARIABLES",
    "Design",
    "PosterError",
    "auto_aspect",
    "design",
    "designs",
    "one_style_prompt",
    "parse_choice",
    "parse_copy",
    "selection_prompt",
]
