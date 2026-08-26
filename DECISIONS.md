# Decisions — Architecture Decision Records

**Last updated:** 2026-08-22

Every deviation from the original `Focus-Toolkit-Plan.md` is recorded here with its evidence.
Nothing changes silently. To reverse a decision, add a new ADR — do not edit an old one.

---

## ADR-001 · Python 3.13, not 3.11

**Date:** 2026-08-22
**Context:** The source plan specified Python 3.11. The dev Mac has 3.14 installed by default.
Neither is obviously right — 3.11 is ageing, 3.14 is bleeding-edge and risks missing wheels.
**Decision:** Pin **3.13**.
**Evidence:** Checked PyPI classifiers for every dependency. `onnxruntime` 1.29.0, `rembg` 2.0.81,
`torch` 2.13.0, `transformers` 5.15.1, `fastapi` 0.141.1, `numpy` 2.5.2 and `opencv-python-headless`
5.0.0.93 all publish 3.13 **and** 3.14 wheels. 3.13 is one release behind the edge, so transitive
wheel coverage is complete rather than merely probable.
**Consequences:** Safe today, and a one-line bump to 3.14 later. Note `rembg` requires ≥3.11 and
`numpy` ≥3.12, so 3.11 would have constrained us anyway.

---

## ADR-002 · uv, not pip + venv

**Date:** 2026-08-22
**Context:** The source plan used `python -m venv` and `pip install`, with different activation
commands per OS and no lockfile.
**Decision:** Use **uv** for Python installation, dependency resolution, and locking.
**Evidence:** uv 0.12 is already installed on the dev machine and can install CPython itself, so the
shop PC does not need a separately managed Python.
**Consequences:** One `uv sync` reproduces the exact environment on Mac and Windows from a committed
`uv.lock`. Phase-grouped optional dependencies mean Phase 1 installs ~30 MB instead of 3.5 GB. Cost:
one new tool for the operator to install — offset by removing venv activation from the launchers.

---

## ADR-003 · React + shadcn/ui, not Alpine.js

**Date:** 2026-08-22
**Context:** The source plan chose HTML + Alpine.js from a CDN specifically to avoid a build step.
The stated product requirement is a clean, modern UI with smooth UX.
**Decision:** **React 19 + TypeScript + Vite + Tailwind v4 + shadcn/ui.**
**Reasoning:** The instruction was to reuse code from git and not write unwanted code. shadcn/ui is
*exactly* that — accessible, polished components copied in from git. Alpine would mean hand-writing
every modal, toast, drag-drop zone, progress bar and canvas panel, which is **more** code written,
not less, for a plainer result.
The build-step objection does not survive scrutiny: `npm run build` runs on the dev machine and
emits `frontend/dist/`, which FastAPI serves statically. **The shop PC never installs Node.**
**Consequences:** The dev machine needs Node (v26 already present). `frontend/dist/` must be rebuilt
and shipped when the UI changes — a real ongoing obligation, and the honest cost of this choice.

---

## ADR-004 · Vendor the map file; treat Payyans as an optional spike

**Date:** 2026-08-22
**Context:** The source plan's Phase 1 depends on `pip install git+.../libindic/payyans.git`, and
flagged its licence as an open question.
**Decision:** Vendor `ML-TTKarthika.map` into `data/maps/` and build against the **map file**.
Payyans becomes a 2-hour timeboxed spike at the start of Phase 1, not a hard dependency.
**Evidence:**
- Payyans on PyPI is **dead**: last release v0.2, **2013-07-31**, source-only, Python 2 era, no
  `requires_python`. It will not install on 3.13 as published.
- Its licence is **LGPL-3.0** (PyPI metadata) — which would have been fine as a pip dependency, so
  licensing was never the real risk. Staleness was.
- `libindic/unicode-conversion-maps` publishes `maps/ML-TTKarthika.map`: a **151-line plain
  `ascii=unicode` table**, community-maintained, with a README explicitly inviting reuse across
  tools (Payyans, Freaknz, Chekkans).
**Consequences:** Phase 1 cannot be blocked by a dead package. If the git version of Payyans does
install and pass the golden tests, we use it — it carries tested reordering logic. If not, we write
~150 lines against the same map. Either path shares the same map file and the same tests, so the
spike is cheap and the outcome is safe.

### Spike outcome — 2026-08-22

