# QC — check every tool, in and out

**Status:** Living checklist · created 2026-08-31
Companion docs: [CLAUDE](CLAUDE.md) · [DESIGN](DESIGN.md) · [ROADMAP](ROADMAP.md) · [NEXT](NEXT.md)

---

## What this is

Five features, one settings screen, and no CI. `scripts/qc.py` runs everything a machine
can check; this file is everything it cannot. A green suite is **not** a pass — NEXT.md's
own history says so: the bug that printed the styling hint "festive" onto a client's
poster was caught by *running* `check-ai`, not by the test suite.

## The loop

```bash
uv run python scripts/qc.py
```

1. Any **FAIL**: fix it and run again. Do not open a browser over a red gate.
2. Any **SKIP**: read it. A skipped gate has proved nothing, and the line says what to
   install to un-skip it.
3. Start the app, and walk this file top to bottom in **light theme at 1366×768** — the
   shop PC's screen, which is the smallest one this has to work on.
4. Repeat sections **G** and **H**: dark theme, then keyboard only.
5. Log every defect in the run log below **as you hit it**. Do not fix mid-walk — a fix
   invalidates the rest of the pass and you lose your place.
6. Triage the log into NEXT.md's numbered P0–P4 sections, keeping its `[ran it]` /
   `[read it]` / `[doc gap]` markers. **NEXT.md is the durable ledger**; this file's log
   is one run's raw notes and is replaced each time.
7. Fix, then go to 1. The walk is clean only when a full pass adds nothing new.
8. Before a release: `uv run python scripts/qc.py --strict`, which also runs
   `backend.diagnose` and fails on any skip.

**Not every finding reproduces.** NEXT.md 3.10 and 3.13 did not. Verify against the
running app before promoting one, and record the non-reproductions too.

## Severity, from NEXT.md

**P0** costs a reprint or a client's trust · **P1** money or correctness · **P2**
robustness · **P3** operator-visible polish · **P4** process.

## Run record

| | |
|---|---|
| Date | |
| Commit | |
| Machine / OS | |
| Browser | |
| Extras installed | `uv sync --extra …` |
| Theme walked | light / dark |
| Viewport | |

---

## A · Whole app

- [ ] Sidebar lists five features plus Settings; the active one has `aria-current="page"`.
- [ ] Theme button cycles Light → Dark → System and the choice survives a reload.
- [ ] Start a long job, navigate away and back — **the result is still there.** Screens
      stay mounted on purpose (NEXT.md 0.4); an 80-second cutout was destroyed by this once.
- [ ] A toast raised on one screen does not follow you to another.
- [ ] Content stays within 1200px and nothing scrolls sideways at 1366×768.
- [ ] Stop the backend, click anything — the error names the server, not a stack trace.

## B · Malayalam converter

- [ ] Paste box has focus on load.
- [ ] ⌘/Ctrl+Enter converts **and** copies, with a toast.
- [ ] `Copy for CorelDRAW` copies; its tooltip appears on hover and on focus.
- [ ] `Switch to ASCII → Malayalam` reverses direction, and back again.
- [ ] `Clear` empties both panes.
- [ ] `Try …` sample button appears only while the box is empty.
- [ ] Output is labelled so ASCII gibberish never reads as an error.
- [ ] Deny clipboard permission → the "Press ⌘C / Ctrl+C" fallback appears.

## C · Image tools

- [ ] Drop zone accepts a **click** and a **drag-and-drop**.
- [ ] A non-image file gives a `role="alert"` message, not a crash.
- [ ] Facts line shows width×height, megapixels, format, transparency.
- [ ] `What is it for?` select changes the target size.
- [ ] Width / Height / Units drive the DPI verdict; the verdict is the loudest thing on
      screen and uses an icon **and** words, never colour alone.
- [ ] `Enlarge by` radio group is operable with **arrow keys**.
- [ ] Start a cutout: progress names a step and shows elapsed time past 10s.
- [ ] **Cancel mid-job** → says "Cancelling…", and the partial result is not presented as
      finished.
- [ ] Cutout preview sits on the checkerboard, so transparency reads as transparency.
- [ ] `Download` produces the file.
- [ ] AI photo edit with **no API key set** → says so plainly and offers no paid button.

