# Poster layout presets — specification

**Status:** Approved, not built · **Written:** 2026-09-02 · Phase 4 work
Decided by [ADR-031](DECISIONS.md). Companions: [ROADMAP](ROADMAP.md) Phase 4 ·
[DECISIONS](DECISIONS.md) ADR-019, ADR-020, ADR-027 · [SETTINGS](SETTINGS.md) §2a

Specifies a **layout preset**: a named arrangement of type and shapes on the canvas, held as data
and drawn by the app. It is the missing half of a design style — ADR-027 gave a style the *look*
(colours and how the picture is asked for); this gives it the *arrangement*.

Nothing here is built. §14 says what changes and §15 says in what order.

---

## 1 · What a preset is, and what it is not

The source material for this work was nineteen written art-direction templates and a seven-step
selection algorithm. **The algorithm is adopted. The templates are not**, because every one of them
specifies typography inside an image-generation prompt — *"hero very large extra-bold warm gold
Happy Onam"* — which is the one thing Phase 4 exists to prevent.

The division stays exactly where ADR-019 put it:

| Layer | Owns | Where it lives | Costs |
|---|---|---|---|
| **Design style** (exists) | Colours, weights, and how the picture is asked for | `poster_styles` in SQLite, editable in Settings | One artwork call |
| **Layout preset** (new) | Zones, ranking, shapes, alignment axis, margins | Code constants, like `CANVAS_PRESETS` | **Nothing** |

A poster is `canvas × preset × style × copy`, optionally over a background image.

**Presets are geometry, so they are code, not a settings table.** A style's wording is the shop's
and must be editable; a preset's numbers are load-bearing, and a hand-edited overlap produces a bad
print with no warning. They are seeded as frozen dataclasses beside `CANVAS_PRESETS` in
`features/posters.py`. Add a table later if the operator asks for one — not before.

**The layout AI call becomes optional.** `poster-layout` in the prompt library stays for freeform
work, but the default path is a preset, which is free, offline, deterministic and testable. On a
₹500 top-up that is not a small thing.

---

## 2 · The gap that is larger than shapes

Almost every one of the nineteen templates contains a phrase like *"Up to ₹78,000 Central
Government Subsidy with ₹78,000 larger and gold"* — one text block, mixed sizes and colours inside
it. Template 04 is nothing but that idea.

`TextBlock` is uniform: one `size`, one `weight`, one `colour`, one `tracking` for the whole block
([posters.py:145](backend/features/posters.py:145)). There is no way to express it today.

**This is the single biggest change in the spec, and it is bigger than all eight shape primitives
put together**, because it touches the fitter, the renderer, the browser measurement path and the
style defaults. It is specified in §7. If only one thing gets built, build this — it improves every
existing poster, not only the new presets.

---

## 3 · The data shape

Four new frozen dataclasses. Fractions are of the canvas, matching `TextBlock` throughout.

```python
@dataclass(frozen=True)
class Zone:
    """A named rectangle that type is allowed to occupy."""
    key: str                    # "type-field", "brand", "metrics"
    x: float; y: float
    width: float; height: float
    align: Align                # one axis per zone — see §10
    anchor: str                 # "top" | "middle" | "bottom": how blocks stack
    over: str                   # "image" | "field" | "shape:<key>" — what auto-colour samples


@dataclass(frozen=True)
class Slot:
    """One content block's place in the hierarchy."""
    role: str                   # "headline" | "offer" | "occasion" | "phone" | "brand" | "free"
    rank: int                   # 1..4. Exactly one rank-1 per preset.
    zone: str                   # Zone.key
    order: int                  # position within the zone, top to bottom
    optional: bool = True       # a preset must render with the line absent


@dataclass(frozen=True)
class Shape:
    """One drawn element. See §5 for the kinds."""
    key: str
    kind: str                   # "band" | "pill" | "rule" | "disc" | "scrim" | "mask" | "chip"
    x: float; y: float
    width: float; height: float
    fill: str | None            # hex, or None for outline-only
    stroke: str | None = None
    stroke_width: float = 0.0   # fraction of canvas width
    radius: float = 0.0         # fraction of the shape's shorter side
    opacity: float = 1.0
    layer: str = "under-text"   # "under-text" | "over-text" — see §6
    role: str = "highlight"     # "highlight" | "container" | "rule" — counted by the validator


@dataclass(frozen=True)
class LayoutPreset:
    key: str                    # stable handle a saved poster remembers
    name: str
    description: str
    zones: tuple[Zone, ...]
    slots: tuple[Slot, ...]
    shapes: tuple[Shape, ...] = ()
    margin: float = 0.08        # design margin — NOT the trim margin, see §11
    hero_fraction: float = 0.135
    ratio: tuple[float, float, float, float] = (1.0, 0.35, 0.18, 0.12)
    reserve: str = ""           # the sentence handed to the artwork prompt, §9
    needs_image: bool = True    # false for the cream/navy field presets
```

