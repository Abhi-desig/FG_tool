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
      approved" and the grammar matches the number.
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
- [ ] `Show only these` / `Show all rows` toggles.
- [ ] Edit a Malayalam cell → the advisory count beside `Remember my corrections` moves.
- [ ] `Export .xlsx` downloads, and the toast says how many corrections were remembered.
- [ ] Re-run the **same sheet**: the edited cell comes back filled, badged from memory.
- [ ] Untick `Remember my corrections`, export → nothing new is remembered.
- [ ] `Choose another` resets cleanly.

## E · Poster designer

- [ ] Paste a WhatsApp message → it auto-splits on paste; `Sort it out` also works.
- [ ] Lines it could not place are announced as extra lines, never dropped.
- [ ] The four role fields are editable and drive the canvas.
- [ ] **AI wording:** brief box, `Leaves this computer` notice naming the phone
      exclusion, live ₹ quote, and `See exactly what will be sent` — all free.
- [ ] Press `Write the words` → three alternatives, English beside Malayalam.
- [ ] `Use this` on one field changes only that field; `Use English` / `Use Malayalam`
      change the set.
- [ ] An alternative that invented a figure is **absent**, and a warning says which figure.
- [ ] The phone number on the poster is the one you typed, character for character.
- [ ] Picking a look repaints the words **and** the background.
- [ ] Type a phone number *after* picking a look → it takes that look, not white.
- [ ] **Click a line on the poster while step 1 is open** → the inspector opens below the
      steps and the accordion does not jump. *(The reported bug.)*
- [ ] Drag a block; arrow keys nudge; Shift+arrow nudges further.
- [ ] Focus ring and selection ring are distinguishable.
- [ ] `Fine typography`: exact size % shows a mm equivalent; letter spacing and line
      spacing change the preview; UPPERCASE changes Latin and leaves Malayalam alone.
- [ ] Make a line overflow the trim → export is disabled, the warning is **above** the
      buttons, and `Make it fit` clears it with an Undo toast.
- [ ] A line too long even at the smallest size says so honestly rather than silently
      failing.
- [ ] `Remove` a line → a toast offers **Undo**, and Undo restores the words.
- [ ] `PNG proof` and `Export SVG` both download.
- [ ] Open the SVG: the text is real `<text>`, selectable, not paths.
- [ ] Set a block to ML-TTKarthika → the SVG carries ASCII codes and the PNG still shows
      readable Malayalam (the proof deliberately ignores export mode).

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
- [ ] Poster styles: pick a look → the **preview card** repaints; hex field accepts a
      pasted brand colour; the contrast readout gives a number **and** words.
- [ ] Prompt library: edit, `Save`, `Use this one`, `Restore default`.
- [ ] The `poster-copy` scope is listed and restorable.

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
