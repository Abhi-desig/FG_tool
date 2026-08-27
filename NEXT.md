# NEXT — remediation plan

**Status:** Worked through · **Written:** 2026-08-26 · **Actioned:** 2026-08-27 ·
Companion to [ROADMAP.md](ROADMAP.md)

Findings from a full read of the codebase plus a hands-on run of every feature against a live
server (CoreML, all extras installed, real weights). Ordered by what costs the shop money, a
reprint, or a client's trust — not by how hard it is to fix.

Each item says how it was found:

- **[ran it]** — reproduced against the running app
- **[read it]** — found by reading the code, not exercised
- **[doc gap]** — the code and a project document disagree

No code was changed to *produce* this document. Everything below has since been
worked through — see the status block.

---

## Status, 2026-08-27

Every item is addressed except where noted. Commits `f827b77`..`aa473cb`.

| Item | State |
|---|---|
| 0.1 dist is the pre-Phase-5 UI | **done** — rebuilt, force-added, `scripts/check_release.py` gates it |
| 0.2 Malayalam headline overflow | **done** — auto-shrink then wrap, measured in the browser, golden fixture |
| 0.3 glossary placeholder debris | **done** — source passed to `restore()`, debris swept, distinct warning |
| 0.4 result destroyed by navigation | **done** — screens stay mounted, recent-jobs panel, toast is a link |
| 1.1 budget is decorative | **done** — refuses past budget with an explicit override, fields bounded |
| 1.2 dropped layout block | **done** — every copy field asserted present; see the note below |
| 1.3 nonsense model auto-repair | **done** — allowlist by family, refuses rather than guessing |
| 1.4 batch toggle saves nothing | **done** — hidden where no discount exists |
| 1.5 SVG references an unembedded font | **done** — woff2 embedded; CorelDRAW caveat stated at export |
| 1.6 single-word mistranslations | **done** — unit/number/trade-term/short-cell checks; all 6 measured rows flag |
| 1.7 no glossary unless picked | **done** — remembered per session, warned before and after |
| 2.1 decompression-bomb guard | **done** — pixel and side caps read from the header, uploads spooled to disk, impossible scales refused |
| 2.2 cancel appears dead | **done** — `cancelling` exposed, "Cancelling…" with why |
| 2.3 progress stuck at 35% | **done** — `opaque_step`; the step names the wait and the device |
| 2.4 temp files accumulate | **done** — TTL sweep, `410` with an explanation, SECURITY.md §5 corrected |
| 2.5 no CSRF or Host defence | **done** — `backend/guard.py`, verified against a live server, §1 rewritten |
| 2.6 base install cannot start | **done** — lazy routers, `numpy` declared, `/api/features` |
| 2.7 keyring failure orphans the key | **done** — backend recorded; refuses rather than replacing |
| 3.1–3.15, 3.17 | **done** |
| 3.16 `ai._friendly` across a boundary | **done** — `friendly_error` is public |
| 3.18 stray file in `models/hf/` | **noted, not deleted** — it belongs to another tool and is gitignored; `config.py` now says the directory is shared |
| 4.1 close the gates | **blocked** — needs a real Gemini key. Part A of the self-check is fully green; part B still rejects the configured key |
| 4.2 tests do not cover the P0s | **done** — golden fixtures for 0.2, 0.3 and 1.6; job-lifecycle tests for 0.4 |

**Two findings did not reproduce as written:**

- **3.10** — the poster progress bar with `role="progressbar"` and no
  `aria-valuenow` does not exist in the current code; the only `progressbar` is
  `ApiKeys.tsx`, which sets it correctly. The `aria-current` half was real and is
  fixed.
- **3.13** — `auto` layout preserves `role`. Verified through
  `place_in_calm_space`, through `POST /api/posters/auto`, and in
  `PosterDesigner`. Nothing was changed.

