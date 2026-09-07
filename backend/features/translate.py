"""English → Malayalam translation, with the glossary doing the heavy lifting.

**Two engines, chosen by circumstance.**

`opus-mt-en-ml` (Apache-2.0, 57M params, ~300 MB) is the default because it just
works: open weights, no account, no token. It is also visibly weak — measured on
2026-08-22 it rendered *Coconut oil, 1 litre bottle* as "oil, 1 litre bottle",
dropping "coconut" outright.

`indictrans2` (MIT, 200M) is markedly better for Indian languages, but its
Hugging Face repo is **gated**: a one-time account, accepting the terms, and a
token. Registered and selectable, not the default, so the feature works out of
the box and improves when the operator does that step.

**NLLB-200 is deliberately absent.** It is the obvious popular choice and it
handles Malayalam well, but it is `cc-by-nc-4.0` — non-commercial. The shop sells
this work, so LICENSES.md rules it out.

The engine is an implementation detail behind `translate_rows()`. Everything the
operator relies on — the glossary, the review grid, per-row flags — is engine
independent, which is what makes swapping engines a setting rather than a rewrite.
"""

from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass, field

from backend import models, textkey
from backend.features import glossary as gl
from backend.jobs import Reporter

log = logging.getLogger(__name__)

SRC_LANG = "eng_Latn"
TGT_LANG = "mal_Mlym"

# Rows per model call. Small enough to keep peak memory modest on the shop PC,
# large enough that batching actually pays.
BATCH = 16


class TranslationError(RuntimeError):
    """The engine is unavailable or failed."""


_MALAYALAM = re.compile(r"[ഀ-ൿ]")


def _word_count(text: str) -> int:
    """Whitespace-delimited tokens.

    Deliberately not `\\w+`: Python's `\\w` excludes Malayalam combining marks,
    so it splits a conjunct at the virama — "500 ഗ്രാം" counts as three tokens
    instead of two, which quietly defeated the dropped-word check.
    """
    return len(text.split())

# Below this ratio of output words to input words, the model probably swallowed
# something. Measured against real output: "500 g jar" → "500 ഗ്രാം" (2/3) and
# "Total amount payable" → "മൊത്തം തുക" (2/3) are both real omissions, while
# legitimate Malayalam compounds usually land at 0.7 or above.
_SHORT_RATIO = 0.7
_MIN_WORDS_TO_JUDGE = 3

# --- what a word-count heuristic cannot see -------------------------------
#
# Measured over a realistic 18-string price list on 2026-08-26. The *only*
# detector that fired was the word-count check above, on 4 rows. Everything
# below came back with `needs_attention: false`:
#
#   Standee       → "Saint Kitts and Nevis"   (a country)
#   Brochure      → "breaking"
#   Visiting Card → "card is visiting"
#   Focus Digitals→ "Digital Digitals"        (the shop's own name)
#   300 gsm matte → "300 gsm mathematics"
#   6x4 feet      → 6x4 മീറ്റ (metre)         (a unit error on a price list)
#
# A word-count heuristic *cannot* fire on a one-word cell, and one-word cells
# are exactly the product names.
#
# **Five of those six no longer reach the model at all** (ADR-032). The word
# library answers them, so the checks below no longer have to carry the whole
# burden of a vocabulary this engine does not have. What they still carry is
# damage that is *provable* from the text — a price that vanished, a unit that
# changed. What they deliberately no longer do is tell the operator to go and
# teach the tool a word: a price list is nothing but short product names, so
# that note fired on nearly every row, and a grid where everything is urgent is
# a grid where nothing is.

# Units the shop actually prices in. A unit that changes between source and
# output is a defect, not a translation choice — `feet` becoming `metre` on a
# price list is the kind of error that gets a banner printed at the wrong size.
_UNIT_WORDS: dict[str, tuple[str, ...]] = {
    "feet": ("അടി",),
    "foot": ("അടി",),
    "ft": ("അടി", "ft"),
    "inch": ("ഇഞ്ച്",),
    "inches": ("ഇഞ്ച്",),
    "metre": ("മീറ്റർ", "മീറ്റ"),
    "meter": ("മീറ്റർ", "മീറ്റ"),
    "mm": ("മില്ലിമീറ്റർ", "mm"),
    "cm": ("സെന്റിമീറ്റർ", "cm"),
    "gsm": ("gsm", "ജിഎസ്എം"),
    "litre": ("ലിറ്റർ",),
    "liter": ("ലിറ്റർ",),
    "kg": ("കിലോ", "kg"),
    "kilo": ("കിലോ",),
    "kilogram": ("കിലോഗ്രാം", "കിലോ"),
    "gram": ("ഗ്രാം",),
    "g": ("ഗ്രാം", "g"),
}

# Every Malayalam unit form, used to spot a *different* unit standing where the
# expected one should have been.
#
# Only consulted when the expected unit is genuinely absent from the output. A
# broader "the model invented a unit" check was tried and removed: these are
# substring matches, and Malayalam has no spaces at the boundaries a simple
# check can see, so `കിലോ` matched inside `കിലോഗ്രാം` and flagged a correct
# translation of "Price per kilogram".
_UNIT_TARGETS = frozenset(
    target for targets in _UNIT_WORDS.values() for target in targets
)

# Below this many words, the word-count check is structurally blind. Cells this
# short are flagged for a look instead of assumed safe — see 1.6.
_SHORT_CELL_WORDS = 2