The spike **installed and ran** on Python 3.13 (the git repo is Python 3, unlike the 2013 PyPI
release) and its reordering was correct on every case. But we still wrote our own converter, for
three reasons found during the spike:

1. **A defective map entry it selects.** `ന്ന` has two codes: `¶` (U+00B6) and `∂` (U+2202).
   ML-TT fonts are 8-bit, so every glyph must sit at one cp1252 byte — and `∂` is the *only* key in
   the whole map that cannot. Payyans takes the last duplicate and therefore emits `∂` for one of
   the commonest conjuncts in Malayalam, which would paste into CorelDRAW as a broken glyph.
2. **It does not prefer the longest match.** `സ്റ്റ` is a single 5-codepoint ligature `Ì`; payyans
   emits `kvä` instead, which renders with a visible chandrakkala.
3. **A git dependency is a deployment risk.** `git+https://…` has no released version to pin, tracks
   the default branch (so upstream changes could silently alter output), and needs GitHub reachable
   at install time — which failed intermittently even while doing this work.

Our loader instead **rejects any key outside cp1252**, prefers the first *visible* code among
duplicates (so `ണ്ട` resolves to `ï` rather than the soft hyphen listed above it), and matches
longest-first.

**Payyans is retained as a dev-only test oracle.** `tests/test_fonts.py` asserts we agree with it on
every golden pair where duplicate selection does not apply — 20+ cases — so a future change to our
reordering cannot drift unnoticed. One deliberate divergence (`ൌ`) is pinned by its own test.

---

## ADR-005 · `google-genai`, not `google-generativeai`

**Date:** 2026-08-22
**Context:** The source plan specified `pip install google-generativeai`.
**Decision:** Use **`google-genai`** (2.19.0).
**Evidence:** `google-generativeai` is the deprecated SDK — stuck at 0.8.6 and capping at Python
3.12, so it would not even install on 3.13. `google-genai` 2.19.0 supports 3.10–3.14.
**Consequences:** Different import surface from most older tutorials. Model IDs also updated:
`gemini-3-pro-image` (Nano Banana Pro, ~₹11.5, ~₹6 batch) and `gemini-3.1-flash-image` (~₹4).

---

## ADR-006 · Accept PyTorch for Phase 3

**Date:** 2026-08-22
**Context:** The source plan claimed ONNX Runtime would run "all local AI", and budgeted ~1 GB of
models. The user chose local IndicTrans2 over a cloud translation API.
**Decision:** Accept **PyTorch + transformers + IndicTransToolkit** for Phase 3, and correct the
storage budget to **~4 GB**.
**Evidence:** IndicTrans2 has no maintained ONNX export path; the HF models require transformers,
and its tokenizer now lives in the separate `IndicTransToolkit` package (MIT, 1.1.1). PyTorch alone
is ~2.5 GB.
**Consequences:** The "everything runs on ONNX Runtime" simplification is false and must not be
repeated in any doc. Phase 3 deps are **optional extras**, installed only on reaching Phase 3, so
Phases 1–2 stay small. Models live on the 932 GB HDD, not the 112 GB SSD. In exchange: translation
is free forever, works offline, and no client catalogue leaves the building.

---

## ADR-007 · Lock BiRefNet; ban `bria-rmbg`; never copy Upscayl

**Date:** 2026-08-22
**Context:** The shop is client-facing and sells the output, so licence mistakes are commercial risk.
**Decision:** `MODEL = "birefnet-general"` is a locked constant. `bria-rmbg` weights are banned.
Upscayl may be read for reference but **never** copied.
**Evidence:** BiRefNet is MIT and confirmed available in rembg as `birefnet-general`. `bria-rmbg`
requires a paid commercial licence. Upscayl is AGPL-3.0 — copying its source would force us to
open-source this app. FLUX Kontext dev is non-commercial and also banned.
**Consequences:** Recorded in [LICENSES.md](LICENSES.md) and enforced as a rule in
[CLAUDE.md](CLAUDE.md). Pin `rembg>=2.0.81` (see ADR-008).

---

## ADR-008 · Pin `rembg>=2.0.81`

**Date:** 2026-08-22
**Context:** The source plan said pin ≥2.0.75 due to a CVE.
**Decision:** Pin **≥2.0.81** and do not run rembg's bundled HTTP server.
**Evidence:** **CVE-2026-40086** is real — path traversal via the `model_path` parameter in rembg's
HTTP server, allowing arbitrary file reads (CVSS 5.3, CWE-22), fixed in 2.0.75. Current release is
2.0.81. We import the library rather than running its server, so we are not exposed either way, but
the pin is free.
**Consequences:** The vulnerability's *shape* — a user-supplied path reaching a model loader — is
recorded in [SECURITY.md](SECURITY.md) as the in-house rule that models are selected from a fixed
registry by key, never by path.