Everything above is derived from what `TextBlock` and `Canvas` already express. Nothing in it
requires a schema change: `db.py` builds its tables with `CREATE TABLE IF NOT EXISTS` and has no
migration path, which is exactly why presets stay in code.

---

## 4 · Applying a preset

Deterministic, and every step already has a home:

1. `split_copy` tags the pasted message with roles ([posters.py:714](backend/features/posters.py:714)).
2. Each tagged line is matched to the `Slot` with that role. Unmatched lines take `role="free"`
   slots in order; if none are left, they fall to the overflow zone rather than being dropped —
   the rule in the `split_copy` docstring holds here too.
3. Rank sets `size_fraction` = `hero_fraction × ratio[rank-1]`, clamped to
   `MIN_SIZE_FRACTION`/`MAX_SIZE_FRACTION`.
4. Blocks are stacked inside their zone by `order`, honouring `anchor` and the zone's `align`.
5. The style's `text_defaults` paint colour and weight per role, as they do now.
6. Auto-colour samples luminance behind each *zone* rather than each band (§8).
7. `fit_layout` shrinks and wraps as it does today.

**Step 2 is done locally, and that is an improvement on the source algorithm.** Its Steps 1 and 2
ask a model to count and rank the blocks. This app already does it in `split_copy`, for free, with
the guesses shown and every one correctable — and when two lines both look like the hero, the
operator decides, not the model.

---

## 5 · Shape primitives

Eight kinds cover all nineteen templates. `render_svg` currently emits one `<rect>`, one `<image>`
and text ([posters.py:507](backend/features/posters.py:507)) — everything below is new.

| Kind | Covers | SVG emitted | CorelDRAW risk |
|---|---|---|---|
| `band` | Letterbox bars, the white slab, bento modules | `<rect>` | None |
| `pill` | The "BANK LOAN AT 5.75%" tag, solid or outline | `<rect rx>` | None |
| `rule` | Hairline grid, metric separators, an underline | `<line>` | None |
| `disc` | The halo behind a figure | `<circle>` | None flat; **blur is a risk** |
| `scrim` | Bottom-third fall-off with no visible edge | `<linearGradient>` + `<rect>` | **Unverified** |
| `mask` | Circle medallion, arch window, rounded photo frame | `<clipPath>` around the existing `<image>` | **Unverified** |
| `chip` | A white label with a leader line to a point on the photo | `<rect>` + `<line>` + a text block | None |
| `field` | Flat cream/navy ground | already exists as `Layout.background_colour` | None |

**The CorelDRAW column is the important one.** NEXT.md item 1.5 was an SVG that referenced a font
CorelDRAW did not have, and it was only discoverable by opening the file. Gradients, clip paths and
filters are the same class of risk: they either import, or they silently do not. **Each of the three
"unverified" rows needs one test import before a preset depends on it** — that is a five-minute
check and it belongs in [QC.md](QC.md), not in a unit test.

**Frosted glass is dropped.** Templates 08 and 13 want the photograph blurred *behind* a panel.
SVG has no `backdrop-filter`; the honest options are a filtered second copy of the image or a
server-side blurred crop, and both are heavy for a decorative effect that may not survive import.
Those two presets use a flat semi-transparent `band` instead. It reads nearly the same in print.

