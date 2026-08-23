import { useCallback, useEffect, useState } from "react"
import { toast } from "sonner"

import { OffMachineNotice } from "@/components/OffMachineNotice"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { type AiResult, aiEstimate, aiStatus, editPhoto } from "@/lib/api"

/**
 * AI photo editing. The only place in the image tools that leaves the machine.
 *
 * ROADMAP.md exit gate: a refused call must never lose the operator's work —
 * so the instruction stays in the box, the original image is untouched, and the
 * reason is shown in plain words.
 */
export function AiPhotoEdit({ file }: { file: File }) {
  const [configured, setConfigured] = useState<boolean | null>(null)
  const [cost, setCost] = useState<number | null>(null)
  const [instruction, setInstruction] = useState("")
  const [preserve, setPreserve] = useState("")
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<AiResult | null>(null)

  useEffect(() => {
    aiStatus()
      .then((s) => setConfigured(s.configured))
      .catch(() => setConfigured(false))
    aiEstimate("photo-edit", false)
      .then((e) => setCost(e.cost_rupees))
      .catch(() => setCost(null))
  }, [])

  const run = useCallback(async () => {
    if (!instruction.trim()) return
    setBusy(true)
    try {
      const outcome = await editPhoto(file, instruction, preserve)
      setResult(outcome)
      if (outcome.ok) {
        toast.success(`Edited · ₹${outcome.cost_rupees.toFixed(2)}`, {
          description: outcome.warnings[0],
        })
      } else {
        // Deliberately not clearing the form: the operator's words survive.
        toast.error("Google could not do that", { description: outcome.error ?? "" })
      }
    } catch {
      toast.error("Could not reach the AI editor")
    } finally {
      setBusy(false)
    }
  }, [file, instruction, preserve])

  if (configured === false) {
    return (
      <section className="space-y-2 rounded-xl border border-dashed bg-card/50 p-4">
        <h2 className="text-sm font-medium">AI photo editing</h2>
        <p className="text-xs text-muted-foreground">
          Add a Gemini API key in Settings to turn this on. Everything else in the image
          tools works without one.
        </p>
      </section>
    )
  }

  return (
    <section className="space-y-3 rounded-xl border bg-card p-4">
      <h2 className="text-sm font-semibold">AI photo editing</h2>

      <OffMachineNotice what="This client photograph" costRupees={cost} />

      <div className="space-y-1.5">
        <Label htmlFor="ai-instruction">What should change?</Label>
        <Input
          id="ai-instruction"
          value={instruction}
          placeholder="remove the plastic chair on the left"
          onChange={(e) => setInstruction(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void run()}
        />
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="ai-preserve">Keep exactly as it is (optional)</Label>
        <Input
          id="ai-preserve"
          value={preserve}
          placeholder="the man's face, the shop sign"
          onChange={(e) => setPreserve(e.target.value)}
        />
      </div>

      <Button onClick={() => void run()} disabled={busy || !instruction.trim()}>
        {busy ? "Editing…" : `Edit${cost != null ? ` · ₹${cost.toFixed(2)}` : ""}`}
      </Button>

      {result && !result.ok && (
        <p role="alert" className="text-sm text-destructive">
          {result.error} Your instruction and the original image are untouched.
        </p>
      )}

      {result?.ok && result.image && (
        <div className="space-y-2">
          <img
            src={`data:${result.media_type ?? "image/png"};base64,${result.image}`}
            alt="AI edited result"
            className="w-full rounded-lg border"
          />
          <div className="flex flex-wrap items-center gap-2">
            <a
              href={`data:${result.media_type ?? "image/png"};base64,${result.image}`}
              download="edited.png"
              className="text-sm text-primary underline"
            >
              Download
            </a>
            <span className="text-xs text-muted-foreground">
              ₹{result.cost_rupees.toFixed(2)} · {result.model}
            </span>
          </div>
          {result.warnings.map((w) => (
            <p key={w} className="text-xs text-muted-foreground">
              {w}
            </p>
          ))}
        </div>
      )}
    </section>
  )
}