## D · Excel translator

- [ ] Drop zone: click and drag-and-drop both accept a `.xlsx`.
- [ ] Facts line: sheets, cells to translate, distinct strings, formulas left alone.
- [ ] **Change the client dropdown** → the sheet is re-inspected and the quote changes.
- [ ] With corrections stored, the green line reads "N of these **is/are** already
      known" and the grammar matches the number. With a sheet of product names it also
      names the word library as one of the sources.
- [ ] `No glossary` → the warning says client terms are off **and** that remembered
      corrections still apply.
- [ ] `Malayalam check` → the Claude option is disabled with no Anthropic key.
- [ ] Choosing the check shows the cell count, the ₹ estimate and the request count
      **before** the run.
- [ ] Over budget → the override checkbox appears and the Translate button is disabled
      until it is ticked.
- [ ] `Translate`, then **cancel mid-check** → the job still finishes, the grid is usable,
      and a warning says where it stopped. *(This is the ADR-029 fix; getting it wrong
      loses the whole translation.)*
- [ ] Rows needing attention are tinted, and `must_fix` is redder than `needs_attention`.
- [ ] A row filled from memory carries a **from memory** badge and **no warnings**.
- [ ] A glossary-only row carries **from glossary** and was never sent to Claude.
- [ ] A row like `Standee` carries **from word library**, has no warnings, and is
      correct Malayalam — not the country. *(ADR-032; this is the whole feature.)*
- [ ] The grid **never** says "add a glossary entry" or "nothing here can check it".
      Those nags are gone; a price list should now be mostly quiet.
- [ ] A genuinely wrong row still shouts: plant a wrong price or a `feet`→`metre` and
      confirm it is still flagged red.
- [ ] `Show only these` / `Show all rows` toggles.
- [ ] Edit a Malayalam cell → the advisory count beside `Remember my corrections` moves.
- [ ] `Export .xlsx` downloads, and the toast says how many corrections were remembered.
- [ ] Re-run the **same sheet**: the edited cell comes back filled, badged from memory.
- [ ] Untick `Remember my corrections`, export → nothing new is remembered.
- [ ] `Choose another` resets cleanly.

### D1 · Name columns *(ADR-033)*

- [ ] Load a member list → the **Which columns are names?** panel appears above the
      Translate button, with `Name`, `House name`, `Place` and the like already ticked
      and a product column left clear.
- [ ] Each column shows its heading, its letter, how many cells, and a few real values.
- [ ] Untick one and re-translate → those cells are translated again, not spelled out.
- [ ] Translate with the columns ticked → name rows carry a **written by sound** badge,
      no warnings, and were never sent to Claude.
- [ ] **The heading row is translated, not spelled.** `Name` must read പേര്, not നമെ.
      *(This is the one that looks right in a spot check and is wrong at the top of
      every column the client reads first.)*
- [ ] Read ten name rows against the English. They should be recognisable Malayalam.
      Expect vowel length to be off — `Menon` comes out മെനൊൻ, not മേനോൻ — because
      English spelling does not mark it. Fix one by hand and export.
- [ ] Re-run the same sheet: the corrected name comes back from memory, badged
      **from memory**, not re-spelled by the rule.

### D2 · Word library *(ADR-032)*

- [ ] **Read `data/dictionary/trade-en-ml.tsv` once, end to end.** ~90 lines. It is the
      shop's own vocabulary and it overrides the general dictionary, so a wrong line
      here prints wrong on every sheet. Anything wrong: fix the file, or override it in
      the panel.
- [ ] `Open` the panel → it says how many words are bundled (~59,000).
- [ ] Search `standee` → one row, badged **Print trade**.
- [ ] Search `coconut oil` → badged **Olam**, with other meanings listed beside it.
- [ ] Change a Malayalam word and `Save` → the badge becomes **Yours** and the line
      below shows what it replaced.
- [ ] Re-translate a sheet using that word → the new wording is used, still free and
      still offline.