---

## 6 · Rendering order

Today: background rect → image → safe-zone guide → text. It becomes:

```
background field  →  image (optionally inside a mask)  →  shapes[layer="under-text"]
                  →  text  →  shapes[layer="over-text"]  →  safe-zone guide
```

**Text is last except where a preset says otherwise**, and only one template needs the exception —
the arch window, where the hero word sits behind the arch. Allow it, but a preset with an
`over-text` shape covering a block should warn: obscuring editable text fights the reason the export
is SVG at all.

---

## 7 · Mixed-size runs inside one block

The change from §2. A block's `text` gains an optional parallel field:

```python
runs: tuple[Run, ...] = ()      # empty means the block is uniform, as today

@dataclass(frozen=True)
class Run:
    text: str
    scale: float = 1.0          # multiple of the block's own font size
    colour: str | None = None   # None inherits the block's colour
    weight: Weight | None = None
```

Rules that keep this from becoming a second layout engine:

- **Runs are inline, never wrapped independently.** They join into one string for fitting; the
  fitter's width estimate multiplies each run's advance by its `scale`.
- **`text` stays authoritative.** `"".join(run.text for run in runs) == block.text` is an invariant
  the API validates. Copy is never edited by the run machinery — the ADR-030 rule that the app
  never rewrites the operator's digits applies here without exception.
- **SVG emits one `<tspan>` per run** with its own `font-size` and `fill`, inside the same `<text>`.
  That stays a real text element, so ADR-019 and `test_svg_carries_real_text_not_paths` still hold.
- **The browser measures runs, not just blocks.** `textFit.ts` and `estimate_text_width` both need
  the run-aware width; they are already pinned to each other by
  `test_both_copies_of_the_fitter_use_the_same_constants`, and that pin must extend to this.
- **Baselines stay shared.** A larger run inside a line raises the line height, so `fit_block` uses
  `max(scale)` when computing leading.

One caveat, stated plainly: **a multi-line block with mixed runs is where this gets expensive.** If
that proves hard, restrict runs to single-line blocks first. Nearly every real use — a price inside
a sentence — is one line.

---

## 8 · Zones and auto-colour

`find_calm_regions` and `place_in_calm_space` ([posters.py:266](backend/features/posters.py:266))
stay, with a change of job:

- **Auto placement** (`POST /api/posters/auto`) is unchanged. It is the no-preset path.
- **Preset placement** does not move blocks — the preset already decided where they go. It calls
  the same luminance machinery to sample **the zone's rectangle** and flip each block light or dark,
  which is the half of `place_in_calm_space` worth keeping here.
- **A busy zone is a warning, not a silent failure.** If the sampled busyness in a zone is too high,
  the designer says so: *"the picture is busy behind the headline."* The operator then re-generates
  the artwork, moves the block, or turns on the shadow pass.

**There is no busyness threshold in the code today** — `find_calm_regions` computes the number and
only ever *ranks* by it ([posters.py:266](backend/features/posters.py:266)), so nothing has ever had
to say what counts as too busy. That number has to be chosen against real client photographs, which
is the same evidence the open Phase 4 gate item on auto-colour is waiting for. **Pick it there, in
one pass, rather than guessing it here.**

---

## 9 · The reserved zone, and the one idea worth taking from the templates

The seeded style bodies say *"Leave the upper third and the lower fifth calm and uncluttered"*
([styles.py:79](backend/features/styles.py:79)). The templates say *"The upper 55% is completely
clean empty deep blue sky — this is the type field. No shapes anywhere."*

The second is materially better, and the reason is written into the source algorithm: state the
reserve as a percentage **or the model fills every corner**.

So a preset carries a `reserve` sentence generated from its own type zone, and it reaches the
artwork prompt as a new variable:

