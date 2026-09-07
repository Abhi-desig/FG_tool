import { useCallback, useEffect, useRef, useState } from "react"
import { toast } from "sonner"

import { OffMachineNotice } from "@/components/OffMachineNotice"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import {
  ApiError,
  type AiResult,
  type PosterCopy,
  type PosterDesign,
  aiStatus,
  generatePoster,
  parsePosterCopy,
  posterDesigns,
  refinePoster,
} from "@/lib/api"

/** How many earlier posters to keep, so a worse attempt is recoverable. */
const HISTORY = 6

/** A data URI back into bytes, for sending a poster up to be changed. */
function toBlob(image: string, mediaType: string): Blob {
  const binary = atob(image)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i)
  return new Blob([bytes], { type: mediaType })
}

interface Attempt {
  image: string
  mediaType: string
  /** What produced it, shown in the history strip. */
  note: string
}

/**
 * Posters. Paste the copy, pick a design, let Gemini draw the whole thing.
 *
 * **This screen is entirely off-machine**, unlike every other screen in the app.
 * It also gives up two things the old designer guaranteed, and both are said out
 * loud below rather than left to be discovered on a printed poster: an image
 * model cannot spell Malayalam, and the result is a picture, so no line is
 * editable afterwards. ADR-034 has the reasoning.
 */
