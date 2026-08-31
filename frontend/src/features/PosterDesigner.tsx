import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { toast } from "sonner"

import { AiArtwork } from "@/components/AiArtwork"
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"
import { type Copy, EMPTY_COPY, PosterCopy } from "@/components/PosterCopy"
import { StylePicker } from "@/components/StylePicker"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import {
  type BlockRole,
  type BlockSize,
  type CanvasPreset,
  type CopyLine,
  type CalmRegion,
  type DesignStyle,
  type StyleTextDefault,
  type PosterBlock,
  type PosterCheck,
  type PosterLayout,
  autoLayout,
  checkPoster,
  listStyles,
  analysePoster,
  posterPresets,
  posterSvg,
} from "@/lib/api"
import { rasterIsReduced, renderPosterPng } from "@/lib/posterPng"
import {
  LINE_HEIGHT,
  SIZE_SCALE,
  applyCase,
  fitBlocks,
  leadingOf,
  measureBlocks,
  sizeFraction,
  trackingOf,
} from "@/lib/textFit"

const SIZES: BlockSize[] = ["small", "medium", "large", "huge"]

// Where each line starts out. A poster that opens looking like a poster is
// easier to correct than one that opens as four lines stacked in the middle.
const ROLE_LAYOUT: Record<
  Exclude<BlockRole, "free">,
  { y: number; size: BlockSize; order: number }
> = {
  occasion: { y: 0.1, size: "medium", order: 0 },
  headline: { y: 0.2, size: "large", order: 1 },
  offer: { y: 0.45, size: "huge", order: 2 },
  phone: { y: 0.86, size: "small", order: 3 },
}

const ROLE_LABEL: Record<BlockRole, string> = {
  headline: "Headline",
  offer: "Offer",
  occasion: "Occasion",
  phone: "Phone",
  free: "Extra line",
}

const STEPS = [
  { n: 1, title: "The words" },
  { n: 2, title: "The look" },
  { n: 3, title: "The picture" },
  { n: 4, title: "Size & finish" },
] as const

let counter = 0
const nextId = () => `t${++counter}`

function newBlock(
  text: string,
  y: number,
  size: BlockSize,
  role: BlockRole = "free",
): PosterBlock {
  return {
    id: nextId(),
    text,
    x: 0.1,
    y,
    width: 0.8,
    size,
    size_fraction: null,
    weight: size === "huge" || size === "large" ? "bold" : "regular",
    tracking: 0,
    leading: LINE_HEIGHT,
    colour: "#ffffff",
    align: "centre",
    case: "as-typed",
    mode: "unicode",
    shadow: true,
    role,
  }
}

/**
 * The eight fields a saved style is allowed to set on a block.
 *
 * An explicit list, not a spread. `text_defaults` is an unvalidated JSON column
 * end to end, so `{...b, ...spec}` let a hand-edited style carrying
 * `"headline": {"id": "t1"}` overwrite a block's identity — after which two
 * blocks collide on one id and the fit map loses one of them. The server drops
 * unknown keys too (`styles.normalise_text_defaults`); this is the second half
 * of the same guard, on the side that actually assigns them.
 */
function fromStyle(block: PosterBlock, spec: StyleTextDefault): PosterBlock {
  return {
    ...block,
    colour: spec.colour ?? block.colour,
    size: spec.size ?? block.size,
    size_fraction: spec.size_fraction ?? null,
    weight: spec.weight ?? block.weight,
    tracking: spec.tracking ?? 0,
    leading: spec.leading ?? LINE_HEIGHT,
    align: spec.align ?? block.align,
    case: spec.case ?? "as-typed",
  }
}

/**
 * Phase 4 + 5. The app draws the text; the AI only ever supplies the picture.
 *
 * Text boxes are DOM elements rather than canvas objects, so the browser shapes
 * Malayalam natively, the boxes are keyboard-operable, and each one maps 1:1
 * onto an SVG `<text>` at export. See ADR-019.
 *
 * **The flow is the feature.** The words are typed once and everything else is
 * built from them: each line becomes a text block, and the same words — through
 * the chosen style's saved prompt — become the picture. The previous screen
 * opened on a canvas of sample Malayalam with a separate "Picture of…" box, so
 * the operator wrote the poster twice and nothing made the halves agree.
 */