- [ ] `Download as Excel` opens in Excel with **readable Malayalam**, not boxes.
- [ ] Load that file straight back unedited → it reports *nothing was changed* and
      saves nothing. *(If it reports thousands of rows added, the diff is broken and
      the library is now frozen at today's version.)*
- [ ] Edit two rows in Excel, load it back → exactly two corrections reported.
- [ ] Load a file with the columns **the wrong way round** → refused with a plain
      message, and nothing is saved.

## E · Poster designer *(ADR-034)*

**Read this section before running it.** The app no longer sets the poster's
type. Gemini draws every word into the picture, so nothing here can check the
spelling and Malayalam will often be wrong. The check below is the only one
there is, and it is yours.

- [ ] With `data/poster_prompts/` empty of designs, the screen says where to put
      them and shows the folder path. It does not look broken.
- [ ] Drop a design file in → it appears in the list **without restarting the server**.
- [ ] A design file with a typo in its header is skipped and the others still list.
- [ ] Paste `main: … / h1: … / h2: …` → the three lines are read back correctly
      **before** anything is spent. Check `h1` and `h2` are not swapped.
- [ ] Paste an untagged first line → it is taken as `main`.
- [ ] `Leaves this computer` shows on this screen. *(Every other screen in the app
      is offline; this one never is, and that contrast is the point.)*
- [ ] Press `Make the poster` with no reference picture → a poster comes back, and
      the cost shown includes **both** calls, not just the picture.
- [ ] Add a reference picture and generate again → it costs less, because the
      visual-idea call is skipped.
- [ ] **Read every word on the poster against the copy shown beside it.** Numbers,
      the phone number, and any Malayalam. This is the step that replaces the
      guarantee the app used to give for free (ADR-030 is superseded).
- [ ] Type a change — "make the background darker" — and press `Change`. The poster
      you were looking at is adjusted; it is not replaced by a fresh one.
- [ ] Generate twice more, then click an earlier attempt → it comes back, and
      nothing is charged for going back.
- [ ] Turn the key off in Settings, press generate → a plain reason, **your copy
      still in the box**, and no charge recorded.
- [ ] `Download` saves the poster. Note its pixel size; it is whatever Google
      returned, and there is no upscaling step.

## F · Settings

- [ ] Print defaults: DPI, colour profile, units each save immediately; a failure toasts.
- [ ] Hardware and Models tables are read-only and state the licence.
- [ ] Glossary: add a client, add a term, remove a term.
- [ ] **Remembered corrections:** scope select, server-side search, `Load from Excel`,
      `Download as Excel`, inline Malayalam edit saving on blur and on Enter.
- [ ] Load a **swapped** corrections file (Malayalam in column A) → refused, with the fix
      named, and nothing is stored.
- [ ] Download then re-load the same file → no duplicates.
- [ ] `Remove` a correction needs **two clicks**.
- [ ] Translation engine table; NLLB-200's exclusion is explained.
- [ ] `Malayalam check model`: type a name, it saves; `Use the default` clears it.
- [ ] API keys: a saved key shows only a 4-char hint; `Test` on the Gemini key reports
      OK / invalid / no internet; the spend meter says it is an estimate.
- [ ] AI models: `Refresh from Google` lists models; a retired name is badged.
- [ ] Prompt library: it **opens on a real scope**, not an empty panel. Edit, `Save`,
      `Use this one`, `Restore default`.
- [ ] The `poster-concept` scope is listed and restorable. *(Poster designs are files
      now, not a settings screen — see `data/poster_prompts/`.)*

## G · Both themes

- [ ] Every screen in light and in dark.
- [ ] `--ok`, `--warn` and `--destructive` all clear 3:1 against their backgrounds.
- [ ] The checkerboard reads as transparency in both.
- [ ] The two native `<input type="color">` widgets are unthemed — acceptable, noted here
      so it is not re-reported.

## H · Keyboard only

Unplug the mouse.

- [ ] Every control reachable in a sensible tab order, with a visible focus ring.
- [ ] Poster blocks are reachable and nudgeable; the stage announces itself.
- [ ] `Enlarge by` takes arrow keys.
- [ ] Every Select opens, moves and closes from the keyboard.
- [ ] Icon-only buttons announce their `aria-label`.
- [ ] No control is reachable only by hover.

---

## Run log

Replaced each pass. Promote anything real into NEXT.md before clearing.

| # | Section | Control | What happened | Expected | Severity |
|---|---------|---------|---------------|----------|----------|
| | | | | | |
