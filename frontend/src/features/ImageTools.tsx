import { useCallback, useEffect, useRef, useState } from "react"
import { toast } from "sonner"

import { AiPhotoEdit } from "@/components/AiPhotoEdit"
import { JobProgress } from "@/components/JobProgress"
import { PrintVerdict } from "@/components/PrintVerdict"
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
  ApiError,
  type Assessment,
  type Inspection,
  type Job,
  type PrintClass,
  type PrintUnit,
  type UpscaleScale,
  assessPrint,
  inspectImage,
  jobPreviewUrl,
  jobResultUrl,
  printClasses,
  startCutout,
  startUpscale,
} from "@/lib/api"

const UNITS: PrintUnit[] = ["feet", "inch", "cm", "mm"]

/**
 * Phase 2. Inspect a client image, get an honest answer about what size it can
 * print at, then cut it out or enlarge it.
 *
 * DESIGN.md: the DPI verdict is the loudest element on the screen, because a
 * wrong "looks fine" costs a reprint.
 */
export function ImageTools() {
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<string | null>(null)
  const [inspection, setInspection] = useState<Inspection | null>(null)
  const [classes, setClasses] = useState<PrintClass[]>([])
  const [assessment, setAssessment] = useState<Assessment | null>(null)
  const [job, setJob] = useState<Job | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)

  const [printClass, setPrintClass] = useState("flex")
  const [unit, setUnit] = useState<PrintUnit>("feet")
  const [targetW, setTargetW] = useState("6")
  const [targetH, setTargetH] = useState("4")
  const [scale, setScale] = useState<UpscaleScale>("4x")

  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    printClasses()
      .then(setClasses)
      .catch(() => setClasses([]))
  }, [])

  // Revoke the object URL when it changes, or the tab leaks memory over a batch.
  useEffect(() => {
    return () => {
      if (preview) URL.revokeObjectURL(preview)
    }
  }, [preview])

  const accept = useCallback(async (next: File) => {
    setError(null)
    setJob(null)
    setAssessment(null)
    setFile(next)
    setPreview(URL.createObjectURL(next))
    try {
      setInspection(await inspectImage(next, unit))
    } catch (err: unknown) {
      setInspection(null)
      setError(err instanceof ApiError ? err.message : "Could not read that image.")
    }
  }, [unit])

  // After a successful enlargement the verdict must describe the NEW pixels —
  // otherwise a red "Not enough" sits above the result that just fixed it.
  const enlarged =
    job?.kind === "upscale" && job.status === "done" && job.result
      ? ([job.result.width, job.result.height] as const)
      : null

  // Re-assess whenever the image, the result, or any print parameter changes.
  useEffect(() => {
    if (!inspection) return
    const w = Number(targetW)
    const h = Number(targetH)
    if (!Number.isFinite(w) || !Number.isFinite(h) || w <= 0 || h <= 0) {
      setAssessment(null)
      return
    }
    let cancelled = false
    assessPrint({
      pixels_w: enlarged?.[0] ?? inspection.facts.width,
      pixels_h: enlarged?.[1] ?? inspection.facts.height,
      target_w: w,
      target_h: h,
      unit,
      print_class: printClass,
    })
      .then((next) => {
        if (!cancelled) setAssessment(next)
      })
      .catch(() => {
        if (!cancelled) setAssessment(null)
      })
    return () => {
      cancelled = true
    }
  }, [inspection, enlarged, targetW, targetH, unit, printClass])

  const run = useCallback(
    async (kind: "cutout" | "upscale") => {
      if (!file) return
      setError(null)
      try {
        const started =
          kind === "cutout"
            ? await startCutout(file, 300)
            : await startUpscale(file, scale, assessment?.upscale_to ?? null, {
                dpi: 300,
                fmt: "PNG",
                cmyk: false,
              })
        setJob(started)
      } catch (err: unknown) {
        setError(err instanceof ApiError ? err.message : "Could not start that job.")
      }
    },
    [file, scale, assessment],
  )

  /** What the chosen scale will actually produce. */
  const outputSize = ((): [number, number] | null => {
    if (!inspection) return null
    if (scale === "print") return assessment?.upscale_to ?? null
    const option = inspection.scale_options.find((o) => o.scale === scale)
    return option ? [option.width, option.height] : null
  })()

  const onDrop = (event: React.DragEvent) => {
    event.preventDefault()
    setDragging(false)
    const dropped = event.dataTransfer.files[0]
    if (dropped) void accept(dropped)
  }


  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Image tools</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Find out what size an image can really print at — then cut it out or enlarge it.
          </p>
        </div>
        <Badge variant="secondary" className="font-normal">
          Offline · free
        </Badge>
      </header>

      {!file && (
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          onDragOver={(e) => {
            e.preventDefault()
            setDragging(true)
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={`flex min-h-[280px] w-full flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed transition-colors ${
            dragging
              ? "border-primary bg-accent"
              : "border-border bg-card hover:border-primary/50"
          }`}
        >
          <span className="text-base font-medium">Drop a client image here</span>
          <span className="text-sm text-muted-foreground">
            or click to browse — PNG, JPEG, TIFF, WEBP
          </span>
        </button>
      )}

      <input
        ref={inputRef}
        type="file"
        accept="image/*"
        className="sr-only"
        onChange={(e) => {
          const picked = e.target.files?.[0]
          if (picked) void accept(picked)
        }}
      />

      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}

      {file && inspection && (
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_360px]">
          {/* Left: the answer */}
          <div className="space-y-4">
            {assessment && (
              <PrintVerdict assessment={assessment} describing={enlarged ? "enlarged" : "original"} />
            )}

            {job && (
              <JobProgress
                job={job}
                onUpdate={setJob}
                onDone={(finished) =>
                  toast.success(
                    finished.kind === "cutout" ? "Background removed" : "Image enlarged",
                    { description: finished.result?.note ?? "Ready to download." },
                  )
                }
              />
            )}

            {job?.status === "done" && job.result && (
              <div className="rounded-xl border bg-card p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <p className="text-sm font-medium">Result</p>
                    <p className="text-xs text-muted-foreground">
                      {job.result.width}×{job.result.height} px ·{" "}
                      {job.result.dpi} DPI ·{" "}
                      {(job.result.bytes / 1_048_576).toFixed(1)} MB
                      {job.result.tiles ? ` · ${job.result.tiles} tiles` : ""}
                    </p>
                  </div>
                  <Button asChild>
                    <a href={jobResultUrl(job.id)} download>
                      Download
                    </a>
                  </Button>
                </div>
                <img
                  src={jobPreviewUrl(job.id)}
                  alt="Result preview"
                  className="mt-3 max-h-[320px] w-full rounded-lg bg-[#808080] object-contain"
                />
              </div>
            )}

            {preview && !job && (
              <img
                src={preview}
                alt="Uploaded image"
                /* Neutral mid-grey in both themes so colour is judged fairly. */
                className="max-h-[360px] w-full rounded-xl border bg-[#808080] object-contain"
              />
            )}
          </div>

          {/* Right: the controls */}
          <aside className="space-y-4">
            <div className="rounded-xl border bg-card p-4">
              <p className="text-sm font-medium">{file.name}</p>
              <p className="mt-1 text-xs text-muted-foreground">
                {inspection.facts.width}×{inspection.facts.height} px ·{" "}
                {inspection.facts.megapixels} MP · {inspection.facts.format}
                {inspection.facts.has_alpha && " · has transparency"}
              </p>
              <Button
                variant="ghost"
                size="sm"
                className="mt-2 -ml-2 h-8 px-2 text-xs"
                onClick={() => {
                  setFile(null)
                  setPreview(null)
                  setInspection(null)
                  setAssessment(null)
                  setJob(null)
                }}
              >
                Choose a different image
              </Button>
            </div>

            <div className="space-y-3 rounded-xl border bg-card p-4">
              <div className="space-y-1.5">
                <Label htmlFor="print-class">What is it for?</Label>
                <Select value={printClass} onValueChange={setPrintClass}>
                  <SelectTrigger id="print-class">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {classes.map((c) => (
                      <SelectItem key={c.key} value={c.key}>
                        {c.label} — {c.viewing}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="grid grid-cols-[1fr_1fr_auto] items-end gap-2">
                <div className="space-y-1.5">
                  <Label htmlFor="target-w">Width</Label>
                  <Input
                    id="target-w"
                    inputMode="decimal"
                    value={targetW}
                    onChange={(e) => setTargetW(e.target.value)}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="target-h">Height</Label>
                  <Input
                    id="target-h"
                    inputMode="decimal"
                    value={targetH}
                    onChange={(e) => setTargetH(e.target.value)}
                  />
                </div>
                <Select value={unit} onValueChange={(v) => setUnit(v as PrintUnit)}>
                  <SelectTrigger className="w-[84px]" aria-label="Units">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {UNITS.map((u) => (
                      <SelectItem key={u} value={u}>
                        {u}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className="space-y-3 rounded-xl border bg-card p-4">
              <div className="space-y-1.5">
                <Label>Enlarge by</Label>
                {/* Radio-style group: the choice is small and worth seeing all
                    of at once, rather than hidden behind a dropdown. */}
                <div
                  role="radiogroup"
                  aria-label="Enlargement amount"
                  className="grid grid-cols-3 gap-1 rounded-lg bg-muted p-1"
                >
                  {(["2x", "4x", "print"] as const).map((option) => {
                    const disabled = option === "print" && !assessment?.upscale_to
                    return (
                      <button
                        key={option}
                        type="button"
                        role="radio"
                        aria-checked={scale === option}
                        disabled={disabled}
                        onClick={() => setScale(option)}
                        className={`rounded-md px-2 py-1.5 text-sm transition-colors disabled:opacity-40 ${
                          scale === option
                            ? "bg-card font-medium shadow-sm"
                            : "text-muted-foreground hover:text-foreground"
                        }`}
                      >
                        {option === "print" ? "For print" : option.replace("x", "×")}
                      </button>
                    )
                  })}
                </div>
                <p className="text-xs text-muted-foreground">
                  {scale === "print"
                    ? assessment?.upscale_to
                      ? "Exactly the pixels this print size needs."
                      : "Already big enough for this print size."
                    : outputSize
                      ? `Gives ${outputSize[0]}×${outputSize[1]} px.`
                      : ""}
                </p>
              </div>

              <Button
                className="w-full"
                onClick={() => void run("upscale")}
                disabled={
                  job?.status === "running" ||
                  job?.status === "queued" ||
                  (scale === "print" && !assessment?.upscale_to)
                }
              >
                {outputSize
                  ? `Enlarge to ${outputSize[0]}×${outputSize[1]}`
                  : "Enlarge"}
              </Button>
              <Button
                variant="outline"
                className="w-full"
                onClick={() => void run("cutout")}
                disabled={job?.status === "running" || job?.status === "queued"}
              >
                Remove background
              </Button>
              <Separator className="my-1" />
              <p className="text-xs text-muted-foreground">
                Runs on {inspection.device} — {inspection.tiles} tiles, roughly{" "}
                {inspection.estimated_upscale_seconds < 90
                  ? `${Math.round(inspection.estimated_upscale_seconds)}s`
                  : `${Math.round(inspection.estimated_upscale_seconds / 60)} min`}
                . 2× and 4× take the same time — the model always runs at 4× and the
                result is resampled, so nothing is thrown away first.
              </p>
            </div>

            <AiPhotoEdit file={file} />
          </aside>
        </div>
      )}
    </div>
  )
}
