import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { toast } from "sonner"

import { AiArtwork } from "@/components/AiArtwork"
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
  type BlockSize,
  type CanvasPreset,
  type PosterBlock,
  type PosterCheck,
  type PosterLayout,
  autoLayout,
  checkPoster,
  posterPresets,
  posterSvg,
} from "@/lib/api"
import { rasterIsReduced, renderPosterPng } from "@/lib/posterPng"

const SIZES: BlockSize[] = ["small", "medium", "large", "huge"]

// Must match SIZE_SCALE in backend/features/posters.py — text is sized as a
// fraction of canvas height so a layout looks the same on A4 and on a banner.
const SIZE_FRACTION: Record<string, number> = {
  small: 0.035,
  medium: 0.055,
  large: 0.085,
  huge: 0.135,
}

let counter = 0
const nextId = () => `t${++counter}`

function newBlock(text: string, y: number, size: BlockSize): PosterBlock {
  return {
    id: nextId(),
    text,
    x: 0.1,
    y,
    width: 0.8,
    size,
    weight: size === "huge" || size === "large" ? "bold" : "regular",
    colour: "#ffffff",
    align: "centre",
    mode: "unicode",
    shadow: true,
  }
}

const STARTER: PosterBlock[] = [
  newBlock("ഗ്രാൻഡ് സെയിൽ", 0.12, "medium"),
  newBlock("50% OFF", 0.4, "huge"),
  newBlock("9847 000 000", 0.85, "small"),
]

/**
 * Phase 4. The app draws the text; the AI (Phase 5) will only ever supply the
 * picture and a layout plan.
 *
 * Text boxes are DOM elements rather than canvas objects, so the browser shapes
 * Malayalam natively, the boxes are keyboard-operable, and each one maps 1:1
 * onto an SVG <text> at export. See ADR-019.
 */