---

## ADR-009 · SQLite via stdlib, no ORM

**Date:** 2026-08-22
**Context:** Glossary, prompt library, presets, API keys and job history need persistence.
**Decision:** **`sqlite3` from the standard library.** No SQLAlchemy, no SQLModel.
**Reasoning:** A few thousand rows, one writer, a handful of tables. An ORM would add a dependency,
a migration framework, and a learning surface for zero benefit at this scale.
**Consequences:** Hand-written SQL in `db.py`. If the schema ever gets genuinely complex, revisit
with a new ADR — do not drift into one.

---

## ADR-011 · Convert Malayalam only; pass everything else through

**Date:** 2026-08-22
**Context:** The map contains `þ=-`, so a naive reverse lookup rewrites every ASCII hyphen. A golden
test caught `A-1, M.G. Road` becoming `Aþ1, M.G. Road`.
**Decision:** Only sequences containing a **Malayalam codepoint (U+0D00–U+0D7F)** are converted.
Latin letters, digits, punctuation and whitespace pass through untouched.
**Reasoning:** The shop constantly pastes mixed content — addresses, phone numbers, product codes.
Corrupting ASCII punctuation would be a daily, silent error, and `-` almost certainly already renders
as a hyphen at its own ASCII position in the font.
**Consequences:** `þ` stays in the ASCII→Unicode direction (harmless and correct there). Also
documented: `ഌ` and `നു` are genuinely indistinguishable in this encoding — both are `\p` — so
ASCII→Unicode resolves to `നു`, the overwhelmingly common one, and archaic `ഌ` does not round-trip.

---

## ADR-012 · Copying degrades in three steps, and never lies about it

**Date:** 2026-08-22
**Context:** Getting text onto the clipboard *is* the feature. During verification
`navigator.clipboard.writeText` was **denied by browser policy** even on localhost in a secure
context — which can happen on the operator's machine too (enterprise policy, or Firefox's stricter
rules).
**Decision:** Three steps: async clipboard → `execCommand("copy")` → leave the result selected.
The UI **probes the capability on load** and changes what it promises: with the async clipboard it
says "pasting converts and copies automatically"; without it, "pasting converts and selects the
result — press ⌘C / Ctrl+C".
**Reasoning:** `execCommand` needs no permission but does need a user gesture, so it covers the
button and the shortcut but not an async auto-copy. Rather than silently failing after a paste, the
tool selects the text and says so. One keystroke, and the hint is never wrong.
**Consequences:** `execCommand` is deprecated but universally supported and has no replacement for
this case. Verified working end to end with `clipboard-write` denied.

---

## ADR-013 · Flex "good" is 50 DPI, not 72

**Date:** 2026-08-22
**Context:** First cut set the flex banner threshold at the top of the 30–72 DPI
range the source plan quotes. A test then showed a 4000 px photo at 6×4 ft —
routine shop work — being reported as *borderline*.
**Decision:** flex = **50 DPI good, 30 DPI minimum**.
**Reasoning:** 50 is what the arcminute rule asks for at the near end of the
stated viewing distance (≈2 m); 30 matches the far end. PRD.md says a false YES
is a failure and a false NO merely cautious — but a tool that cries wolf on
everyday jobs stops being consulted, which costs the same trust by a slower
route. `test_ordinary_shop_job_is_not_flagged` pins this.
**Consequences:** Also corrects an over-optimistic line in PRD.md: an 800 px logo
is **not** "fine as a large flex banner". It tops out near 1.3 ft of flex, and
only reaches 3 ft treated as a distant hoarding. Pinned by
`test_800px_logo_is_not_a_large_banner_either`.

---

## ADR-014 · Upscaler weights: pinned commit plus checksum

