# Roadmap — Focus Toolkit

**Status:** Draft for review · **Last updated:** 2026-08-22

Five phases. Each has an **exit gate** it must pass before the next begins.

The source plan gave day estimates. Those are replaced with gates, because "two to three days" is a
guess but "renders correctly in CorelDRAW" is a fact. A phase is done when its gate passes, not when
the code compiles.

**Rule:** do not start Phase N+1 while Phase N's gate is unmet. A half-finished Phase 2 is worse
than no Phase 2.

---

## Phase 0 · Documentation ✅ complete — 2026-08-22

Write the context bible so no session re-derives decisions or re-inherits corrected errors.

**Deliverables:** `PRD` · `ARCHITECTURE` · `CLAUDE` · `SECURITY` · `DESIGN` · `DECISIONS` ·
`ROADMAP` · `LICENSES` · `SETTINGS` · `README` · `.gitignore` · `.env.example`

**Exit gate**
- [x] Operator has read all ten documents and corrected anything wrong
- [x] `PRD.md` describes the product the operator actually wants
- [x] No document contains `google-generativeai`, `python3.11`, `pip install payyans`, or `rembg>=2.0.75`
- [x] `.gitignore` covers `.env`, `models/`, `*.db` **before** `git init` is run

---

## Phase 1 · Malayalam font converter ← one gate item left (CorelDRAW check)

No AI, no GPU, no cost — a dictionary lookup with careful reordering. First because it works
instantly, proves the whole app shape, and gets used fifty times a day.

**Scope:** project scaffold (uv + FastAPI + Vite/React/shadcn) · vendored `ML-TTKarthika.map` ·
Unicode→ASCII conversion (and the reverse) · `POST /api/fonts/convert` · paste → convert → auto-copy
screen · golden test suite.

### Spike result: it installed, and we still wrote our own

Payyans' git repo **is** Python 3 and its reordering was correct — but it selects a map entry (`∂`,
U+2202) that cannot exist in an 8-bit font, misses the longest conjunct match, and would be an
unpinnable git dependency. Full reasoning in [ADR-004](DECISIONS.md). It is retained as a dev-only
test oracle that cross-checks our reordering on 20+ pairs.

### The real risk — write this down

The source plan claims *"zero risk of broken letters or spelling because the conversion is a fixed
character map and not AI."* **The map is the easy half.** These converters actually break on:

- **Pre-base vowel reordering** — െ േ ൈ sit *after* the consonant in Unicode but the glyph is typed
  *before* it in ASCII fonts. `കെ` → `sI`, not `Is`.
- **Conjuncts** (കൂട്ടക്ഷരം) — ML-TTKarthika has dedicated glyphs; naive substitution produces
  broken clusters.
- **Chillu letters** (ൻ ർ ൽ ൾ ൺ) and **ZWJ/ZWNJ**, which are invisible and survive a copy-paste
  from WhatsApp.

A green test suite does not prove any of this. **Only the font does.**

**Exit gate**
- [x] 25 golden Malayalam strings round-trip correctly, covering vowel signs, conjuncts and chillu
      — 44 pairs, 114 tests passing
- [ ] **A converted string pastes into CorelDRAW in ML-TTKarthika and renders correctly** — checked visually
- [x] Text copied straight from WhatsApp converts without manual cleanup — ZWJ chillu, NFC
      and joiner handling are covered by tests
- [x] Paste → convert → clipboard works without touching the mouse — verified in-browser,
      including with `clipboard-write` denied
- [x] App launches and serves the built UI — `start.command` / `start.bat`

### The one item left, and exactly what to check

Three things need a human eye on the font, because only the glyphs can settle them:

| Check | Type this | Expect |
|---|---|---|
| **Common conjunct** | `¶` | ന്ന — if it shows a box or wrong letter, the duplicate policy needs flipping |
| **ra-sign position** | `{Kmaw` | ഗ്രാമം — we place `{` *before* the consonant, following payyans. If ഗ്ര looks wrong, move `്ര` out of `PRE_BASE` in `backend/features/fonts.py` (one line) |
| **Long ligature** | `tÌj³` | സ്റ്റേഷൻ — proves the 5-codepoint `സ്റ്റ` ligature |

Paste each into CorelDRAW, set ML-TTKarthika, and compare against the Malayalam in the right-hand
column. All three are one-line fixes if they are wrong.

---

## Phase 2 · Image tools ← built, two gate items need the shop PC

`rembg` + BiRefNet for cutouts, Real-ESRGAN for upscaling, and the print-DPI layer on top.

**Scope:** background removal at 300 DPI · protect-subject brush · **2× / 4× / for-print
upscaling, any input size** · **print-size calculator** · job queue · one-model-at-a-time
memory discipline.