- Add `reserve` to `styles.VARIABLES`.
- Seeded bodies replace the fixed "upper third and lower fifth" line with `{{reserve}}`.
- `styles.validate()` warns if a body drops it, the way it warns on any missing variable.
- `render()` already drops a bare label when a value is empty, so a preset with no reserve
  degrades to the old behaviour rather than sending the model a dangling word.

**This is the bridge between the two halves**, and it is the reason a preset must be chosen *before*
the artwork is generated rather than after. The designer's step order changes to match.

---

## 10 · Margins, and two things that must not be conflated

| | What it is | Where it comes from | What it does |
|---|---|---|---|
| **Trim margin** | What the printer's grip will eat | `Canvas.safe_mm` — 5 mm on A4, 50 mm on flex | Red ring and a written warning |
| **Design margin** | Where type stops looking cramped | `LayoutPreset.margin`, default 0.08 | Where zones are allowed to start |

The design margin is the larger of the two everywhere: 8% against 2.4% on A4 and 4.1% of the height
on a 6×4 ft flex. **Do not let the preset overwrite `safe_mm`.** They answer different questions, one
is about the printer and one is about taste, and the trim check must keep failing independently —
that gate is closed by a real print, not by a layout.

---

## 11 · Validation

Mirrors `styles.validate()`: a list of problems returned, never raised, so the designer shows all of
them at once and an odd preset renders with defaults instead of failing a page.

| Rule | Refuse or warn |
|---|---|
| Exactly one rank-1 slot | Refuse |
| Every slot names a zone that exists | Refuse |
| Every zone lies inside `margin` | Refuse |
| One `align` value per zone | Refuse |
| Runs reassemble to the block's exact text | Refuse |
| At most one `role="highlight"` shape plus one `role="container"` | Warn |
| At most four distinct colours across text and shapes, background excluded | Warn |
| Zones do not overlap unless declared | Warn |
| `reserve` states a percentage | Warn |

**The colour rule needs the background excluded or it fails the shipped Festival style on day one** —
that style already carries a background plus four text colours
([styles.py:106](backend/features/styles.py:106)). Counting the ground as one of the four is the
source algorithm's reading; this codebase already ships a counter-example, so the ground does not
count.

---

## 12 · Choosing a preset

The source algorithm's Step 4 scoring becomes a local function over `split_copy` output, suggesting
one preset with the operator free to pick another:

| Signal | Weight | Source |
|---|---|---|
| Content load | ×3 | number of tagged lines |
| Occasion | ×2 | `_looks_like_occasion` already exists |
| Numbers present | ×2 | `_MONEY` / `_offer_strength` already exist |
| Visual strength | ×2 | `find_calm_regions` busyness on the uploaded image |
| Human present | ×1 | **not available** — would need subject detection |
| Recency | ×1 | **not available** — see below |

Two honest deletions. **Recency has nothing to score:** posters are not saved and are not linked to
a client — the client concept exists only to scope the translation glossary. Either drop the signal
or build a poster history table first; do not fake it. **Human-present** needs a detector the app
does not have; the operator knows, so make it a checkbox or drop the signal.

And a caution worth writing down: the weights and the content-load bands in the source algorithm are
asserted, not measured. Keep them in one module constant with a comment saying so. They are a
tie-breaker, not a fact.

---

## 13 · The nineteen, classified

| Preset | New primitives needed | Verdict |
|---|---|---|
| 01 Sky-led · 06 Shadow-side · 17 Masthead | none | **Build first.** Pure zones over an image |
| 04 Word-level highlight | none — needs §7 runs | **Build first**, once runs exist |
| 02 Letterbox · 14 Vertical slab | `band` | Cheap, and both print well |
| 16 Swiss grid | `rule` | Cheap |
| 07 Centred · 05 Depth frame | `pill` | Cheap |
| 19 Metric strip | `scrim`, `rule`, runs | Worth it — the figures are the shop's actual selling point |
| 03 Radial halo | `disc` | Flat disc only; blur unverified |
| 08 Gradient scrim · 13 Column | `scrim` / `band` | **Frosted glass dropped**, see §5 |
| 11 Medallion · 15 Offset border | `mask`, `rule` | After the CorelDRAW import check |
| 12 Arch window | `mask` + `over-text` | Hardest of the family. Last |
| 18 Bento | many `band`, `mask` | Most work, all deterministic. Several modules need no photo at all |
| 09 Duotone split | image grading, not a shape | Belongs in `features/images.py`, not here |
| 10 Chip and label | `chip` | Leaders need anchor points on the photo. **Operator-placed only** — the app cannot find the roof |

