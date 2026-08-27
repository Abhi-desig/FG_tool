import { useCallback, useEffect, useState } from "react"
import { toast } from "sonner"

import type { Copy } from "@/components/PosterCopy"
import { OffMachineNotice } from "@/components/OffMachineNotice"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import {
  type AiResult,
  type ArtworkRequest,
  type DesignStyle,
  aiEstimate,
  aiStatus,
  artworkPrompt,
  generateArtwork,
} from "@/lib/api"

/**
 * AI poster artwork — the picture only.
 *
 * ROADMAP.md's golden rule: **AI makes the picture, the app makes the text.**
 * Every word on the finished poster is drawn by the poster editor in a real
 * font, which is why Malayalam comes out right and why this beats Canva here.
 *
 * What changed in this pass: the picture is generated **from the poster's own
 * copy**, through the chosen style's saved prompt structure. The screen used to
 * ask for a separate "Picture of…" description, which meant the operator wrote
 * the poster twice and the two halves rarely agreed. Now the headline is the
 * brief, the style is the look, and the optional idea box below is there for
 * when the designer has something specific in mind.
 *
 * The assembled prompt is shown before anything is sent. It is fetched free, so
 * nothing about a paid call is taken on trust.
 */
export function AiArtwork({
  copy,
  style,
  aspect,
  onArtwork,
}: {
  copy: Copy
  style: DesignStyle | null
  aspect: string
  onArtwork: (file: File) => void
}) {
  const [configured, setConfigured] = useState<boolean | null>(null)
  const [batch, setBatch] = useState(true)
  const [cost, setCost] = useState<number | null>(null)
  /** Whether waiting actually saves anything here (NEXT.md 1.4). */
  const [batchDiscount, setBatchDiscount] = useState(true)
  /** The operator's explicit "spend past the budget" (NEXT.md 1.1). */
  const [overBudgetOk, setOverBudgetOk] = useState(false)
  const [overBudget, setOverBudget] = useState(false)
  const [idea, setIdea] = useState("")
  const [preview, setPreview] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<AiResult | null>(null)

  const request: ArtworkRequest = {
    style_key: style?.key ?? null,
    headline: copy.headline,
    offer: copy.offer,
    occasion: copy.occasion,
    phone: copy.phone,
    idea,
    aspect,
    batch,
    over_budget_ok: overBudgetOk,
  }

  const ready = copy.headline.trim().length > 0 && style !== null

  useEffect(() => {
    aiStatus()
      .then((s) => {
        setConfigured(s.configured)
        setOverBudget(s.budget?.over_budget ?? false)
      })
      .catch(() => setConfigured(false))
  }, [])

  useEffect(() => {
    aiEstimate("poster-artwork", batch)
      .then((e) => {
        setCost(e.cost_rupees)
        setBatchDiscount(e.batch_discount)
      })
      .catch(() => setCost(null))
  }, [batch])

  // Assemble the prompt as the operator types. Free, and it makes the link
  // between the words and the picture visible rather than a promise.
  useEffect(() => {
    if (!ready) {
      setPreview(null)
      return
    }
    const timer = setTimeout(() => {
      artworkPrompt(request)
        .then((r) => setPreview(r.prompt))
        .catch(() => setPreview(null))
    }, 400)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- request is rebuilt each render
  }, [ready, style?.key, copy.headline, copy.offer, copy.occasion, copy.phone, idea, aspect])

  const run = useCallback(async () => {
    if (!ready) return
    setBusy(true)
    try {
      const outcome = await generateArtwork(request)
      setResult(outcome)
      if (outcome.ok && outcome.image) {
        const bytes = Uint8Array.from(atob(outcome.image), (c) => c.charCodeAt(0))
        onArtwork(
          new File([bytes], "artwork.png", {
            type: outcome.media_type ?? "image/png",
          }),
        )
        toast.success(`Picture ready · ₹${outcome.cost_rupees.toFixed(2)}`, {
          description: "Placed behind your words. Nothing in it is text.",
        })
      } else {
        toast.error("Google could not make that", { description: outcome.error ?? "" })
      }
    } catch {
      toast.error("Could not reach the artwork generator")
    } finally {
      setBusy(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- request is rebuilt each render
  }, [ready, style?.key, copy, idea, aspect, batch, onArtwork])

  if (configured === false) {
    return (
      <div className="space-y-2 rounded-lg border border-dashed p-3">
        <p className="text-xs text-muted-foreground">
          Add a Gemini API key in Settings to have the picture made for you. The
          poster designer works fully without one — use your own picture instead.
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <OffMachineNotice
        what="Your poster's words (no client photo)"
        costRupees={cost}
        batch={batch}
      />

      {!style && (
        <p className="text-xs text-[color:var(--warn)]">
          Choose a look in step 2 first — it decides how the picture is asked for.
        </p>
      )}
      {style && !copy.headline.trim() && (
        <p className="text-xs text-[color:var(--warn)]">
          Write the headline in step 1 first — the picture is made from it.
        </p>
      )}

      <div className="space-y-1.5">
        <Label htmlFor="art-idea">Your own idea (optional)</Label>
        <Textarea
          id="art-idea"
          value={idea}
          rows={2}
          placeholder="a single brass lamp on a banana leaf, shot from above"
          onChange={(e) => setIdea(e.target.value)}
          className="text-sm"
        />
        <p className="text-xs text-muted-foreground">
          Leave this empty and the picture is worked out from your words alone.
        </p>
      </div>

      {/*
        NEXT.md 1.4: the toggle was shown on all three AI features but only
        artwork has a batch rate. Offering a wait that saves nothing is worse
        than not offering it.
      */}
      {batchDiscount && (
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={batch}
            onChange={(e) => setBatch(e.target.checked)}
            className="size-4 accent-[color:var(--primary)]"
          />
          <span>Batch mode — half price, takes a few minutes</span>
        </label>
      )}

      {/*
        NEXT.md 1.1: the ₹2,000 budget was decorative — no paid route consulted
        it. The server now refuses past it, so the override has to be reachable
        here: it is the shop's money, and the ceiling must be passable. Just not
        by accident.
      */}
      {overBudget && (
        <div className="rounded-lg border border-[color:var(--warn)]/50 bg-[color:var(--warn)]/10 p-3">
          <p className="text-sm font-medium">
            This month&rsquo;s ₹2,000 AI budget is used up.
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            The figure here is an estimate — check Google&rsquo;s console for the
            real one before deciding.
          </p>
          <label className="mt-2 flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={overBudgetOk}
              onChange={(e) => setOverBudgetOk(e.target.checked)}
              className="size-4 accent-[color:var(--primary)]"
            />
            <span>Spend past the budget anyway</span>
          </label>
        </div>
      )}

      <Button
        onClick={() => void run()}
        disabled={busy || !ready || (overBudget && !overBudgetOk)}
        className="w-full"
      >
        {busy
          ? "Making the picture…"
          : `Make the picture${cost != null ? ` · ₹${cost.toFixed(2)}` : ""}`}
      </Button>

      {preview && (
        <details className="rounded-lg border bg-muted/40 p-2">
          <summary className="cursor-pointer text-xs font-medium">
            See exactly what will be sent
          </summary>
          <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap break-words text-[11px] leading-relaxed text-muted-foreground">
            {preview}
          </pre>
          <p className="mt-1 text-[11px] text-muted-foreground">
            The wording around your text comes from the <strong>{style?.name}</strong>{" "}
            style. Change it in Settings → Poster design styles.
          </p>
        </details>
      )}

      {result && !result.ok && (
        <p role="alert" className="text-sm text-destructive">
          {result.error} Your words are still here — nothing was lost.
        </p>
      )}
    </div>
  )
}
