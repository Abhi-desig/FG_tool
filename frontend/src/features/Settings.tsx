import { useEffect, useState } from "react"
import { toast } from "sonner"

import { ApiKeys } from "@/components/ApiKeys"
import { GlossaryManager } from "@/components/GlossaryManager"
import { PromptLibrary } from "@/components/PromptLibrary"
import { Badge } from "@/components/ui/badge"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  type EngineRow,
  type ModelSettings,
  type Preferences,
  getPreferences,
  modelSettings,
  setPreferences,
  translationSettings,
} from "@/lib/api"

/**
 * Settings. Built up phase by phase per SETTINGS.md, and now complete: print
 * defaults, hardware, models, glossary, translation engine, API keys and the
 * prompt library.
 */
export function Settings() {
  const [models, setModels] = useState<ModelSettings | null>(null)
  const [prefs, setPrefs] = useState<Preferences | null>(null)
  const [engines, setEngines] = useState<EngineRow[]>([])

  useEffect(() => {
    modelSettings().then(setModels).catch(() => setModels(null))
    getPreferences().then(setPrefs).catch(() => setPrefs(null))
    translationSettings().then((t) => setEngines(t.engines)).catch(() => setEngines([]))
  }, [])

  const save = (key: string, value: string) => {
    setPrefs((current) => (current ? { ...current, [key]: value } : current))
    setPreferences({ [key]: value })
      .then(setPrefs)
      .catch(() => toast.error("Could not save that setting"))
  }

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          How the tool behaves. Nothing here needs a code change.
        </p>
      </header>

      {/* --- Print defaults --- */}
      <section className="space-y-4 rounded-xl border bg-card p-4">
        <h2 className="text-lg font-semibold">Print defaults</h2>
        {prefs && (
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="Output DPI" id="pref-dpi">
              <Select
                value={prefs.default_dpi}
                onValueChange={(v) => save("default_dpi", v)}
              >
                <SelectTrigger id="pref-dpi">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {["72", "150", "300"].map((d) => (
                    <SelectItem key={d} value={d}>
                      {d} DPI
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>

            <Field label="Colour profile" id="pref-colour">
              <Select
                value={prefs.colour_profile}
                onValueChange={(v) => save("colour_profile", v)}
              >
                <SelectTrigger id="pref-colour">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="cmyk">CMYK (print)</SelectItem>
                  <SelectItem value="rgb">RGB (screen)</SelectItem>
                </SelectContent>
              </Select>
            </Field>

            <Field label="Default units" id="pref-units">
              <Select
                value={prefs.print_units}
                onValueChange={(v) => save("print_units", v)}
              >
                <SelectTrigger id="pref-units">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {["feet", "inch", "cm", "mm"].map((u) => (
                    <SelectItem key={u} value={u}>
                      {u}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
          </div>
        )}
      </section>

      {/* --- Hardware --- */}
      <section className="space-y-3 rounded-xl border bg-card p-4">
        <h2 className="text-lg font-semibold">Hardware</h2>
        {models && (
          <>
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="text-muted-foreground">Detected:</span>
              <Badge variant="secondary">{models.device}</Badge>
              <span className="text-xs text-muted-foreground">
                {models.providers.join(" → ")}
              </span>
            </div>
            <p className="text-xs text-muted-foreground">
              Chosen automatically at startup — fastest available first. Enlarging is
              roughly 40× faster on a GPU than on a CPU, so the same job can be a
              minute here and most of an hour on a machine without one.
            </p>
          </>
        )}
      </section>

      {/* --- Models --- */}
      <section className="space-y-3 rounded-xl border bg-card p-4">
        <h2 className="text-lg font-semibold">Models</h2>
        {models && (
          <>
            <p className="text-xs text-muted-foreground">
              <code className="rounded bg-muted px-1 py-0.5">{models.models_dir}</code>{" "}
              · {models.free_gb} GB free. Keep this on your large drive, not the
              system SSD.
            </p>
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-muted-foreground">
                  <th className="py-1.5 font-medium">Model</th>
                  <th className="py-1.5 font-medium">Licence</th>
                  <th className="py-1.5 font-medium">Size</th>
                  <th className="py-1.5 text-right font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {models.models.map((m) => (
                  <tr key={m.key} className="border-t">
                    <td className="py-2">
                      <span className="font-medium">{m.label}</span>
                      <br />
                      <code className="text-xs text-muted-foreground">{m.key}</code>
                    </td>
                    <td className="py-2 text-muted-foreground">{m.licence}</td>
                    <td className="py-2 tabular-nums text-muted-foreground">
                      {m.size_mb ? `${m.size_mb} MB` : "—"}
                    </td>
                    <td className="py-2 text-right">
                      <Badge variant={m.available ? "secondary" : "outline"}>
                        {m.available ? "Ready" : "Not downloaded"}
                      </Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="text-xs text-muted-foreground">
              Model choice is locked deliberately — see LICENSES.md. The shop sells
              this work, so a licence mistake is a commercial risk, not a footnote.
            </p>
          </>
        )}
      </section>

      <GlossaryManager />

      {/* --- Translation engine --- */}
      <section className="space-y-3 rounded-xl border bg-card p-4">
        <h2 className="text-lg font-semibold">Translation engine</h2>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-muted-foreground">
              <th className="py-1.5 font-medium">Engine</th>
              <th className="py-1.5 font-medium">Licence</th>
              <th className="py-1.5 text-right font-medium">Status</th>
            </tr>
          </thead>
          <tbody>
            {engines.map((e) => (
              <tr key={e.key} className="border-t align-top">
                <td className="py-2">
                  <span className="font-medium">{e.label}</span>
                  {e.default && (
                    <Badge variant="secondary" className="ml-2 text-[11px]">
                      in use
                    </Badge>
                  )}
                  <p className="mt-1 max-w-[46ch] text-xs text-muted-foreground">
                    {e.notes}
                  </p>
                </td>
                <td className="py-2 text-muted-foreground">{e.licence}</td>
                <td className="py-2 text-right">
                  <Badge variant={e.downloaded ? "secondary" : "outline"}>
                    {e.downloaded ? "Ready" : e.gated ? "Needs setup" : "Not downloaded"}
                  </Badge>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="text-xs text-muted-foreground">
          NLLB-200 is deliberately not offered: it handles Malayalam well but is
          licensed cc-by-nc-4.0 — non-commercial. You sell this work, so it is out.
        </p>
      </section>

      <ApiKeys />

      <PromptLibrary />
    </div>
  )
}

function Field({
  label,
  id,
  children,
}: {
  label: string
  id: string
  children: React.ReactNode
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
    </div>
  )
}