# Extra words that turn a short cell from "translated" into "reinterpreted".
# Two is enough to catch a one-word product name becoming a place name, without
# firing on an ordinary Malayalam compound that needs a second word.
_RUNAWAY_EXTRA_WORDS = 2

_DIGIT_RUN = re.compile(r"\d+")


def _numbers(text: str) -> list[str]:
    return _DIGIT_RUN.findall(text)


_LATIN_RUN = re.compile(r"[A-Za-z]")

# --- the confidence layer (ADR-035) ---------------------------------------
#
# The four surface checks above catch damage that is *provable* from the text.
# They cannot see fluent-but-wrong output, which is the failure that actually
# reaches clients: `Vishnu Prasad` → വിഷ്ണുപുരാണം is well-formed Malayalam and
# every surface check passes it. These signals are about the model's own
# uncertainty rather than about the text.

# Beam disagreement above this is a model with no clear answer. Measured on real
# output: `Accountant` and `Driver` score 0.00 and are right; `Business Analyst`
# scores 0.29 and is wrong. Set below that gap, not on it.
DIVERGENCE_LIMIT = 0.18

# Length-normalised beam score below this is the model straining. Scores cluster
# tightly around -0.7 to -0.9 on this model, so this is deliberately generous —
# it is the outliers that matter, not the distribution.
SCORE_FLOOR = -1.6

# Output longer than this multiple of the source has stopped translating and
# started explaining.
LENGTH_RUNAWAY = 3.0

# --- why the round-trip does not raise a flag -----------------------------
#
# It was built to, and measured against twelve cells whose correctness had
# already been established. It **caught 0 of 3 real errors and cried wolf on 1
# of 9 correct cells**:
#
#   Price per kilogram  ok     -> "The Friday's Eve — Ancient of Israel"  0.33  FLAGGED
#   Legal Advisor       wrong  -> "The legalator"                         0.62  passed
#   Total amount payable wrong -> "Total amount of"                       0.74  passed
#   500 g jar           wrong  -> "500 pounds"                            0.42  passed
#
# The signal is inverted, and structurally so. A wrong translation is usually
# *near* the source in meaning — `legal advisor` becoming "legal auditor" reads
# back close to the original — while a correct translation of an idiom can round
# trip through a second 57M model into nonsense. String similarity between a
# source and its back-translation measures how good the reverse model is, not
# whether the forward translation was right.
#
# So the reading is still produced and still shown, because a human can judge
# "this says X" far better than a ratio can. It raises nothing on its own, and
# it is off by default: a second model load and a second pass over every
# distinct string is real time on a 33,000-cell sheet, for information the
# operator — who reads Malayalam — mostly does not need.

# What is allowed to appear in a Malayalam cell: the Malayalam block, digits,
# ordinary punctuation, whitespace, and the `X0X` markers the glossary uses.
_ALLOWED_OUTPUT = re.compile(
    r"^[\u0d00-\u0d7f0-9\s.,;:!?%/()\[\]{}'\"’“”…\-–—+&#*@X]*$"
)


def _trigrams(text: str) -> list[tuple[str, ...]]:
    words = text.split()
    return [tuple(words[i : i + 3]) for i in range(len(words) - 2)]

# `6x4` is a size, not a word. The `x` is the only Latin letter allowed to
# survive into a composed cell — without this exception `6x4 feet` would fail
# the "is it fully covered?" test on a dimension separator.
_DIMENSION_X = re.compile(r"(?<=\d)\s*[xX×]\s*(?=\d)")


def compose(text: str, phrases: list[tuple[str, str]]) -> str | None:
    """Build a whole cell out of trade terms alone, or return None.

    **This replaced masking, and the reason is measured.** Trade terms were
    first handled the way glossary terms are — lifted out, translated around,
    put back. That works for the handful of terms one client's glossary holds.
    Handing the same mechanism ~110 terms that occur in almost every cell did
    not: `Flex banner` masked to `X1X X0X`, a cell containing nothing but
    markers, which was then sent to a 57M model to be mangled. Measured output
    on 2026-09-05, all of it written straight into the client's spreadsheet:

        Flex banner          → ഫ്ലക്സ് 0X ബാനർ      (raw marker debris)
        6x4 feet             → 6x4X അടി             (marker fused to the size)
        Total amount payable → എക്സ്1തുക ...        (marker transliterated)
        300 gsm matte        → 300 ജിഎസ്എംമാറ്റ്     (no space between terms)

    `glossary.py` says it plainly in its own header: **nothing survives every
    time.** So a cell that is entirely trade terms and numbers is now composed
    directly and never reaches the model, and a cell that is only partly
    covered is sent to the model *untouched* rather than half-marked up.

    Safe only because of what is in the overlay. These are loanwords and units
    in a noun phrase — `Flex banner` → ഫ്ലക്സ് ബാനർ — where Malayalam keeps
    English word order. It would not be safe over a general dictionary, which
    is why `phrases` is the curated list and never the whole library.
    """
    if not phrases or not _LATIN_RUN.search(text):
        return None

    result = text
    matched = False
    for source, target in phrases:
        # Longest first, so `visiting card` wins over `card`.
        pattern = re.compile(
            rf"(?<!\w){re.escape(source)}(?!\w)" if source[0].isalnum() else re.escape(source),
            re.IGNORECASE,
        )
        if not pattern.search(result):
            continue
        # A lambda, not the string: a target is data and must never be read as
        # a backreference.
        result = pattern.sub(lambda _match, t=target: t, result)
        matched = True

    # Any English left means this cell is not ours to answer. Handing back a
    # half-translated cell would be worse than not answering at all.
    if not matched or _LATIN_RUN.search(_DIMENSION_X.sub("", result)):
        return None
    return re.sub(r"\s{2,}", " ", result).strip()


