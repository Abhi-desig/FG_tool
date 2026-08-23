import { useCallback, useEffect, useState } from "react"
import { toast } from "sonner"

import { OffMachineNotice } from "@/components/OffMachineNotice"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { type AiResult, aiEstimate, aiStatus, generateArtwork } from "@/lib/api"

/**
 * AI poster artwork — the picture only.
 *
 * ROADMAP.md's golden rule: **AI makes the picture, the app makes the text.**
 * The prompt asks for no lettering at all, and every word on the finished poster
 * is drawn by the poster editor as real text. That is why Malayalam comes out
 * right, and it is the shop's biggest advantage over Canva.
 */
export function AiArtwork({ onArtwork }: { onArtwork: (file: File) => void }) {
  const [configured, setConfigured] = useState<boolean | null>(null)
  const [batch, setBatch] = useState(true)
  const [cost, setCost] = useState<number | null>(null)
  const [subject, setSubject] = useState("")
  const [style, setStyle] = useState("")
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<AiResult | null>(null)

  useEffect(() => {
    aiStatus()
      .then((s) => setConfigured(s.configured))
      .catch(() => setConfigured(false))
  }, [])

  useEffect(() => {
    aiEstimate("poster-artwork", batch)
      .then((e) => setCost(e.cost_rupees))
      .catch(() => setCost(null))
  }, [batch])

  const run = useCallback(async () => {
    if (!subject.trim()) return
    setBusy(true)
    try {
      const outcome = await generateArtwork({ subject, style, batch })
      setResult(outcome)
      if (outcome.ok && outcome.image) {
        const bytes = Uint8Array.from(atob(outcome.image), (c) => c.charCodeAt(0))
        onArtwork(
          new File([bytes], "artwork.png", {
            type: outcome.media_type ?? "image/png",
          }),
        )
        toast.success(`Artwork ready · ₹${outcome.cost_rupees.toFixed(2)}`, {
          description: "Placed as the poster background. Your text is drawn on top.",
        })
      } else {
        toast.error("Google could not make that", { description: outcome.error ?? "" })
      }
    } catch {
      toast.error("Could not reach the artwork generator")
    } finally {
      setBusy(false)
    }
  }, [subject, style, batch, onArtwork])

  if (configured === false) {
    return (
      <section className="space-y-2 rounded-xl border border-dashed bg-card/50 p-4">
        <h2 className="text-sm font-medium">AI artwork</h2>
        <p className="text-xs text-muted-foreground">
          Add a Gemini API key in Settings to generate backgrounds. The poster designer
          works fully without one — add your own picture instead.
        </p>
      </section>
    )
  }

  return (
    <section className="space-y-3 rounded-xl border bg-card p-4">
      <h2 className="text-sm font-semibold">AI artwork</h2>

      <OffMachineNotice
        what="Your description (no client photo)"
        costRupees={cost}
        batch={batch}
      />

      <div className="space-y-1.5">
        <Label htmlFor="art-subject">Picture of…</Label>
        <Input
          id="art-subject"
          value={subject}
          placeholder="a Kerala spice market at golden hour"
          onChange={(e) => setSubject(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void run()}
        />
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="art-style">Style (optional)</Label>
        <Input
          id="art-style"
          value={style}
          placeholder="warm, photographic, shallow depth of field"
          onChange={(e) => setStyle(e.target.value)}
        />
      </div>

      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={batch}
          onChange={(e) => setBatch(e.target.checked)}
          className="size-4 accent-[color:var(--primary)]"
        />
        <span>
          Batch mode — half price, takes a few minutes
        </span>
      </label>

      <Button onClick={() => void run()} disabled={busy || !subject.trim()}>
        {busy
          ? "Generating…"
          : `Generate${cost != null ? ` · ₹${cost.toFixed(2)}` : ""}`}
      </Button>

      {result && !result.ok && (
        <p role="alert" className="text-sm text-destructive">
          {result.error} Your description is still here — nothing was lost.
        </p>
      )}

      <p className="text-xs text-muted-foreground">
        The picture never contains words. Every line of text on the poster is drawn by
        this app in a real font, which is why Malayalam comes out correct.
      </p>
    </section>
  )
}
