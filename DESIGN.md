# Design — Focus Toolkit

**Status:** Draft for review · **Last updated:** 2026-08-22

The operator uses this tool fifty times a day between other work. It should be fast, quiet, and
unambiguous. Not impressive — **calm**.

---

## Principles

1. **The answer is the interface.** Converted text, the DPI verdict, the cutout — the result is the
   largest thing on screen. Controls are secondary.
2. **Never lie, never hedge.** A wrong "looks fine" costs a reprint. Say ✅, ⚠️, or ❌ plainly.
3. **One screen, one job.** Five features, five tabs. No nesting, no wizards.
4. **Slow is fine; unclear is not.** A 90-second upscale is acceptable if the operator can see it
   working and knows how long is left.
5. **Nothing is lost.** No destructive action without confirmation. No edit that discards a working
   prompt or glossary term.

## Component source

**shadcn/ui is the only source of UI components.** Copied into `frontend/src/components/ui/` from
git. Do not write a custom modal, toast, dropdown, tooltip, or dialog — shadcn has all of them,
already accessible and keyboard-complete.

If a component does not exist in shadcn, ask before building it.

## Tokens

Defined once in `frontend/src/index.css` as CSS custom properties. **Never hardcode a colour or a
spacing value in a component.**

### Colour

Neutral slate base so client artwork is judged against a non-competing background — the operator is
assessing colour accuracy on screen, and a tinted UI would interfere.

```css
:root {
  --bg:            #fbfbfd;   /* page */
  --surface:       #ffffff;   /* cards, panels */
  --surface-sunk:  #f4f4f7;   /* wells, canvas backdrop */
  --border:        #e3e3e9;
  --text:          #16161d;
  --text-muted:    #6b6b7b;

  --accent:        #3b5bdb;   /* primary actions, focus */
  --accent-hover:  #2f4bc4;
  --accent-weak:   #eef1fd;

  --ok:            #157f3d;   /* print size fine */
  --warn:          #a16207;   /* borderline */
  --danger:        #b42318;   /* will print badly / destructive */
}
```

Dark mode inverts surfaces and lifts semantic colours for contrast. **Image preview areas keep a
neutral mid-grey backdrop in both themes** — never pure black or white, which distorts perceived
contrast in client photos.

### Spacing, radius, type

```css
--space: 4px;              /* all spacing is a multiple: 4 8 12 16 24 32 48 */
--radius:    8px;          /* controls, inputs */
--radius-lg: 12px;         /* cards, dialogs */
```

| Role | Size / weight |
|---|---|
| Page title | 24px / 600 |
| Section | 18px / 600 |
| Body | 15px / 400 |
| Label, meta | 13px / 500 |
| Verdict (DPI) | 28px / 700 |
| Mono (converted text, codes) | 14px `ui-monospace` |

## Malayalam rendering

**The UI ships its own Unicode Malayalam font** (Manjari or Noto Sans Malayalam, subset, self-hosted
in `frontend/src/assets/fonts/`). It must never fall back to whatever the machine happens to have —
a missing glyph in the preview would make correct output look broken and destroy trust in Phase 1.

- Converted **ML-TTKarthika output is ASCII** and displays in mono. It will look like gibberish in
  the browser — that is correct and expected. Label it clearly so it never reads as an error.
- Show **Unicode input and ASCII output side by side**, so the operator can see the source is intact.
- Malayalam text needs more line height than Latin: `line-height: 1.7` minimum.

## Motion

Budget: **150–200 ms**, `ease-out`. Motion confirms a change; it never entertains.

- Animate: panel transitions, toast entry, progress, hover/focus.
- Never animate: layout on load, anything blocking input, anything looping.
- Honour `prefers-reduced-motion: reduce` — drop to opacity-only or none.

## Long-running jobs

Phases 2, 3 and 5 have jobs that run 15 s to 2 min. These rules are not optional:

- **Real progress, not a spinner.** Percentage or step count ("image 3 of 12").
- Name the current step: *"Loading model…"* → *"Removing background…"* → *"Writing 300 DPI PNG…"*
- Show elapsed time past 10 s. On the shop PC, a silent 90-second wait reads as a crash.
- Every job is cancellable.
- Disable the submit button while running — the memory rule forbids two concurrent model jobs.

## Feature-specific rules

**Font converter (Phase 1)** — The paste box is focused on page load. Convert on `Cmd/Ctrl+Enter`.
Auto-copy to clipboard on success with a clear toast. The operator should be able to paste, convert,
and be back in CorelDRAW without touching the mouse.

**Image tools (Phase 2)** — The **DPI verdict is the loudest element on the screen** — 28px, semantic
colour, plain words: *"✅ Good for 6×4 ft flex"* / *"❌ Not enough for A3 brochure"*. State the
effective DPI beneath it, and always show the input dimensions so the operator can sanity-check.

**Excel translator (Phase 3)** — Side-by-side grid, source left, translation right, editable. Rows
touched by a glossary term are marked. Nothing is written to the file until the operator accepts.

**Posters (Phase 4)** — Text objects stay selectable and editable at all times. Print safe zone
drawn as a dashed inset; text dragged inside it turns red, because trimming will cut it off.

## Accessibility floor

**WCAG 2.1 AA minimum.** Not aspirational — the operator works long days, often in a bright shop.

- Contrast: 4.5:1 body text, 3:1 large text and UI boundaries.
- Every control reachable and operable by keyboard, in a sensible tab order.
- Visible focus ring on every interactive element. Never `outline: none` without a replacement.
- Colour is never the only signal — the DPI verdict pairs colour with an icon *and* words.
- Real `<label>` on every input. Icon-only buttons carry `aria-label`.
- Respect `prefers-reduced-motion` and `prefers-color-scheme`.

## Layout

Fixed left sidebar for the five features plus Settings. Content area max-width 1200px, centred.
Designed for 1366×768 (the shop PC) upward — that is the real constraint, not mobile.
