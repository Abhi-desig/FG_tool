/**
 * Measure Malayalam text with the real font, and fit it to the page.
 *
 * **Why this is in the browser.** A Malayalam shop name does not fit the canvas
 * at any size that reads as a headline — measured 2026-08-26, `ഫോക്കസ്
 * ഡിജിറ്റൽസ്` at "huge" renders 5730 px wide on a 2480 px A4 page, 231% of it.
 * The backend cannot detect this reliably: its Pillow has no HarfBuzz, so it
 * cannot shape a conjunct, let alone measure one (see `features/posters.py`).
 * `measureText` with the bundled woff2 loaded does both exactly.
 *
 * **The measurement is what travels, not the fit.** Widths go to the server at
 * 1 em, and `features/posters.fit_block` applies the same shrink-then-wrap rule
 * to them. The constants below are deliberately duplicated there and must stay
 * in step — that pairing is what makes the preview, the PNG proof and the
 * exported SVG agree on where the text breaks. They are no longer duplicated on
 * trust: `test_both_copies_of_the_fitter_use_the_same_constants` parses this
 * file and compares them.
 *
 * **What travels is the width that will be *drawn*, not the natural width.** It
 * includes the block's letter spacing and text case, because the string
 * measured here is the string that gets rendered. `fit_block` must therefore
 * not add tracking to a supplied measurement a second time — see the matching
 * note on `CheckRequest.measured` in `backend/api/posters.py`.
 */

import type { CanvasPreset, PosterBlock } from "@/lib/api"

/**
 * Text size as a fraction of canvas height. Mirrors `posters.SIZE_SCALE`, so a
 * "huge" headline is huge on A4 and on a 6 ft banner alike.
 */
export const SIZE_SCALE: Record<string, number> = {
  small: 0.035,
  medium: 0.055,
  large: 0.085,
  huge: 0.135,
}

/** Mirrors `posters.MIN_FIT_SCALE`. Below this it wraps instead of shrinking. */
const MIN_FIT_SCALE = 0.6

/**
 * Mirrors `posters.LINE_HEIGHT`. The *default* line spacing — a block may carry
 * its own `leading`. Kept exported: `posterPng` and `PosterDesigner` both read
 * it, and the constants test pins it to the Python value.
 */
export const LINE_HEIGHT = 1.25

/** Mirrors the step count in `posters.fit_block`, so both pick the same size. */
const WRAP_STEPS = 40

/**
 * Text height as a fraction of canvas height. Mirrors `posters.size_fraction`.
 *
 * The four size buckets survive as presets over a number: replacing them would
 * touch the Pydantic Literal, the AI's fallback placement, the published
 * presets and a golden fixture, for a cosmetic win.
 */
export function sizeFraction(block: PosterBlock): number {
  const fraction = block.size_fraction
  if (fraction != null && fraction > 0) return fraction
  return SIZE_SCALE[block.size] ?? 0.055
}

/** Line spacing for one block, falling back to the shared default. */
export function leadingOf(block: PosterBlock): number {
  return block.leading ?? LINE_HEIGHT
}

/** Letter spacing for one block, in ems. */
export function trackingOf(block: PosterBlock): number {
  return block.tracking ?? 0
}

/**
 * Apply the block's text case. Mirrors `posters.cased`.
 *
 * Done here rather than with CSS `text-transform` so the preview, the PNG proof
 * and the SVG all draw the same characters — and so the measurement below
 * measures what is actually drawn. Malayalam is unicameral, so this is a no-op
 * on it.
 */
export function applyCase(text: string, textCase?: string): string {
  return textCase === "upper" ? text.toUpperCase() : text
}

/** Measuring at a large size keeps rounding out of the em ratio. */
const MEASURE_PX = 200

let ctx: CanvasRenderingContext2D | null = null

function context(): CanvasRenderingContext2D | null {
  if (ctx) return ctx
  ctx = document.createElement("canvas").getContext("2d")
  return ctx
}

/**
 * Width of `text` in ems, with the poster font at the block's weight.
 *
 * Returns null when there is no 2D context to measure with, which is the signal
 * for the caller to leave the server on its own estimate rather than send a
 * number it made up.
 */
export function measureEm(text: string, bold: boolean, tracking = 0): number | null {
  const c = context()
  if (!c) return null
  c.font = `${bold ? 700 : 400} ${MEASURE_PX}px "Noto Sans Malayalam", sans-serif`
  const natural = c.measureText(text).width / MEASURE_PX
  // Added, never measured: Canvas2D `letterSpacing` is not universally
  // available and Python has no shaping engine at all, so arithmetic is the
  // only rule both copies of the fitter can follow identically. The full
  // character count, not count - 1: CSS and SVG add spacing after the last
  // character too.
  return natural + tracking * text.length
}