# The shop's own vocabulary used to live here, as a frozenset of words to warn
# about. It has moved to `data/dictionary/trade-en-ml.tsv`, where each word now
# carries its approved Malayalam instead of merely raising a flag — the same
# list, doing the job properly. It is deliberately not duplicated back here: two
# copies of this vocabulary would drift, and the one that drifted would be the
# one nobody was reading.


@dataclass(frozen=True)
class Resolved:
    """One cell answered without the model, or deliberately refused.

    Built by the router from `features/translit.py` and handed down as plain
    data, for the same reason `terms` and `dictionary` are: a feature module may
    not import another one. `unresolved_reason` being set is the refusal — the
    text is then only a suggestion, never the answer (ADR-035).
    """

    text: str
    # Set when nothing here can answer the cell: the grid then shows the English
    # and refuses to offer a translation as though it were one.
    unresolved_reason: str = ""
    # Set when the answer is the right *operation* but its spelling is uncertain
    # — a house name written by sound, where English does not mark vowel length.
    #
    # The distinction is the point. A wrong translation is fluent and invisible:
    # `Thoppil Veedu` came back as "Eucalyptus" and read perfectly. A
    # transliteration is never wrong about *what the cell is*, only possibly
    # about one vowel — and refusing those would mean retyping thirty house
    # names on every sheet, since no gazetteer will ever hold a private house
    # name. So it is translated and flagged, not withheld (ADR-035).
    reading_note: str = ""


