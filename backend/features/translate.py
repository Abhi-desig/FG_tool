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

from backend import models
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


@dataclass
class Row:
    """One reviewable row in the grid."""

    source: str
    translation: str
    glossary_terms: list[str] = field(default_factory=list)
    # True when the glossary alone produced this — no model involved.
    glossary_only: bool = False
    # Terms the model dropped. The operator must place these by hand.
    lost_terms: list[str] = field(default_factory=list)

    @property
    def warnings(self) -> list[str]:
        """Why this row is suspect, in words the operator can act on.

        The model fails *confidently* — measured on real output it turned
        "Product" into നിർമ്മാണം ("manufacturing") and dropped "jar" from
        "500 g jar" without any signal. Flagging lost glossary terms alone was
        not enough, so these cheap checks surface the likely omissions too.
        """
        notes: list[str] = []
        target = self.translation.strip()

        if not target:
            notes.append("Nothing came back — translate this by hand.")
            return notes

        if self.lost_terms:
            notes.append(
                f"The model dropped {', '.join(self.lost_terms)} — it was added at "
                f"the end. Move it into place."
            )

        # Glossary-only rows are exact by construction; the rest are guesses.
        if self.glossary_only:
            return notes

        if not _MALAYALAM.search(target):
            notes.append("Still in English — the model left this untranslated.")
            return notes

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
    def needs_attention(self) -> bool:
        """Rows the operator must look at, not merely may."""
        return bool(self.warnings)


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
) -> list[Row]:
    """Translate distinct strings, applying the glossary around the model.

    Cells the glossary covers entirely never reach the model — that is both
    faster and safer, since a weak model can only make an already-correct term
    worse. A catalogue of product names is mostly this case.
    """
    if not sources:
        return []

    engine = engine or available_engine() or "opus-mt-en-ml"

    # Split the work: glossary-only rows need no model at all.
    direct: dict[int, Row] = {}
    needs_model: list[tuple[int, gl.Masked]] = []

    for index, source in enumerate(sources):
        masked = gl.mask(source, terms)
        if gl.is_fully_covered(source, terms):
            restored = gl.restore(masked.text, masked)
            direct[index] = Row(
                source=source,
                translation=restored.text,
                glossary_terms=masked.matched,
                glossary_only=True,
            )
        else:
            needs_model.append((index, masked))

    if reporter:
        reporter.step(
            f"{len(direct)} rows from the glossary, {len(needs_model)} to translate…",
            0.1,
        )

    results: dict[int, Row] = dict(direct)

    if needs_model:
        translated = _run_engine(
            engine, [m.text for _, m in needs_model], reporter, done_base=len(direct),
            total=len(sources),
        )
        for (index, masked), output in zip(needs_model, translated, strict=True):
            restored = gl.restore(output, masked)
            results[index] = Row(
                source=sources[index],
                translation=restored.text,
                glossary_terms=masked.matched,
                lost_terms=restored.lost,
            )

    return [results[i] for i in range(len(sources))]


def _run_engine(
    engine: str,
    texts: list[str],
    reporter: Reporter | None,
    done_base: int = 0,
    total: int | None = None,
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
                    f"Translating — row {done} of {total}", 0.1 + 0.85 * done / total
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
