import { useCallback, useEffect, useState } from "react"

import { OffMachineNotice } from "@/components/OffMachineNotice"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import {
  type BlockRole,
  type CopyAlternative,
  type CopyResult,
  aiStatus,
  copyPrompt,
  writeCopy,
} from "@/lib/api"

type Role = Exclude<BlockRole, "free">
type Copy = Record<Role, string>

const ROLE_LABEL: Record<Role, string> = {
  headline: "Headline",
  offer: "Offer",
  occasion: "Occasion",
  phone: "Phone",
}

/**
 * The AI writes the words; the app still draws every one of them.
 *
 * ADR-030. This sits *below* the paste box on purpose: offline and free comes
 * first, paid second, and an install with no key loses nothing.
 *
 * Two rules the operator can rely on, both enforced on the server:
 *
 * * **Their phone number never leaves the machine.** It is not in the request;
 *   the app puts it back afterwards.
 * * **A figure they did not type is never printed.** Any alternative that
 *   invents a price or a percentage is thrown away, and the reason is shown.
 */
export function AiCopyWriter({
  copy,
  onUse,
}: {
  copy: Copy
  onUse: (patch: Partial<Copy>) => void
}) {
  const [brief, setBrief] = useState("")
  const [keep, setKeep] = useState<Record<Role, boolean>>({
    headline: false,
    offer: false,
    occasion: false,
    phone: false,
  })
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<CopyResult | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const [prompt, setPrompt] = useState("")
  const [cost, setCost] = useState<number | null>(null)
  const [overBudget, setOverBudget] = useState(false)
  const [overBudgetOk, setOverBudgetOk] = useState(false)

  const body = useCallback(
    () => ({
      brief,
      occasion: copy.occasion,
      phone: copy.phone,
      keep_headline: keep.headline ? copy.headline : "",
      keep_offer: keep.offer ? copy.offer : "",
      keep_occasion: keep.occasion ? copy.occasion : "",
      over_budget_ok: overBudgetOk,
    }),
    [brief, copy, keep, overBudgetOk],
  )

  useEffect(() => {
    aiStatus()
      .then((s) => setOverBudget(s.budget.over_budget))
      .catch(() => setOverBudget(false))
  }, [])

  // Free, and debounced so typing a brief does not fire a request per keystroke.
  useEffect(() => {
    if (!brief.trim()) {
      setPrompt("")
      return
    }
    const timer = setTimeout(() => {
      copyPrompt(body())
        .then((r) => {
          setPrompt(r.prompt)
          setCost(r.estimate.cost_rupees)
        })
        .catch(() => setPrompt(""))
    }, 400)
    return () => clearTimeout(timer)
  }, [brief, body])

  const run = useCallback(async () => {
    setBusy(true)
    setFailure(null)
    try {
      setResult(await writeCopy(body()))
    } catch {
      // A transport failure, not a refusal — `AiResult` describes the latter,
      // and faking one here would claim the call reached Google.
      setResult(null)
      setFailure("Could not reach the server.")
    } finally {
      setBusy(false)
    }
  }, [body])

  const applyAll = (alt: CopyAlternative, malayalam: boolean) => {
    const patch: Partial<Copy> = {}
    for (const b of malayalam ? alt.blocks_ml : alt.blocks) {
      if (b.id !== "free") patch[b.id as Role] = b.text
    }
    onUse(patch)
  }

  return (
    <div className="space-y-3 rounded-lg border p-3">
      <div>
        <p className="text-sm font-medium">Or let the AI write it</p>
        <p className="mt-1 text-xs text-muted-foreground">
          Say what the poster is for and get three sets of words to pick from and
          change. Every word stays yours to edit.
        </p>
      </div>

      <OffMachineNotice
        what="Your brief. Not your phone number, and not any picture."
        costRupees={cost}
      />

      <div className="space-y-1.5">
        <Label htmlFor="copy-brief">What is the poster for?</Label>
        <Textarea
          id="copy-brief"
          rows={3}
          value={brief}
          placeholder="Onam sale, 50% off gold, Thrissur showroom"
          onChange={(e) => setBrief(e.target.value)}
        />
      </div>

      {/* Only offered for lines that actually have words in them. */}
      {(["headline", "offer", "occasion"] as Role[]).some((r) => copy[r].trim()) && (
        <div className="space-y-1">
          <p className="text-xs text-muted-foreground">Keep exactly as I wrote it:</p>
          <div className="flex flex-wrap gap-3">
            {(["headline", "offer", "occasion"] as Role[])
              .filter((r) => copy[r].trim())
              .map((r) => (
                <div key={r} className="flex items-center gap-1.5 text-sm">
                  <Checkbox
                    id={`keep-${r}`}
                    checked={keep[r]}
                    onCheckedChange={(v) => setKeep((k) => ({ ...k, [r]: v === true }))}
                  />
                  <Label htmlFor={`keep-${r}`} className="font-normal">
                    {ROLE_LABEL[r]}
                  </Label>
                </div>
              ))}
          </div>
        </div>
      )}

      {overBudget && (
        <div className="space-y-2 rounded-lg border border-[color:var(--warn)]/40 bg-[color:var(--warn)]/10 p-3">
          <p className="text-sm text-[color:var(--warn)]">
            This month's AI budget is already spent.
          </p>
          <div className="flex items-center gap-2 text-sm">
            <Checkbox
              id="copy-over-budget"
              checked={overBudgetOk}
              onCheckedChange={(v) => setOverBudgetOk(v === true)}
            />
            <Label htmlFor="copy-over-budget" className="font-normal">
              Spend past the budget anyway
            </Label>
          </div>
        </div>
      )}

      <Button
        onClick={() => void run()}
        disabled={busy || !brief.trim() || (overBudget && !overBudgetOk)}
      >
        {busy
          ? "Writing…"
          : `Write the words${cost != null ? ` · ₹${cost.toFixed(2)}` : ""}`}
      </Button>

      {prompt && (
        <details className="rounded-lg border">
          <summary className="cursor-pointer px-3 py-2 text-xs">
            See exactly what will be sent
          </summary>
          <pre className="overflow-x-auto whitespace-pre-wrap border-t p-3 text-[11px] text-muted-foreground">
            {prompt}
          </pre>
        </details>
      )}

      {(failure ?? result?.error) && (
        <p role="alert" className="text-sm text-destructive">
          {failure ?? result?.error} Your words are still here.
        </p>
      )}

      {/* How the operator learns an option was thrown away, and why. */}
      {(result?.warnings ?? []).map((w, i) => (
        <p key={i} role="alert" className="text-xs text-[color:var(--warn)]">
          {w}
        </p>
      ))}

      {result?.alternatives?.map((alt) => (
        <div key={alt.id} className="space-y-2 rounded-lg border p-3">
          <div className="flex items-center justify-between gap-2">
            <Badge variant="secondary">{alt.label}</Badge>
            <div className="flex gap-1">
              <Button size="sm" variant="outline" onClick={() => applyAll(alt, false)}>
                Use English
              </Button>
              <Button size="sm" variant="outline" onClick={() => applyAll(alt, true)}>
                Use Malayalam
              </Button>
            </div>
          </div>
          {/*
            Side by side, not tabbed: the operator is choosing between them, and
            DESIGN.md says to show the Malayalam next to what it came from.
          */}
          <div className="grid grid-cols-2 gap-2 text-sm">
            {alt.blocks.map((b, i) => {
              const ml = alt.blocks_ml[i]
              return (
                <div key={b.id} className="contents">
                  <div className="rounded bg-muted/40 p-2">
                    <p className="text-[11px] text-muted-foreground">
                      {ROLE_LABEL[b.id as Role] ?? b.id}
                    </p>
                    <p>{b.text}</p>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="mt-1 h-6 px-1 text-[11px]"
                      aria-label={`Use the English ${b.id}: ${b.text}`}
                      onClick={() => onUse({ [b.id as Role]: b.text } as Partial<Copy>)}
                    >
                      Use this
                    </Button>
                  </div>
                  <div className="rounded bg-muted/40 p-2">
                    <p className="text-[11px] text-muted-foreground">Malayalam</p>
                    <p className="malayalam">{ml?.text ?? ""}</p>
                    {ml && (
                      <Button
                        size="sm"
                        variant="ghost"
                        className="mt-1 h-6 px-1 text-[11px]"
                        aria-label={`Use the Malayalam ${ml.id}: ${ml.text}`}
                        onClick={() =>
                          onUse({ [ml.id as Role]: ml.text } as Partial<Copy>)
                        }
                      >
                        Use this
                      </Button>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      ))}
    </div>
  )
}