@dataclass
class Row:
    """One reviewable row in the grid."""

    source: str
    translation: str
    glossary_terms: list[str] = field(default_factory=list)
    # True when the glossary alone produced this — no model involved.
    glossary_only: bool = False
    # True when this whole cell came from the corrections memory: a cell the
    # operator approved on an earlier sheet. Exact by construction in the same
    # way `glossary_only` is, and flagged for the same reason — so the review
    # grid can say why it never reached the model, and so the paid check never
    # pays to second-guess an answer the operator already gave (ADR-029).
    from_memory: bool = False
    # True when the bundled word library answered this whole cell. Exact by
    # construction like the two above — a headword lookup, not a guess — so the
    # heuristics skip it and the paid check does not re-buy it (ADR-032).
    from_dictionary: bool = False
    # True when this cell sits in a column the operator marked as names, and was
    # therefore written by sound rather than translated for meaning. Exact in
    # the sense that matters — it is a rule, not a guess about meaning — though
    # English spelling does not mark vowel length, so it is approximate script
    # rather than a certainty. See `features/translit.py`.
    from_name: bool = False
    # Which column class routed this cell (ADR-035). Fixed for the whole job.
    cls: str = "FREE_TEXT"
    # Nothing here could answer this cell and nothing may guess at it. The
    # translation is left as the *source text*, and the grid shows it
    # untranslated — a rule-derived spelling presented as an answer is exactly
    # the confident wrongness this whole change exists to stop.
    unresolved: bool = False
    unresolved_reason: str = ""
    # What the rules would have said, carried but never presented as the answer.
    # The grid offers it as one click to accept; accepting is the operator's
    # act, not the app's.
    suggestion: str = ""
    # Length-normalised beam score, and how far the four beams disagreed. Both
    # come free from a generate() call that was already computing them and
    # throwing them away.
    score: float | None = None
    divergence: float | None = None
    # What the round-trip model read the Malayalam back as, when it ran.
    back_translation: str = ""
    # Terms the model dropped. The operator must place these by hand.
    lost_terms: list[str] = field(default_factory=list)
    # Placeholder wreckage that was cleaned out of the output. Its presence means
    # the locked term could not be placed, which is a different problem from the
    # term merely being missing.
    debris: list[str] = field(default_factory=list)
    # A lost term was appended to the tail of the cell rather than placed.
    term_appended: bool = False
    # True when no glossary was in force for this row at all (NEXT.md 1.7).
    no_glossary: bool = False
    # Written by sound, correctly, but with a spelling worth reading once.
    reading_note: str = ""

    @property
    def warnings(self) -> list[str]:
        """Everything worth saying about this row, most serious first.

        Kept as the flat list the grid has always consumed. `problems` and
        `checks` split it by how much certainty is behind each note — see
        `problems`.
        """
        return self.problems + self.checks

    @property
    def problems(self) -> list[str]:
        """Something is demonstrably wrong. The operator must fix it.

        The model fails *confidently* — measured on real output it turned
        "Product" into നിർമ്മാണം ("manufacturing") and dropped "jar" from
        "500 g jar" without any signal. Flagging lost glossary terms alone was
        not enough, so these cheap checks surface the likely omissions too.

        Split from `checks` because a sheet of short product names would
        otherwise flag almost every row at the same volume, and a grid where
        everything is urgent is a grid where nothing is.
        """
        notes: list[str] = []
        target = self.translation.strip()

        if self.unresolved:
            return [
                self.unresolved_reason
                or "Nothing here could answer this cell — it is left in English."
            ]

        if not target:
            return ["Nothing came back — translate this by hand."]

        # The locked term could not be placed at all. Said first and said
        # plainly, because the old code reported this row as merely "much
        # shorter than the English" — the wrong diagnosis entirely.
        if self.debris:
            notes.append(
                "The locked term could not be placed — review this cell. "
                "Leftover markers were removed from the output."
            )

        if self.lost_terms:
            where = (
                " It was added at the end of the cell, out of place — move it."
                if self.term_appended
                else ""
            )
            notes.append(
                f"The model dropped {', '.join(self.lost_terms)}.{where}"
            )

        # Exact by construction; the rest are guesses.
        if self.exact:
            return notes

        if not _MALAYALAM.search(target):
            notes.append("Still in English — the model left this untranslated.")
            return notes

        notes.extend(self._unit_warnings(target))
        notes.extend(self._number_warnings(target))

        # A measured omission, not a suspicion: "500 g jar" came back as
        # "500 ഗ്രാം" and "Total amount payable" as "മൊത്തം തുക".
        source_words = _word_count(self.source)
        target_words = _word_count(target)
        if (
            source_words >= _MIN_WORDS_TO_JUDGE
            and target_words < source_words * _SHORT_RATIO
        ):
            notes.append(
                f"Much shorter than the English ({target_words} words vs "
                f"{source_words}) — check nothing was dropped."
            )

        return notes

    @property
    def exact(self) -> bool:
        """This cell was looked up, not guessed, so no heuristic applies to it.

        The operator's own correction, the client's locked term, and the word
        library are all exact by construction. Running a suspicion check over
        any of them can only produce a false alarm about an answer that was
        already right.
        """
        # An unresolved cell is never exact, whatever produced it: the whole
        # point of the state is that nothing here can vouch for the answer.
        if self.unresolved:
            return False
        return (
            self.glossary_only
            or self.from_memory
            or self.from_dictionary
            or self.from_name
        )

    @property
    def checks(self) -> list[str]:
        """Nothing is provably wrong, but nothing here can vouch for it either.

        One check, and only one. A word-count heuristic cannot fire on a
        one-word cell, and one-word cells are exactly the product names —
        `Standee` came back as the Malayalam for "Saint Kitts and Nevis" with
        `needs_attention: false`.

        What used to live here as well were two notes telling the operator to go
        and add a glossary entry, for print terms and for any short cell. Both
        are gone. The word library now knows those words, so the advice was
        being given about cells that are already right, on nearly every row of a
        price list — which trained the operator to scroll past the colour that
        also marks a real defect.
        """
        target = self.translation.strip()
        if not target:
            return []
        # A reading note outlives `exact`: the row *is* exact about what the
        # cell is, and uncertain only about how it is spelled.
        if self.reading_note:
            return [self.reading_note]
        if self.exact:
            return []
        if not _MALAYALAM.search(target):
            return []

        notes: list[str] = self._confidence_warnings(target)
        source_words = _word_count(self.source)
        target_words = _word_count(target)

        # The mirror of the short check, and the one that catches a product name
        # turning into a country: "Standee" (1 word) came back as the Malayalam
        # for "Saint Kitts and Nevis" (4). A proper-noun detector would need a
        # gazetteer this project is not going to ship; runaway expansion of a
        # short cell is the same signal for free.
        if (
            source_words <= _SHORT_CELL_WORDS
            and target_words >= source_words + _RUNAWAY_EXTRA_WORDS
        ):
            notes.append(
                f"“{self.source.strip()}” is {source_words} word"
                f"{'' if source_words == 1 else 's'} in English but "
                f"{target_words} in Malayalam — the model may have translated it "
                f"as a name or a place. Check it."
            )

        return notes

    def _confidence_warnings(self, target: str) -> list[str]:
        """What the model's own numbers say about this row.

        Every signal here is free: `generate` was already computing the beam
        scores and the alternative beams, and throwing both away.
        """
        notes: list[str] = []

        if self.divergence is not None and self.divergence > DIVERGENCE_LIMIT:
            notes.append(
                f"The model gave four different answers for this "
                f"({self.divergence:.0%} apart) — it had no clear one. Read it."
            )

        if self.score is not None and self.score < SCORE_FLOOR:
            notes.append(
                "The model was unusually unsure of this line. Read it."
            )

        source_words = _word_count(self.source)
        if source_words and _word_count(target) > source_words * LENGTH_RUNAWAY:
            notes.append(
                f"Far longer than the English ({_word_count(target)} words vs "
                f"{source_words}) — this looks like an explanation, not a "
                f"translation."
            )

        repeated = _trigrams(target)
        if len(repeated) != len(set(repeated)):
            notes.append(
                "The same phrase repeats inside this cell — the model looped."
            )

        # Latin that was in the source is an acronym coming through correctly —
        # `HR Officer` should read HR ഓഫീസർ. Latin that was *not* is leakage:
        # measured, this model appends `usa. kgm` and `City in Ontario Canada`.
        # The source is the only thing that tells the two apart.
        source_words = {w.casefold() for w in re.findall(r"[A-Za-z]+", self.source)}
        leaked = [
            w
            for w in re.findall(r"[A-Za-z]+", target)
            if w.casefold() not in source_words
        ]
        if leaked:
            notes.append(
                f"“{' '.join(leaked[:4])}” is English that was not in the cell — "
                f"the model leaked its training data."
            )

        return notes

    def _source_units(self) -> list[str]:
        """Units named in the English, longest match first."""
        lowered = self.source.lower()
        found = [
            unit
            for unit in _UNIT_WORDS
            if re.search(rf"(?<![a-z]){re.escape(unit)}(?![a-z])", lowered)
        ]
        return sorted(found, key=len, reverse=True)

    def _unit_warnings(self, target: str) -> list[str]:
        """Units that changed or disappeared.

        `6x4 feet` came back as `6x4 മീറ്റ` — metre. On a price list that is a
        quotation for the wrong size of banner.
        """
        notes: list[str] = []
        source_units = self._source_units()

        for unit in source_units:
            expected = _UNIT_WORDS[unit]
            if any(form in target for form in expected):
                continue
            # The unit is gone. Is a *different* unit standing where it was?
            intruder = next(
                (
                    other
                    for other in _UNIT_TARGETS
                    if other in target and other not in expected
                ),
                None,
            )
            if intruder:
                notes.append(
                    f"The unit changed: the English says “{unit}” but the "
                    f"Malayalam says “{intruder}”. Check the size before quoting."
                )
            else:
                notes.append(
                    f"The unit “{unit}” is missing from the Malayalam — "
                    f"put it back."
                )

        return notes

    def _number_warnings(self, target: str) -> list[str]:
        """Digits are prices and sizes. They must survive exactly."""
        before, after = _numbers(self.source), _numbers(target)
        if before == after:
            return []
        missing = [n for n in before if n not in after]
        if missing:
            return [
                f"{'A number' if len(missing) == 1 else 'Numbers'} in the English "
                f"({', '.join(missing)}) {'is' if len(missing) == 1 else 'are'} not "
                f"in the Malayalam — check the price and the size."
            ]
        added = [n for n in after if n not in before]
        if added:
            return [
                f"The Malayalam has a number the English does not "
                f"({', '.join(added)}) — check it."
            ]
        return []

    @property
    def needs_attention(self) -> bool:
        """Rows the operator must look at, not merely may."""
        return bool(self.warnings)

    @property
    def must_fix(self) -> bool:
        """Something is demonstrably wrong with this row."""
        return bool(self.problems)


