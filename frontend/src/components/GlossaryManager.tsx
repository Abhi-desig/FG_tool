import { useCallback, useEffect, useState } from "react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  type ClientRow,
  type GlossaryTerm,
  addClient,
  deleteTerm,
  getGlossary,
  listClients,
  putGlossary,
} from "@/lib/api"

/**
 * Per-client glossary. Scoped, never global — client A's brand terms must not
 * leak into client B's catalogue (ROADMAP.md Phase 3).
 */
export function GlossaryManager() {
  const [clients, setClients] = useState<ClientRow[]>([])
  const [active, setActive] = useState<string>("")
  const [terms, setTerms] = useState<GlossaryTerm[]>([])
  const [newName, setNewName] = useState("")
  const [source, setSource] = useState("")
  const [target, setTarget] = useState("")

  const refreshClients = useCallback(async () => {
    const rows = await listClients().catch(() => [])
    setClients(rows)
    if (rows.length && !active) setActive(String(rows[0].id))
  }, [active])

  useEffect(() => {
    void refreshClients()
  }, [refreshClients])

  useEffect(() => {
    if (!active) return
    getGlossary(Number(active))
      .then(setTerms)
      .catch(() => setTerms([]))
  }, [active])

  const createClient = async () => {
    const name = newName.trim()
    if (!name) return
    try {
      const made = await addClient(name)
      setNewName("")
      await refreshClients()
      setActive(String(made.id))
    } catch {
      toast.error("Could not add that client")
    }
  }

  const addTerm = async () => {
    if (!active || !source.trim() || !target.trim()) return
    try {
      setTerms(
        await putGlossary(Number(active), [
          { source_term: source.trim(), target_term: target.trim() },
        ]),
      )
      setSource("")
      setTarget("")
    } catch {
      toast.error("Could not save that term")
    }
  }

  const remove = async (id?: number) => {
    if (!active || id == null) return
    setTerms(await deleteTerm(Number(active), id))
  }

  return (
    <section className="space-y-4 rounded-xl border bg-card p-4">
      <div>
        <h2 className="text-lg font-semibold">Clients and glossary</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          Approved terms per client. Correcting a term once fixes every later use of it —
          and a client's terms are never applied to another client's sheet.
        </p>
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <div className="w-[220px] space-y-1.5">
          <Label htmlFor="glossary-client">Client</Label>
          <Select value={active} onValueChange={setActive}>
            <SelectTrigger id="glossary-client">
              <SelectValue placeholder="No clients yet" />
            </SelectTrigger>
            <SelectContent>
              {clients.map((c) => (
                <SelectItem key={c.id} value={String(c.id)}>
                  {c.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="w-[220px] space-y-1.5">
          <Label htmlFor="new-client">Add a client</Label>
          <Input
            id="new-client"
            value={newName}
            placeholder="Client name"
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && void createClient()}
          />
        </div>
        <Button variant="outline" onClick={() => void createClient()} disabled={!newName.trim()}>
          Add
        </Button>
      </div>

      {active && (
        <>
          <div className="flex flex-wrap items-end gap-3 border-t pt-4">
            <div className="min-w-[180px] flex-1 space-y-1.5">
              <Label htmlFor="term-source">English term</Label>
              <Input
                id="term-source"
                value={source}
                placeholder="coconut oil"
                onChange={(e) => setSource(e.target.value)}
              />
            </div>
            <div className="min-w-[180px] flex-1 space-y-1.5">
              <Label htmlFor="term-target">Approved Malayalam</Label>
              <Input
                id="term-target"
                value={target}
                placeholder="വെളിച്ചെണ്ണ"
                className="malayalam"
                onChange={(e) => setTarget(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && void addTerm()}
              />
            </div>
            <Button onClick={() => void addTerm()} disabled={!source.trim() || !target.trim()}>
              Lock term
            </Button>
          </div>

          {terms.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              No terms yet. Add the words this client always gets wrong — brand names,
              product words, units.
            </p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-muted-foreground">
                  <th className="py-1.5 font-medium">English</th>
                  <th className="py-1.5 font-medium">Malayalam</th>
                  <th className="py-1.5" />
                </tr>
              </thead>
              <tbody>
                {terms.map((t) => (
                  <tr key={t.id ?? t.source_term} className="border-t">
                    <td className="py-2">{t.source_term}</td>
                    <td className="malayalam py-2">{t.target_term}</td>
                    <td className="py-2 text-right">
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 px-2 text-xs"
                        onClick={() => void remove(t.id)}
                      >
                        Remove
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="text-xs text-muted-foreground">
            <Badge variant="outline" className="mr-1 text-[11px]">
              how it works
            </Badge>
            Locked terms are lifted out of the sentence before translation and put back
            afterwards, so the model never gets a chance to mistranslate them.
          </p>
        </>
      )}
    </section>
  )
}