**Do not skip the print-size calculator.** The operator enters a target size in feet or millimetres
and the tool answers plainly: *"✅ Good for 6×4 ft flex"* or *"❌ Not enough for A3 brochure"*.
Flex banners print at 30–72 DPI because they are viewed from distance; brochures need 300. This one
feature turns a limitation into the shop's most trusted function.

**A false *yes* is a failure. A false *no* is merely cautious.** Tune accordingly.

**Exit gate**
- [x] Only one model resident in memory at any time — enforced by a single-worker
      executor and asserted by `test_jobs_run_one_at_a_time`, not left to convention
- [x] Progress, named step and elapsed time throughout; job is cancellable —
      verified in-browser (`tile 46 of 70 @ 64%`)
- [x] The calculator has never approved a size that printed badly — asserted
      across every print class by `test_never_approves_below_required_dpi`
- [x] Cutout produces genuine 300 DPI RGBA with the background removed
- [ ] Cutout and upscale both complete on the **shop PC** inside its budget —
      RAM is fine (constant 128 px tiles, ~1.9 GB peak), but **time** needs
      confirming there: expect 2–3 min per image, not seconds. See ADR-015/016
- [ ] The DPI calculator's verdict matches a **real test print** on real stock

### What Phase 2 changed about the plan

Measuring instead of estimating moved three numbers:

| Assumption | Measured |
|---|---|
| Upscale 60–90 s on the shop PC | **2–3 min** for a normal job; ~56 min for 4× of a 4000 px source |
| Phase 2 models ~500 MB | **992 MB** (BiRefNet 928 + Real-ESRGAN 64) |
| Flex "good" = 72 DPI | **50 DPI** — 72 flagged routine jobs as borderline |

The 40× CPU-vs-GPU gap is the number to remember. The tool no longer refuses a
slow job (ADR-016) — it states the estimate up front and stays cancellable, so
the choice of 2×, 4× or for-print is the operator's.

---

## Phase 3 · Excel translator ← built, needs a real client sheet

IndicTrans2 locally, in-place cell writing, side-by-side review grid.

**Scope:** `openpyxl` read/write preserving formatting · IndicTrans2 en→ml · **per-client glossary**
· review grid · accept/reject per row.

**The glossary is per-client, never global.** Client A's brand terms must not leak into Client B's
catalogue. This is the feature that makes translation trustworthy over time — the first correction
of a term is manual; every later use is automatic.

Heavy phase: ~2.5 GB PyTorch + ~1 GB model, on the HDD. Slow on the shop PC's CPU — batch it.

**Exit gate**
- [x] Formatting survives — fonts, fills, widths, merges, number formats and
      formulas all asserted intact by `test_apply_preserves_formatting`
- [x] Glossary terms are honoured, and correcting one fixes every later occurrence
- [x] Glossary is scoped per client — two clients with a conflicting term, proven
- [x] Nothing is written until the operator accepts — extraction is byte-identical,
      and `/export` is the only route that produces a file
- [x] The grid flags suspect rows — lost terms, output still in English, and output
      much shorter than its source (which caught a real dropped word)
- [ ] **A real client sheet** translates acceptably — needs one of your actual
      catalogues, not a synthetic one
- [ ] Runs on the shop PC inside its RAM budget

### What Phase 3 changed about the plan

| Assumption | Reality |
|---|---|
| IndicTrans2 is just a `pip install` | **Repo is gated** — needs an HF account + token. Its toolkit is also broken against transformers 5 |
| The plan's engine would be the default | Default is **opus-mt-en-ml** (Apache-2.0); IndicTrans2 is opt-in. See ADR-017 |
| Glossary makes repeated terms "perfect" | Masking is **~5/6 reliable** — nothing survives MT every time, so lost terms are flagged, not assumed impossible. See ADR-018 |

NLLB-200 would have been the obvious engine and is **rejected on licence**
(cc-by-nc-4.0, non-commercial). Worth knowing before anyone suggests it.

---

### What ADR-035 changed about Phase 3

Measured on a real employee sheet, the pipeline returned ~50 wrong cells and
flagged 2. Almost every text cell was being sent to a sentence-level model,
including the names and house names it can only invent meanings for. Columns are
now classified before anything is translated and only ordinary wording reaches
the model. Provable errors on the frozen gold set fell from 54 to 0.

**Still open:** 99 of those 131 cells are translated but unverifiable by rule, and
the Malayalam in the bundled name, place and trade assets needs one read by a
Malayalam speaker. Both are recorded in ADR-035.

---

## Phase 4 · Posters ← rebuilt, unproven against the live API