**Date:** 2026-08-22
**Context:** Real-ESRGAN ships PyTorch `.pth`, not ONNX. Converting it ourselves
would pull in PyTorch, which Phase 2 exists to avoid.
**Decision:** Use the ONNX export from `qualcomm/Real-ESRGAN-x4plus`, fetched
from an **immutable commit hash** and verified against a pinned **SHA-256** on
every download. Refuse the file if the hash does not match.
**Evidence:** Qualcomm's LICENSE adds no terms — it defers to the original,
which is **BSD-3**. The graph has a **static `1×3×128×128` input**, confirmed by
inspection, so tiling is mandatory rather than an optimisation.
**Consequences:** No floating branch, no silent upstream change — the failure
mode that made a git dependency unacceptable in ADR-004. The static shape turns
out to help: 128 px tiles mean constant memory regardless of image size, which
is what lets this run on the shop PC at all.

---

## ADR-015 · Guard upscaling on time, not just memory

**Date:** 2026-08-22
**Context:** Measured one 128 px tile: **59 ms on CoreML, 2 487 ms on CPU**. A
40× gap. The shop PC has no usable GPU, so a blind 4× of a 4000 px photo is
1.3 minutes on the Nitro V and **56 minutes** on the shop PC.
**Decision:** Two guards. Upscaling targets **a pixel size, not a factor** —
normally `printsize.Assessment.upscale_to`. And `estimate_seconds()` refuses the
model path past `MAX_ESTIMATED_SECONDS`, falling back to Lanczos with a note
that says why and does not claim to add detail.
**Reasoning:** Phase 2's real constraint was never RAM — tiling made memory
constant. It is wall-clock, and it depends on which machine is running.
**Consequences:** Realistic cost with a target on the shop PC is **2–3 minutes**,
not the plan's 60–90 s; ARCHITECTURE.md now carries the measured numbers. The
guard is device-dependent, so `test_compute_guard_is_device_dependent`
monkeypatches the provider to assert both branches on any machine.

---

## ADR-016 · Accept any size; the operator picks 2× or 4×

**Date:** 2026-08-22
**Context:** ADR-015 had the tool choose the enlargement for you and refuse jobs
it judged too slow. The operator asked for the opposite: accept every file, and
let them choose 2× or 4×.
**Decision:** Three explicit modes — **2×**, **4×**, and **For print** (exactly the
pixels the calculator says the job needs). Any input size is accepted. Slowness
no longer refuses anything.
**What changed**
- Decode limit **120 MP → 1 000 MP**, upload cap **60 MB → 1 GB** (both still
  finite, so a genuine decompression bomb is still refused — SECURITY.md).
- `MAX_ESTIMATED_SECONDS` **deleted**. The estimate is shown before the operator
  commits, and every job is cancellable, so a long run is their call to make.
- The model now always runs at its native 4× on the **full** source and the
  result is resampled to the requested size. Nothing is discarded before the
  model sees it, and cost depends only on the source — so **2× and 4× take the
  same time**, which the UI states rather than leaving to be discovered.
**The one limit that remains** is `MAX_OUTPUT_PIXELS` (300 MP ≈ 21000×14000):
what can be assembled and written as a single file. Past it Lanczos still
delivers a result rather than failing.
**A mechanism tried and removed:** assembling huge results in a memory-mapped
file. `Image.frombuffer` only shares memory for byte-aligned modes (RGBA/RGBX/L),
**not** 3-byte RGB — it silently copies — and even with RGBA the encoder
materialises the whole image, so the peak lands at encode either way. It was
deleted rather than shipped with a comment claiming a benefit it did not deliver.
**Consequences:** A slow machine no longer silently downgrades the operator's
choice. The trade is that they can now start a job that runs for a long time —
mitigated by the up-front estimate, live tile progress, and cancellation.

---

## ADR-017 · Translation engine: open by default, IndicTrans2 opt-in

**Date:** 2026-08-22
**Context:** ROADMAP.md Phase 3 specified IndicTrans2 (MIT, purpose-built for
Indian languages). Two things blocked it:

1. **The repo is gated.** `ai4bharat/indictrans2-*` returns 401 — it needs an HF
   account, accepting terms, and a token. Still free and MIT, but not automatic.
2. **`IndicTransToolkit` 1.1.1 is broken against transformers 5.x.** It imports
   `PreTrainedTokenizerBase` from `transformers.tokenization_utils`, which moved
   to `tokenization_utils_base`. It declares a bare `transformers` with no upper
   bound, so it installs and then fails at import.

**Decision:** Ship **`Helsinki-NLP/opus-mt-en-ml`** (Apache-2.0, 57M) as the
working default, and register IndicTrans2 as a selectable engine with its setup
requirements stated in the UI. Engine choice sits behind `translate_rows()`, so
switching is configuration rather than a rewrite. The `indictrans` extra pins
`transformers<5`; uv's `conflicts` declaration lets both extras coexist in one
lockfile without being installed together.

