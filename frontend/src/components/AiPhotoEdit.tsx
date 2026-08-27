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
  /** NEXT.md 1.1 — the budget is now a limit, so its override must be reachable. */
  const [overBudget, setOverBudget] = useState(false)
  const [overBudgetOk, setOverBudgetOk] = useState(false)

  useEffect(() => {
    aiStatus()
      .then((s) => {
        setConfigured(s.configured)
        setOverBudget(s.budget?.over_budget ?? false)
      })
      .catch(() => setConfigured(false))
    // No batch toggle here: photo editing has no batch rate, so waiting would
    // save nothing (NEXT.md 1.4).
    aiEstimate("photo-edit", false)
      .then((e) => setCost(e.cost_rupees))
      .catch(() => setCost(null))
  }, [])

  const run = useCallback(async () => {
    if (!instruction.trim()) return
    setBusy(true)
    try {
      const outcome = await editPhoto(file, instruction, preserve, overBudgetOk)
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
  }, [file, instruction, preserve, overBudgetOk])

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

      {overBudget && (
        <div className="rounded-lg border border-[color:var(--warn)]/50 bg-[color:var(--warn)]/10 p-3">
          <p className="text-sm font-medium">
            This month&rsquo;s ₹2,000 AI budget is used up.
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            That total is an estimate — check Google&rsquo;s console for the real
            figure before deciding.
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
        disabled={busy || !instruction.trim() || (overBudget && !overBudgetOk)}
      >
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
