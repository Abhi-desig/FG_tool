name: (this file is the instructions, not a style)
description: Delete or ignore — `designs()` skips it, having no `---` line.

# Poster styles

One file per style. Nine ship with the app. Drop yours in beside them and they
count immediately — no restart, no database, no screen to fill in.

**The operator never picks one.** They paste the copy; the model is shown every
style in this folder and chooses the one that fits the words, in the same call
that draws the poster (ADR-037). So these files are not just looks — they are
what the choice is made from, and `when:` and `tone:` are the fields it is made
on.

## The shape of a file

    name: Offer block
    description: One figure, enormous. Everything else gets out of its way.
    aspect: 4:5
    when: 2-4 blocks, and the copy's point is a price or a percentage.
    tone: A short hook, then the figure, then terms in small print.
    ---
    <the style spec>

Everything above the `---` is the header; everything below is the spec. A file
with no `---` line is skipped and says so in the log.

| Field | What it does |
|---|---|
| `name` | What the style is called. Falls back to the filename |
| `description` | One line, for the dev override list |
| `aspect` | The poster's shape. See below |
| `when` | **Read by the model to choose.** The content load and occasion this style suits |
| `tone` | **Read by the model to choose.** The shape of copy it can carry |

Files are ordered by filename, which is why the nine are numbered. `01-` sorts
first; a title would not.

## A spec describes the look only — never the words

This is the one rule that matters. **Do not put `{{main}}`, `{{h1}}` or
`{{h2}}` in a spec.** Nothing substitutes them any more, so they would reach
Google as those literal characters and print on the poster. The app writes the
copy itself, once, in quotes, before your spec — because in auto mode all nine
specs go in one prompt, and specs carrying the copy would repeat the operator's
words nine times over.

A leftover placeholder is logged loudly and the style still loads. The symptom
if you miss it is a poster with `{{main}}` lettered onto it.

Write the spec as five things, which is how the nine are written:

1. **Layout**, geometry first, with the reserved zone as a percentage.
2. **Scene** — a named material with its finish, a stated light direction, a
   camera position and distance, one named specular highlight, one small
   authentic imperfection.
3. **Type** — one alignment axis, and the relative size of each level.
4. **Shapes** — at most one highlight shape plus one container, declared
   numerically. If none: say so explicitly.
5. **Palette** — as a count, in the form `Four colours only — …`, then name them.

## `when:` and `tone:` decide whether your style ever gets used

A style with a vague `when` will be chosen at random or never. Make it
discriminating, and say what it is *not* for:

    when: 1-2 blocks of copy. A premium or restrained occasion — an opening, a
          condolence, a service business. Never for a discount or a festival.

`tone` is the one people skip and it does real work. A style built for seven
short lines fails on one long line, and nothing about the look says so:

    tone: Long. Up to seven short lines. Every line must be short — this style
          handles many lines, not long ones.

## `aspect:`

Worth setting. Written only in prose a ratio drifts, usually to square, which
crops the type off a portrait poster — so this is also sent to Google as a
request parameter. One of `1:1 2:3 3:2 3:4 4:3 4:5 5:4 9:16 16:9 21:9`.

**Give every style the same aspect unless you mean not to.** In auto mode the
ratio must be sent before the choice is known, so it can only be forced when all
styles agree. If they disagree, nothing is forced and each spec's own words are
left to argue for the shape.

## Testing a style you just wrote

Copy contrived to trigger one style is a poor test of the other eight. Start the
server with the override on and pin it:

    DEV_TOOLS=true uv run python -m backend.main

A "Pin a style" control appears on the poster screen. A pinned style is drawn
from its spec at full length, with no catalogue around it. **Each run is a real
charge.**

## What the app checks before it spends anything

A finished prompt is read against the poster engine's reject list
(`backend/posterspec.py`) before the call goes out. **One thing refuses**: a
number in the copy has gone missing from the prompt. Everything else is a note
beside the poster and spends anyway:

- more than **30 words** or **7 lines** of quoted text — Gemini's lettering gets
  worse the more of it there is
- generic adjectives that tell an image model nothing: *stunning, vibrant,
  professional, 4k, masterpiece, perfect* and the rest. The operator's copy is
  never flagged, only the prompt's prose — **including your `when:` line**, which
  goes into the prompt
- no reserved zone stated **as a percentage**
- no aspect ratio in the opening clause; no declared palette, or more than four
  colours in it; no closing *"No watermarks, no extra text"* lock

## Two things worth knowing before you write one

**Malayalam will be misspelled.** Image models do not shape complex scripts. This
repo measured `കേരളം` coming back with its vowel sign on the wrong side and every
conjunct broken. If a poster's copy is Malayalam, expect to read the result
carefully — the app cannot check it for you.

**The poster is a picture, not a document.** There is no editable text layer, so
a wrong word means generating again rather than fixing a line, and CorelDRAW
cannot correct it.