**NLLB-200 was rejected on licence, not capability.** It is the obvious choice —
1.3M downloads a month and good Malayalam — but it is **cc-by-nc-4.0**,
non-commercial. The shop sells this work, so LICENSES.md rule 4 applies. Pinned
by `test_every_engine_licence_permits_commercial_use`.

**Consequences:** Phase 3 works out of the box and gets better when the operator
does the one-time gate step. The default is weak enough that the glossary and
review grid are not optional niceties — see ADR-018.

---

## ADR-018 · The glossary is enforced by masking, and it is not sufficient alone

**Date:** 2026-08-22
**Context:** Measured against the default engine, real catalogue rows came back
wrong in ways that matter: *Coconut oil, 1 litre bottle* → "oil, 1 litre bottle"
(**"coconut" dropped**), *bulk orders* → ആജ്ഞകൾ (military orders), *500 g jar* →
"500 ഗ്രാം" (**"jar" dropped**), *Product* → നിർമ്മാണം ("manufacturing").

**Decision — three layers, because one is not enough:**

1. **Bypass.** A cell the glossary covers entirely never reaches the model. A
   product-name catalogue is mostly this case: faster, and a weak model can only
   make an already-correct term worse.
2. **Masking.** Otherwise terms are lifted out, the sentence is translated, and
   the approved terms are put back. Post-translation find-and-replace cannot
   work — by then the term is already mangled.
3. **Flagging.** Masking is **not reliable**. Six placeholder styles were run
   through the real model across four sentence shapes: `X<n>X` scored 5/6, and
   **nothing scored 6/6** (`QQ<n>QQ` was transliterated to ക്യു; every
   punctuation style failed outright). So a lost term is a routine ~1-in-6 event,
   not an edge case. Lost terms are detected, kept in the output, and the row is
   flagged.

**Two bugs this design caught, both data-destroying:**
- A source containing `X0X` — a client part number — was being **deleted** by the
  restore pass. Unknown placeholders are now left exactly as found.
- `\w+` excludes Malayalam combining marks, so it split conjuncts at the virama
  and counted "500 ഗ്രാം" as three words, silently defeating the dropped-word
  check. Word counting is whitespace-based.

**Consequences:** `Row.warnings` also flags output still in English and output
much shorter than its source. It cannot flag a *confidently wrong* single word —
നിർമ്മാണം for "Product" passes every check. That is what the human review is
for, and why the grid shows every row rather than only the suspicious ones.

---

## ADR-019 · Posters are SVG, and the backend never rasterises Malayalam

**Date:** 2026-08-23
**Context:** The Phase 4 exit gate asks for text that is sharp **and editable**.
It also has to be Malayalam, which needs complex-script shaping — reordering the
pre-base vowel and forming conjunct ligatures.
**Evidence:** This Pillow has no Raqm or HarfBuzz (`features.check("raqm")` is
False), so it does no shaping at all. Rendered and inspected on 2026-08-23:
``കേരളം`` came out with the ``േ`` sign **after** ``ക`` instead of before it, and
``ക്ക`` / ``സ്റ്റ`` / ``ന്ന`` came out as loose letters with a visible
chandrakkala rather than ligatures. Every conjunct and every pre-base vowel wrong
— the same failure class as Phase 1's naive converter.
**Decision:**
- **SVG is the deliverable.** Real `<text>` elements, zero `<path>` elements,
  physical `mm` dimensions. CorelDRAW opens it with every line still editable, so
  a typo is a text edit rather than a re-render.
- **The backend never rasterises text.** `posters.py` does not import
  `ImageDraw`, and a test asserts it never will while Raqm is missing.
- **The PNG proof is rendered by the browser**, which shapes complex scripts
  correctly as a matter of course.
**Also:** blocks may export as ``ascii`` — Phase 1's ML-TTKarthika conversion. The
shaping is then *already done*, so the glyph order is the visual order and no
shaping engine is involved anywhere in the chain. That is the shop's native
CorelDRAW workflow, and it falls straight out of Phase 1.
**Consequences:** No PDF export yet (reportlab would be a new dependency and
would hit the same shaping wall). SVG covers the gate; PDF can follow if the
shop's workflow actually wants it.

