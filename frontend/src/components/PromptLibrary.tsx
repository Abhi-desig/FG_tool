import { useCallback, useEffect, useMemo, useState } from "react"
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
import { Textarea } from "@/components/ui/textarea"
import {
  type PromptRow,
  type PromptScope,
  activatePrompt,
  listPrompts,
  restorePromptDefault,
  savePrompt,
  validatePrompt,
} from "@/lib/api"

/**
 * The prompt library. SETTINGS.md: this is what makes AI output tunable without
 * touching code.
 *
 * Editing is safe by construction — every save keeps the previous body, and a
 * shipped default can always be restored exactly.
 */
export function PromptLibrary() {
  const [scopes, setScopes] = useState<PromptScope[]>([])
  const [rows, setRows] = useState<PromptRow[]>([])
  const [scope, setScope] = useState("poster-layout")
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [body, setBody] = useState("")
  const [problems, setProblems] = useState<string[]>([])
  const [dirty, setDirty] = useState(false)

  const refresh = useCallback(
    async (keepId?: number) => {
      const data = await listPrompts().catch(() => null)
      if (!data) return
      setScopes(data.scopes)
      setRows(data.prompts)
      const forScope = data.prompts.filter((p) => p.scope === scope)
      const pick =
        forScope.find((p) => p.id === keepId) ??
        forScope.find((p) => p.is_active) ??
        forScope[0]
      if (pick) {
        setSelectedId(pick.id)
        setBody(pick.body)
        setDirty(false)
      }
    },
    [scope],
  )

  useEffect(() => {
    void refresh()
  }, [refresh])

  const spec = useMemo(() => scopes.find((s) => s.key === scope) ?? null, [scopes, scope])
  const forScope = useMemo(() => rows.filter((p) => p.scope === scope), [rows, scope])
  const selected = useMemo(
    () => forScope.find((p) => p.id === selectedId) ?? null,
    [forScope, selectedId],
  )

  // Check variables as the operator types — catching a bad name here is far
  // cheaper than discovering it mid-job.
  useEffect(() => {
    if (!dirty) return
    const timer = setTimeout(() => {
      validatePrompt(scope, body)
        .then((r) => setProblems(r.problems))
        .catch(() => setProblems([]))
    }, 300)
    return () => clearTimeout(timer)
  }, [scope, body, dirty])

  const save = async () => {
    if (!selected) return
    try {
      const result = await savePrompt(scope, selected.name, body, selected.id)
      setProblems(result.problems)
      setDirty(false)
      await refresh(selected.id)
      toast.success("Prompt saved", {
        description: "The previous version was kept.",
      })
    } catch {
      toast.error("Could not save that prompt")
    }
  }

  return (
    <section className="space-y-4 rounded-xl border bg-card p-4">
      <div>
        <h2 className="text-lg font-semibold">Prompt library</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          How the AI is asked. Change the wording here rather than in code — the previous
          version is always kept, and a shipped default can always be put back.
        </p>
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <div className="w-[240px] space-y-1.5">
          <Label htmlFor="prompt-scope">Used for</Label>
          <Select
            value={scope}
            onValueChange={(v) => {
              setScope(v)
              setDirty(false)
              setProblems([])
            }}
          >
            <SelectTrigger id="prompt-scope">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {scopes.map((s) => (
                <SelectItem key={s.key} value={s.key}>
                  {s.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {forScope.length > 1 && (
          <div className="w-[200px] space-y-1.5">
            <Label htmlFor="prompt-pick">Template</Label>
            <Select
              value={String(selectedId ?? "")}
              onValueChange={(v) => {
                const found = forScope.find((p) => p.id === Number(v))
                if (found) {
                  setSelectedId(found.id)
                  setBody(found.body)
                  setDirty(false)
                }
              }}
            >
              <SelectTrigger id="prompt-pick">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {forScope.map((p) => (
                  <SelectItem key={p.id} value={String(p.id)}>
                    {p.name}
                    {p.is_active ? " (in use)" : ""}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}
      </div>

      {spec && <p className="text-xs text-muted-foreground">{spec.description}</p>}

      {spec && (
        <div className="flex flex-wrap gap-1">
          {spec.variables.map((v) => (
            <Badge
              key={v}
              variant={spec.required.includes(v) ? "secondary" : "outline"}
              className="cursor-pointer font-mono text-[11px]"
              onClick={() => {
                setBody((prev) => `${prev}{{${v}}}`)
                setDirty(true)
              }}
            >
              {`{{${v}}}`}
              {spec.required.includes(v) ? " *" : ""}
            </Badge>
          ))}
        </div>
      )}

      <Textarea
        value={body}
        onChange={(e) => {
          setBody(e.target.value)
          setDirty(true)
        }}
        spellCheck={false}
        className="min-h-[220px] font-mono text-xs"
        aria-label="Prompt text"
      />

      {problems.map((p) => (
        <p key={p} className="text-xs text-[color:var(--warn)]">
          {p}
        </p>
      ))}

      <div className="flex flex-wrap items-center gap-2">
        <Button onClick={() => void save()} disabled={!dirty || !selected}>
          Save
        </Button>
        {selected && !selected.is_active && (
          <Button
            variant="outline"
            onClick={async () => {
              await activatePrompt(selected.id)
              await refresh(selected.id)
              toast.success("This template is now in use")
            }}
          >
            Use this one
          </Button>
        )}
        {selected?.is_default && (
          <Button
            variant="ghost"
            onClick={async () => {
              const restored = await restorePromptDefault(selected.id)
              setBody(restored.prompt.body)
              setDirty(false)
              setProblems([])
              toast.success("Default restored")
            }}
          >
            Restore default
          </Button>
        )}
        {dirty && (
          <span className="text-xs text-muted-foreground">Unsaved changes</span>
        )}
      </div>
    </section>
  )
}