**One bug was introduced and fixed while working through this.** The 1.2 repair
first re-inserted *every* non-empty input field the model had not returned, which
included `tone` — a styling hint — and would have printed the word "festive" on a
client's poster. Caught by running `check-ai`, not by the suite. Restricted to
the four copy roles, and `diagnose.py` now checks block ids rather than a count.

**Also corrected in 0.2:** the estimator's safety margin was applied after
fitting, so text the fitter had brought exactly to the limit was reported as
overflowing. The margin now narrows the fit target, and `over_box` ("drag the box
wider") is separated from `overflows` ("will be cut off"). Only the second blocks
an export.

---

## P0 — Ship-blockers

### 0.1 The committed `frontend/dist` is the pre-Phase-5 UI  **[ran it]**

`git show HEAD:frontend/dist/assets/index-CkNRyGuO.js` contains **zero** references to
`api/styles` or `api/ai/models` — both added in `519de89`. That commit changed ~2,000 lines of
frontend and never re-force-added `dist`.

The working tree is in a trap state: the current build (`index-doUN5KJP.js`) is untracked and
gitignored, while the two old tracked assets show as **deleted** and `dist/index.html` as
**modified**. One `git checkout .` or `git stash` reverts the shop PC to the old bundle.

- [x] Rebuild and `git add -f frontend/dist` as a release commit
- [x] Add a release check that fails when `dist/index.html`'s asset hashes are not the tracked ones
- [x] Decide: keep force-adding, or ship `dist` as a release artifact and stop tracking it
      (the note in [.gitignore](.gitignore) offers both and the repo has drifted between them)

### 0.2 A Malayalam headline cannot fit the canvas at the default size  **[ran it]**

Measured in the browser with `NotoSansMalayalam.woff2` actually loaded, A4 portrait (2480 px wide,
safe box 2083 px):

| Block | Size | Rendered width | % of canvas | Fits box? | Fits canvas? |
|---|---|---|---|---|---|
| `ഫോക്കസ് ഡിജിറ്റൽസ്` | huge (473.6 px) | 5730 px | **231%** | no | no |
| `ഫോക്കസ് ഡിജിറ്റൽസ്` | large (298.2 px) | 3608 px | **145%** | no | no |
| `50% വരെ കിഴിവ്` | large | 2426 px | 98% | no | yes |
| `ഓണം ആശംസകൾ` | medium | 1897 px | 76% | yes | yes |
| `ഫോൺ: 9847012345` | small | 1385 px | 56% | yes | yes |

"Focus Digitals" in Malayalam — an entirely ordinary shop name — overflows at every size that
reads as a headline. The exported SVG has `text-anchor="middle"` with **no wrapping, no
`textLength`, and no auto-shrink**, so the text runs off both edges of the page.

The overflow warning *does* fire, correctly worded — but it renders **below** the `PNG proof` and
`Export SVG` buttons, so export is reachable before the warning is read.

- [x] Auto-fit: shrink to fit the safe box, or wrap, before falling back to a warning
- [x] Move overflow/safe-zone warnings **above** the export controls
- [x] Consider disabling export while any block overflows the canvas
- [x] Add a golden layout test using real font metrics — this is not catchable by eye

### 0.3 Glossary placeholder debris reaches the client's spreadsheet  **[ran it]**

Source cell: `Roll-up standee with stand, 2x6 ft`, glossary `Standee → സ്റ്റാൻഡി`.
Exported `.xlsx` cell B6 contains:

```
X-px X X X നിലവിലുളള റോൾ x 2x6 x സ്റ്റാൻഡി
```

Mechanism ([glossary.py](backend/features/glossary.py)): `Standee` is masked to `X0X`; the 57M-param
model explodes it into `X-px X X X` and drops the digit; `_PLACEHOLDER_RE` requires `X\s*(\d+)\s*X`
so nothing restores; the term is correctly appended and flagged as lost — but the debris is left in
place by the deliberate "a stray `X..X` might be a client part number" rule in `restore()`.

That rule is right in principle but the two cases are distinguishable: **any `X..X`-shaped token not
present in the source text is debris.** The source is available at restore time.

Compounding it: the row's only warning says *"Much shorter than the English (2 words vs 6) — check
nothing was dropped"* — the wrong diagnosis entirely.

- [x] Pass the source text into `restore()`; strip placeholder-shaped tokens absent from it
- [x] Emit a distinct warning: "the locked term could not be placed — review this cell"
- [x] Never append a lost term to the tail of a cell without saying so in the row warning

### 0.4 An 80-second result is destroyed by clicking another screen  **[ran it]**

Ran a cutout from the UI (79.7 s). Clicked *Excel translator*, clicked back to *Image tools* —
the result was gone, dropzone empty. A toast still read **"Background removed · Ready to
download."** pointing at nothing.

The work is not lost server-side. All 9 jobs from this session were still in `/api/jobs`, all 9
output files still in `tmp/`, and the "lost" result still returned **HTTP 200, 32,293 bytes**.

The frontend only ever calls `api/jobs/{id}` for polling — it never calls `GET /api/jobs`. The
history endpoint exists, is populated, keeps 200 jobs, and is completely unused by the UI.

- [x] Add a recent-jobs panel backed by the existing `GET /api/jobs`
- [x] Keep per-screen state across navigation, or warn before discarding a finished result
- [x] Make the completion toast a link to the result rather than a dead statement

---

## P1 — Money and correctness

### 1.1 The ₹2,000 budget is decorative  **[read it]**

`over_budget` ([ai.py:705](backend/features/ai.py:705)) is consumed in exactly one place: the bar
colour in [ApiKeys.tsx:134](frontend/src/components/ApiKeys.tsx:134). No paid route consults it.
`/ai/artwork`, `/ai/photo-edit` and `/ai/layout-plan` will spend past ₹2,000 indefinitely.

Two compounding gaps:

- **Unbounded prompt fields.** `ArtworkIn.style`, `palette`, `aspect` and every `LayoutPlanIn`
  field except `headline` have no `max_length` ([api/ai.py:319](backend/api/ai.py:319)). Cost is
  recorded as a flat per-call rate regardless of tokens, so a large prompt grows the real bill
  while the meter does not move.
- **"Nothing was charged" is asserted on paths where a response arrived** — a safety refusal, or a
  response containing no image ([ai.py:501](backend/features/ai.py:501)). The app cannot know this.
  Phase 5's gate wants the ledger to reconcile with Google's console to within a few rupees; this
  drifts one way.

- [x] Refuse paid routes when `over_budget`, with an explicit operator override
- [x] Add `max_length` to every field that reaches a prompt
- [x] Replace "Nothing was charged" with "This may still have been billed — check the console"
      on any path where a response was received

### 1.2 A dropped layout block passes silently  **[read it]**

`verify_text_unchanged` ([ai.py:665](backend/features/ai.py:665)) only repairs blocks that came
back. If the model omits the phone-number block, or returns it under a different `id`, nothing
notices — the exact failure its own docstring says it prevents.

- [x] Assert every non-empty input field has a corresponding block; re-insert or fail loudly

### 1.3 Model auto-repair can select a nonsense model  **[read it]**

`resolve_models()` ([ai.py:406](backend/features/ai.py:406)) last-resort matches on
`image_output == role.needs_image`, where `image_output` is `"image" in name`. For the layout role
that means "first alphabetical model without 'image' in its name" — and the `supported_actions`
filter deliberately admits models that report no actions, so an embedding model can win.

- [x] Restrict the last-resort pick to a known-good allowlist, or leave it unset and say so

### 1.4 The batch toggle saves nothing on two of three features  **[ran it]**

| Feature | Instant | Batch |
|---|---|---|
| poster-artwork | ₹11.50 | ₹6.00 |
| photo-edit | ₹4.00 | **₹4.00** |
| poster-layout | ₹0.05 | **₹0.05** |

Only `artwork` defines `batch_rate_paise`. The module docstring says *"Batch is the default. Half
price for a few minutes' wait."* For photo editing and layout that is a wait for nothing.

- [x] Hide or disable the batch toggle where no discount exists

### 1.5 The exported SVG references a font it does not embed  **[ran it]**

`/api/posters/svg` emits `font-family="Noto Sans Malayalam, sans-serif"` with **no `@font-face` and
no base64 payload** (grep count: 0). The app ships the woff2 for its own UI, so the browser preview
and PNG proof are fine — but the SVG deliverable renders correctly only on a machine that already
has the font installed. Elsewhere it falls back to `sans-serif`: tofu or wrong metrics.

This sits directly under Phase 4's open gate item about CorelDRAW.

- [x] Embed the subset font in the exported SVG, or state the install requirement at export time
- [ ] Fold this into the Phase 1 / Phase 4 CorelDRAW check rather than testing it twice
      *(needs the real CorelDRAW check — see 4.1)*

### 1.6 Single-word mistranslations are structurally uncatchable  **[ran it]**

Full run over a realistic 18-string price list. The **only** detector that fired was a word-count
heuristic, on 4 rows. Everything below was returned with `needs_attention: false`:

| Cell | English | Malayalam back-translation |
|---|---|---|
| A6 | Standee | **"Saint Kitts and Nevis"** |
| A5 | Brochure | "breaking" |
| A4 | Visiting Card | "card is visiting" |
| A1 | Focus Digitals | "Digital Digitals" (the shop's own name) |
| B4 | 300 gsm matte | "300 gsm mathematics" |
| B3 | 6x4 **feet** | 6x4 **മീറ്റ** (metre) — a unit error on a price list |

A word-count heuristic cannot fire on a one-word cell, and one-word cells are exactly the product
names. ROADMAP Phase 3 claims the grid flags *"lost terms, output still in English, and output much
shorter"* — only the third fired here.

- [x] Flag any cell where the output is a proper noun or a place name
- [x] Flag numeric/unit tokens that changed between source and output (`feet` → `മീറ്റ`)
- [x] Flag short cells with low confidence rather than assuming short means safe
- [x] Prompt for a glossary entry on any 1–2 word cell that is not already covered

### 1.7 No glossary is applied unless the operator picks a client every time  **[read it]**

[ExcelTranslator.tsx:40](frontend/src/features/ExcelTranslator.tsx:40) defaults `clientId` to
`"none"`, and the picker only appears after a file is dropped. The safe default is right, but there
is no persistence of the last-used client and no warning that approved terms are being skipped.

Given the glossary is the stated mitigation for a weak model, translating with it off is the most
likely operator error — and it produces exactly the output in 1.6. Per-client isolation itself is
sound: verified two clients with the same source term keep separate targets.

- [x] Remember the last client per session
- [x] Warn plainly when translating with no glossary selected

---

## P2 — Robustness

### 2.1 The decompression-bomb guard is 11× Pillow's default  **[ran it]**

[images.py:47](backend/features/images.py:47) sets `Image.MAX_IMAGE_PIXELS = 1_000_000_000`. There
is no pixel-dimension cap anywhere. A 546 KB PNG at 24000×24000 (576 Mpx) was **accepted** by
`/api/images/inspect` — and the response offered a 4× upscale to **96000×96000 (9.2 Gpx)**, quoting
62 minutes and 62,500 tiles, with no indication it is impossible.

Bombs are small, so the 1 GB byte cap does not help. At the 1 Gpx ceiling an RGBA decode is ~4 GB on
a 12 GB machine. `_read_upload` also holds the whole file in memory while the decoded image is
captured in the queued job's closure, so a queue of large jobs stacks decoded images.

[SECURITY.md](SECURITY.md) §4 requires capping pixel dimensions. **[doc gap]**

- [x] Cap source pixels to something a print shop actually produces, with a clear refusal message
- [x] Hide or refuse scale options whose output exceeds `MAX_OUTPUT_PIXELS`
- [x] Stream uploads to disk rather than holding bytes plus decoded image in RAM

### 2.2 Cancel appears dead for 11 seconds  **[ran it]**

Cancelled a running cutout: **11 seconds** from the POST to the job reporting `cancelled`, because
cancellation is cooperative and the BiRefNet call is one uninterruptible block. Throughout, the UI
kept showing *"Removing background…"*.

`Job` has a `cancelling` property — but `as_dict()` never exposes it, so the UI **cannot** show a
"Cancelling…" state. On the shop PC's CPU that block is minutes, not seconds.

- [x] Expose `cancelling` in `as_dict()` and render "Cancelling…" immediately

### 2.3 Progress sits at 35% for most of a cutout  **[ran it]**

Measured, GPU: 5% at 0.2 s → 35% at 7.0 s → **35% for the next 54 seconds** → done at 61.2 s. The
step text stays *"Removing background…"* the whole time. Confirmed in the UI: `35% · 39s`.

The elapsed counter does tick, which is what keeps it from reading as a hard freeze, and the upscale
path is genuinely good (*"Enlarging 12 tiles — about 1s on GPU (CoreML)"*). But
[DESIGN.md](DESIGN.md) asks for real progress and a named step, and Phase 2's gate marks this
`[x]`. On an i3 the frozen stretch is minutes.

- [ ] Report progress inside the background-removal call, or split it into named sub-steps
      *(not possible without patching rembg — the alternative below was taken instead)*
- [x] If neither is possible, say so: "this step reports no progress — expect about N minutes"

### 2.4 Client artwork accumulates for the whole session  **[read it, confirmed by ran it]**

[SECURITY.md](SECURITY.md) §5 says *"Temp files are cleaned up after each job."* They are not —
[main.py:48](backend/main.py:48) wipes `WORK_DIR` at startup and clean shutdown only. After this
session `tmp/` held 9 files including client photos and two client spreadsheets. A power cut leaves
them there until the next launch. **[doc gap]**

- [x] Delete a job's directory once its result has been downloaded, or after a short TTL
- [x] Correct SECURITY.md §5 to match whatever is actually implemented

### 2.5 No CSRF or Host-header defence  **[read it]**

[SECURITY.md](SECURITY.md) §1 treats the `127.0.0.1` bind as the whole answer. There is no Host
check and no CSRF token. A multipart POST is a CORS *simple* request, so any page open in the
operator's browser can fire `/api/ai/photo-edit` or `/api/images/upscale` at `127.0.0.1:8000`. It
cannot read the reply, but the spend and the CPU burn happen. DNS rebinding is open for the same
reason. **[doc gap]**

- [x] Validate the `Host` header against `127.0.0.1`/`localhost`
- [x] Require a same-origin header or a token on every state-changing route
- [x] Update §1 to say what actually holds the line

### 2.6 Base install cannot start the app; numpy is undeclared  **[read it]**

`pyproject.toml` says extras exist *"so the font converter never pulls down 3.5 GB."* That does not
hold: [main.py:22](backend/main.py:22) unconditionally imports `api.images` → `features.images` →
`numpy` + `PIL`; `api.excel` → `openpyxl`; `db` → [crypto.py:25](backend/crypto.py:25) →
`cryptography`.

**`numpy` is never declared anywhere in `pyproject.toml`** — it is a direct import that arrives only
transitively via `rembg`/`onnxruntime`. On the 112 GB SSD this is exactly the cost the extras were
meant to avoid.

- [x] Declare `numpy` explicitly
- [x] Either import the feature routers lazily, or drop the per-phase install claim and say the
      full install is required

### 2.7 A transient keyring failure orphans the stored API key  **[read it]**

`encryption_key()` ([crypto.py:77](backend/crypto.py:77)) treats "keyring read returned nothing" as
"no key exists" and mints a fresh one. The stored ciphertext then cannot be decrypted. Recoverable
by re-pasting, but it fails quietly toward data loss and there is no memory of the keyring having
worked before.

- [x] Record which backend holds the key; refuse to mint a replacement when that backend is
      expected but unreachable

---

## P3 — Polish (each one is visible to the operator)

| # | Finding | Where | How found |
|---|---|---|---|
| 3.1 | `2×1.33333 feet` in the verdict prose while the tile beside it says `2×1.33 feet` — same number, two formats, one screen | [printsize.py](backend/features/printsize.py) | ran it |
| 3.2 | `0.0 MB` shown for a 32 KB result — reads as an empty file | [ImageTools.tsx](frontend/src/features/ImageTools.tsx) | ran it |
| 3.3 | Transparent cutout previewed on opaque grey — no checkerboard, so "did the background come off?" is unanswerable | [ImageTools.tsx](frontend/src/features/ImageTools.tsx) | ran it |
| 3.4 | Emoji pass through the font converter unchanged and unflagged; the helper text *"This looks like gibberish here — that is correct"* trains the operator to ignore it. WhatsApp text is the primary input | [fonts.py](backend/features/fonts.py) | ran it |
| 3.5 | Toasts persist across 5 navigations and cover live UI (obscured the "2 The look" header and the Settings models table) | [App.tsx](frontend/src/App.tsx) | ran it |
| 3.6 | Stale red verdict card still shown above a finished cutout — the assessment refers to the original upload and is never re-run or dismissed | [ImageTools.tsx](frontend/src/features/ImageTools.tsx) | ran it |
| 3.7 | `crop_fraction: 0.8` computed but never mentioned in the prose — the operator is not told 80% of the picture would be cropped | [printsize.py](backend/features/printsize.py) | ran it |
| 3.8 | Safe-zone check tests box position only, so a block whose text overflows the trim reports `outside_safe_zone: false` while overflow reports a violation. Two checks that do not talk | [posters.py](backend/features/posters.py) | ran it |
| 3.9 | Dropzone lists "PNG, JPEG, TIFF, WEBP"; backend `ALLOWED_FORMATS` also accepts BMP | [ImageTools.tsx](frontend/src/features/ImageTools.tsx) | ran it |
| 3.10 | Poster progress bar has `role="progressbar"` with no `aria-valuenow` (ApiKeys.tsx sets it correctly); no `aria-current` on the active nav item | [PosterDesigner.tsx](frontend/src/features/PosterDesigner.tsx) | ran it |
| 3.11 | Cutout PNG DPI reads `299.9994`, not `300` — PNG stores px/metre; harmless but visible in CorelDRAW | [images.py](backend/features/images.py) | ran it |
| 3.12 | `/api/clients` returns a bare list and takes a bare list for glossary PUT, while every other route uses a wrapped envelope | [api/excel.py](backend/api/excel.py) | ran it |
| 3.13 | `auto` layout returns `role: "free"` for every block, discarding the roles `split-copy` just inferred | [posters.py](backend/features/posters.py) | ran it |
| 3.14 | `put_preferences(updates: dict[str, str])` is hand-rolled dict parsing — CLAUDE.md and SECURITY.md §4 both require Pydantic bodies. `set_preferences` does reject unknown keys, so it is a convention break not a hole | [main.py:117](backend/main.py:117) | read it |
| 3.15 | `db.py` docstring still says *"Phase 2 uses the `preferences` table only"* — ten tables later | [db.py](backend/db.py) | read it |
| 3.16 | `api/ai.py` reaches into `ai._friendly` twice across a module boundary | [api/ai.py:84](backend/api/ai.py:84) | read it |
| 3.17 | `_friendly` fallback echoes 200 chars of raw SDK exception to the UI — the one path where a credential could surface, against SECURITY.md §2 | [ai.py:310](backend/features/ai.py:310) | read it |
| 3.18 | A stray `.agent_harnesses.json` from another tool has landed in `models/hf/` — harmless (`HF_HOME` points there) but that directory is not only weights | `models/hf/` | ran it |

---

## P4 — Process

### 4.1 Nothing has passed a gate, and Phase 5 has never made a real call  **[ran it]**

CLAUDE.md rule 6 forbids building Phase N+1 before Phase N passes its gate. All five phases are
built; **not one has closed its gate**. Every remaining item needs real hardware or real output —
the CorelDRAW paste, the shop-PC run, a real test print, a real client sheet, one live API call.

The shipped self-check (`check-ai.command` → [diagnose.py](backend/diagnose.py)) confirms the state
exactly:

```
A. THE MACHINERY   (Google stubbed out — no internet, no money)
  PASS  artwork / layout / text guard / failure is free / ledger
B. THE KEY         (one free call — lists models, generates nothing)
  FAIL  Google will not accept this key.
```

The configured key (`…UwhQ`) is rejected, `last_tested_at` is null, and the ledger holds 3 failed
runs and zero successful ones. Phase 5's response shapes have never been proven against the live
API.

- [ ] Replace the Gemini key and run the self-check until part B passes
- [ ] Then spend a few paise on one real artwork and one real layout call
- [ ] Close the gates in order, or amend rule 6 to say what is actually being done

### 4.2 Test suite is green but does not cover the P0s  **[ran it]**

`458 passed, 1 skipped` in 73 s. None of 0.2, 0.3, 0.4 or 1.6 is covered — all four are output
correctness bugs in the class CLAUDE.md calls *"the logic that would silently produce wrong output."*

- [x] Golden test: Malayalam layout widths against real font metrics (catches 0.2)
- [x] Golden test: glossary mask/restore under a mangled placeholder (catches 0.3)
- [x] Golden test: the 1.6 table as expected-flagged rows

---

## What is genuinely good

Worth recording so it does not get refactored away:

- **`diagnose.py` is the best thing in the repo.** It separates "the machinery is broken" from "the
  key is broken", stubs Google out for part A, writes to a throwaway DB so it can never pollute the
  real ledger, and ends with a sentence a non-programmer can act on. That last line —
  *"Nothing on this computer needs reinstalling"* — is the whole design brief in eight words.
- **Excel formatting survival is exact.** Merges, fonts, fills, colours, number formats
  (`"₹"#,##0.00`), column widths and formulas-as-formulas all round-tripped byte-identical.
  Formulas correctly skipped (6) and numbers left alone (10). Phase 3's formatting gate genuinely
  passes.
- **Per-client glossary isolation works.** Two clients, same source term, separate targets — verified.
- **The print-size calculator's verdicts are excellent** — plain language, a `best_use` table
  instead of a bare refusal, and honest arithmetic.
- **`/ai/artwork/prompt` is free and shows the exact bytes that would be sent.** Nothing hidden
  before money is spent.
- **Frontend conventions are clean:** `strict: true`, zero `any`, zero `@ts-ignore`, every `fetch`
  behind [api.ts](frontend/src/lib/api.ts), labels properly associated, live regions present.
- **The single-worker queue does what ARCHITECTURE.md claims.** Three concurrent cutouts serialised
  correctly; one ran, two queued.
- **The upscale step text is the model for the rest of the app:** *"Enlarging 12 tiles — about 1s on
  GPU (CoreML)"*, plus the note that 2× and 4× cost the same because the model always runs at 4×.
- **Settings is honest about licences** — the OPUS-MT note admits the model *"drops words and picks
  wrong senses"*, and the NLLB-200 exclusion explains itself in one line.

---

## Suggested order

1. **0.1** — one command, unblocks everything else
2. **0.2 + 1.5** — the poster is the flagship and both land in the same CorelDRAW check
3. **0.3 + 1.6 + 1.7** — Phase 3 output correctness, all in the translate path
4. **0.4 + 2.2 + 2.3** — job UX; 0.4 is mostly wiring up an endpoint that already exists
5. **1.1–1.4** — before the key is replaced, so the first real spend is capped
6. **4.1** — replace the key, then close gates in phase order
7. **2.x, 3.x** — as they are touched

Item 0.1 is the only one that is safe to do without a decision. Everything else changes behaviour
the operator will notice; **2.6** in particular needs a call on whether the per-phase install claim
is kept or dropped.
