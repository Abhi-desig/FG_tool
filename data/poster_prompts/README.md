name: (this file is the instructions, not a design)
description: Delete or ignore — `designs()` skips it, having no `---` line.

# Poster designs

One file per design. Drop yours in beside `example-festival.md` and it appears in
the Poster tab immediately — no restart, no database, no screen to fill in.

## The shape of a file

    name: Festival Flex
    description: Bright, high contrast, big headline
    ---
    <the prompt, in your own words>

Everything above the `---` is the header; everything below is the prompt sent to
Google. A file with no `---` line is skipped and says so in the log.

`name` and `description` are what the operator sees when choosing. Leave `name`
out and the filename is used.

## What you can refer to

Four placeholders, filled from the poster's copy:

| Placeholder | What it holds |
|---|---|
| `{{main}}` | the headline |
| `{{h1}}` | the second line |
| `{{h2}}` | the third line |
| `{{concept}}` | the visual idea, written by the AI from the copy — empty when the operator supplied a reference image instead |

Anything else is emptied before the prompt is sent, and the line it sat on is
dropped if that leaves it bare. A placeholder a poster cannot provide is noted in
the log, so a typo is findable rather than silent.

## Two things worth knowing before you write one

**Malayalam will be misspelled.** Image models do not shape complex scripts. This
repo measured `കേരളം` coming back with its vowel sign on the wrong side and every
conjunct broken. If a design's copy is Malayalam, expect to read the result
carefully — the app cannot check it for you.

**The poster is a picture, not a document.** There is no editable text layer, so
a wrong word means generating again rather than fixing a line, and CorelDRAW
cannot correct it. Ask for the words you want in the prompt as precisely as you
would tell a designer.
