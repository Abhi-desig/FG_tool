"""Writing a name in Malayalam script by sound, with rules and no model.

**Why.** A co-operative bank's member list is names, guardian names, house names
and addresses — almost entirely proper nouns. Handed one, a *translation* model
has to find meaning, so it invents. Measured on a real sheet and recorded in
`verify.py`: ``ELAVUNKAL VEEDU,VADASERIKARA`` came back as "യൂക്കാലിപ്റ്റസ്"
(Eucalyptus), and ``THOPPIL VEEDU,UTHIMOODU P.O,`` came back carrying a
fabricated "retrieved on June 2, 2019" citation. Those are not spelling errors.

That failure already had one answer — the paid Claude check — and its instruction
to the model is exactly the rule implemented here: **a person, house or place is
written by sound in Malayalam script, never translated for meaning.** A rule can
do that offline and for nothing, which is what the rest of this feature promises.

**What it is good at, and what it is not.** A Kerala member list is romanised
*Malayalam* — `Thoppil`, `Elavunkal`, `Vadaserikara` — where the spelling already
encodes the sound and this is close to exact. English orthography is not
phonetic, so an English brand name is only approximated: `Focus` comes out
ഫൊകുസ് rather than the ഫോക്കസ് a person would write. Those are the cells the
corrections memory is for — fix once, right forever (ADR-029).

Deliberately dependency-free and importing nothing from `features/`, so it can be
called from a router and tested on its own.
"""

from __future__ import annotations

import re

# --- the alphabet ---------------------------------------------------------
#
# Ordered longest-first at point of use, because `ch` must beat `c` and `zh`
# must beat `z`. Malayalam is an abugida: a consonant carries an inherent "a",
# a following vowel becomes a *sign*, and a consonant with no vowel takes the
# chandrakkala.

CONSONANTS: dict[str, str] = {
    "ksh": "ക്ഷ", "chh": "ഛ", "shh": "ഷ",
    "kh": "ഖ", "gh": "ഘ", "ng": "ങ", "ch": "ച", "jh": "ഝ", "nj": "ഞ",
    "th": "ത", "dh": "ധ", "ph": "ഫ", "bh": "ഭ", "sh": "ഷ", "zh": "ഴ",
    "ny": "ഞ", "tt": "ട്ട", "nn": "ന്ന", "ll": "ല്ല", "mm": "മ്മ",
    # `d` is the dental ദ, not the retroflex ഡ: personal names carry it far more
    # often than house names do — Devi, Deepa, Dinesh, Radha, Sudha.
    "k": "ക", "g": "ഗ", "c": "ക", "j": "ജ", "t": "ട", "d": "ദ",
    "n": "ന", "p": "പ", "b": "ബ", "m": "മ", "y": "യ", "r": "ര",
    "l": "ല", "v": "വ", "w": "വ", "s": "സ", "h": "ഹ", "f": "ഫ",
    "z": "സ", "x": "ക്സ", "q": "ക",
}

# Independent forms, used at the start of a word.
VOWELS: dict[str, str] = {
    "aa": "ആ", "ee": "ഈ", "ii": "ഈ", "oo": "ഊ", "uu": "ഊ",
    "ai": "ഐ", "au": "ഔ", "ou": "ഔ", "ea": "ഇ", "oa": "ഓ",
    "a": "അ", "i": "ഇ", "u": "ഉ", "e": "എ", "o": "ഒ",
}

# The same vowels as signs, hung off the preceding consonant. "a" is the
# inherent vowel and therefore writes nothing at all.
SIGNS: dict[str, str] = {
    "aa": "ാ", "ee": "ീ", "ii": "ീ", "oo": "ൂ", "uu": "ൂ",
    "ai": "ൈ", "au": "ൗ", "ou": "ൗ", "ea": "ി", "oa": "ോ",
    "a": "", "i": "ി", "u": "ു", "e": "െ", "o": "ൊ",
}

# Word-final consonants take their chillu form. Without this every name ends on
# a visible chandrakkala, which is how a transliteration announces that a
# machine wrote it: വീട് is right, വീട്‌ is not what anyone types.
CHILLU: dict[str, str] = {"ന": "ൻ", "ര": "ർ", "ല": "ൽ", "ള": "ൾ", "ണ": "ൺ"}

VIRAMA = "്"

# A word ending in "m" ends in the anusvara, not a chandrakkala'd മ. Malayalam
# is full of these — Malappuram, Kottayam, Ernakulam — and മലപ്പുരമ് is the
# spelling that tells a reader a machine wrote it.
ANUSVARA = "ം"