Note also that `CANVAS_PRESETS` has no 4:5 portrait. The templates are all 4:5 social. One line.

---

## 14 · What changes, file by file

| File | Change | Size |
|---|---|---|
| `backend/features/posters.py` | `Zone`/`Slot`/`Shape`/`LayoutPreset`, seeded presets, `apply_preset`, shape emission in `render_svg`, `Run` support in `TextBlock`/`fit_block`/`estimate_text_width` | **Large** |
| `frontend/src/lib/textFit.ts` | Run-aware measurement, kept pinned to the Python copy | Medium |
| `backend/api/posters.py` | `GET /posters/layouts`, preset key on `LayoutIn`, run validation on `TextBlockIn` | Small |
| `backend/features/styles.py` | `reserve` in `VARIABLES`; seeded bodies use `{{reserve}}` | Small |
| `frontend/src/features/PosterDesigner.tsx` | Preset picker, and preset chosen *before* artwork | Medium |
| `backend/features/ai.py` | `_FALLBACK_PLACEMENT` becomes "the default preset" | Small |
| `POSTER_LAYOUTS.md`, `SETTINGS.md` §2a, `QC.md` | This doc, the preset list, the CorelDRAW import checks | Small |

Nothing here adds a dependency. Nothing here needs a schema migration. Both are deliberate.

---

## 15 · Order of work, and the gate

[CLAUDE.md](CLAUDE.md) hard rule 6 applies. **Phase 4 has two open gate items** — auto-colour
against real client photographs, and a real print confirming the safe margin. This is Phase 4 work,
so it is not jumping the queue, but the sequencing matters:

1. **Mixed-size runs** (§7). Improves every poster the shop makes today, preset or not.
2. **Zones, slots, ranking, `{{reserve}}`** (§3, §4, §9) and the four presets needing no shapes.
3. **Close the Phase 4 gates.** Print one. Nineteen new ways to place type near an edge are worth
   nothing until the margin is confirmed on the operator's own printer, and the busy-zone warning
   in §8 is exactly what the auto-colour gate is asking about.
4. **`band` / `rule` / `pill`** — the four cheap presets. No import risk.
5. **The CorelDRAW check** on gradient, clip path and filter, added to QC.md.
6. **`scrim` / `mask` / `disc`**, and the presets that depend on them, only if step 5 passes.

Steps 1 and 2 are most of the value. Steps 4 onward are variety.

---

## 16 · Tests that would matter

Not coverage — the things that would silently produce a wrong poster:

- Runs reassemble to the block's exact text, including a Malayalam block and a price
- A preset with a missing optional line still renders, with nothing overlapping
- `hero_fraction × ratio` never leaves `MIN_SIZE_FRACTION`..`MAX_SIZE_FRACTION` on any canvas preset
- Every seeded preset passes its own validator
- No zone crosses the trim margin on the widest and narrowest canvas
- The SVG still contains zero `<path>` with shapes and runs present — ADR-019 holds
- Python and TypeScript fitters agree on a run-mixed block, extending the existing pin

The golden fixture for the last one is a browser measurement, as `poster_widths.tsv` already is.

---

## 17 · The decision

Recorded as **[ADR-031](DECISIONS.md)** — *a layout is data the app draws, never a description the
model draws*. Kept there and not repeated here: DECISIONS.md is the ledger, and two copies of one
decision is exactly the drift [CLAUDE.md](CLAUDE.md)'s anti-drift rule exists to prevent.

To reverse any of this, add a new ADR. Do not edit ADR-031, and do not quietly edit this document
away from it.
