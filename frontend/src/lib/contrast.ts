/**
 * Contrast between two hex colours, for the style editor.
 *
 * DESIGN.md sets a WCAG 2.1 AA floor, and these colours end up on printed
 * posters where a low-contrast headline is a reprint rather than an
 * inconvenience. Plain maths, no dependency.
 *
 * **What it can and cannot promise.** It compares the text colour against the
 * style's own background colour. With an AI photograph behind the words the
 * real answer comes from `posters.suggest_colour` and `place_in_calm_space`, so
 * anything shown from here has to say what it measured against — claiming a
 * contrast guarantee the app cannot keep is exactly the confident wrong answer
 * this project forbids.
 */

function channel(value: number): number {
  const c = value / 255
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
}

/** WCAG relative luminance. Returns null for anything that is not #rrggbb. */
export function relativeLuminance(hex: string): number | null {
  const match = /^#([0-9a-f]{6})$/i.exec(hex.trim())
  if (!match) return null
  const n = parseInt(match[1], 16)
  const r = channel((n >> 16) & 255)
  const g = channel((n >> 8) & 255)
  const b = channel(n & 255)
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

/** WCAG contrast ratio, 1–21. Null when either colour is unreadable. */
export function contrastRatio(a: string, b: string): number | null {
  const la = relativeLuminance(a)
  const lb = relativeLuminance(b)
  if (la === null || lb === null) return null
  const [hi, lo] = la > lb ? [la, lb] : [lb, la]
  return (hi + 0.05) / (lo + 0.05)
}

/**
 * The ratio in words as well as a number.
 *
 * Never colour alone — DESIGN.md, and the operator may be looking at this on a
 * screen that is not colour-managed.
 */
export function contrastVerdict(ratio: number | null): string {
  if (ratio === null) return ""
  const rounded = ratio.toFixed(1)
  if (ratio >= 4.5) return `${rounded}:1 — good`
  if (ratio >= 3) return `${rounded}:1 — only large text`
  return `${rounded}:1 — too low`
}
