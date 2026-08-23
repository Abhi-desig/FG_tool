import { useCallback, useEffect, useState } from "react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Separator } from "@/components/ui/separator"
import {
  type Budget,
  type KeyRow,
  aiStatus,
  listKeys,
  removeKey,
  saveKey,
  testKey,
} from "@/lib/api"

const LABELS: Record<string, string> = {
  GEMINI_API_KEY: "Google Gemini",
  FAL_API_KEY: "fal.ai (optional fallback)",
  REPLICATE_API_TOKEN: "Replicate (optional fallback)",
}

/**
 * API keys and the money they spend.
 *
 * SECURITY.md: the plaintext key is never returned by the server, so this
 * component can only ever show a four-character hint. Re-entering replaces a
 * key; nothing reveals one.
 */
export function ApiKeys() {
  const [keys, setKeys] = useState<KeyRow[]>([])
  const [location, setLocation] = useState("")
  const [budget, setBudget] = useState<Budget | null>(null)
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [testing, setTesting] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    const [keyBody, status] = await Promise.all([
      listKeys().catch(() => null),
      aiStatus().catch(() => null),
    ])
    if (keyBody) {
      setKeys(keyBody.keys)
      setLocation(keyBody.encryption_key_location)
    }
    if (status) setBudget(status.budget)
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const save = async (name: string) => {
    const value = (drafts[name] ?? "").trim()
    if (!value) return
    try {
      setKeys((await saveKey(name, value)).keys)
      // Drop the plaintext from component state the moment it is stored.
      setDrafts((prev) => ({ ...prev, [name]: "" }))
      toast.success("Key saved", { description: "Encrypted on this machine." })
    } catch {
      toast.error("Could not save that key")
    }
  }

  const check = async (name: string) => {
    setTesting(name)
    try {
      const result = await testKey(name)
      setKeys(result.keys)
      if (result.ok) toast.success("Connected")
      else toast.error("That key did not work", { description: result.message })
    } catch {
      toast.error("Could not test that key")
    } finally {
      setTesting(null)
    }
  }

  return (
    <section className="space-y-4 rounded-xl border bg-card p-4">
      <div>
        <h2 className="text-lg font-semibold">API keys</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          Only the poster artwork and photo editing features use these. Everything else
          works offline with no key at all.
        </p>
      </div>

      {budget && (
        <div className="space-y-2 rounded-lg bg-muted/60 p-3">
          <div className="flex items-baseline justify-between text-sm">
            <span className="font-medium">
              ₹{budget.spent_rupees.toFixed(2)} of ₹{budget.budget_rupees.toFixed(0)} this
              month
            </span>
            <span className="text-xs text-muted-foreground">
              {budget.runs} job{budget.runs === 1 ? "" : "s"}
              {budget.failed_runs > 0 && ` · ${budget.failed_runs} failed, not charged`}
            </span>
          </div>
          <div
            className="h-2 overflow-hidden rounded-full bg-border"
            role="progressbar"
            aria-valuenow={Math.round(budget.fraction_used * 100)}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label="Monthly AI spend"
          >
            <div
              className={`h-full rounded-full transition-[width] duration-200 ${
                budget.over_budget ? "bg-destructive" : "bg-primary"
              }`}
              style={{ width: `${Math.round(budget.fraction_used * 100)}%` }}
            />
          </div>
          <p className="text-xs text-muted-foreground">
            An estimate from this app's own job history. Google's console is the real
            number.
          </p>
        </div>
      )}

      <Separator />

      {keys.map((row) => (
        <div key={row.name} className="space-y-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <Label htmlFor={`key-${row.name}`} className="text-sm">
              {LABELS[row.name] ?? row.name}
            </Label>
            <div className="flex items-center gap-2">
              {row.is_set ? (
                <Badge variant="secondary" className="font-mono text-[11px]">
                  {row.hint}
                </Badge>
              ) : (
                <Badge variant="outline" className="text-[11px]">
                  Not set
                </Badge>
              )}
              {row.last_test_ok === true && (
                <Badge variant="outline" className="border-[color:var(--ok)]/60 text-[11px] text-[color:var(--ok)]">
                  Tested OK
                </Badge>
              )}
              {row.last_test_ok === false && (
                <Badge variant="outline" className="border-destructive/60 text-[11px] text-destructive">
                  Failed
                </Badge>
              )}
            </div>
          </div>

          <div className="flex flex-wrap gap-2">
            <Input
              id={`key-${row.name}`}
              type="password"
              autoComplete="off"
              spellCheck={false}
              placeholder={row.is_set ? "Enter a new key to replace it" : "Paste the key"}
              value={drafts[row.name] ?? ""}
              onChange={(e) =>
                setDrafts((prev) => ({ ...prev, [row.name]: e.target.value }))
              }
              onKeyDown={(e) => e.key === "Enter" && void save(row.name)}
              className="min-w-[200px] flex-1 font-mono text-xs"
            />
            <Button
              variant="outline"
              onClick={() => void save(row.name)}
              disabled={!(drafts[row.name] ?? "").trim()}
            >
              Save
            </Button>
            {row.name === "GEMINI_API_KEY" && (
              <Button
                variant="ghost"
                onClick={() => void check(row.name)}
                disabled={!row.is_set || testing === row.name}
              >
                {testing === row.name ? "Testing…" : "Test"}
              </Button>
            )}
            {row.is_set && (
              <Button
                variant="ghost"
                onClick={async () => {
                  setKeys((await removeKey(row.name)).keys)
                  toast.success("Key removed")
                }}
              >
                Remove
              </Button>
            )}
          </div>
        </div>
      ))}

      <p className="text-xs text-muted-foreground">
        Keys are encrypted before they touch the disk, and the app can only ever show
        you the last four characters. Encryption key held in: <code>{location}</code>.
      </p>
    </section>
  )
}
