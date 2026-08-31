"""Poster design styles — a named look, with the prompt structure that makes it.

A style is the thing the designer actually chooses. It carries two halves that
have to agree with each other:

* **A fixed prompt structure.** The wording is owned by the shop, edited in
  Settings, and never retyped per job. The operator's copy is substituted into
  it, so the picture is generated *from the words on the poster* rather than
  from a separate description of a picture nobody asked for.
* **Text defaults.** The colours, weights and sizes the words take on when this
  style is picked. A festival look with the minimal style's thin grey text is
  not the festival look, so the two travel together.

**The copy is context, never content.** Every seeded body says so three times,
because an image model handed a headline will otherwise try to draw it — and a
drawn Malayalam headline is exactly the wrong output. The app draws every word
itself; that is the whole reason this shop can print Malayalam posters at all.

Styles are editable and deletable, and a shipped one can always be restored,
so experimenting with a prompt can never cost the shop a look that was working.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from backend import db
from backend.templating import render, variables_in

# What a style body may refer to. `headline`, `offer` and `occasion` are the
# operator's own copy; `idea` is their optional own suggestion for the picture.
VARIABLES: tuple[str, ...] = (
    "headline",
    "offer",
    "occasion",
    "phone",
    "idea",
    "subject",
    "palette",
    "aspect",
)

# A style that never mentions the copy would generate a picture unrelated to the
# poster, which is the one thing this feature exists to prevent.
REQUIRED: tuple[str, ...] = ("headline",)

KEY_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,38}$")

# Every seeded body ends with this. Kept as one constant so a wording fix
# reaches all six styles, and so the rule is visibly the same everywhere.
_NO_LETTERING = (
    "The words above are context only — they tell you what the poster is about.\n"
    "Do NOT draw them. No lettering, no words, no numbers, no logos and no\n"
    "watermarks anywhere in the image. Every word is added afterwards by the app\n"
    "as real, editable text."
)


class StyleError(ValueError):
    """The style or its prompt is not usable."""


@dataclass(frozen=True)
class Seed:
    key: str
    name: str
    description: str
    palette: str
    swatches: tuple[str, ...]
    body: str
    text_defaults: dict[str, Any]


def _body(look: str) -> str:
    """Assemble a style body from the half that differs — the look."""
    return (
        "A background picture for a printed poster made by a shop in Kerala.\n"
        "\n"
        "The poster will carry these words:\n"
        "  Headline: {{headline}}\n"
        "  Offer: {{offer}}\n"
        "  Occasion: {{occasion}}\n"
        "\n"
        "Make the picture *about* that message. Choose a subject a customer would\n"
        "connect with those exact words at a glance.\n"
        "Extra instruction: {{idea}}\n"
        "Also consider: {{subject}}\n"
        "\n"
        f"{look}\n"
        "Colours: {{palette}}\n"
        "Aspect: {{aspect}}\n"
        "\n"
        "Leave the upper third and the lower fifth calm and uncluttered — the\n"
        "words sit there and must stay readable.\n"
        "\n"
        f"{_NO_LETTERING}"
    )


SEEDS: tuple[Seed, ...] = (
    Seed(
        key="festival",
        name="Festival",
        description="Onam, Vishu, Diwali. Warm, garlanded, lamp-lit.",
        palette="marigold gold, deep maroon, kasavu cream",
        swatches=("#c8102e", "#e8a33d", "#f6e7c8"),
        body=_body(
            "Look: warm festival photography. Marigold and jasmine garlands, brass\n"
            "lamps, banana leaf, kasavu gold borders, soft evening light, shallow\n"
            "depth of field. Rich and celebratory — full of colour, never garish."
        ),
        text_defaults={
            "background_colour": "#3a0d12",
            "headline": {"colour": "#f6e7c8", "size": "large", "weight": "bold"},
            "offer": {"colour": "#ffffff", "size": "huge", "weight": "bold"},
            "occasion": {"colour": "#e8a33d", "size": "medium", "weight": "regular"},
            "phone": {"colour": "#f6e7c8", "size": "small", "weight": "regular"},
        },
    ),
    Seed(
        key="offer",
        name="Big offer",
        description="Sale, discount, clearance. Loud, high contrast, one hero.",
        palette="signal red, bright yellow, clean white",
        swatches=("#d81f26", "#ffc400", "#ffffff"),
        body=_body(
            "Look: bold retail advertising photography. One clear hero subject,\n"
            "strong directional light, saturated high-contrast colour, a simple\n"
            "sweeping background with no clutter. Energetic and unmissable from\n"
            "across a street."
        ),
        text_defaults={
            "background_colour": "#b3151b",
            "headline": {"colour": "#ffc400", "size": "large", "weight": "bold"},
            "offer": {"colour": "#ffffff", "size": "huge", "weight": "bold"},
            "occasion": {"colour": "#ffffff", "size": "small", "weight": "regular"},
            "phone": {"colour": "#ffffff", "size": "small", "weight": "bold"},
        },
    ),
    Seed(
        key="wedding",
        name="Wedding",
        description="Invitations, engagements, anniversaries. Soft and formal.",
        palette="blush, ivory, muted rose gold",
        swatches=("#c9a227", "#f3e6e2", "#8a6f6a"),
        body=_body(
            "Look: soft, elegant editorial photography. Fine florals, silk and\n"
            "chiffon texture, gentle diffused light, generous empty space, muted\n"
            "pastel palette. Restrained and formal — quiet rather than busy."
        ),
        text_defaults={
            "background_colour": "#f3e6e2",
            "headline": {"colour": "#5b4038", "size": "large", "weight": "regular"},
            "offer": {"colour": "#8a6f6a", "size": "medium", "weight": "regular"},
            "occasion": {"colour": "#c9a227", "size": "medium", "weight": "regular"},
            "phone": {"colour": "#5b4038", "size": "small", "weight": "regular"},
        },
    ),
    Seed(
        key="minimal",
        name="Modern minimal",
        description="Openings, announcements. Lots of empty space, one idea.",
        palette="off-white, ink, a single accent",
        swatches=("#16161d", "#f5f5f0", "#2f6f5e"),
        body=_body(
            "Look: clean modern minimalism. One simple subject, large areas of flat\n"
            "uninterrupted colour or soft gradient, even light, no texture noise.\n"
            "Most of the frame is deliberately empty."
        ),
        text_defaults={
            "background_colour": "#f5f5f0",
            "headline": {"colour": "#16161d", "size": "large", "weight": "bold"},
            "offer": {"colour": "#2f6f5e", "size": "medium", "weight": "bold"},
            "occasion": {"colour": "#4a4a52", "size": "small", "weight": "regular"},
            "phone": {"colour": "#16161d", "size": "small", "weight": "regular"},
        },
    ),
    Seed(
        key="product",
        name="Product shot",
        description="One item, studio lit, on a clean backdrop.",
        palette="neutral grey backdrop, true product colour",
        swatches=("#2b2b31", "#c9ccd1", "#ffffff"),
        body=_body(
            "Look: studio product photography. A single item centred low in the\n"
            "frame, soft box lighting with a gentle reflection beneath it, seamless\n"
            "graduated backdrop, true colour, no props competing for attention."
        ),
        text_defaults={
            "background_colour": "#2b2b31",
            "headline": {"colour": "#ffffff", "size": "large", "weight": "bold"},
            "offer": {"colour": "#ffc400", "size": "huge", "weight": "bold"},
            "occasion": {"colour": "#c9ccd1", "size": "small", "weight": "regular"},
            "phone": {"colour": "#ffffff", "size": "small", "weight": "regular"},
        },
    ),
    Seed(
        key="event",
        name="Event banner",
        description="Inaugurations, meetings, sports days. Flat, bold, flex-ready.",
        palette="deep blue, white, one hot accent",
        swatches=("#123a6b", "#ffffff", "#f26522"),
        body=_body(
            "Look: flat graphic poster art, not photography. Bold blocks of colour,\n"
            "simple geometric shapes, strong diagonals, a clear horizon. Designed to\n"
            "hold up printed large and read from a distance on a flex banner."
        ),
        text_defaults={
            "background_colour": "#123a6b",
            "headline": {"colour": "#ffffff", "size": "huge", "weight": "bold"},
            "offer": {"colour": "#f26522", "size": "large", "weight": "bold"},
            "occasion": {"colour": "#ffffff", "size": "medium", "weight": "regular"},
            "phone": {"colour": "#ffffff", "size": "small", "weight": "bold"},
        },
    ),
)

SEED_KEYS = {s.key: s for s in SEEDS}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _row(row: Any) -> dict[str, Any]:
    """One database row as the shape the API and UI use."""
    return {
        "id": row["id"],
        "key": row["key"],
        "name": row["name"],
        "description": row["description"],
        "body": row["body"],
        "palette": row["palette"],
        "swatches": _loads(row["swatches"], []),
        "text_defaults": normalise_text_defaults(_loads(row["text_defaults"], {})),
        "is_default": bool(row["is_default"]),
        "sort_order": row["sort_order"],
        "updated_at": row["updated_at"],
    }


def _loads(raw: str, fallback: Any) -> Any:
    """Tolerate a hand-edited or older JSON column rather than failing a page."""
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return fallback


# --- the text defaults shape ------------------------------------------------
#
# A style has always carried `{colour, size, weight}` per role. Typography adds
# five more fields, and they go into the same JSON column rather than new
# columns: `db.py` creates its whole schema with `CREATE TABLE IF NOT EXISTS`
# and has no migration mechanism at all, so a new column would need a
# hand-written ALTER on a machine with nobody to run it. A JSON key costs
# nothing and an old row simply lacks it.
#
# `normalise_text_defaults` is applied on *read* as well as on write, which is
# what makes that safe: every row already in the operator's database is
# repaired in memory on the way out, so nothing has to be migrated and an old
# style renders exactly as it did before.

ROLES: tuple[str, ...] = ("headline", "offer", "occasion", "phone")

# Bounds, mirrored in `features/posters.py`. A style is a set of defaults, not a
# licence to produce an unprintable poster.
MIN_SIZE_FRACTION = 0.01
MAX_SIZE_FRACTION = 0.40
MIN_LEADING = 0.8
MAX_LEADING = 3.0
MIN_TRACKING = -0.05
MAX_TRACKING = 0.5

SIZES = ("small", "medium", "large", "huge")
WEIGHTS = ("regular", "bold")
ALIGNS = ("left", "centre", "right")
# Only two. Malayalam is unicameral so title case is meaningless there, and for
# Latin it is locale-dependent — neither belongs on a poster the shop prints.
CASES = ("as-typed", "upper")

DEFAULT_BACKGROUND = "#111111"

ROLE_DEFAULT: dict[str, Any] = {
    "colour": "#ffffff",
    # Kept as the preset. Replacing the buckets outright would touch
    # `TextBlockIn.size`'s Literal, `ai._FALLBACK_PLACEMENT`, the published
    # `/posters/presets` sizes and a golden fixture — four blast radii for a
    # cosmetic win. `size_fraction` overrides it when the operator sets one.
    "size": "medium",
    "size_fraction": None,
    "weight": "regular",
    "tracking": 0.0,
    "leading": 1.25,
    "align": "centre",
    "case": "as-typed",
}

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _colour(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    return text if _HEX.match(text) else fallback


def _one_of(value: Any, allowed: tuple[str, ...], fallback: str) -> str:
    text = str(value or "").strip().lower()
    return text if text in allowed else fallback


def _number(value: Any, low: float, high: float, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return min(max(number, low), high)


def normalise_text_defaults(raw: Any) -> dict[str, Any]:
    """Every role, every field, clamped — and nothing else.

    Unknown keys inside a role are **dropped**, which is a fix as well as a
    tidy-up: `PosterDesigner` spread this straight onto a block, so a
    hand-edited style containing `"headline": {"id": "t1"}` could overwrite a
    block's identity and collide two blocks onto one id.

    Returned rather than raised, in the spirit of `_loads` above: a style that
    has been edited by hand into something odd should render with sensible
    defaults, not fail the whole Settings page.
    """
    source = raw if isinstance(raw, dict) else {}
    out: dict[str, Any] = {
        "background_colour": _colour(source.get("background_colour"), DEFAULT_BACKGROUND)
    }
    for role in ROLES:
        spec = source.get(role)
        spec = spec if isinstance(spec, dict) else {}
        fraction = spec.get("size_fraction")
        out[role] = {
            "colour": _colour(spec.get("colour"), ROLE_DEFAULT["colour"]),
            "size": _one_of(spec.get("size"), SIZES, ROLE_DEFAULT["size"]),
            # None means "use the bucket". A number outside the printable range
            # is treated the same way rather than clamped to a size nobody chose.
            "size_fraction": (
                None
                if fraction is None
                else _number(fraction, MIN_SIZE_FRACTION, MAX_SIZE_FRACTION, None)  # type: ignore[arg-type]
            ),
            "weight": _one_of(spec.get("weight"), WEIGHTS, ROLE_DEFAULT["weight"]),
            "tracking": _number(
                spec.get("tracking"), MIN_TRACKING, MAX_TRACKING, ROLE_DEFAULT["tracking"]
            ),
            "leading": _number(
                spec.get("leading"), MIN_LEADING, MAX_LEADING, ROLE_DEFAULT["leading"]
            ),
            "align": _one_of(spec.get("align"), ALIGNS, ROLE_DEFAULT["align"]),
            "case": _one_of(spec.get("case"), CASES, ROLE_DEFAULT["case"]),
        }
    return out


def validate(body: str) -> list[str]:
    """Problems with a style body, in words the operator can act on.

    Returned rather than raised, so the editor can show every issue at once
    instead of one per save.
    """
    problems: list[str] = []
    if not body.strip():
        problems.append("The prompt is empty.")
        return problems

    used = set(variables_in(body))
    unknown = used - set(VARIABLES)
    if unknown:
        problems.append(
            f"A style does not provide {', '.join('{{' + u + '}}' for u in sorted(unknown))}. "
            f"Available: {', '.join('{{' + v + '}}' for v in VARIABLES)}."
        )
    missing = set(REQUIRED) - used
    if missing:
        problems.append(
            f"Missing {', '.join('{{' + m + '}}' for m in sorted(missing))} — without it "
            "the picture will have nothing to do with the poster."
        )
    lowered = body.lower()
    if "no lettering" not in lowered and "no words" not in lowered:
        problems.append(
            "This prompt never forbids lettering. The image model will put words "
            "into the picture, and Malayalam drawn by the model comes out wrong."
        )
    return problems


def seed_defaults() -> None:
    """Insert the shipped styles once, without disturbing edits."""
    with db.cursor() as cur:
        for order, seed in enumerate(SEEDS):
            cur.execute(
                "INSERT INTO poster_styles"
                "(key, name, description, body, palette, swatches, text_defaults, "
                " is_default, sort_order, updated_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, 1, ?, ?) "
                "ON CONFLICT(key) DO NOTHING",
                (
                    seed.key,
                    seed.name,
                    seed.description,
                    seed.body,
                    seed.palette,
                    json.dumps(list(seed.swatches)),
                    json.dumps(seed.text_defaults),
                    order,
                    _now(),
                ),
            )


def list_styles() -> list[dict[str, Any]]:
    seed_defaults()
    with db.cursor() as cur:
        return [
            _row(r)
            for r in cur.execute(
                "SELECT * FROM poster_styles ORDER BY sort_order, name COLLATE NOCASE"
            )
        ]


def get_style(style_id: int) -> dict[str, Any]:
    with db.cursor() as cur:
        row = cur.execute(
            "SELECT * FROM poster_styles WHERE id = ?", (style_id,)
        ).fetchone()
    if row is None:
        raise db.NotFound(f"no style with id {style_id}")
    return _row(row)


def by_key(key: str) -> dict[str, Any]:
    seed_defaults()
    with db.cursor() as cur:
        row = cur.execute("SELECT * FROM poster_styles WHERE key = ?", (key,)).fetchone()
    if row is None:
        raise db.NotFound(f"no style called {key!r}")
    return _row(row)


def save_style(
    key: str,
    name: str,
    description: str,
    body: str,
    palette: str,
    swatches: list[str],
    text_defaults: dict[str, Any],
    style_id: int | None = None,
) -> dict[str, Any]:
    """Create or update a style. The key is the stable handle a poster stores."""
    text_defaults = normalise_text_defaults(text_defaults)
    key = key.strip().lower()
    if not KEY_PATTERN.fullmatch(key):
        raise StyleError(
            "A style key is lowercase letters, numbers and hyphens, 2–39 characters."
        )
    name = name.strip() or "Untitled style"

    with db.cursor() as cur:
        if style_id is not None:
            existing = cur.execute(
                "SELECT id FROM poster_styles WHERE id = ?", (style_id,)
            ).fetchone()
            if existing is None:
                raise db.NotFound(f"no style with id {style_id}")
            clash = cur.execute(
                "SELECT id FROM poster_styles WHERE key = ? AND id != ?", (key, style_id)
            ).fetchone()
            if clash is not None:
                raise StyleError(f"another style already uses the key {key!r}")
            cur.execute(
                "UPDATE poster_styles SET key = ?, name = ?, description = ?, body = ?, "
                "  palette = ?, swatches = ?, text_defaults = ?, updated_at = ? "
                "WHERE id = ?",
                (
                    key,
                    name,
                    description.strip(),
                    body,
                    palette.strip(),
                    json.dumps(swatches),
                    json.dumps(text_defaults),
                    _now(),
                    style_id,
                ),
            )
            new_id = style_id
        else:
            order = cur.execute(
                "SELECT COALESCE(MAX(sort_order), 0) + 1 AS n FROM poster_styles"
            ).fetchone()["n"]
            cur.execute(
                "INSERT INTO poster_styles"
                "(key, name, description, body, palette, swatches, text_defaults, "
                " is_default, sort_order, updated_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, 0, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET name=excluded.name, "
                "  description=excluded.description, body=excluded.body, "
                "  palette=excluded.palette, swatches=excluded.swatches, "
                "  text_defaults=excluded.text_defaults, updated_at=excluded.updated_at",
                (
                    key,
                    name,
                    description.strip(),
                    body,
                    palette.strip(),
                    json.dumps(swatches),
                    json.dumps(text_defaults),
                    order,
                    _now(),
                ),
            )
            new_id = cur.execute(
                "SELECT id FROM poster_styles WHERE key = ?", (key,)
            ).fetchone()["id"]

    return get_style(new_id)


def restore_default(style_id: int) -> dict[str, Any]:
    """Put a shipped style back exactly as it left the factory."""
    style = get_style(style_id)
    seed = SEED_KEYS.get(style["key"])
    if seed is None or not style["is_default"]:
        raise StyleError("only a shipped style can be restored")
    return save_style(
        seed.key,
        seed.name,
        seed.description,
        seed.body,
        seed.palette,
        list(seed.swatches),
        seed.text_defaults,
        style_id,
    )


def delete_style(style_id: int) -> None:
    style = get_style(style_id)
    if style["is_default"]:
        raise StyleError(
            "a shipped style cannot be deleted, only edited — or restored to default"
        )
    with db.cursor() as cur:
        cur.execute("DELETE FROM poster_styles WHERE id = ?", (style_id,))


def build_prompt(style: dict[str, Any], values: dict[str, str]) -> str:
    """The style's fixed structure with the operator's copy substituted in.

    Free and side-effect free, so the designer can show the operator exactly
    what is about to be sent before any money is spent on it.
    """
    filled = dict(values)
    # The style owns the palette unless the operator overrode it for this job.
    filled.setdefault("palette", "")
    if not filled["palette"].strip():
        filled["palette"] = style.get("palette", "")
    return render(style["body"], filled)
