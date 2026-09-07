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
        if not target or self.exact:
            return []
        if not _MALAYALAM.search(target):
            return []

        notes: list[str] = []
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
    names: dict[str, str] | None = None,
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
    names = names or {}
    direct: dict[int, Row] = {}
    remembered_count = 0
    dictionary_count = 0
    name_count = 0
    needs_model: list[tuple[int, gl.Masked]] = []

    no_glossary = not terms

    for index, source in enumerate(sources):
        remembered = memory.get(textkey.normalise(source))
        if remembered:
            direct[index] = Row(
                source=source,
                translation=remembered,
                from_memory=True,
            )
            remembered_count += 1
            continue

        written = names.get(source)
        if written:
            direct[index] = Row(
                source=source,
                translation=written,
                from_name=True,
            )
            name_count += 1
            continue

        masked = gl.mask(source, terms)
        if gl.is_fully_covered(source, terms):
            restored = gl.restore(masked.text, masked)
            direct[index] = Row(
                source=source,
                translation=restored.text,
                glossary_terms=masked.matched,
                glossary_only=True,
            )
            continue

        # Only when the glossary matched *nothing* in this cell. A cell the
        # glossary touched but did not cover goes to the model with that term
        # masked, so the client's own approved wording is never quietly
        # replaced by a general dictionary's idea of the same word.
        if not masked.terms:
            # A headword first, then a cell built entirely out of trade terms
            # and numbers — `Flex banner`, `Art card 300 gsm`, `6x4 feet`.
            answer = dictionary.get(textkey.normalise(source)) or compose(
                source, phrases
            )
            if answer:
                direct[index] = Row(
                    source=source,
                    translation=answer,
                    from_dictionary=True,
                )
                dictionary_count += 1
                continue

        # Untouched by the library. A partly-covered cell is deliberately *not*
        # half-marked-up before it goes to the model — see `compose`.
        needs_model.append((index, masked))

    if reporter:
        already = []
        if remembered_count:
            already.append(f"{remembered_count} rows from your corrections")
        if name_count:
            already.append(f"{name_count} names written by sound")
        if dictionary_count:
            already.append(f"{dictionary_count} from the word library")
        already.append(
            f"{len(direct) - remembered_count - dictionary_count - name_count} "
            f"from the glossary"
        )
        reporter.step(
            f"{', '.join(already)}, {len(needs_model)} to translate…",
            0.1,
        )

    results: dict[int, Row] = dict(direct)

    if needs_model:
        translated = _run_engine(
            engine, [m.text for _, m in needs_model], reporter, done_base=len(direct),
            total=len(sources), progress_to=progress_to,
        )
        for (index, masked), output in zip(needs_model, translated, strict=True):
            # The source goes in so `restore` can tell its own mangled markers
            # from a client's part number — see glossary._debris.
            restored = gl.restore(output, masked, source=sources[index])
            results[index] = Row(
                source=sources[index],
                translation=restored.text,
                glossary_terms=masked.matched,
                lost_terms=restored.lost,
                debris=restored.debris,
                term_appended=restored.appended,
                no_glossary=no_glossary,
            )

    return [results[i] for i in range(len(sources))]


def _run_engine(
    engine: str,
    texts: list[str],
    reporter: Reporter | None,
    done_base: int = 0,
    total: int | None = None,
    progress_to: float = 0.95,
) -> list[str]:
    """Load the model, translate in batches, free it."""
    spec = models.spec(engine)
    total = total or len(texts)

    import torch

    outputs: list[str] = []
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
                    **batch, num_beams=4, max_length=512, early_stopping=True
                )
            outputs.extend(
                tokenizer.batch_decode(generated, skip_special_tokens=True)
            )
            if reporter:
                done = done_base + len(outputs)
                reporter.step(
                    f"Translating — row {done} of {total}",
                    0.1 + (progress_to - 0.1) * done / total,
                )

    if spec.key.startswith("indictrans2"):
        outputs = [_strip_tags(o) for o in outputs]
    return outputs


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
