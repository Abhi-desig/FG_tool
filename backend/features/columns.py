"""What kind of thing a column holds, decided once before anything is translated.

**The failure this exists to stop.** Measured on a real employee sheet: the
pipeline sent almost every text cell to a sentence-level model and got ~50 wrong
cells back while flagging 2. `Vishnu Prasad` became വിഷ്ണുപുരാണം — the Vishnu
Purana. `Thekkeveettil` became ദ്വിതീയ, "secondary". `Driver` became the
Malayalam for a golf club. None of those are translation errors the model could
have avoided: a name has no meaning to find, and a model whose job is to find
meaning will invent one. The fix is not a better model. It is not asking.

So every column gets exactly one class before the run starts, and the class picks
the route. Only `FREE_TEXT` ever reaches the model.

**Six classes, and why each is its own route** (ADR-035):

``PERSON_NAME``   written by sound from a lexicon; never translated
``ADDRESS``       decomposed — gazetteer, then structure, then sound
``CODE``          passed through untouched, and asserted untouched
``NUMERIC_DATE``  the same
``CATEGORICAL``   a short repeated vocabulary; approved once, then glossary
``FREE_TEXT``     sentences. The model's actual job

**The class is a suggestion until the operator confirms it.** Guessing wrong is
symmetrical — spelling a real word by sound is exactly as wrong as translating a
name — so this module ranks and explains, and the screen lets them override
before they press Translate. Once the job starts the class is fixed for every
cell in that column; nothing reclassifies mid-run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from backend import textkey

# How many values to profile. Enough to be representative of a 33,000-row sheet,
# small enough that inspection stays instant.
SAMPLE_LIMIT = 200

# --- what counts as categorical -------------------------------------------
#
# The spec proposed `distinct/total < 0.05`. Measured against a real sheet, that
# rule can never fire: Department is the textbook case at 10 distinct values in
# 30 cells, which is 0.33, and on any sheet under ~200 rows the ratio floor is
# 1/n. A ratio alone cannot express "a short vocabulary, repeated" on both a
# 30-row and a 33,000-row sheet.
#
# Two conditions instead, and both are absolute rather than proportional:
# a vocabulary the operator could read in one sitting, and enough repetition that
# approving it once pays for itself.
CATEGORICAL_MAX_DISTINCT = 40
CATEGORICAL_MAX_SHARE = 0.5

CLASSES = (
    "PERSON_NAME",
    "ADDRESS",
    "CODE",
    "NUMERIC_DATE",
    "CATEGORICAL",
    "FREE_TEXT",
)

# Headers that name their own class outright. Whole-header match, casefolded —
# a substring test catches "Product name" and mislabels a product column.
_PERSON_HEADERS = frozenset({
    "name", "names", "full name", "member name", "customer name", "employee name",
    "guardian", "guardian name", "father", "father's name", "fathers name",
    "mother", "mother's name", "husband", "husband's name", "spouse", "wife",
    "applicant", "applicant name", "beneficiary", "nominee", "contact person",
    "proprietor", "owner", "partner", "student name", "staff name",
})
_ADDRESS_HEADERS = frozenset({
    "address", "house name", "house", "house address", "residence",
    "permanent address", "present address", "place", "village", "post",
    "post office", "district", "city", "town", "street", "locality", "landmark",
})
_CODE_HEADERS = frozenset({
    "sl no", "sl. no", "sl no.", "serial", "serial no", "sno", "s no", "id",
    "code", "emp id", "employee id", "employee code", "account no",
    "account number", "reg no", "registration no", "invoice no", "bill no",
    "gst", "gstin", "pan", "aadhaar", "aadhar", "ifsc", "phone", "phone number",
    "mobile", "mobile number", "contact", "contact no", "pin", "pin code",
    "pincode",
})
# Headings that are definitely *not* names, places or codes. Present because the
# header has to be authoritative in both directions: without this, a `Product`
# column of `Standee` / `Flex banner` fell through to the short-and-unknown rule
# and came out PERSON_NAME, which would have spelled the shop's own product list
# out by sound.
_TEXT_HEADERS = frozenset({
    "product", "products", "item", "items", "description", "particulars",
    "designation", "department", "remarks", "notes", "note", "comment",
    "comments", "details", "purpose", "reason", "category", "type", "make",
    "model", "brand", "service", "work", "job", "title", "post held",
    "qualification", "subject", "course",
})

_NUMERIC_HEADERS = frozenset({
    "date", "date of joining", "date of birth", "dob", "doj", "joining date",
    "amount", "salary", "salary (inr)", "rate", "price", "total", "qty",
    "quantity", "age", "year", "month",
})

# A cell that is only digits and separators carries no language at all.
_CODEISH = re.compile(r"^[\d\s./:\-+()]+$")
# `EMP-014`, `KL07AB1234`, `9847012301` — an identifier, not a word.
_IDENTIFIER = re.compile(r"^(?=.*\d)[A-Za-z0-9][A-Za-z0-9\s./\-]*$")
_LATIN_WORD = re.compile(r"[A-Za-z]+")
_HAS_DIGIT = re.compile(r"\d")


@dataclass(frozen=True)
class Profile:
    """What the sample told us about one column."""

    total: int
    distinct: int
    mean_words: float
    share_codeish: float
    share_with_digits: float
    share_known_names: float
    share_known_places: float
    share_dictionary_words: float


@dataclass(frozen=True)
class Classified:
    """One column, its class, and why."""

    key: str
    sheet: str
    letter: str
    header: str
    count: int
    sample: list[str] = field(default_factory=list)
    cls: str = "FREE_TEXT"
    # Plain words for the operator. This is a suggestion they can override, so
    # it has to say what it noticed rather than only what it decided.
    why: str = ""
    profile: Profile | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "sheet": self.sheet,
            "letter": self.letter,
            "header": self.header,
            "count": self.count,
            "sample": self.sample,
            "cls": self.cls,
            "why": self.why,
        }


def _profile(
    values: list[str],
    distinct: int,
    names: frozenset[str],
    places: frozenset[str],
    dictionary: frozenset[str],
) -> Profile:
    """Measure a sample. No decisions here — just the numbers."""
    total = len(values)
    if total == 0:
        return Profile(0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    codeish = sum(1 for v in values if _CODEISH.match(v) or _IDENTIFIER.match(v))
    with_digits = sum(1 for v in values if _HAS_DIGIT.search(v))
    words = [w for v in values for w in _LATIN_WORD.findall(v.casefold())]
    known_names = sum(1 for w in words if w in names)
    known_places = sum(1 for w in words if w in places)
    in_dictionary = sum(1 for w in words if w in dictionary)
    word_count = len(words) or 1

    return Profile(
        total=total,
        distinct=distinct,
        mean_words=sum(len(v.split()) for v in values) / total,
        share_codeish=codeish / total,
        share_with_digits=with_digits / total,
        share_known_names=known_names / word_count,
        share_known_places=known_places / word_count,
        share_dictionary_words=in_dictionary / word_count,
    )


def classify(
    header: str,
    values: list[str],
    distinct: int,
    names: frozenset[str],
    places: frozenset[str],
    dictionary: frozenset[str],
) -> tuple[str, str, Profile]:
    """One column's class, the reason, and its profile.

    The header is trusted above the values, deliberately. On a member list every
    column looks the same from its values — unknown words in title case — and
    guessing from them was tried and dropped: it cannot tell a column of house
    names from a column of occupations. A heading is the operator's own label for
    what the column holds, which is exactly the information needed.

    The value profile decides only where the header is silent or absent.
    """
    profile = _profile(values, distinct, names, places, dictionary)
    label = textkey.normalise(header).rstrip(":.")

    # 1. The header says so.
    if label in _PERSON_HEADERS:
        return "PERSON_NAME", f"the heading “{header}” names people", profile
    if label in _ADDRESS_HEADERS:
        return "ADDRESS", f"the heading “{header}” names places", profile
    if label in _CODE_HEADERS:
        return "CODE", f"the heading “{header}” is a number or a code", profile
    if label in _NUMERIC_HEADERS:
        return "NUMERIC_DATE", f"the heading “{header}” is a figure or a date", profile


    if profile.total == 0:
        return "FREE_TEXT", "nothing in this column to look at", profile

    # A heading in `_TEXT_HEADERS` rules out *identity* — this column is not
    # people or places — but says nothing about repetition. `Department` is both
    # ordinary wording and the textbook categorical column, so this is a flag
    # rather than an early return; it suppresses the name and place inferences
    # below without pre-empting the categorical test.
    header_says_text = label in _TEXT_HEADERS

    # 2. The values are not language.
    if profile.share_codeish >= 0.8:
        return (
            "CODE",
            f"{profile.share_codeish:.0%} of these are numbers or codes",
            profile,
        )

    # 3. A short vocabulary, repeated. Checked before the name and place tests:
    # a Department column of ten values repeated thirty times is categorical even
    # though none of those ten words is in any dictionary.
    if (
        profile.distinct <= CATEGORICAL_MAX_DISTINCT
        and profile.distinct < profile.total * CATEGORICAL_MAX_SHARE
    ):
        return (
            "CATEGORICAL",
            f"only {profile.distinct} different values across "
            f"{profile.total} cells — approve them once and they are fixed",
            profile,
        )

    if header_says_text:
        return (
            "FREE_TEXT",
            f"the heading “{header}” is wording, not names or numbers",
            profile,
        )

    # 4. The values look like names or places.
    if profile.share_known_names >= 0.4 and profile.mean_words <= 4:
        return (
            "PERSON_NAME",
            f"{profile.share_known_names:.0%} of the words are known names",
            profile,
        )
    if profile.share_known_places >= 0.3:
        return (
            "ADDRESS",
            f"{profile.share_known_places:.0%} of the words are known places",
            profile,
        )

    # 5. Short, unknown, no digits — a name or place this app has not met.
    # Reported as uncertain so the operator looks, because this is the branch
    # most likely to be wrong in either direction.
    if (
        profile.mean_words <= 3
        and profile.share_dictionary_words <= 0.35
        and profile.share_with_digits <= 0.2
    ):
        return (
            "PERSON_NAME",
            f"short entries that are mostly not dictionary words "
            f"({profile.share_dictionary_words:.0%} are) — check this one",
            profile,
        )

    return (
        "FREE_TEXT",
        f"reads like ordinary wording ({profile.mean_words:.1f} words a cell)",
        profile,
    )


__all__ = [
    "CATEGORICAL_MAX_DISTINCT",
    "CATEGORICAL_MAX_SHARE",
    "CLASSES",
    "SAMPLE_LIMIT",
    "Classified",
    "Profile",
    "classify",
]
