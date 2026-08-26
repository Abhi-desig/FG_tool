import { useCallback, useEffect, useState } from "react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { type AiModelSettings, aiModels, setAiModels } from "@/lib/api"

/**
 * Which Gemini model does which job.
 *
 * This exists because the shipped names were wrong. Google renames and retires
 * models on its own schedule, and a name that no longer exists fails as a 404
 * halfway through a paid job with a message about API versions. Fetching the
 * real list and letting the operator pick from it turns that from a code change
 * into a dropdown.
 *
 * The list needs a working key, so a failure here is shown as a message rather
 * than an empty screen — the current choices stay visible either way.
 */
export function AiModels() {
  const [settings, setSettings] = useState<AiModelSettings | null>(null)
  const [loading, setLoading] = useState(false)
  const [fetched, setFetched] = useState(false)

  const load = useCallback(async (refresh: boolean) => {
    setLoading(true)
    try {
      const data = await aiModels(refresh)
      setSettings(data)
      if (refresh) {
        setFetched(true)
        if (data.error) toast.error("Could not read the model list", { description: data.error })
        else toast.success(`${data.models.length} models available`)
      }
    } catch {
      toast.error("Could not read the model settings")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load(false)
  }, [load])

  const choose = async (role: "artwork" | "photo" | "layout", name: string) => {
    // An empty value means "back to the shipped default".
    const value = name === "__default__" ? "" : name
    try {
      const updated = await setAiModels({ [role]: value })
      setSettings((prev) => (prev ? { ...updated, models: prev.models } : updated))
    } catch {
      toast.error("Could not save that choice")
    }
  }

  if (!settings) return null

  return (
    <section className="space-y-4 rounded-xl border bg-card p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="text-lg font-semibold">AI models</h2>
          <p className="mt-1 max-w-[60ch] text-xs text-muted-foreground">
            Which Google model does which job. Google retires model names without
            warning — if a job fails saying a model was not found, press Refresh and
            pick a name from the list.
          </p>
        </div>
        <Button variant="outline" onClick={() => void load(true)} disabled={loading}>
          {loading ? "Checking…" : "Refresh from Google"}
        </Button>
      </div>

      {settings.error && (
        <p role="alert" className="text-sm text-destructive">
          {settings.error}
        </p>
      )}

      <div className="space-y-4">
        {settings.roles.map((role) => (
          <div key={role.key} className="space-y-1.5">
            <div className="flex flex-wrap items-center gap-2">
              <Label htmlFor={`model-${role.key}`}>{role.label}</Label>
              <Badge variant="outline" className="text-[11px]">
                ≈ ₹{role.cost_rupees.toFixed(2)} a call
              </Badge>
              {role.confirmed === false && (
                <Badge
                  variant="outline"
                  className="border-destructive/60 text-[11px] text-destructive"
                >
                  Google does not have this model
                </Badge>
              )}
              {role.confirmed === true && !role.is_default && (
                <Badge variant="secondary" className="text-[11px]">
                  Changed from default
                </Badge>
              )}
            </div>

            {settings.models.length > 0 ? (
              <Select
                value={role.chosen}
                onValueChange={(v) => void choose(role.key, v)}
              >
                <SelectTrigger id={`model-${role.key}`}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {/* The configured name stays selectable even when Google no
                      longer lists it, so the dropdown never silently rewrites
                      the operator's choice just by being opened. */}
                  {!settings.models.some((m) => m.name === role.chosen) && (
                    <SelectItem value={role.chosen}>{role.chosen} (missing)</SelectItem>
                  )}
                  {settings.models
                    .filter((m) => m.image_output === role.needs_image)
                    .map((m) => (
                      <SelectItem key={m.name} value={m.name}>
                        {m.name}
                      </SelectItem>
                    ))}
                </SelectContent>
              </Select>
            ) : (
              <p className="rounded-md border bg-muted/50 px-3 py-2 font-mono text-xs">
                {role.chosen}
              </p>
            )}

            <p className="text-xs text-muted-foreground">
              {role.description}
              {!fetched && settings.models.length === 0 &&
                " Press Refresh to see what Google actually offers."}
            </p>
          </div>
        ))}
      </div>

      <p className="text-xs text-muted-foreground">
        Prices are this app's own estimate for the job, not a quote from Google.
        Choosing a larger model will cost more than the figure shown.
      </p>
    </section>
  )
}