# --- the FREE_TEXT model path ---------------------------------------------
#
# Three things happen to a fragment before it reaches a sentence-level model, and
# all three are because it *is* a sentence-level model. Handed two words it has
# no sentence to translate, so it reaches for one — which is how `Team Lead`
# became ടീം and `Business Analyst` became വ്യാപാരം ("trade").

# Under this many tokens, a cell is a fragment rather than a sentence.
FRAGMENT_TOKENS = 5

# A frame that gives the model a sentence to translate, and which it reproduces
# reliably enough to strip afterwards. Deliberately banal: anything vivid comes
# back paraphrased and takes the payload with it.
_CARRIER = "The label reads: {}."
_CARRIER_OPEN = "The label reads:"


def frame(text: str) -> tuple[str, bool]:
    """Wrap a short fragment in a carrier sentence. Returns (text, wrapped)."""
    if len(text.split()) >= FRAGMENT_TOKENS:
        return text, False
    return _CARRIER.format(text.rstrip(".")), True


_CARRIER_ML = re.compile(r"^[^:：]{0,40}[:：]\s*")


def unframe(output: str) -> str | None:
    """Take the carrier back off, or None if it did not survive.

    None is a refusal, not a fallback. If the frame is gone the model has
    rewritten the whole sentence, and whatever is left is not a translation of
    the label — it is a translation of something the app made up.
    """
    stripped = _CARRIER_ML.sub("", output.strip(), count=1).strip()
    if not stripped or stripped == output.strip():
        return None
    return stripped.rstrip(".").strip()


# An all-caps run this short is an acronym, not shouting.
_ACRONYM = re.compile(r"\b[A-Z]{2,5}\b")


def soften_case(text: str) -> str:
    """Lowercase for the tokeniser, but leave acronyms alone.

    Measured on 2026-09-07, and the reason this is not a plain `casefold()`:

        the label reads: hr officer.  ->  ലേബലുകൾ:              (payload lost)
        the label reads: HR officer.  ->  ലേബലുകൾ: HR ഓഫീസർ.    (correct)
        the label reads: qa engineer. ->  ... കീയാ എഞ്ചിനീയർ    (garbled)
        the label reads: QA Engineer. ->  ... QA എൻജിനീയർ       (correct)

    Lowercasing helps ordinary words, because the tokeniser has seen them that
    way far more often. It destroys `HR` and `QA`, which it has only ever seen
    in capitals. So the acronyms are held back and everything else is folded.
    """
    kept = _ACRONYM.findall(text)
    lowered = text.casefold()
    for acronym in kept:
        lowered = re.sub(
            rf"\b{re.escape(acronym.casefold())}\b", acronym, lowered, count=1
        )
    return lowered


def restore_case(source: str, translated: str) -> str:
    """Put the source's casing back on any Latin left in the output.

    Generation runs on lowercased input — measured, `HR Officer` and `hr officer`
    produce different output, and the uppercase form is the worse of the two
    because the tokeniser has seen it less. Malayalam has no case, so this only
    ever touches Latin that came through untranslated.
    """
    if not _LATIN_RUN.search(translated):
        return translated
    originals = {w.casefold(): w for w in re.findall(r"[A-Za-z]+", source)}

    def swap(match: re.Match[str]) -> str:
        return originals.get(match.group(0).casefold(), match.group(0))

    return re.sub(r"[A-Za-z]+", swap, translated)


