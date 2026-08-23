"""Malayalam Unicode ⇄ ML-TTKarthika ASCII conversion.

WhatsApp gives Unicode. CorelDRAW needs ML-TTKarthika, a legacy 8-bit font where
each glyph sits at one byte. Converting between them is a table lookup plus two
things that a table alone does not capture:

1. **Pre-base vowel reordering.** In Unicode a vowel sign follows its consonant
   (``ക`` + ``േ``). In the font the glyph is typed *before* it (``t`` then ``I``),
   so ``കേരളം`` is ``tIcfw`` and not ``Itcfw``. Three signs (``ൊ ോ ൌ``) split
   around the consonant, contributing a glyph on each side.

2. **Longest-match conjuncts.** ``ക്ക`` is one ligature glyph ``¡``, not
   ``I`` + ``v`` + ``I``. Matching must try the longest map entry first or common
   conjuncts render with a visible chandrakkala.

The reordering rules here were cross-checked against libindic/payyans, which
agrees on every case. We do not depend on it at runtime — see ADR-004.
"""

from __future__ import annotations

import unicodedata
from functools import lru_cache
from pathlib import Path

MAP_DIR = Path(__file__).resolve().parents[2] / "data" / "maps"
DEFAULT_FONT = "ML-TTKarthika"

ZWJ = "‍"
ZWNJ = "‌"
VIRAMA = "്"

# WhatsApp often produces chillu as consonant + virama + ZWJ. The map holds the
# atomic form, so normalise before lookup or these letters silently fall through.
CHILLU = {
    "ണ": "ൺ",  # ണ് + ZWJ → ൺ
    "ന": "ൻ",  # ന്  + ZWJ → ൻ
    "ര": "ർ",  # ര്  + ZWJ → ർ
    "ല": "ൽ",  # ല്  + ZWJ → ൽ
    "ള": "ൾ",  # ള്  + ZWJ → ൾ
    "ക": "ൿ",  # ക്  + ZWJ → ൿ
}

# Signs whose glyph precedes the consonant, as (pre-base, post-base) ASCII.
# Validated against the loaded map at import: pre + post must equal the map's
# own code for the sign, so a map edit surfaces as an error rather than as
# quietly wrong output.
PRE_BASE: dict[str, tuple[str, str]] = {
    "െ": ("s", ""),  # െ
    "േ": ("t", ""),  # േ
    "ൈ": ("ss", ""),  # ൈ
    "ൊ": ("s", "m"),  # ൊ = െ + ാ  → splits around the consonant
    "ോ": ("t", "m"),  # ോ = േ + ാ
    "ൌ": ("s", "u"),  # ൌ = െ + ൗ
    # The ra-sign is pre-base in ML-TT fonts: ത്ര is `{X`, not `X{`.
    # This follows payyans. FLAGGED for the CorelDRAW check in ROADMAP.md —
    # if wrong, moving this one line into the post-base set is the whole fix.
    "്ര": ("{", ""),  # ്ര
}

# `\p` (ഌ) is indistinguishable from `\`+`p` (നു) in this encoding — the font
# has no way to tell them apart. നു is overwhelmingly more common in real text,
# so ASCII→Unicode resolves that way. ഌ is archaic and does not round-trip.
_AMBIGUOUS_ASCII = frozenset({"\\p"})


class MapError(RuntimeError):
    """The font map is missing, unreadable, or internally inconsistent."""


def _fits_in_font(ascii_key: str) -> bool:
    """True if every character can occupy a single byte in the font.

    ML-TT fonts are 8-bit. A key outside cp1252 cannot address a glyph, so it is
    a defect in the map. This is what rejects ``∂`` (U+2202) for ``ന്ന``.
    """
    try:
        ascii_key.encode("cp1252")
    except UnicodeEncodeError:
        return False
    return True


def _has_malayalam(text: str) -> bool:
    """True if any character is in the Malayalam block (U+0D00–U+0D7F)."""
    return any("ഀ" <= ch <= "ൿ" for ch in text)