# `n` assimilates before a velar: Elavu**nk**al is എലവു**ങ്ക**ൽ, not ...ന്ക...
_VELARS = frozenset({"ക", "ഖ", "ഗ", "ഘ"})

# How a single letter is *read aloud*, for the initials that fill a member
# list — `K.M. Nair`, `P.O`. Spelling those out phonetically gives പ്.ഒ, which
# is not how anyone writes an initial.
INITIALS: dict[str, str] = {
    "a": "എ", "b": "ബി", "c": "സി", "d": "ഡി", "e": "ഇ", "f": "എഫ്",
    "g": "ജി", "h": "എച്ച്", "i": "ഐ", "j": "ജെ", "k": "കെ", "l": "എൽ",
    "m": "എം", "n": "എൻ", "o": "ഒ", "p": "പി", "q": "ക്യൂ", "r": "ആർ",
    "s": "എസ്", "t": "ടി", "u": "യു", "v": "വി", "w": "ഡബ്ല്യു",
    "x": "എക്സ്", "y": "വൈ", "z": "സെഡ്",
}

# The handful of words that appear in almost every Kerala address, where the
# spelling-by-sound rules give a defensible but visibly wrong answer. `veedu`
# is the important one: it is in most house names on a member list, and the
# retroflex it needs is exactly the one `d` does not carry.
WORDS: dict[str, str] = {
    "veedu": "വീട്",
    "veettil": "വീട്ടിൽ",
    "house": "ഹൗസ്",
    "nagar": "നഗർ",
    "road": "റോഡ്",
    "post": "പോസ്റ്റ്",
    "po": "പി.ഒ",
}

_VOWEL_KEYS = sorted(VOWELS, key=len, reverse=True)
_CONSONANT_KEYS = sorted(CONSONANTS, key=len, reverse=True)

# A run this transliterator has an opinion about. Anything else — digits,
# punctuation, an existing Malayalam word — is passed through untouched.
_LATIN_WORD = re.compile(r"[A-Za-z]+")


def _match(text: str, index: int, keys: list[str]) -> str | None:
    """The longest key that starts at `index`, or None."""
    lowered = text.lower()
    for key in keys:
        if lowered.startswith(key, index):
            return key
    return None


def word(latin: str) -> str:
    """One Latin word, written in Malayalam script by sound."""
    if not latin:
        return ""

    lowered = latin.lower()
    if lowered in WORDS:
        return WORDS[lowered]
    if len(lowered) == 1 and lowered in INITIALS:
        return INITIALS[lowered]

    out: list[str] = []
    index = 0

    while index < len(latin):
        consonant = _match(latin, index, _CONSONANT_KEYS)
        if consonant:
            index += len(consonant)
            glyph = CONSONANTS[consonant]

            # `n` before a velar is ങ്. Decided by looking ahead, because the
            # spelling gives no other signal: Elavu**nk**al.
            if glyph == "ന":
                ahead = _match(latin, index, _CONSONANT_KEYS)
                if ahead and CONSONANTS[ahead][0] in _VELARS:
                    out.append("ങ" + VIRAMA)
                    continue

            vowel = _match(latin, index, _VOWEL_KEYS)
            if vowel:
                index += len(vowel)
                out.append(glyph + SIGNS[vowel])
            elif index >= len(latin):
                # End of the word with no vowel. `m` becomes the anusvara,
                # anything with a chillu takes it, and the rest get a plain
                # chandrakkala.
                if glyph == "മ":
                    out.append(ANUSVARA)
                else:
                    out.append(CHILLU.get(glyph, glyph + VIRAMA))
            elif glyph in CHILLU:
                # A cluster, and this letter has a chillu. Malayalam uses it
                # mid-word too: Ernakulam is എർണാകുളം, never എര്നാ...
                out.append(CHILLU[glyph])
            else:
                # A cluster — the next letter is another consonant.
                out.append(glyph + VIRAMA)
            continue

        vowel = _match(latin, index, _VOWEL_KEYS)
        if vowel:
            index += len(vowel)
            # Independent, whether it opens the word or sits in hiatus after
            # another vowel. A sign would need a consonant to hang from.
            out.append(VOWELS[vowel])
            continue

        # Not a letter this alphabet knows. Keep it and move on.
        out.append(latin[index])
        index += 1

    return "".join(out)


def line(text: str) -> str:
    """A whole cell: every Latin word by sound, everything else left alone.

    Punctuation, digits and spacing survive exactly, because a member list's
    ``THOPPIL VEEDU,UTHIMOODU P.O,`` is an address whose shape carries meaning.
    """
    return _LATIN_WORD.sub(lambda m: word(m.group(0)), text)


__all__ = ["CHILLU", "CONSONANTS", "SIGNS", "VOWELS", "line", "word"]