def available_engine() -> str | None:
    """The engine that can actually run right now, or None."""
    for key in ("opus-mt-en-ml", "indictrans2-en-indic"):
        spec = models.REGISTRY.get(key)
        if spec is None:
            continue
        if models.hf_cached(key):
            return key
    return None


def translate_rows(
    sources: list[str],
    terms: list[tuple[str, str]],
    engine: str | None = None,
    reporter: Reporter | None = None,
    progress_to: float = 0.95,
    memory: dict[str, str] | None = None,
    dictionary: dict[str, str] | None = None,
    phrases: list[tuple[str, str]] | None = None,
    resolved: dict[str, Resolved] | None = None,
    classes: dict[str, str] | None = None,
    read_back: bool = False,
) -> list[Row]:
    """Translate distinct strings, applying the glossary around the model.

    Cells the glossary covers entirely never reach the model — that is both
    faster and safer, since a weak model can only make an already-correct term
    worse. A catalogue of product names is mostly this case.

    `memory` is the corrections memory, keyed by `textkey.normalise` — cells the
    operator approved on an earlier sheet. It is passed in rather than read here
    because a feature module may not import `db`; the router owns storage and
    hands this down as plain data, exactly as it already does with `terms`.

    `dictionary` is the bundled word library, keyed the same way, with the
    operator's own overrides already merged over it. `phrases` are the print
    terms it is safe to lock away from the model inside a longer sentence. Both
    arrive as plain data for the same reason and by the same route — a feature
    module may not import another feature module, so the router asks
    `features/dictionary.py` for these and passes them down (ADR-032).

    `progress_to` is where this pass leaves the job's bar. It is the whole job
    on its own, but only the first half when the optional Claude check runs
    after it — and a bar that reaches 95% and then restarts reads as a fault.
    """
    if not sources:
        return []

    engine = engine or available_engine() or "opus-mt-en-ml"

    # Split the work four ways; only the last needs the model. The order is the
    # precedence order, and it is not arbitrary — each layer outranks the next
    # because it is more specifically the operator's own answer:
    #
    #   1. memory      the cell this operator approved by hand, on this shop's work
    #   2. names       a column the operator marked: written by sound, never translated
    #   3. glossary    this client's locked wording
    #   4. dictionary  a headword the shop ships, right for anyone
    #   5. model       a guess
    #
    # Names sit second because the operator ticked that column: a cell in it is
    # a person or a place, and no amount of glossary or dictionary coverage
    # makes ഏലക്കായ the right answer for a member called Cardamom. Only their
    # own earlier correction of that exact cell outranks it.
    memory = memory or {}
    dictionary = dictionary or {}
    phrases = phrases or []
    resolved = resolved or {}
    classes = classes or {}
    direct: dict[int, Row] = {}
    remembered_count = 0
    dictionary_count = 0
    routed_count = 0
    unresolved_count = 0
    needs_model: list[tuple[int, gl.Masked]] = []

    no_glossary = not terms

    for index, source in enumerate(sources):
        cls = classes.get(source, "FREE_TEXT")

        remembered = memory.get(textkey.normalise(source))
        if remembered:
            direct[index] = Row(
                source=source,
                translation=remembered,
                from_memory=True,
                cls=cls,
            )
            remembered_count += 1
            continue

        # Everything but FREE_TEXT was answered before this function was
        # called — by the name lexicon, the gazetteer, or by being left alone.
        # The router did it because `features/` modules may not import each
        # other; what arrives here is the decision, not the machinery.
        answer = resolved.get(source)
        if answer is not None:
            if answer.reading_note:
                direct[index] = Row(
                    source=source,
                    translation=answer.text,
                    cls=cls,
                    from_name=True,
                    reading_note=answer.reading_note,
                )
                routed_count += 1
                continue

            if answer.unresolved_reason:
                # The invariant: fail to review, never to the model. The cell
                # shows its own English, the reason is on the row, and the rule's
                # attempt rides along as a suggestion the operator may accept.
                direct[index] = Row(
                    source=source,
                    translation=source,
                    cls=cls,
                    unresolved=True,
                    unresolved_reason=answer.unresolved_reason,
                    suggestion=answer.text,
                )
                unresolved_count += 1
            else:
                direct[index] = Row(
                    source=source,
                    translation=answer.text,
                    cls=cls,
                    from_name=cls in ("PERSON_NAME", "ADDRESS"),
                )
                routed_count += 1
            continue

        masked = gl.mask(source, terms)
        if gl.is_fully_covered(source, terms):
            restored = gl.restore(masked.text, masked)
            direct[index] = Row(
                source=source,
                translation=restored.text,
                glossary_terms=masked.matched,
                glossary_only=True,
                cls=cls,
            )
            continue

        if not masked.terms:
            answer_text = dictionary.get(textkey.normalise(source)) or compose(
                source, phrases
            )
            if answer_text:
                direct[index] = Row(
                    source=source,
                    translation=answer_text,
                    from_dictionary=True,
                    cls=cls,
                )
                dictionary_count += 1
                continue

        needs_model.append((index, masked))

    if reporter:
        already = []
        if remembered_count:
            already.append(f"{remembered_count} from your corrections")
        if routed_count:
            already.append(f"{routed_count} names, places and codes")
        if dictionary_count:
            already.append(f"{dictionary_count} from the word library")
        if unresolved_count:
            already.append(f"{unresolved_count} left for you to read")
        glossary_count = (
            len(direct)
            - remembered_count
            - dictionary_count
            - routed_count
            - unresolved_count
        )
        if glossary_count:
            already.append(f"{glossary_count} from the glossary")
        reporter.step(
            f"{', '.join(already)}, {len(needs_model)} to translate…",
            0.1,
        )

    results: dict[int, Row] = dict(direct)

    if needs_model:
        # Lowercased and, for fragments, framed. Both are undone afterwards.
        framed = [frame(soften_case(m.text)) for _, m in needs_model]
        attempts = _run_engine(
            engine,
            [text for text, _ in framed],
            reporter,
            done_base=len(direct),
            total=len(sources),
            progress_to=progress_to,
        )
        for (index, masked), (_, wrapped), attempt in zip(
            needs_model, framed, attempts, strict=True
        ):
            source = sources[index]

            if wrapped:
                # Every beam, not just the best one. Measured: `driver` came
                # back correctly as ഡ്രൈവർ in beams 0 and 2 while beams 1 and 3
                # collapsed the frame — taking only beam 0 would have been luck.
                payloads = [p for p in (unframe(b) for b in attempt.alternatives) if p]
                if not payloads:
                    results[index] = Row(
                        source=source,
                        translation=source,
                        cls=classes.get(source, "FREE_TEXT"),
                        unresolved=True,
                        unresolved_reason=(
                            "This is a fragment, not a sentence, and the model "
                            "rewrote it instead of translating it. Type it by hand."
                        ),
                        suggestion=attempt.text,
                        score=attempt.score,
                    )
                    continue
                output = payloads[0]
                spread = divergence(payloads)
            else:
                output = attempt.text
                spread = divergence(attempt.alternatives)

            # The source goes in so `restore` can tell its own mangled markers
            # from a client's part number — see glossary._debris.
            restored = gl.restore(output, masked, source=source)
            results[index] = Row(
                source=source,
                translation=restore_case(source, restored.text),
                glossary_terms=masked.matched,
                lost_terms=restored.lost,
                debris=restored.debris,
                term_appended=restored.appended,
                no_glossary=no_glossary,
                cls=classes.get(source, "FREE_TEXT"),
                score=attempt.score,
                divergence=spread,
            )

    ordered = [results[i] for i in range(len(sources))]
    if needs_model and read_back:
        # Only the rows a model produced are worth reading back, and only after
        # the forward model has been freed by `models.loaded`.
        round_trip(ordered, reporter)
    return ordered


