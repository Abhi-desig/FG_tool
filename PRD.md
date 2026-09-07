# PRD — Focus Toolkit

**Status:** Draft for review · **Last updated:** 2026-08-22

---

## Problem

A Kerala printing and advertising shop loses hours every day to five manual chores — retyping
Malayalam into a legacy print font, rescuing low-quality client images, cutting out backgrounds,
translating client spreadsheets, and laying out posters — and every existing tool either cannot
handle Malayalam, costs a monthly fee, or requires a GPU the shop does not have.

## Target user

**One shop. Not a market.**

The operator of a single printing and advertising business in Kerala, who:

- works in **CorelDRAW** as the primary design tool, and needs output that pastes into it
- receives client material over **WhatsApp** — compressed images, Malayalam text, Excel files
- sells the printed result to clients, so **mistakes are expensive and visible**
- runs a **shop PC** (i3-9100F, 12 GB RAM, 112 GB SSD + 932 GB HDD, no usable GPU) and an
  **Acer Nitro V** (RTX 4050) as the fast machine
- has a hard ceiling of about **₹2,000/month** for software
- is not a programmer and will not maintain a build pipeline

This is a tool for that person. Not a SaaS, not a product for other shops.

## Core user stories

Five features, in build order. Each is independently useful — the tool is worth running after
story 1 alone.

**1 · Malayalam font converter**
> As the operator, I paste Malayalam text copied from WhatsApp, press one button, and get
> ML-TTKarthika-encoded text on my clipboard, so I can paste it straight into CorelDRAW without
> retyping it and without spelling errors.

**2 · Image upscaler + print-size calculator**
> As the operator, I drop in a client's low-quality image, tell the tool the size I need to print,
> and it tells me honestly whether that image will hold up — and upscales it if that will help.

**3 · Background remover**
> As the operator, I cut a subject out of its background at print resolution, and brush over any
> area I want protected so the tool keeps it and removes only the rest.

**4 · Excel English → Malayalam translator**
> As the operator, I translate a client's spreadsheet into Malayalam in place, with a per-client
> glossary that locks approved terms, and review every row side by side before I accept it.

**5 · AI photo editor + poster designer**
> As the operator, I paste the poster's words, pick one of my own designs, and get a finished
> poster back in one step — then change it in plain language until it is right.

## Success metrics

The product is working when these are true — not when the features merely run.

| # | Feature | Done means |
|---|---|---|
| 1 | Font converter | 25 golden Malayalam strings round-trip correctly, **and** a converted string renders correctly when pasted into CorelDRAW |
| 2 | Print calculator | It has never once said "good for this size" for an image that printed badly. A false *yes* is a failure; a false *no* is merely cautious |
| 2 | Upscaler | Completes on the shop PC without exhausting 12 GB RAM |
| 3 | Background remover | Output is usable at 300 DPI without manual cleanup on a typical client photo |
| 4 | Translator | Glossary terms are never mistranslated twice; the review grid surfaces a deliberately planted error |
| 5 | Posters | A poster the shop would actually send to a client comes back within two or three attempts, and the operator has read every word on it |
| — | Cost | Features 1–4 cost ₹0/month, forever. Feature 5 stays inside ₹2,000/month |
| — | Speed | The operator reaches for this tool instead of doing the job manually |

## Non-goals

Written down so the scope cannot drift, and so nothing here gets promised to a client.

**Not building:**
- Cloud sync, user accounts, or multi-user access — this is a single-operator local tool
- A mobile or tablet interface — desktop browser only
- A hosted or public deployment — the server binds to localhost and never leaves the machine
- A general CorelDRAW plugin — the clipboard is the integration point
- A product for other print shops

**Cannot do, and must not be promised:**
- Turn an 800 px WhatsApp logo into a sharp A3 brochure. It may work as a large flex banner viewed
  from a distance. The print-size calculator exists to tell the truth about this
- Undo genuine motion blur. Upscaling invents detail from low resolution; it cannot un-shake a camera
- Translate perfectly without review. The glossary makes *repeated* terms reliable; new sentences
  still need human eyes, which is why the review grid is mandatory and not optional
- **Spell Malayalam correctly on a generated poster.** Image models do not shape complex scripts:
  measured, `കേരളം` came back with its vowel sign on the wrong side and every conjunct broken. The
  app used to draw all text itself for exactly this reason; ADR-034 traded that away for the
  operator's own prompt-driven designs, deliberately and with the cost stated. **Every word on a
  generated poster must be read before it is printed** — the app cannot check them
- Edit a word on a finished poster. It is a picture, not a document. A typo means generating again,
  and CorelDRAW cannot correct it

## Known caveat to disclose

Google embeds an invisible **SynthID watermark** in AI-generated images. It does not affect printing,
but the operator should know it exists before a client asks.
