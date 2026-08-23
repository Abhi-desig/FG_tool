/**
 * Rasterise a poster in the browser, not on the server.
 *
 * The backend cannot do this: its Pillow has no Raqm/HarfBuzz, so it cannot
 * shape Malayalam — measured 2026-08-23, it put the `േ` sign after `ക` and
 * produced no conjunct ligatures. The browser shapes complex scripts correctly
 * as a matter of course, so the proof PNG is made here and the server stays
 * vector-only.
 *
 * This is a *proof*, not the deliverable. The SVG is the deliverable, because
 * its text stays editable in CorelDRAW.
 */

import type { CanvasPreset, PosterBlock } from "@/lib/api"

const SIZE_SCALE: Record<string, number> = {
  small: 0.035,
  medium: 0.055,
  large: 0.085,
  huge: 0.135,
}

/** Cap the raster so a 6 ft banner does not try to allocate 5184×3456×4 bytes. */
const MAX_PIXELS = 40_000_000

function scaleFor(preset: CanvasPreset): number {
  const full = preset.width_px * preset.height_px
  return full > MAX_PIXELS ? Math.sqrt(MAX_PIXELS / full) : 1
}

export interface RenderOptions {
  preset: CanvasPreset
  blocks: PosterBlock[]
  backgroundColour: string
  backgroundImage: HTMLImageElement | null
  safeZone: boolean
}

export async function renderPosterPng(opts: RenderOptions): Promise<Blob> {
  const { preset, blocks, backgroundColour, backgroundImage, safeZone } = opts
  const scale = scaleFor(preset)
  const width = Math.round(preset.width_px * scale)
  const height = Math.round(preset.height_px * scale)

  const canvas = document.createElement("canvas")
  canvas.width = width
  canvas.height = height
  const ctx = canvas.getContext("2d")
  if (!ctx) throw new Error("Could not get a drawing context.")

  ctx.fillStyle = backgroundColour
  ctx.fillRect(0, 0, width, height)

  if (backgroundImage) {
    // Cover, matching the SVG's preserveAspectRatio="slice".
    const ratio = Math.max(
      width / backgroundImage.naturalWidth,
      height / backgroundImage.naturalHeight,
    )
    const w = backgroundImage.naturalWidth * ratio
    const h = backgroundImage.naturalHeight * ratio
    ctx.drawImage(backgroundImage, (width - w) / 2, (height - h) / 2, w, h)
  }

  // The bundled font must be loaded before fillText, or the first draw silently
  // falls back to a system face that may lack Malayalam entirely.
  await document.fonts.ready

  for (const block of blocks) {
    const fontPx = (SIZE_SCALE[block.size] ?? 0.055) * height
    // Always the Unicode text in a Unicode font, whatever the block's export
    // mode. A block set to `ascii` still holds Unicode here — pairing that with
    // ML-TTKarthika, which has no Unicode Malayalam glyphs, would render tofu.
    // The ASCII conversion belongs in the SVG, where CorelDRAW consumes it.
    ctx.font = `${block.weight === "bold" ? 700 : 400} ${fontPx}px "Noto Sans Malayalam", sans-serif`
    ctx.textAlign =
      block.align === "left" ? "left" : block.align === "right" ? "right" : "center"
    ctx.textBaseline = "alphabetic"

    const offset = block.align === "left" ? 0 : block.align === "right" ? 1 : 0.5
    const x = (block.x + block.width * offset) * width
    const y = block.y * height + fontPx * 0.8

    if (block.shadow) {
      ctx.lineJoin = "round"
      ctx.strokeStyle = "rgba(0,0,0,0.45)"
      ctx.lineWidth = Math.max(1, fontPx * 0.14)
      ctx.strokeText(block.text, x, y)
    }
    ctx.fillStyle = block.colour
    ctx.fillText(block.text, x, y)
  }

  if (safeZone) {
    const insetX = (preset.safe_mm / preset.width_mm) * width
    const insetY = (preset.safe_mm / preset.height_mm) * height
    ctx.strokeStyle = "#b42318"
    ctx.lineWidth = Math.max(2, width / 400)
    ctx.setLineDash([width / 80, width / 120])
    ctx.strokeRect(insetX, insetY, width - insetX * 2, height - insetY * 2)
    ctx.setLineDash([])
  }

  const blob = await new Promise<Blob | null>((resolve) =>
    canvas.toBlob(resolve, "image/png"),
  )
  if (!blob) throw new Error("Could not encode the PNG.")
  return blob
}

/** True when the raster had to be shrunk below the print size. */
export function rasterIsReduced(preset: CanvasPreset): boolean {
  return scaleFor(preset) < 1
}