@dataclass(frozen=True)
class Attempt:
    """What one generate() call produced, including what it usually discards."""

    text: str
    # Length-normalised log-probability of the chosen beam. Less negative is
    # more confident. Absolute values mean little; the spread across a sheet is
    # what identifies the rows worth reading.
    score: float | None = None
    # Every beam, raw. Kept because divergence has to be measured on the
    # *payload* and only the caller knows whether a carrier frame was used —
    # measured, the four beams for `accountant` agreed on the answer and
    # differed only in how they worded the frame, which read as total
    # disagreement until the frame was taken off first.
    beams: list[str] = field(default_factory=list)

    @property
    def alternatives(self) -> list[str]:
        """Every beam, or just the chosen text when none were kept.

        `beams` is empty only when an `Attempt` was built by hand — a test
        double, or a future caller that does not need the spread. Falling back
        here rather than treating empty as "no answer" matters: the framing
        check reads this list, and an empty one made it refuse a row that had a
        perfectly good translation in `text`.
        """
        return self.beams or ([self.text] if self.text else [])


def divergence(payloads: list[str]) -> float:
    """How far the beams disagreed, 0.0 (identical) to 1.0 (nothing in common).

    Character-level, not token-level, and that is from measurement: the beams for
    `accountant` came back as അക്കൌണ്ടൻറ് and അക്കൗണ്ടൻറ് — the same word with
    one vowel sign written two ways. As token sets those share nothing and score
    1.0, which is the opposite of the truth. As characters they are 0.08 apart.

    The question being asked is not *how* the beams differ but whether the model
    had an answer at all. Measured on real output, `Business Analyst` scores 0.6+
    and is wrong; `Accountant` scores under 0.1 and is right.
    """
    kept = [p.strip() for p in payloads if p.strip()]
    if len(kept) < 2:
        return 0.0
    best = kept[0]
    ratios = [
        difflib.SequenceMatcher(None, best, other).ratio() for other in kept[1:]
    ]
    return 1.0 - (sum(ratios) / len(ratios))


# Beams to return. Four is what `num_beams` already searched, so asking for all
# four costs nothing extra — the alternatives were computed and dropped.
RETURN_BEAMS = 4