---

## ADR-020 · DOM text boxes, not Fabric.js

**Date:** 2026-08-23
**Context:** ARCHITECTURE.md locked Fabric.js v6 for the poster canvas. Building
the editor made three problems with that concrete.
**Decision:** Absolutely-positioned DOM elements with pointer events instead.
**Reasoning:**
1. **Malayalam.** DOM text is shaped by the browser's own text engine, with the
   bundled Noto font, no special handling.
2. **Accessibility.** DESIGN.md sets a WCAG 2.1 AA floor and requires every
   control be keyboard operable. A canvas is a single opaque element; each text
   box here is focusable and nudgeable with the arrow keys (Shift for larger
   steps).
3. **Dependencies.** Zero added. CLAUDE.md says do not add a dependency without
   asking, and each DOM block maps 1:1 onto an SVG `<text>` at export, which
   keeps preview and output structurally identical.
**Consequences:** No rotation or free scaling — added later with pointer handlers
if wanted. ARCHITECTURE.md's Fabric.js entry is superseded by this ADR.

---

## ADR-021 · Preview must lie about nothing

**Date:** 2026-08-23
**Context:** Two fidelity bugs found by looking at the running editor rather than
at the tests.
**Decision:** The preview renders under exactly the constraints the export has.
**What was wrong:**
- **Wrapping.** The DOM boxes wrapped long text over three lines; SVG `<text>`
  never wraps. The preview looked fine and the export would have overflowed.
  Fixed with `white-space: nowrap`, so text overflows on screen exactly as it
  will on paper — and `overflow_warnings()` says so in words.
- **Font pairing in the PNG proof.** A block set to `ascii` still holds Unicode
  in the editor; the proof was pairing that Unicode with ML-TTKarthika, a font
  with no Unicode Malayalam glyphs. On the shop PC that is tofu. The proof now
  always renders Unicode text in the Unicode font; the ASCII conversion belongs
  in the SVG, where CorelDRAW consumes it.
**Also fixed here:** auto-placement ranked individual grid cells and put all
three lines of a poster in one band of a gradient sky — the calmest cells, and
unusable because they overlapped. On a two-tone image four bands score an
identical `0.00`, so the winner was arbitrary. Placement now gives each block its
own zone and finds the calmest band *within* it. The original test passed the bug
because `sorted([y, y, y]) == [y, y, y]`; it now asserts genuine separation.

---

## ADR-022 · An API key is stored encrypted and has no route out

**Date:** 2026-08-23
**Context:** The key is the one thing in this app with direct monetary value.
**Decision:** Encrypted with Fernet before it touches the disk. The encryption
key lives in the **OS keyring** (Keychain / Credential Locker), falling back to a
file in the user's home with owner-only permissions when no keyring is available.
**No route returns a key** — `db.list_api_keys()` returns a four-character hint
and nothing else, and there is deliberately no endpoint that could.
**Why not `.env`:** a `.env` gets copied to a USB stick when the app moves
machines, pasted into a chat when something breaks, and swept into folder
backups. An encrypted row plus an OS-held key survives all three.
**Enforced by test:** `test_no_route_returns_the_key` walks every settings GET
and greps the response for the secret. Adding a leaky field breaks the suite.
**Consequences:** Losing the encryption key means re-entering the API key — a
30-second job, and the right trade against storing it in the clear.

---

## ADR-023 · A refused AI call is a 200, not an error

**Date:** 2026-08-23
**Context:** Exit gate: *"a failed or refused API call degrades gracefully and
never loses the operator's work."*
**Decision:** Every AI route returns HTTP 200 with `{"ok": false, "error": …}`.
The SDK exception is translated into something actionable — *"That API key was
rejected. Check it in Settings"*, *"Google's quota is exhausted"* — rather than
surfaced raw.
**Reasoning:** An HTTP error makes the browser treat the request as failed and
throw away the form. A 200 keeps the instruction the operator typed and the
image they chose on screen, with the reason beside them.
**Also:** a refused call is recorded as a **failed run costing nothing**, not as
spend. Counting it would make the monthly total disagree with Google's console
for no reason, and that figure is only useful if it can be trusted.

---

## ADR-024 · Money is counted in paise, and the total says it is an estimate