**Rebuilt on 2026-09-06 (ADR-034), and the golden rule it was built on is gone.**
It used to read *"AI makes the picture, the app makes the text"* — the AI returned
a layout plan as data and the app drew every word as real SVG `<text>`, which is
what made correct Malayalam and CorelDRAW-editable output possible.

The operator now has their own finished set of poster-design prompts, so the
whole poster is generated instead:

1. Paste the copy, tagged `main:`, `h1:`, `h2:`.
2. Optionally add a reference picture. Without one, a cheap text call turns the
   copy into a visual idea.
3. Pick one of the designs in `data/poster_prompts/`.
4. Gemini draws the finished poster, words and all.
5. Regenerate, or describe a change and have it applied to that same image.

**Scope:** `features/posters.py` reads the design folder; `features/ai.py` makes
the calls; one screen. Deleted with the old phase: the canvas editor, layout
presets, design styles, the SVG export, `POSTER_LAYOUTS.md`, and roughly 8,000
lines around them.

**Exit gate**
- [x] A design dropped into the folder appears without restarting the server
- [x] The copy is read back before anything is spent, so `h1`/`h2` cannot be
      silently swapped
- [x] A refused call is a 200 with a plain reason and the operator's copy intact
- [x] The copy as typed is shown beside every poster, with the warning that the
      app cannot check the words
- [ ] **One real call proves the response shapes** — shared with Phase 5 below,
      and still never made
- [ ] A poster the shop would actually send a client, within two or three attempts
- [ ] **A real print.** Nothing above says whether generating the whole poster
      was the right trade; only a print does

### What the rebuild gave up

Written down because it is not recoverable by trying harder:

| Was guaranteed | Now |
|---|---|
| Malayalam spelled correctly | **Not.** Image models do not shape complex scripts (ADR-019 measured it) |
| Every line editable in CorelDRAW | **Not.** The poster is a raster; a typo means generating again |
| No figure the operator did not type | **Not enforceable.** ADR-030's rule can only be asked for in the prompt now |

---

## Phase 5 · AI editing and artwork ← built, unproven against the live API

The only phase that touches the internet or costs money.

**Scope:** Gemini via `google-genai` · photo editing (`gemini-3.1-flash-image`, ~₹4) · poster artwork
(`gemini-3-pro-image`, ~₹11.5 instant / **~₹6 batch**) · prompt library from
[SETTINGS.md](SETTINGS.md) · spend tracking.

**Default to batch mode.** It is half price, and poster work tolerates a few minutes' wait. That
alone doubles the monthly budget.

Start with a **₹500 top-up** — roughly 140 edits — and test properly before spending more.

**Exit gate**
- [x] The UI makes it unmistakable when a job sends client material off-machine —
      an amber "Leaves this computer" panel on every AI action, naming what is sent
      and what it costs. Features 1–4 never show it, and that contrast is the point
- [x] Prompt templates are editable from Settings without touching code — with
      variable checking, version history, and restore-default
- [x] A failed or refused call degrades gracefully — HTTP 200 with a plain-English
      reason, the operator's inputs untouched, and no charge recorded (ADR-023)
- [ ] **One real call proves the response shapes** — see below. Costs a few paise
      plus one ₹6 image
- [ ] A ₹500 top-up survives a week of real client jobs
- [ ] Spend tracking matches the Google console to within a few rupees

### The one thing that could not be tested here

Verifying the paid path means spending your money with a key that is yours to
hold. So every **local** guarantee is tested against a faked client — 72 tests
covering key handling, cost arithmetic, spend accounting, error translation,
layout parsing and prompt rendering — and **no test makes a real API call**.

What that leaves unverified is narrow and specific: whether Google's live
response objects match the shapes `ai._extract_image` and `ai._extract_text`
walk. If the SDK returns a different structure, the first real call fails with
*"Google returned no image"* and costs one image.

**To close it:** add your key in Settings → press **Test** (a few paise) → run one
artwork generation with batch mode on (~₹6). If both work, the shapes are right.

### What Phase 5 changed about the plan

| Assumption | Reality |
|---|---|
| Poster artwork ~₹6 batch / ~₹12 instant | Confirmed: `gemini-3-pro-image` at ₹6 batch, ₹11.50 instant |
| Keys in `.env` | **Encrypted in SQLite**, encryption key in the OS keyring. A `.env` travels on USB sticks and into backups (ADR-022) |
| — | Costs are **integer paise**, never floats, because this total is compared against Google's console (ADR-024) |

---

## Not scheduled

Deliberately unplanned. Revisit only after Phase 5 has run in production for a month:

- Reference-image analysis for poster style matching
- Additional Malayalam ASCII fonts beyond ML-TTKarthika
- Inpainting (IOPaint)
- Face restoration (GFPGAN)