export function PosterDesigner() {
  const [presets, setPresets] = useState<CanvasPreset[]>([])
  const [canvasKey, setCanvasKey] = useState("a4-portrait")
  const [blocks, setBlocks] = useState<PosterBlock[]>(STARTER)
  const [selected, setSelected] = useState<string | null>(STARTER[0].id)
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

  const layout: PosterLayout = useMemo(
    () => ({ canvas: canvasKey, blocks, background_colour: bgColour }),
    [canvasKey, blocks, bgColour],
  )

  useEffect(() => {
    posterPresets()
      .then((p) => setPresets(p.canvases))
      .catch(() => setPresets([]))
  }, [])

  useEffect(() => {
    return () => {
      if (bgUrl) URL.revokeObjectURL(bgUrl)
    }
  }, [bgUrl])

  // Safe-zone and overflow warnings, debounced while dragging.
  useEffect(() => {
    const timer = setTimeout(() => {
      checkPoster(layout)
        .then(setCheck)
        .catch(() => setCheck(null))
    }, 250)
    return () => clearTimeout(timer)
  }, [layout])

  const unsafe = useMemo(
    () => new Set((check?.safe_zone ?? []).filter((s) => s.outside_safe_zone).map((s) => s.id)),
    [check],
  )

  const update = useCallback((id: string, patch: Partial<PosterBlock>) => {
    setBlocks((prev) => prev.map((b) => (b.id === id ? { ...b, ...patch } : b)))
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

  const acceptBackground = (file: File) => {
    if (bgUrl) URL.revokeObjectURL(bgUrl)
    const url = URL.createObjectURL(file)
    const img = new Image()
    img.onload = () => {
      bgImageRef.current = img
    }
    img.src = url
    setBgFile(file)
    setBgUrl(url)
  }

  const runAuto = async () => {
    if (!bgFile) {
      toast.error("Add a background picture first")
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
      download(await posterSvg(layout, bgFile, false), "svg")
      toast.success("SVG saved", {
        description: "Open in CorelDRAW — every line is still editable text.",
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
  const aspect = preset ? preset.width_mm / preset.height_mm : 210 / 297
  const safeInset = preset
    ? { x: (preset.safe_mm / preset.width_mm) * 100, y: (preset.safe_mm / preset.height_mm) * 100 }
    : { x: 2, y: 2 }

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Poster designer</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Every word stays real, editable text — which is why Malayalam comes out right.
          </p>
        </div>
        <Badge variant="secondary" className="font-normal">
          Offline · free
        </Badge>
      </header>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_320px]">
        {/* Stage */}
        <div className="space-y-3">
          <div
            ref={stageRef}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            className="relative w-full overflow-hidden rounded-xl border bg-muted"
            style={{
              aspectRatio: String(aspect),
              backgroundColor: bgColour,
              // Makes `cqh` on the text boxes mean "% of stage height", so the
              // preview scales exactly like the export does.
              containerType: "size",
            }}
          >
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

            {blocks.map((block) => (
              <div
                key={block.id}
                role="button"
                tabIndex={0}
                aria-label={`Text: ${block.text}`}
                onPointerDown={(e) => onPointerDown(e, block)}
                onKeyDown={(e) => onKeyDown(e, block)}
                onFocus={() => setSelected(block.id)}
                className={`absolute cursor-move select-none rounded px-1 outline-offset-2 ${
                  selected === block.id ? "ring-2 ring-primary" : ""
                } ${unsafe.has(block.id) ? "ring-2 ring-destructive" : ""}`}
                style={{
                  left: `${block.x * 100}%`,
                  top: `${block.y * 100}%`,
                  width: `${block.width * 100}%`,
                  color: block.colour,
                  fontSize: `${(SIZE_FRACTION[block.size] ?? 0.055) * 100}cqh`,
                  fontWeight: block.weight === "bold" ? 700 : 400,
                  textAlign: block.align === "centre" ? "center" : block.align,
                  textShadow: block.shadow ? "0 0 0.18em rgba(0,0,0,0.55)" : undefined,
                  lineHeight: 1.25,
                  // SVG <text> is a single line and never wraps. If the preview
                  // wrapped, what you see would not be what you export — so it
                  // must overflow here exactly as it will overflow there. The
                  // overflow warning below is how the operator finds out.
                  whiteSpace: "nowrap",
                }}
              >
                <span className="malayalam">{block.text}</span>
              </div>
            ))}
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" onClick={() => bgInputRef.current?.click()}>
              {bgFile ? "Change picture" : "Add picture"}
            </Button>
            <Button variant="outline" onClick={() => void runAuto()} disabled={busy || !bgFile}>
              Place automatically
            </Button>
            <Button
              variant="ghost"
              onClick={() =>
                setBlocks((prev) => [...prev, newBlock("New line", 0.5, "medium")])
              }
            >
              Add text
            </Button>
            <Button
              variant="ghost"
              onClick={() => setShowSafe((v) => !v)}
              aria-pressed={showSafe}
            >
              {showSafe ? "Hide" : "Show"} trim guide
            </Button>
            <div className="ml-auto flex gap-2">
              <Button variant="outline" onClick={() => void exportPng()} disabled={busy}>
                PNG proof
              </Button>
              <Button onClick={() => void exportSvg()} disabled={busy}>
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

          {unsafe.size > 0 && (
            <p role="alert" className="text-sm text-destructive">
              {unsafe.size} line{unsafe.size === 1 ? "" : "s"} sit too close to the edge —
              trimming will cut into them.
            </p>
          )}
          {(check?.overflow ?? []).map((o) => (
            <p key={o.id} className="text-sm text-[color:var(--warn)]">
              {o.message}
            </p>
          ))}
        </div>

        {/* Inspector */}
        <div className="space-y-4">
          <section className="space-y-3 rounded-xl border bg-card p-4">
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
          </section>

          <AiArtwork onArtwork={acceptBackground} />

          {current && (
            <section className="space-y-3 rounded-xl border bg-card p-4">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-semibold">Selected text</h2>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7 px-2 text-xs"
                  onClick={() => {
                    setBlocks((prev) => prev.filter((b) => b.id !== current.id))
                    setSelected(null)
                  }}
                >
                  Remove
                </Button>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="block-text">Words</Label>
                <Input
                  id="block-text"
                  value={current.text}
                  className="malayalam"
                  onChange={(e) => update(current.id, { text: e.target.value })}
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <Label htmlFor="block-size">Size</Label>
                  <Select
                    value={current.size}
                    onValueChange={(v) => update(current.id, { size: v as BlockSize })}
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
                    value={current.colour}
                    onChange={(e) => update(current.id, { colour: e.target.value })}
                    className="h-9 w-full p-1"
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <Label htmlFor="block-align">Align</Label>
                  <Select
                    value={current.align}
                    onValueChange={(v) =>
                      update(current.id, { align: v as PosterBlock["align"] })
                    }
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
                    value={current.weight}
                    onValueChange={(v) =>
                      update(current.id, { weight: v as PosterBlock["weight"] })
                    }
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

              <Separator />

              <div className="space-y-1.5">
                <Label htmlFor="block-mode">Font for export</Label>
                <Select
                  value={current.mode}
                  onValueChange={(v) =>
                    update(current.id, { mode: v as PosterBlock["mode"] })
                  }
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
                  {current.mode === "ascii"
                    ? "The SVG carries ML-TTKarthika codes, converted exactly like the Malayalam converter does. Correct in CorelDRAW — and gibberish anywhere that font is not installed."
                    : "Real Unicode text. Use this unless CorelDRAW is set to ML-TTKarthika."}
                </p>
              </div>

              <p className="text-xs text-muted-foreground">
                Drag to move, or select and use the arrow keys — hold Shift for bigger steps.
              </p>
            </section>
          )}
        </div>
      </div>
    </div>
  )
}