**Date:** 2026-08-23
**Context:** The shop has a ₹2,000/month ceiling and needs to see where it stands.
**Decision:** Costs are integers in **paise**. Rates are a table in `ai.py`,
editable from Settings because Google will change them.
**Reasoning:** Currency in floating point drifts, and this total gets compared
against Google's console — a few paise of disagreement would undermine
confidence in the whole figure. Integers cannot drift.
**Batch is the default** for artwork: half price (₹6 vs ₹11.50) for a few
minutes' wait, which on poster work is no wait at all.
**The UI states it is an estimate.** It is built from this app's own job history,
so it cannot know about spend from elsewhere, and claiming otherwise would be a
small lie that eventually costs trust.

---

## ADR-025 · Phase 5 is built but not proven against the live API

**Date:** 2026-08-23
**Context:** Verifying the paid path means spending the shop's money with a key
that is theirs to hold, not mine to enter.
**Decision:** Every local guarantee is tested against a faked Gemini client —
key handling, cost arithmetic, spend accounting, error translation, layout
parsing, prompt rendering. **No test makes a real API call.**
**What that leaves unverified,** stated plainly rather than glossed over: whether
Google's live response objects match the shapes `_extract_image` and
`_extract_text` walk. If the SDK returns a different structure, the first real
call fails with *"Google returned no image"* and costs one image.
**How to close it:** enter a key in Settings, press **Test** (a few paise), then
run one artwork generation in batch mode (~₹6). If both work, the shapes are
right. That is the first item on the Phase 5 exit gate.

---

## ADR-026 · A Gemini model name is a setting, not a constant

**Date:** 2026-08-23
**Context:** ADR-025 predicted the first live call would fail on a response
shape. It failed earlier and more stupidly than that: `gemini-3-flash`,
`gemini-3-pro-image` and `gemini-3.1-flash-image` were written from memory and
**none of the three exist**. The operator saw *"models/gemini-3-flash is not
found for API version v1beta"* and reasonably concluded their key was bad.
**Decision:** Model names move out of `ai.py` constants and into preferences
(`ai_model_artwork`, `ai_model_photo`, `ai_model_layout`), chosen in Settings
from a list fetched live from `ModelService.ListModels`. The constants that
remain are defaults only. Testing the key calls `resolve_models()`, which
replaces any configured name Google no longer lists.
**Reasoning:** Google renames and retires these on its own schedule. A name that
has moved should cost the operator a dropdown, not a code change and a support
conversation — and this shop has no one to have that conversation with.
**Consequences:** Prices are keyed by *role* rather than by model, because one
image model now serves both artwork and photo editing at different sizes. The
figures stay estimates; `budget.is_estimate` was already true and still is.
**What this does not fix:** the defaults are still unverified guesses. They are
the most conservative names available and the picker exists precisely because
they may be wrong too.

---

## ADR-027 · A design style owns both the prompt and the text colours

**Date:** 2026-08-23
**Context:** The poster screen asked for the copy *and* a separate "Picture of…"
description. The operator wrote the poster twice and nothing made the two halves
agree, so the picture was routinely unrelated to the words printed over it.
**Decision:** A **design style** is a first-class saved object: a fixed prompt
structure the shop owns, plus the colours and sizes its words take on. Choosing
one in step 2 of the designer repaints the text *and* decides how the picture is
asked for. The poster's own copy is substituted into the style's prompt, so the
brief for the picture is the message on the poster.
**Reasoning:** Keeping the two apart made them a matter of the operator's memory.
A look that colours the headline but says nothing about the picture — or the
reverse — is half a style, and the half that is missing is the one that goes
wrong at the printer.
**Consequences:** `poster_styles` in SQLite, six seeded looks, and a `role` field
on every text block so a style knows which line is the headline. Every seeded
body forbids lettering three times and `styles.validate()` refuses to save one
that does not, because a model handed a Malayalam headline will draw it, and it
will draw it wrong.

---

## ADR-010 · Docs before code

**Date:** 2026-08-22
**Context:** `vibe-coding-starter-kit.md` prescribes six context files before writing any code.
**Decision:** Write ten markdown files first, review them, and only then begin Phase 1.
**Reasoning:** Four dependency claims in the source plan turned out to be wrong. Writing them into
code first would have propagated them into every subsequent session. The four added files
(ROADMAP, LICENSES, SETTINGS, README) each carry load the standard six do not: phase exit gates,
commercial licence risk, a cross-cutting feature spec, and cross-platform setup.
**Consequences:** No working software on day one. The trade is that the assumptions get corrected
at the cheapest possible moment.