export function PosterDesigner() {
  const [designs, setDesigns] = useState<PosterDesign[]>([])
  const [folder, setFolder] = useState("")
  const [design, setDesign] = useState("")
  const [copy, setCopy] = useState("")
  const [parsed, setParsed] = useState<PosterCopy>({})
  const [reference, setReference] = useState<File | null>(null)
  const [busy, setBusy] = useState<"" | "generate" | "refine">("")
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<AiResult | null>(null)
  const [history, setHistory] = useState<Attempt[]>([])
  const [note, setNote] = useState("")
  const [overBudget, setOverBudget] = useState(false)
  const [overBudgetOk, setOverBudgetOk] = useState(false)

  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    posterDesigns()
      .then((body) => {
        setDesigns(body.designs)
        setFolder(body.folder)
        if (body.designs.length) setDesign((d) => d || body.designs[0].key)
      })
      .catch(() => setDesigns([]))
    aiStatus()
      .then((s) => setOverBudget(s.budget?.over_budget ?? false))
      .catch(() => setOverBudget(false))
  }, [])

  /*
   * Parse as they type, debounced. Free and offline — and getting `h1` and `h2`
   * the wrong way round costs nothing to fix here and a whole poster to notice
   * after it has been generated.
   */
  useEffect(() => {
    if (!copy.trim()) {
      setParsed({})
      return
    }
    const timer = setTimeout(() => {
      parsePosterCopy(copy)
        .then((body) => setParsed(body.copy))
        .catch(() => setParsed({}))
    }, 250)
    return () => clearTimeout(timer)
  }, [copy])

  /**
   * The poster on screen, tracked outside React state.
   *
   * `keep` needs to read the previous poster to push it into the history, and
   * doing that inside a `setResult` updater made the updater impure — React
   * double-invokes those in development to surface exactly this, and every
   * refinement landed in the history twice.
   */
  const current = useRef<AiResult | null>(null)

  const keep = useCallback((outcome: AiResult) => {
    const previous = current.current
    const kept = previous?.image
    if (kept) {
      setHistory((old) =>
        [
          {
            image: kept,
            mediaType: previous.media_type ?? "image/png",
            note: "earlier attempt",
          },
          ...old,
        ].slice(0, HISTORY),
      )
    }
    current.current = outcome
    setResult(outcome)
  }, [])

  const generate = useCallback(async () => {
    if (!design || !parsed.main) return
    setBusy("generate")
    setError(null)
    try {
      const outcome = await generatePoster({
        copy,
        design,
        reference,
        overBudgetOk,
      })
      keep(outcome)
      if (outcome.ok) {
        toast.success(`Poster made · ₹${outcome.cost_rupees.toFixed(2)}`)
      } else {
        // The copy stays in the box: a refused call must never cost the
        // operator their work (ROADMAP.md Phase 5 exit gate).
        toast.error("Google could not do that", { description: outcome.error ?? "" })
      }
    } catch (err: unknown) {
      setError(err instanceof ApiError ? err.message : "Could not reach the poster maker.")
    } finally {
      setBusy("")
    }
  }, [copy, design, parsed.main, reference, overBudgetOk, keep])

  const refine = useCallback(async () => {
    if (!result?.image || !note.trim()) return
    setBusy("refine")
    setError(null)
    try {
      const outcome = await refinePoster({
        poster: toBlob(result.image, result.media_type ?? "image/png"),
        note,
        overBudgetOk,
      })
      keep(outcome)
      if (outcome.ok) {
        setNote("")
        toast.success(`Changed · ₹${outcome.cost_rupees.toFixed(2)}`)
      } else {
        toast.error("Google could not do that", { description: outcome.error ?? "" })
      }
    } catch (err: unknown) {
      setError(err instanceof ApiError ? err.message : "Could not reach the poster maker.")
    } finally {
      setBusy("")
    }
  }, [result, note, overBudgetOk, keep])

  const source = result?.image
    ? `data:${result.media_type ?? "image/png"};base64,${result.image}`
    : null

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Poster designer</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Paste the words, pick a design, and the AI draws the poster. Change it as
            many times as you like.
          </p>
        </div>
        <Badge variant="secondary" className="font-normal">
          Leaves this computer · paid
        </Badge>
      </header>

      {designs.length === 0 ? (
        <section className="space-y-2 rounded-xl border border-dashed bg-card/50 p-6">
          <h2 className="text-sm font-medium">No poster designs yet</h2>
          <p className="text-xs text-muted-foreground">
            Put each design in its own <code>.md</code> file here, and it appears in this
            list straight away — no restart:
          </p>
          <code className="block rounded-md bg-muted px-2 py-1.5 text-xs">{folder}</code>
          <p className="text-xs text-muted-foreground">
            The README in that folder shows the shape of a file and which words a
            design can refer to.
          </p>
        </section>
      ) : (
        <div className="grid gap-5 lg:grid-cols-[minmax(0,380px)_minmax(0,1fr)]">
          {/* Left: what the poster is made from */}
          <div className="space-y-4">
            <section className="space-y-3 rounded-xl border bg-card p-4">
              <div className="space-y-1.5">
                <Label htmlFor="poster-design">Design</Label>
                <Select value={design} onValueChange={setDesign}>
                  <SelectTrigger id="poster-design">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {designs.map((d) => (
                      <SelectItem key={d.key} value={d.key}>
                        {d.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground">
                  {designs.find((d) => d.key === design)?.description}
                </p>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="poster-copy">The words</Label>
                <Textarea
                  id="poster-copy"
                  rows={6}
                  value={copy}
                  className="malayalam"
                  placeholder={"main: Onam Sale\nh1: Up to 40% off\nh2: Only this week"}
                  onChange={(e) => setCopy(e.target.value)}
                />
                <p className="text-xs text-muted-foreground">
                  Put <code>main:</code>, <code>h1:</code> and <code>h2:</code> in front
                  of the lines. An untagged first line is taken as the main one.
                </p>
              </div>

              {/* The parse, shown back before anything is spent. */}
              {copy.trim() && (
                <div className="space-y-1 rounded-lg bg-muted/60 p-3 text-xs">
                  {(["main", "h1", "h2"] as const).map((tag) => (
                    <div key={tag} className="flex gap-2">
                      <span className="w-10 shrink-0 text-muted-foreground">{tag}</span>
                      <span className={parsed[tag] ? "malayalam" : "text-muted-foreground"}>
                        {parsed[tag] || "—"}
                      </span>
                    </div>
                  ))}
                </div>
              )}

              <div className="space-y-1.5">
                <Label htmlFor="poster-reference">Reference picture (optional)</Label>
                <div className="flex items-center gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => fileRef.current?.click()}
                  >
                    {reference ? "Change" : "Choose"}
                  </Button>
                  <span className="truncate text-xs text-muted-foreground">
                    {reference ? reference.name : "The AI will imagine one from the words"}
                  </span>
                  {reference && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-7 px-2 text-xs"
                      onClick={() => setReference(null)}
                    >
                      Remove
                    </Button>
                  )}
                </div>
                <input
                  id="poster-reference"
                  ref={fileRef}
                  type="file"
                  accept="image/*"
                  className="sr-only"
                  onChange={(e) => setReference(e.target.files?.[0] ?? null)}
                />
              </div>
            </section>

            <OffMachineNotice
              what="The poster's words and any reference picture"
              costRupees={null}
            />

            {overBudget && (
              <div className="rounded-lg border border-[color:var(--warn)]/50 bg-[color:var(--warn)]/10 p-3">
                <p className="text-sm font-medium">
                  This month&rsquo;s ₹2,000 AI budget is used up.
                </p>
                <div className="mt-2 flex items-center gap-2 text-sm">
                  <Checkbox
                    id="poster-over-budget"
                    checked={overBudgetOk}
                    onCheckedChange={(v) => setOverBudgetOk(v === true)}
                  />
                  <Label htmlFor="poster-over-budget" className="font-normal">
                    Spend past the budget anyway
                  </Label>
                </div>
              </div>
            )}

            <Button
              className="w-full"
              disabled={busy !== "" || !parsed.main || (overBudget && !overBudgetOk)}
              onClick={() => void generate()}
            >
              {busy === "generate"
                ? "Making the poster…"
                : result
                  ? "Make another"
                  : "Make the poster"}
            </Button>

            {error && (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            )}
          </div>

          {/* Right: the poster */}
          <div className="space-y-4">
            {result && !result.ok && (
              <p role="alert" className="text-sm text-destructive">
                {result.error} Your words and your design are untouched.
              </p>
            )}

            {source ? (
              <>
                <div className="rounded-xl border bg-card p-4">
                  <img
                    src={source}
                    alt="The generated poster"
                    className="mx-auto max-h-[520px] w-full rounded-lg object-contain"
                  />
                  <div className="mt-3 flex flex-wrap items-center gap-3">
                    <Button asChild size="sm">
                      <a href={source} download="poster.png">
                        Download
                      </a>
                    </Button>
                    <span className="text-xs text-muted-foreground">
                      ₹{result!.cost_rupees.toFixed(2)} · {result!.model}
                    </span>
                  </div>
                  {result!.warnings.map((w) => (
                    <p key={w} className="mt-2 text-xs text-muted-foreground">
                      {w}
                    </p>
                  ))}
                </div>

                {/*
                  The check that replaces the one the app used to make for free.
                  The words are inside the picture now, so nothing here can
                  verify them — the operator reads them against what they typed.
                */}
                <section className="space-y-2 rounded-xl border border-[color:var(--warn)]/50 bg-[color:var(--warn)]/10 p-4">
                  <h2 className="text-sm font-medium">Read every word before printing</h2>
                  <p className="text-xs text-muted-foreground">
                    The AI drew the words into the picture, so the app cannot check them.
                    Malayalam in particular will often be misspelled. Compare against what
                    you typed:
                  </p>
                  <div className="space-y-1 text-xs">
                    {(["main", "h1", "h2"] as const)
                      .filter((tag) => parsed[tag])
                      .map((tag) => (
                        <div key={tag} className="flex gap-2">
                          <span className="w-10 shrink-0 text-muted-foreground">{tag}</span>
                          <span className="malayalam">{parsed[tag]}</span>
                        </div>
                      ))}
                  </div>
                </section>

                <section className="space-y-2 rounded-xl border bg-card p-4">
                  <Label htmlFor="poster-note">Change something</Label>
                  <div className="flex gap-2">
                    <Input
                      id="poster-note"
                      value={note}
                      placeholder="make the background darker, move the offer line down"
                      onChange={(e) => setNote(e.target.value)}
                      onKeyDown={(e) => e.key === "Enter" && void refine()}
                    />
                    <Button
                      disabled={busy !== "" || !note.trim() || (overBudget && !overBudgetOk)}
                      onClick={() => void refine()}
                    >
                      {busy === "refine" ? "Changing…" : "Change"}
                    </Button>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    This changes the poster above rather than starting again, so what you
                    liked about it stays. A change comes back straight away and costs
                    about twice what a new poster does — those are made in batch, which
                    is half price for a few minutes&rsquo; wait.
                  </p>
                </section>

                {history.length > 0 && (
                  <section className="space-y-2 rounded-xl border bg-card p-4">
                    <h2 className="text-sm font-medium">Earlier attempts</h2>
                    <p className="text-xs text-muted-foreground">
                      Click one to bring it back. Nothing is charged for going back.
                    </p>
                    <div className="flex flex-wrap gap-2">
                      {history.map((attempt, index) => (
                        <button
                          key={`${index}-${attempt.image.slice(0, 16)}`}
                          type="button"
                          title={attempt.note}
                          onClick={() =>
                            keep({
                              ...result!,
                              image: attempt.image,
                              media_type: attempt.mediaType,
                              cost_paise: 0,
                              cost_rupees: 0,
                            })
                          }
                          className="overflow-hidden rounded-lg border transition-colors hover:border-primary"
                        >
                          <img
                            src={`data:${attempt.mediaType};base64,${attempt.image}`}
                            alt={`Attempt ${history.length - index}`}
                            className="h-20 w-20 object-cover"
                          />
                        </button>
                      ))}
                    </div>
                  </section>
                )}
              </>
            ) : (
              <div className="flex min-h-[320px] items-center justify-center rounded-xl border-2 border-dashed bg-card/50 p-6 text-center">
                <p className="text-sm text-muted-foreground">
                  {parsed.main
                    ? "Ready. Press “Make the poster”."
                    : "Paste the poster's words on the left to begin."}
                </p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