export function PosterDesigner() {
  const [step, setStep] = useState(1)
  const [presets, setPresets] = useState<CanvasPreset[]>([])
  const [canvasKey, setCanvasKey] = useState("a4-portrait")
  const [styles, setStyles] = useState<DesignStyle[]>([])
  const [styleKey, setStyleKey] = useState<string | null>(null)
  /** The quietest region of the background picture, if one has been analysed. */
  const [calm, setCalm] = useState<CalmRegion | null>(null)
  const [copy, setCopy] = useState<Copy>(EMPTY_COPY)
  const [blocks, setBlocks] = useState<PosterBlock[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [bgFile, setBgFile] = useState<File | null>(null)
  const [bgUrl, setBgUrl] = useState<string | null>(null)
  const [bgColour, setBgColour] = useState("#1b1b22")
  const [showSafe, setShowSafe] = useState(true)
  const [check, setCheck] = useState<PosterCheck | null>(null)
  const [busy, setBusy] = useState(false)

  const stageRef = useRef<HTMLDivElement>(null)
  const bgInputRef = useRef<HTMLInputElement>(null)
  const bgImageRef = useRef<HTMLImageElement | null>(null)
  const drag = useRef<{ id: string; dx: number; dy: number } | null>(null)

  const preset = useMemo(
    () => presets.find((p) => p.key === canvasKey) ?? null,
    [presets, canvasKey],
  )

  const style = useMemo(
    () => styles.find((s) => s.key === styleKey) ?? null,
    [styles, styleKey],
  )
  const styleDefaults = style?.text_defaults ?? {}

  const layout: PosterLayout = useMemo(
    () => ({ canvas: canvasKey, blocks, background_colour: bgColour }),
    [canvasKey, blocks, bgColour],
  )

  // Described in words the image model can use, not in millimetres.
  const aspect = useMemo(() => {
    if (!preset) return "portrait"
    const ratio = preset.width_mm / preset.height_mm
    const shape = ratio > 1.15 ? "landscape" : ratio < 0.87 ? "portrait" : "square"
    return `${preset.label} (${shape})`
  }, [preset])

  useEffect(() => {
    posterPresets()
      .then((p) => setPresets(p.canvases))
      .catch(() => setPresets([]))
    listStyles()
      .then((s) => setStyles(s.styles))
      .catch(() => setStyles([]))
  }, [])

  useEffect(() => {
    return () => {
      if (bgUrl) URL.revokeObjectURL(bgUrl)
    }
  }, [bgUrl])

  /**
   * Auto-fit, measured with the real font.
   *
   * Computed here rather than read back from the server so the preview resizes
   * as the operator types instead of 250 ms later. The same measurements are
   * posted to `/posters/check` and `/posters/svg`, which run the identical
   * shrink-then-wrap rule — so what is on screen is what exports.
   */
  const fits = useMemo(() => fitBlocks(blocks, preset), [blocks, preset])

  // Safe-zone and overflow warnings, debounced while dragging.
  useEffect(() => {
    if (blocks.length === 0) {
      setCheck(null)
      return
    }
    const timer = setTimeout(() => {
      checkPoster(layout, measureBlocks(blocks))
        .then(setCheck)
        .catch(() => setCheck(null))
    }, 250)
    return () => clearTimeout(timer)
  }, [layout, blocks])

  const unsafe = useMemo(
    () => new Set((check?.safe_zone ?? []).filter((s) => s.outside_safe_zone).map((s) => s.id)),
    [check],
  )

  /**
   * Blocks that will be trimmed off the printed sheet.
   *
   * Export is blocked while any exist: a poster with the shop's name running off
   * both edges is the expensive, visible error this tool is for, and it is worth
   * a hard stop rather than a message the operator can scroll past.
   */
  const pastTrim = useMemo(
    () => new Set(Object.entries(fits).filter(([, f]) => f.overflows).map(([id]) => id)),
    [fits],
  )

  /**
   * Blocks wider than their own box but still inside the trim. Worth saying —
   * the operator drags the box wider and it is done — but not worth blocking an
   * export over, and conflating the two made the message useless.
   */
  const overBox = useMemo(
    () =>
      new Set(
        Object.entries(fits)
          .filter(([, f]) => f.overBox && !f.overflows)
          .map(([id]) => id),
      ),
    [fits],
  )

  const update = useCallback((id: string, patch: Partial<PosterBlock>) => {
    setBlocks((prev) => prev.map((b) => (b.id === id ? { ...b, ...patch } : b)))
  }, [])

  // --- copy drives the blocks -------------------------------------------
  //
  // One line of copy, one block. A block the operator has since dragged or
  // recoloured keeps those changes — only its text follows the copy — because
  // retyping a phone number should not undo ten minutes of placement.
  useEffect(() => {
    setBlocks((prev) => {
      const kept = prev.filter(
        (b) => b.role === "free" || copy[b.role as Exclude<BlockRole, "free">]?.trim(),
      )
      let changed = kept.length !== prev.length
      const next = kept.map((b) => {
        if (b.role === "free") return b
        const text = copy[b.role as Exclude<BlockRole, "free">]
        if (text === b.text) return b
        changed = true
        return { ...b, text }
      })

      for (const [role, spec] of Object.entries(ROLE_LAYOUT)) {
        const text = copy[role as Exclude<BlockRole, "free">].trim()
        if (!text || next.some((b) => b.role === role)) continue
        changed = true
        // Seeded from the chosen style, not from `newBlock`'s white default:
        // typing the phone number *after* picking Festival used to produce a
        // small white line that matched nothing else on the poster.
        const born = newBlock(text, spec.y, spec.size, role as BlockRole)
        const look = styleDefaults[role as Exclude<BlockRole, "free">]
        next.push(look ? fromStyle(born, look) : born)
      }

      if (!changed) return prev
      return next.sort(
        (a, b) =>
          (ROLE_LAYOUT[a.role as Exclude<BlockRole, "free">]?.order ?? 9) -
          (ROLE_LAYOUT[b.role as Exclude<BlockRole, "free">]?.order ?? 9),
      )
    })
  }, [copy])

  /**
   * Take one pasted message and lay the whole poster out from it.
   *
   * Lines the splitter could not place become extra text blocks rather than
   * being discarded — the operator can move or delete them, but they never lose
   * a line of the client's wording without being told.
   */
  const applySplit = useCallback((lines: CopyLine[]) => {
    const next: Copy = { ...EMPTY_COPY }
    const spare: string[] = []
    for (const line of lines) {
      if (line.role === "free" || next[line.role]) spare.push(line.text)
      else next[line.role] = line.text
    }
    setCopy(next)
    // A new paste replaces the last one's leftovers; the four named lines are
    // handled by the copy effect below.
    setBlocks((prev) => [
      ...prev.filter((b) => b.role !== "free"),
      ...spare.map((text, i) => newBlock(text, 0.62 + i * 0.06, "small")),
    ])
  }, [])

  /**
   * Step every over-trim line down a size until it fits.
   *
   * The export is hard-blocked while any line runs past the trim, and until now
   * the only advice was "shorten it, or use a wider canvas" with no control to
   * do either. In practice a single notch is almost always enough — huge to
   * large is a 37% cut before the fitter even starts — so the operator was
   * being stopped by something one click could clear.
   */
  const makeItFit = useCallback(() => {
    const order: BlockSize[] = ["huge", "large", "medium", "small"]
    const before = blocks
    let stubborn: string[] = []

    setBlocks((prev) =>
      prev.map((b) => {
        if (!pastTrim.has(b.id)) return b
        const at = order.indexOf(b.size)
        if (at < 0 || at === order.length - 1) {
          stubborn.push(b.text)
          return b
        }
        // Any hand-set size is cleared too, or the preset change does nothing.
        return { ...b, size: order[at + 1], size_fraction: null }
      }),
    )

    if (stubborn.length) {
      toast.warning("Still too long", {
        description: `“${stubborn[0]}” does not fit this canvas even at the smallest size. Shorten it, or pick a wider size.`,
      })
      return
    }
    toast("Made it fit", {
      description: "Lines past the trim were stepped down a size.",
      action: { label: "Undo", onClick: () => setBlocks(before) },
    })
  }, [blocks, pastTrim])

  /** Picking a look repaints the words too — half a style is not a style. */
  const applyStyle = useCallback((picked: DesignStyle) => {
    setStyleKey(picked.key)
    const defaults = picked.text_defaults ?? {}
    if (defaults.background_colour) setBgColour(defaults.background_colour)
    setBlocks((prev) =>
      prev.map((b) => {
        const spec = defaults[b.role as Exclude<BlockRole, "free">]
        return spec ? fromStyle(b, spec) : b
      }),
    )
  }, [])

  // --- dragging ---------------------------------------------------------

  const onPointerDown = (event: React.PointerEvent, block: PosterBlock) => {
    const stage = stageRef.current
    if (!stage) return
    const rect = stage.getBoundingClientRect()
    setSelected(block.id)
    drag.current = {
      id: block.id,
      dx: (event.clientX - rect.left) / rect.width - block.x,
      dy: (event.clientY - rect.top) / rect.height - block.y,
    }
    ;(event.target as HTMLElement).setPointerCapture(event.pointerId)
  }

  const onPointerMove = (event: React.PointerEvent) => {
    const state = drag.current
    const stage = stageRef.current
    if (!state || !stage) return
    const rect = stage.getBoundingClientRect()
    update(state.id, {
      x: Math.min(Math.max((event.clientX - rect.left) / rect.width - state.dx, 0), 1),
      y: Math.min(Math.max((event.clientY - rect.top) / rect.height - state.dy, 0), 1),
    })
  }

  const onPointerUp = () => {
    drag.current = null
  }

  // Keyboard nudging — DESIGN.md requires every control be keyboard operable,
  // and a mouse cannot place text to the pixel anyway.
  const onKeyDown = (event: React.KeyboardEvent, block: PosterBlock) => {
    const step = event.shiftKey ? 0.05 : 0.005
    const moves: Record<string, [number, number]> = {
      ArrowLeft: [-step, 0],
      ArrowRight: [step, 0],
      ArrowUp: [0, -step],
      ArrowDown: [0, step],
    }
    const move = moves[event.key]
    if (!move) return
    event.preventDefault()
    update(block.id, {
      x: Math.min(Math.max(block.x + move[0], 0), 1),
      y: Math.min(Math.max(block.y + move[1], 0), 1),
    })
  }

  // --- actions ----------------------------------------------------------

  const acceptBackground = useCallback((file: File) => {
    const url = URL.createObjectURL(file)
    const img = new Image()
    img.onload = () => {
      bgImageRef.current = img
    }
    img.src = url
    setBgFile(file)
    setBgUrl((previous) => {
      if (previous) URL.revokeObjectURL(previous)
      return url
    })
    // Phase 4's empty-space finding, made visible. It was only ever reachable
    // through `/posters/auto`, which needs a file *and* re-places every block —
    // so the operator could never simply be shown where the quiet part is.
    // Offline and free.
    setCalm(null)
    analysePoster(file)
      .then((r) => setCalm(r.calm_regions[0] ?? null))
      .catch(() => setCalm(null))
  }, [])

  const runAuto = async () => {
    if (!bgFile) {
      toast.error("Add a picture first")
      return
    }
    setBusy(true)
    try {
      const result = await autoLayout(layout, bgFile, true)
      setBlocks(result.blocks)
      toast.success("Placed on the calm areas", {
        description: "Colours picked from what is behind each line.",
      })
    } catch {
      toast.error("Could not place the text automatically")
    } finally {
      setBusy(false)
    }
  }

  const download = (blob: Blob, extension: string) => {
    const url = URL.createObjectURL(blob)
    const link = document.createElement("a")
    link.href = url
    link.download = `poster.${extension}`
    link.click()
    URL.revokeObjectURL(url)
  }

  const exportSvg = async () => {
    setBusy(true)
    try {
      // The same measurements the preview was fitted from, so the file breaks
      // the text exactly where the operator saw it break.
      download(await posterSvg(layout, bgFile, false, measureBlocks(blocks)), "svg")
      const unicode = blocks.some((b) => b.mode === "unicode")
      toast.success("SVG saved", {
        description: unicode
          ? "Every line is still editable text. The Malayalam font travels inside the file — but CorelDRAW resolves fonts by name, so install Noto Sans Malayalam there, or switch those lines to the print-font mode."
          : "Open in CorelDRAW — every line is still editable text in ML-TTKarthika.",
      })
    } catch {
      toast.error("Could not export the SVG")
    } finally {
      setBusy(false)
    }
  }

  const exportPng = async () => {
    if (!preset) return
    setBusy(true)
    try {
      const blob = await renderPosterPng({
        preset,
        blocks,
        backgroundColour: bgColour,
        backgroundImage: bgImageRef.current,
        safeZone: false,
      })
      download(blob, "png")
      toast.success("PNG proof saved", {
        description: rasterIsReduced(preset)
          ? "Scaled down to stay within memory — the SVG is full size."
          : `${preset.width_px}×${preset.height_px} at ${preset.dpi} DPI.`,
      })
    } catch {
      toast.error("Could not render the PNG")
    } finally {
      setBusy(false)
    }
  }

  const current = blocks.find((b) => b.id === selected) ?? null
  const ratio = preset ? preset.width_mm / preset.height_mm : 210 / 297
  const safeInset = preset
    ? { x: (preset.safe_mm / preset.width_mm) * 100, y: (preset.safe_mm / preset.height_mm) * 100 }
    : { x: 2, y: 2 }

  const done: Record<number, boolean> = {
    1: copy.headline.trim().length > 0,
    2: style !== null,
    3: bgFile !== null,
    4: preset !== null,
  }

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Poster designer</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Type the words once. The look and the picture are built from them — and
            every word stays real, editable text.
          </p>
        </div>
        <Badge variant="secondary" className="font-normal">
          {style ? style.name : "No look chosen"}
        </Badge>
      </header>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_380px]">
        {/* Stage */}
        <div className="space-y-3">
          <div
            ref={stageRef}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            role="group"
            aria-label={`Poster preview — ${preset?.label ?? "no size chosen"}`}
            className="relative w-full overflow-hidden rounded-xl border bg-muted"
            style={{
              aspectRatio: String(ratio),
              backgroundColor: bgColour,
              // Makes `cqh` on the text boxes mean "% of stage height", so the
              // preview scales exactly like the export does.
              containerType: "size",
            }}
          >
            {calm && showSafe && (
              <div
                aria-hidden="true"
                title="Quietest area — words stay readable here"
                className="pointer-events-none absolute rounded border border-dashed border-[color:var(--ok)]/70"
                style={{
                  left: `${calm.x * 100}%`,
                  top: `${calm.y * 100}%`,
                  width: `${calm.width * 100}%`,
                  height: `${calm.height * 100}%`,
                }}
              />
            )}
            {bgUrl && (
              <img
                src={bgUrl}
                alt=""
                className="absolute inset-0 h-full w-full object-cover"
                draggable={false}
              />
            )}

            {showSafe && (
              <div
                className="pointer-events-none absolute border-2 border-dashed border-destructive/70"
                style={{
                  left: `${safeInset.x}%`,
                  top: `${safeInset.y}%`,
                  right: `${safeInset.x}%`,
                  bottom: `${safeInset.y}%`,
                }}
              />
            )}

            {blocks.length === 0 && (
              <p className="absolute inset-0 flex items-center justify-center p-8 text-center text-sm text-muted-foreground">
                Write the headline in step 1 and it appears here.
              </p>
            )}

            {blocks.map((block) => {
              const fit = fits[block.id]
              // The fitted size as a fraction of canvas height, so the preview
              // and the export scale identically. Falls back to the requested
              // size until the preset has loaded and a fit exists.
              const heightFraction =
                fit && preset ? fit.fontPx / preset.height_px : sizeFraction(block)
              // Already cased by the fitter. The fallback cases it too, so the
              // preview never briefly shows the un-shouted text.
              const lines = fit?.lines ?? [applyCase(block.text, block.case)]
              return (
                <div
                  key={block.id}
                  role="button"
                  tabIndex={0}
                  // The text as the operator typed it, not the shouted version:
                  // a screen reader should read what they wrote. The warning
                  // state is folded in, because a coloured ring is not a signal
                  // to somebody who cannot see it (DESIGN.md).
                  aria-label={`${ROLE_LABEL[block.role]}: ${block.text}${
                    pastTrim.has(block.id)
                      ? " — will be cut off when printed"
                      : unsafe.has(block.id)
                        ? " — too close to the edge"
                        : overBox.has(block.id)
                          ? " — wider than its box"
                          : ""
                  }`}
                  // `role="button"` alone tells a screen reader this activates;
                  // arrow keys actually *move* it, which is a different thing.
                  aria-roledescription="draggable text line"
                  aria-keyshortcuts="ArrowUp ArrowDown ArrowLeft ArrowRight"
                  onPointerDown={(e) => onPointerDown(e, block)}
                  onKeyDown={(e) => onKeyDown(e, block)}
                  onFocus={() => setSelected(block.id)}
                  // Focus and selection were drawn identically, so a keyboard
                  // user could not tell which block they had landed on from
                  // which one they had chosen.
                  className={`absolute cursor-move select-none rounded px-1 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 ${
                    selected === block.id ? "ring-2 ring-primary" : ""
                  } ${
                    pastTrim.has(block.id) || unsafe.has(block.id)
                      ? "ring-2 ring-destructive"
                      : overBox.has(block.id)
                        ? "ring-2 ring-[color:var(--warn)]"
                        : ""
                  }`}
                  style={{
                    left: `${block.x * 100}%`,
                    top: `${block.y * 100}%`,
                    width: `${block.width * 100}%`,
                    color: block.colour,
                    fontSize: `${heightFraction * 100}cqh`,
                    fontWeight: block.weight === "bold" ? 700 : 400,
                    textAlign: block.align === "centre" ? "center" : block.align,
                    textShadow: block.shadow ? "0 0 0.18em rgba(0,0,0,0.55)" : undefined,
                    lineHeight: fit?.leading ?? leadingOf(block),
                    // In ems, matching the measurement and the SVG. Not CSS
                    // `text-transform` for case, though — that is applied to
                    // the string itself so all four renderers draw the same
                    // characters and the measurement measures what is drawn.
                    letterSpacing: `${trackingOf(block)}em`,
                    // Each line is its own row, and the SVG emits one <tspan>
                    // per line at the same spacing — so the break the operator
                    // sees is the break that exports. `nowrap` keeps the browser
                    // from adding breaks of its own that the SVG would not have.
                    whiteSpace: "nowrap",
                  }}
                >
                  {lines.map((line, i) => (
                    <span key={i} className="malayalam block">
                      {line}
                    </span>
                  ))}
                </div>
              )
            })}
          </div>

          {/*
            Warnings come before the export buttons, deliberately. They used to
            render below them, so the operator could reach "Export SVG" without
            the overflow warning ever entering view.
          */}
          {pastTrim.size > 0 && (
            <div
              role="alert"
              className="rounded-lg border border-destructive/50 bg-destructive/10 p-3 text-sm"
            >
              <p className="font-medium text-destructive">
                {pastTrim.size === 1 ? "One line will" : `${pastTrim.size} lines will`} be
                cut off when printed.
              </p>
              {(check?.overflow ?? [])
                .filter((o) => o.past_trim)
                .map((o) => (
                  <p key={o.id} className="mt-1 text-destructive">
                    {o.message}
                  </p>
                ))}
              <p className="mt-1 text-muted-foreground">
                Export is off until this is fixed.
              </p>
              <Button
                size="sm"
                variant="outline"
                className="mt-2"
                onClick={makeItFit}
              >
                Make it fit
              </Button>
            </div>
          )}

          {overBox.size > 0 && (
            <div className="rounded-lg border bg-muted/40 p-3 text-sm">
              <p className="font-medium">Room to spare</p>
              {(check?.overflow ?? [])
                .filter((o) => !o.past_trim)
                .map((o) => (
                  <p key={o.id} className="mt-1 text-muted-foreground">
                    {o.message}
                  </p>
                ))}
            </div>
          )}

          {unsafe.size > 0 && (
            <p role="alert" className="text-sm text-destructive">
              {unsafe.size} line{unsafe.size === 1 ? "" : "s"} sit too close to the edge —
              trimming will cut into them.
            </p>
          )}

          {(check?.fitted ?? []).length > 0 && (
            <div className="rounded-lg border bg-muted/40 p-3 text-sm">
              <p className="font-medium">Made to fit</p>
              {(check?.fitted ?? []).map((f) => (
                <p key={f.id} className="mt-1 text-muted-foreground">
                  {f.message}
                </p>
              ))}
            </div>
          )}

          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" onClick={() => bgInputRef.current?.click()}>
              {bgFile ? "Change picture" : "Use my own picture"}
            </Button>
            <Button variant="outline" onClick={() => void runAuto()} disabled={busy || !bgFile}>
              Place automatically
            </Button>
            <Button
              variant="ghost"
              onClick={() => {
                const added = newBlock("New line", 0.5, "medium")
                setBlocks((prev) => [...prev, added])
                // Selecting it is enough now that the inspector is always
                // mounted — jumping the accordion collapsed whatever the
                // operator was reading.
                setSelected(added.id)
              }}
            >
              Add a line
            </Button>
            <Button
              variant="ghost"
              onClick={() => setShowSafe((v) => !v)}
              aria-pressed={showSafe}
            >
              {showSafe ? "Hide" : "Show"} trim guide
            </Button>
            <div className="ml-auto flex gap-2">
              <Button
                variant="outline"
                onClick={() => void exportPng()}
                disabled={busy || blocks.length === 0 || pastTrim.size > 0}
              >
                PNG proof
              </Button>
              <Button
                onClick={() => void exportSvg()}
                disabled={busy || blocks.length === 0 || pastTrim.size > 0}
              >
                Export SVG
              </Button>
            </div>
          </div>

          <input
            ref={bgInputRef}
            type="file"
            accept="image/*"
            className="sr-only"
            onChange={(e) => {
              const picked = e.target.files?.[0]
              if (picked) acceptBackground(picked)
            }}
          />

        </div>

        {/* Steps */}
        <div className="space-y-2">
          <Step
            n={1}
            title="The words"
            summary={copy.headline.trim() || "Nothing written yet"}
            open={step === 1}
            done={done[1]}
            onOpen={() => setStep(1)}
          >
            <PosterCopy copy={copy} onChange={setCopy} onSplit={applySplit} />
          </Step>

          <Step
            n={2}
            title="The look"
            summary={style ? style.name : "No look chosen"}
            open={step === 2}
            done={done[2]}
            onOpen={() => setStep(2)}
          >
            <StylePicker styles={styles} chosen={styleKey} onChoose={applyStyle} />
            <p className="mt-3 text-xs text-muted-foreground">
              A look sets the colours of your words and decides how the picture is
              asked for. The wording behind each one lives in Settings → Poster
              design styles.
            </p>
          </Step>

          <Step
            n={3}
            title="The picture"
            summary={bgFile ? "Picture in place" : "Plain colour"}
            open={step === 3}
            done={done[3]}
            onOpen={() => setStep(3)}
          >
            <AiArtwork
              copy={copy}
              style={style}
              aspect={aspect}
              onArtwork={acceptBackground}
            />
            <Separator className="my-3" />
            <Button
              variant="outline"
              className="w-full"
              onClick={() => bgInputRef.current?.click()}
            >
              Use my own picture instead
            </Button>
          </Step>

          <Step
            n={4}
            title="Size & finish"
            summary={preset?.label ?? "Choose a size"}
            open={step === 4}
            done={done[4]}
            onOpen={() => setStep(4)}
          >
            <div className="space-y-3">
              <div className="space-y-1.5">
                <Label htmlFor="canvas">Size</Label>
                <Select value={canvasKey} onValueChange={setCanvasKey}>
                  <SelectTrigger id="canvas">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {presets.map((p) => (
                      <SelectItem key={p.key} value={p.key}>
                        {p.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {preset && (
                  <p className="text-xs text-muted-foreground">
                    {preset.width_px}×{preset.height_px} px at {preset.dpi} DPI ·{" "}
                    {preset.safe_mm} mm trim margin
                  </p>
                )}
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="bg-colour">Background colour</Label>
                <Input
                  id="bg-colour"
                  type="color"
                  value={bgColour}
                  onChange={(e) => setBgColour(e.target.value)}
                  className="h-9 w-full p-1"
                />
              </div>

            </div>
          </Step>

          {/*
            The inspector lives outside the steps, and is always mounted.

            It used to render inside step 4, and `Step` renders its children
            only while open — so on steps 1 to 3 the operator clicked a line on
            the poster and nothing happened at all. Worse, the sentence
            explaining that ("Click a line on the poster…") was itself inside
            the panel it was explaining. Selecting a block is not part of any
            one step; it is what the operator does throughout.
          */}
          <div className="space-y-3 rounded-xl border bg-card p-4">
            <h3 className="text-sm font-semibold">
              {current ? `Selected: ${ROLE_LABEL[current.role]}` : "Nothing selected"}
            </h3>
            {current ? (
              <BlockInspector
                block={current}
                preset={preset}
                onChange={(patch) => update(current.id, patch)}
                onRemove={() => {
                  const removed = current
                  if (removed.role !== "free") {
                    setCopy((prev) => ({ ...prev, [removed.role]: "" }))
                  }
                  setBlocks((prev) => prev.filter((b) => b.id !== removed.id))
                  setSelected(null)
                  // DESIGN.md principle 5: nothing is lost. A removed line is
                  // the operator's own words, and retyping them is the slowest
                  // part of the job.
                  toast("Line removed", {
                    action: {
                      label: "Undo",
                      onClick: () => {
                        setBlocks((prev) => [...prev, removed])
                        if (removed.role !== "free") {
                          setCopy((prev) => ({ ...prev, [removed.role]: removed.text }))
                        }
                        setSelected(removed.id)
                      },
                    },
                  })
                }}
              />
            ) : (
              <p className="text-xs text-muted-foreground">
                Click a line on the poster to change how it looks. Drag to move it,
                or use the arrow keys — hold Shift for bigger steps.
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

/**
 * One step of the flow.
 *
 * Collapsed steps stay on screen as a one-line summary rather than disappearing,
 * so the operator can always see what they chose without reopening anything.
 */
function Step({
  n,
  title,
  summary,
  open,
  done,
  onOpen,
  children,
}: {
  n: number
  title: string
  summary: string
  open: boolean
  done: boolean
  onOpen: () => void
  children: React.ReactNode
}) {
  // The prop wins. `STEPS` came first and silently shadowed it, so renaming a
  // step at the call site changed nothing — two sources of truth for one
  // string, with the invisible one in charge.
  const label = title || (STEPS[n - 1]?.title ?? "")
  const value = `step-${n}`
  return (
    <Accordion
      type="single"
      collapsible
      value={open ? value : ""}
      onValueChange={(next) => {
        // Radix reports "" when the open item is collapsed. Reopening the same
        // step is what `onOpen` already meant, so closing is a no-op here
        // rather than a fifth state the rest of the screen would have to know
        // about.
        if (next === value) onOpen()
      }}
      className={`rounded-xl border bg-card ${open ? "" : "bg-card/60"}`}
    >
      <AccordionItem value={value} className="border-b-0">
        <AccordionTrigger className="items-center gap-3 p-3 hover:no-underline">
          <span
            className={`flex size-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold ${
              done
                ? "bg-primary text-primary-foreground"
                : "border border-border text-muted-foreground"
            }`}
            aria-hidden="true"
          >
            {done ? "✓" : n}
          </span>
          <span className="min-w-0 flex-1">
            <span className="block text-sm font-semibold">
              {label}
              {/*
                The tick was inside `aria-hidden`, so a screen-reader user got
                no completion signal at all — DESIGN.md: colour, or a glyph, is
                never the only signal.
              */}
              {done && <span className="sr-only"> — done</span>}
            </span>
            {!open && (
              <span className="malayalam block truncate text-xs text-muted-foreground">
                {summary}
              </span>
            )}
          </span>
        </AccordionTrigger>
        <AccordionContent className="border-t p-3">{children}</AccordionContent>
      </AccordionItem>
    </Accordion>
  )
}

function BlockInspector({
  block,
  preset,
  onChange,
  onRemove,
}: {
  block: PosterBlock
  preset: CanvasPreset | null
  onChange: (patch: Partial<PosterBlock>) => void
  onRemove: () => void
}) {
  // A print operator thinks in millimetres, not in fractions of a page.
  const heightMm = preset
    ? ((block.size_fraction ?? SIZE_SCALE[block.size] ?? 0.055) * preset.height_mm).toFixed(1)
    : null

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">{ROLE_LABEL[block.role]}</h3>
        <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={onRemove}>
          Remove
        </Button>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="block-text">Words</Label>
        <Input
          id="block-text"
          value={block.text}
          className="malayalam"
          onChange={(e) => onChange({ text: e.target.value })}
        />
        {block.role !== "free" && (
          <p className="text-xs text-muted-foreground">
            Changing it here also changes it in step 1.
          </p>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label htmlFor="block-size">Size</Label>
          <Select
            value={block.size}
            onValueChange={(v) =>
              // Picking a preset clears any hand-set size, so the two controls
              // never disagree about which one is in force.
              onChange({ size: v as BlockSize, size_fraction: null })
            }
          >
            <SelectTrigger id="block-size">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {SIZES.map((s) => (
                <SelectItem key={s} value={s}>
                  {s}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="block-colour">Colour</Label>
          <Input
            id="block-colour"
            type="color"
            value={block.colour}
            onChange={(e) => onChange({ colour: e.target.value })}
            className="h-9 w-full p-1"
          />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label htmlFor="block-align">Align</Label>
          <Select
            value={block.align}
            onValueChange={(v) => onChange({ align: v as PosterBlock["align"] })}
          >
            <SelectTrigger id="block-align">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="left">Left</SelectItem>
              <SelectItem value="centre">Centre</SelectItem>
              <SelectItem value="right">Right</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="block-weight">Weight</Label>
          <Select
            value={block.weight}
            onValueChange={(v) => onChange({ weight: v as PosterBlock["weight"] })}
          >
            <SelectTrigger id="block-weight">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="regular">Regular</SelectItem>
              <SelectItem value="bold">Bold</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      {/*
        Secondary controls behind a disclosure, matching `AiArtwork`'s
        "See exactly what will be sent". Eight controls flat would bury the
        four the operator touches on every job.
      */}
      <details className="rounded-lg border">
        <summary className="cursor-pointer px-3 py-2 text-sm font-medium">
          Fine typography
        </summary>
        <div className="space-y-3 border-t p-3">
          <div className="space-y-1.5">
            <Label htmlFor="block-size-pct">Exact size (% of poster height)</Label>
            <Input
              id="block-size-pct"
              type="number"
              min={1}
              max={40}
              step={0.5}
              value={
                block.size_fraction != null
                  ? Number((block.size_fraction * 100).toFixed(1))
                  : ""
              }
              placeholder={`${((SIZE_SCALE[block.size] ?? 0.055) * 100).toFixed(1)} (from "${block.size}")`}
              onChange={(e) => {
                const value = e.target.value.trim()
                onChange({
                  size_fraction: value === "" ? null : Number(value) / 100,
                })
              }}
            />
            {heightMm && (
              <p className="text-xs text-muted-foreground">
                About {heightMm} mm tall on this canvas. Clear it to go back to
                the preset.
              </p>
            )}
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="block-tracking">Letter spacing</Label>
              <Input
                id="block-tracking"
                type="number"
                min={-0.05}
                max={0.5}
                step={0.01}
                value={block.tracking}
                onChange={(e) => onChange({ tracking: Number(e.target.value) })}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="block-leading">Line spacing</Label>
              <Input
                id="block-leading"
                type="number"
                min={0.8}
                max={3}
                step={0.05}
                value={block.leading}
                onChange={(e) => onChange({ leading: Number(e.target.value) })}
              />
            </div>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="block-case">Letter case</Label>
            <Select
              value={block.case}
              onValueChange={(v) => onChange({ case: v as PosterBlock["case"] })}
            >
              <SelectTrigger id="block-case">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="as-typed">As typed</SelectItem>
                <SelectItem value="upper">UPPERCASE</SelectItem>
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              Malayalam has no capitals, so this changes nothing there.
            </p>
          </div>
        </div>
      </details>

      <div className="space-y-1.5">
        <Label htmlFor="block-mode">Font for export</Label>
        <Select
          value={block.mode}
          onValueChange={(v) => onChange({ mode: v as PosterBlock["mode"] })}
        >
          <SelectTrigger id="block-mode">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="unicode">Unicode (Noto Sans Malayalam)</SelectItem>
            <SelectItem value="ascii">ML-TTKarthika (CorelDRAW)</SelectItem>
          </SelectContent>
        </Select>
        <p className="text-xs text-muted-foreground">
          {block.mode === "ascii"
            ? "The SVG carries ML-TTKarthika codes, converted exactly like the Malayalam converter does. Correct in CorelDRAW — and gibberish anywhere that font is not installed."
            : "Real Unicode text. Use this unless CorelDRAW is set to ML-TTKarthika."}
        </p>
      </div>
    </div>
  )
}