def _run_engine(
    engine: str,
    texts: list[str],
    reporter: Reporter | None,
    done_base: int = 0,
    total: int | None = None,
    progress_to: float = 0.95,
) -> list[Attempt]:
    """Load the model, translate in batches, free it.

    Returns the chosen beam plus its score and the spread across the others.
    Those numbers were already being computed inside `generate` and thrown
    away; `output_scores` and `num_return_sequences` are the only cost.
    """
    spec = models.spec(engine)
    total = total or len(texts)

    import torch

    attempts: list[Attempt] = []
    with models.loaded(engine) as bundle:
        tokenizer, model = bundle
        for start in range(0, len(texts), BATCH):
            chunk = texts[start : start + BATCH]
            prepared = [_prepare(engine, t) for t in chunk]
            batch = tokenizer(
                prepared,
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=512,
            )
            with torch.no_grad():
                generated = model.generate(
                    **batch,
                    num_beams=RETURN_BEAMS,
                    num_return_sequences=RETURN_BEAMS,
                    max_length=512,
                    early_stopping=True,
                    output_scores=True,
                    return_dict_in_generate=True,
                )

            decoded = tokenizer.batch_decode(
                generated.sequences, skip_special_tokens=True
            )
            scores = getattr(generated, "sequences_scores", None)
            # `sequences` comes back as batch × beams, flattened. Regroup so each
            # input's own beams are compared with each other and nothing else.
            for row in range(len(chunk)):
                lo = row * RETURN_BEAMS
                beams = decoded[lo : lo + RETURN_BEAMS]
                if not beams:
                    attempts.append(Attempt(text=""))
                    continue
                score = None
                if scores is not None:
                    try:
                        score = float(scores[lo])
                    except (IndexError, TypeError, ValueError):
                        score = None
                attempts.append(
                    Attempt(text=beams[0], score=score, beams=list(beams))
                )

            if reporter:
                done = done_base + len(attempts)
                reporter.step(
                    f"Translating — row {done} of {total}",
                    0.1 + (progress_to - 0.1) * done / total,
                )

    if spec.key.startswith("indictrans2"):
        attempts = [
            Attempt(
                _strip_tags(a.text),
                a.score,
                [_strip_tags(b) for b in a.beams],
            )
            for a in attempts
        ]
    return attempts


ROUND_TRIP_ENGINE = "opus-mt-ml-en"


def round_trip(rows: list[Row], reporter: Reporter | None = None) -> None:
    """Read the Malayalam back into English, and note what it said.

    **Information, not a verdict.** See the note above `ROUND_TRIP` on why this
    raises no flag: measured, it caught none of three real errors and flagged a
    correct one. What it is good for is a human glance — `ഗോൾഫിൽ പന്തടിക്കാനുള്ള
    നീണ്ട വടി` reads back as "Long rod to play on Golbf", which tells the
    operator instantly that `Driver` went somewhere strange.

    Runs **after** the forward model has been freed, never alongside it: the
    shop PC has 12 GB and one model at a time is the standing rule. Rows that
    were answered without the model are skipped — there is nothing to check
    about a lexicon lookup, and loading a second model to confirm the glossary
    would be absurd.
    """
    if not models.hf_cached(ROUND_TRIP_ENGINE):
        # Not downloaded. The other signals still work; this one is simply not
        # available, and saying so beats pretending it ran.
        log.info("Round-trip check skipped: %s is not downloaded", ROUND_TRIP_ENGINE)
        return

    checkable = [
        r
        for r in rows
        if not r.exact and not r.unresolved and _MALAYALAM.search(r.translation)
    ]
    if not checkable:
        return

    if reporter:
        reporter.step(f"Reading {len(checkable)} rows back into English…", 0.96)

    import torch

    # Distinct strings only, exactly as the forward pass does. A member list
    # repeats a department name thirty times and this is a second model call.
    distinct = list({r.translation for r in checkable})
    readings: dict[str, str] = {}

    with models.loaded(ROUND_TRIP_ENGINE) as bundle:
        tokenizer, model = bundle
        for start in range(0, len(distinct), BATCH):
            chunk = distinct[start : start + BATCH]
            batch = tokenizer(
                chunk, return_tensors="pt", padding=True, truncation=True, max_length=512
            )
            with torch.no_grad():
                generated = model.generate(
                    **batch, num_beams=1, max_length=512, early_stopping=True
                )
            for source, reading in zip(
                chunk, tokenizer.batch_decode(generated, skip_special_tokens=True),
                strict=True,
            ):
                readings[source] = reading.strip()

    for row in checkable:
        row.back_translation = readings.get(row.translation, "")


def _prepare(engine: str, text: str) -> str:
    """IndicTrans2 wants explicit language tags; Marian infers the pair."""
    if engine.startswith("indictrans2"):
        return f"{SRC_LANG} {TGT_LANG} {text}"
    return text


def _strip_tags(text: str) -> str:
    return re.sub(rf"^\s*({SRC_LANG}|{TGT_LANG})\s+", "", text).strip()


def engine_status() -> list[dict[str, object]]:
    """What the Settings screen shows about translation."""
    rows: list[dict[str, object]] = []
    for key in ("opus-mt-en-ml", "indictrans2-en-indic"):
        spec = models.REGISTRY.get(key)
        if spec is None:
            continue
        rows.append(
            {
                "key": key,
                "label": spec.label,
                "licence": spec.licence,
                "gated": bool(spec.gated),
                "downloaded": models.hf_cached(key),
                "notes": spec.notes,
                "default": key == "opus-mt-en-ml",
            }
        )
    return rows


__all__ = [
    "BATCH",
    "Row",
    "TranslationError",
    "available_engine",
    "engine_status",
    "translate_rows",
]