/** Every block measured at 1 em, keyed by id — the payload the server wants. */
export function measureBlocks(blocks: PosterBlock[]): Record<string, number> {
  const out: Record<string, number> = {}
  for (const block of blocks) {
    // The string that will actually be drawn, so the server never re-derives
    // case or re-adds tracking.
    const em = measureEm(
      applyCase(block.text, block.case),
      block.weight === "bold",
      trackingOf(block),
    )
    if (em !== null && em > 0) out[block.id] = em
  }
  return out
}

export interface FittedBlock {
  fontPx: number
  /** Font size as a fraction of the size the operator picked. */
  scale: number
  lines: string[]
  shrunk: boolean
  wrapped: boolean
  /**
   * Wider than its own box at the smallest readable size — fixed by dragging
   * the box wider, so it is advice rather than a blocker.
   */
  overBox: boolean
  /**
   * Wider than the safe zone: it will be trimmed off the printed sheet, and no
   * amount of dragging helps. This is what stops an export.
   */
  overflows: boolean
  /** Line spacing actually used, so nothing downstream re-derives it. */
  leading: number
}

function wrapTo(
  text: string,
  limitEm: number,
  bold: boolean,
  tracking = 0,
): { lines: string[]; widest: number } {
  const words = text.split(/\s+/).filter(Boolean)
  if (words.length === 0) return { lines: [text], widest: 0 }

  const lines: string[] = []
  let current = ""
  for (const word of words) {
    const candidate = current ? `${current} ${word}` : word
    if (current && (measureEm(candidate, bold, tracking) ?? 0) > limitEm) {
      lines.push(current)
      current = word
    } else {
      current = candidate
    }
  }
  if (current) lines.push(current)

  const widest = Math.max(...lines.map((l) => measureEm(l, bold, tracking) ?? 0))
  return { lines, widest }
}

/**
 * Shrink, then wrap, so the text stays inside its box and the trim.
 *
 * Shrinking is tried first because a headline wants to be one strong line. Only
 * when that would push the size below `MIN_FIT_SCALE` — small enough that it no
 * longer reads as the size that was picked — does the text wrap instead.
 */
export function fitBlock(block: PosterBlock, preset: CanvasPreset): FittedBlock {
  const requestedPx = sizeFraction(block) * preset.height_px
  const bold = block.weight === "bold"
  const tracking = trackingOf(block)
  const leading = leadingOf(block)
  // Cased once, and used for every wrap and every returned line.
  const source = applyCase(block.text, block.case)
  const totalEm = measureEm(source, bold, tracking) ?? 0

  // The box, or the safe zone, whichever bites first — a block may not be
  // rescued by growing out past the trim.
  const insetX = preset.safe_mm / preset.width_mm
  const limitFraction = Math.min(block.width, 1 - 2 * insetX)
  const limitPx = Math.max(limitFraction * preset.width_px, 1)

  const single = (fontPx: number, scale: number, shrunk: boolean): FittedBlock => ({
    fontPx,
    scale,
    lines: [source],
    shrunk,
    wrapped: false,
    overBox: false,
    overflows: false,
    leading,
  })

  if (totalEm <= 0) return single(requestedPx, 1, false)
  if (totalEm * requestedPx <= limitPx) return single(requestedPx, 1, false)

  const scale = limitPx / (totalEm * requestedPx)
  if (scale >= MIN_FIT_SCALE) return single(requestedPx * scale, scale, true)

  // Take the largest size at or above the floor whose lines all fit, so a
  // two-line headline stays as large as it can.
  for (let step = 0; step <= WRAP_STEPS; step++) {
    const trial = 1 - (step * (1 - MIN_FIT_SCALE)) / WRAP_STEPS
    const fontPx = requestedPx * trial
    const { lines, widest } = wrapTo(source, limitPx / fontPx, bold, tracking)
    if (widest * fontPx <= limitPx) {
      return {
        fontPx,
        scale: trial,
        lines,
        shrunk: trial < 1,
        wrapped: lines.length > 1,
        overBox: false,
        overflows: false,
        leading,
      }
    }
  }

  // One word is wider than the box even at the smallest allowed size. Whether
  // that matters depends on which edge it passes — see FittedBlock.
  const fontPx = requestedPx * MIN_FIT_SCALE
  const { lines, widest } = wrapTo(source, limitPx / fontPx, bold, tracking)
  const width = (widest * fontPx) / preset.width_px
  return {
    fontPx,
    scale: MIN_FIT_SCALE,
    lines,
    shrunk: true,
    wrapped: lines.length > 1,
    overBox: true,
    overflows: width > 1 - 2 * insetX,
    leading,
  }
}

export function fitBlocks(
  blocks: PosterBlock[],
  preset: CanvasPreset | null,
): Record<string, FittedBlock> {
  if (!preset) return {}
  const out: Record<string, FittedBlock> = {}
  for (const block of blocks) out[block.id] = fitBlock(block, preset)
  return out
}