def _is_invisible(ascii_key: str) -> bool:
    """True if the key contains a format or control character.

    Such a code will not survive a clipboard round-trip into CorelDRAW, so a
    visible alternative for the same letter is preferred — this is why ``ണ്ട``
    resolves to ``ï`` rather than to the soft hyphen listed just above it.
    """
    return any(unicodedata.category(ch) in {"Cf", "Cc"} for ch in ascii_key)


def available_fonts() -> tuple[str, ...]:
    """Font names the app will load — the fixed registry, discovered once."""
    if not MAP_DIR.is_dir():
        return ()
    return tuple(sorted(p.stem for p in MAP_DIR.glob("*.map")))


def _map_path(font: str) -> Path:
    """Resolve a font name to its map file, refusing anything else.

    SECURITY.md: a client-supplied value must never address an arbitrary path.
    Selection is by key from `available_fonts()`, so `../../etc/passwd` is not
    a path that gets sanitised — it is simply not a font name.
    """
    known = available_fonts()
    if font not in known:
        raise MapError(f"unknown font {font!r}; available: {', '.join(known) or 'none'}")
    return MAP_DIR / f"{font}.map"


@lru_cache(maxsize=4)
def load_map(font: str = DEFAULT_FONT) -> tuple[dict[str, str], dict[str, str], tuple[str, ...]]:
    """Load a font map, returning (unicode→ascii, ascii→unicode, warnings)."""
    path = _map_path(font)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MapError(f"cannot read font map for {font!r}: {exc}") from exc

    u2a: dict[str, str] = {}
    a2u: dict[str, str] = {}
    warnings: list[str] = []

    for lineno, line in enumerate(raw.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        ascii_key, uni = line.split("=", 1)
        if not ascii_key or not uni:
            continue

        if not _fits_in_font(ascii_key):
            warnings.append(f"line {lineno}: dropped {ascii_key!r} — not representable in the font")
            continue

        uni = unicodedata.normalize("NFC", uni)

        # Only Malayalam is converted. The map also renders `-` as `þ`, but the
        # shop constantly pastes mixed content — addresses, phone numbers,
        # product codes — and rewriting ASCII punctuation would corrupt those
        # silently ("A-1" → "Aþ1"). Latin, digits and punctuation pass through.
        if _has_malayalam(uni):
            # Duplicate targets are alternate glyph positions for one letter.
            # Keep the first, unless it is invisible and a later visible one exists.
            current = u2a.get(uni)
            if current is None or (_is_invisible(current) and not _is_invisible(ascii_key)):
                u2a[uni] = ascii_key

        if ascii_key not in _AMBIGUOUS_ASCII:
            a2u.setdefault(ascii_key, uni)

    if not u2a:
        raise MapError(f"font map {path} yielded no usable entries")

    _validate_pre_base(u2a, font)
    return u2a, a2u, tuple(warnings)


def _validate_pre_base(u2a: dict[str, str], font: str) -> None:
    """Assert the hardcoded split points still match the map's own codes."""
    for sign, (pre, post) in PRE_BASE.items():
        expected = u2a.get(sign)
        if expected is None:
            raise MapError(f"{font}: map has no entry for pre-base sign {sign!r}")
        if pre + post != expected:
            raise MapError(
                f"{font}: pre-base split for {sign!r} is {pre + post!r} "
                f"but the map says {expected!r} — PRE_BASE needs updating"
            )


@lru_cache(maxsize=4)
def _match_lengths(keys: frozenset[str]) -> tuple[int, ...]:
    """Candidate match widths, longest first, so conjuncts beat their parts."""
    return tuple(sorted({len(k) for k in keys}, reverse=True))


def _normalise(text: str) -> str:
    """Fold WhatsApp's spelling variants onto the forms the map holds."""
    text = unicodedata.normalize("NFC", text)
    for base, chillu in CHILLU.items():
        text = text.replace(base + VIRAMA + ZWJ, chillu)
    # Remaining joiners carry no meaning for an 8-bit font.
    return text.replace(ZWJ, "").replace(ZWNJ, "")


def unicode_to_ascii(text: str, font: str = DEFAULT_FONT) -> str:
    """Convert Unicode Malayalam to the font's ASCII encoding.

    This is the direction the shop needs: WhatsApp → CorelDRAW.
    """
    if not text:
        return ""

    u2a, _, _ = load_map(font)
    lengths = _match_lengths(frozenset(u2a))
    text = _normalise(text)

    out: list[str] = []
    base_at: int | None = None  # where the current cluster's base sits in `out`
    i, n = 0, len(text)

    while i < n:
        seq = _longest(text, i, u2a, lengths)
        if seq is None:
            # Latin text, digits, punctuation and whitespace pass through.
            out.append(text[i])
            base_at = None
            i += 1
            continue

        split = PRE_BASE.get(seq)
        if split is None:
            base_at = len(out)
            out.append(u2a[seq])
        else:
            pre, post = split
            if base_at is None:
                out.append(pre)  # no consonant to reorder around
            else:
                out.insert(base_at, pre)
            if post:
                out.append(post)
            base_at = None  # a vowel sign closes the cluster
        i += len(seq)

    return "".join(out)


def ascii_to_unicode(text: str, font: str = DEFAULT_FONT) -> str:
    """Convert the font's ASCII encoding back to Unicode Malayalam."""
    if not text:
        return ""

    _, a2u, _ = load_map(font)
    lengths = _match_lengths(frozenset(a2u))

    # Reverse of PRE_BASE: the leading glyph, and how a trailing glyph completes
    # a split sign. Longest key first so `ss` (ൈ) is not read as two `s`.
    leading = {pre: sign for sign, (pre, post) in PRE_BASE.items() if not post}
    combine = {(pre, post): sign for sign, (pre, post) in PRE_BASE.items() if post}
    lead_keys = sorted(
        {pre for pre, _ in combine} | set(leading), key=len, reverse=True
    )

    out: list[str] = []
    i, n = 0, len(text)

    while i < n:
        direct = _longest(text, i, a2u, lengths)
        pre = next((k for k in lead_keys if text.startswith(k, i)), None)

        # An explicit map entry spanning more than the marker wins: `sF` is the
        # letter ഐ in its own right, not the sign െ applied to എ.
        if pre is not None and (direct is None or len(direct) <= len(pre)):
            base = _longest(text, i + len(pre), a2u, lengths, exclude=set(lead_keys))
            if base is not None:
                cursor = i + len(pre) + len(base)
                sign = leading.get(pre)
                if cursor < n and (pre, text[cursor]) in combine:
                    sign = combine[(pre, text[cursor])]
                    cursor += 1
                out.append(a2u[base])
                if sign is not None:
                    out.append(sign)
                i = cursor
                continue

        if direct is None:
            out.append(text[i])
            i += 1
        else:
            out.append(a2u[direct])
            i += len(direct)

    return unicodedata.normalize("NFC", "".join(out))


def _longest(
    text: str,
    start: int,
    table: dict[str, str],
    lengths: tuple[int, ...],
    exclude: set[str] | None = None,
) -> str | None:
    """Longest table key matching at `start`, or None."""
    n = len(text)
    for width in lengths:
        end = start + width
        if end > n:
            continue
        candidate = text[start:end]
        if candidate in table and (exclude is None or candidate not in exclude):
            return candidate
    return None


def map_report(font: str = DEFAULT_FONT) -> dict[str, object]:
    """Diagnostics for the settings screen and for debugging a bad conversion."""
    u2a, a2u, warnings = load_map(font)
    return {
        "font": font,
        "unicode_entries": len(u2a),
        "ascii_entries": len(a2u),
        "longest_match": max(len(k) for k in u2a),
        "warnings": list(warnings),
    }
