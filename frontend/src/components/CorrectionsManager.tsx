import { useCallback, useEffect, useRef, useState } from "react"
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
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { useConfirm } from "@/lib/useConfirm"
import {
  ApiError,
  type ClientRow,
  type Correction,
  deleteCorrection,
  downloadCorrections,
  importCorrections,
  listClients,
  listCorrections,
  putCorrections,
} from "@/lib/api"

/** One page. There can be tens of thousands of these; the browser holds a page. */
const PAGE = 100

/**
 * The corrections memory — whole cells the operator has already approved.
 *
 * Deliberately not the glossary, and shown next to it so the difference is
 * visible: the glossary locks a *phrase* wherever it appears in a sentence,
 * this remembers an *entire cell* so the second member list never repeats the
 * first one's spelling (ADR-029).
 *
 * Shop-wide unless scoped to a client, because a house name written by sound is
 * right for every client and the shop sees a given co-operative's sheet about
 * once a year — a purely per-client memory would be empty exactly when it
 * mattered.
 */
export function CorrectionsManager() {
  const [clients, setClients] = useState<ClientRow[]>([])
  const [scope, setScope] = useState<string>("all")
  const [rows, setRows] = useState<Correction[]>([])
  const [total, setTotal] = useState(0)
  const [query, setQuery] = useState("")
  const [drafts, setDrafts] = useState<Record<number, string>>({})
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const { pending: confirming, confirm } = useConfirm()

  const fileRef = useRef<HTMLInputElement>(null)
  const clientId = scope === "all" ? null : Number(scope)

  useEffect(() => {
    listClients()
      .then(setClients)
      .catch(() => setClients([]))
  }, [])

  const load = useCallback(
    async (offset = 0) => {
      try {
        const page = await listCorrections({ clientId, q: query, limit: PAGE, offset })
        setRows((current) => (offset ? [...current, ...page.corrections] : page.corrections))
        setTotal(page.total)
      } catch {
        toast.error("Could not read the corrections")
      }
    },
    [clientId, query],
  )

  // Searching is done by the server. Filtering 40,000 rows in the browser would
  // mean fetching 40,000 rows first, which is the version of this that does not
  // survive its first real member list.
  useEffect(() => {
    const timer = setTimeout(() => void load(0), 250)
    return () => clearTimeout(timer)
  }, [load])

  const save = useCallback(
    async (row: Correction) => {
      const target = (drafts[row.id] ?? row.target).trim()
      if (!target || target === row.target) return
      try {
        await putCorrections(clientId, [{ source: row.source, target }])
        setDrafts((current) => {
          const next = { ...current }
          delete next[row.id]
          return next
        })
        await load(0)
        toast.success("Correction updated")
      } catch (err: unknown) {
        toast.error(err instanceof ApiError ? err.message : "Could not save that")
      }
    },
    [drafts, clientId, load],
  )

  const remove = useCallback(
    async (row: Correction) => {
      // Two clicks, not one. A deleted correction is a spelling the shop has
      // to rediscover on the next sheet.
      if (!confirm(row.id)) return
      try {
        await deleteCorrection(row.id, clientId)
        await load(0)
      } catch {
        toast.error("Could not remove that correction")
      }
    },
    [confirm, clientId, load],
  )

  const loadFile = useCallback(
    async (file: File) => {
      setBusy(true)
      setNotice(null)
      try {
        const result = await importCorrections(file, clientId)
        setNotice(
          `Added ${result.added}, updated ${result.updated}` +
            (result.skipped ? `, skipped ${result.skipped}` : "") +
            (result.truncated ? " — the file was longer than the limit" : "") +
            ".",
        )
        await load(0)
      } catch (err: unknown) {
        // Kept on screen rather than in a toast: a refused import names the
        // thing the operator has to fix in their file, and a toast is gone in
        // four seconds.
        setNotice(err instanceof ApiError ? err.message : "Could not read that file.")
      } finally {
        setBusy(false)
        if (fileRef.current) fileRef.current.value = ""
      }
    },
    [clientId, load],
  )

  const save_file = useCallback(async () => {
    try {
      const blob = await downloadCorrections(clientId)
      const url = URL.createObjectURL(blob)
      const link = document.createElement("a")
      link.href = url
      link.download = "corrections.xlsx"
      link.click()
      URL.revokeObjectURL(url)
    } catch {
      toast.error("Could not download the corrections")
    }
  }, [clientId])

  return (
    <section className="space-y-4 rounded-xl border bg-card p-4">
      <div>
        <h2 className="text-lg font-semibold">Remembered corrections</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          Every Malayalam cell you fix in the Excel translator is kept here. The next
          sheet fills those cells in by itself — offline, free, and without asking the
          model again. Corrections apply to every client unless you scope them to one.
        </p>
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <div className="w-[240px] space-y-1.5">
          <Label htmlFor="corrections-scope">Applies to</Label>
          <Select value={scope} onValueChange={setScope}>
            <SelectTrigger id="corrections-scope">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All clients — shop-wide</SelectItem>
              {clients.map((c) => (
                <SelectItem key={c.id} value={String(c.id)}>
                  {c.name} only
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="min-w-[200px] flex-1 space-y-1.5">
          <Label htmlFor="corrections-search">Search</Label>
          <Input
            id="corrections-search"
            value={query}
            placeholder="An English word or a Malayalam one"
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>

        <Button variant="outline" disabled={busy} onClick={() => fileRef.current?.click()}>
          Load from Excel
        </Button>
        <Button variant="outline" onClick={() => void save_file()} disabled={total === 0}>
          Download as Excel
        </Button>
        <input
          ref={fileRef}
          type="file"
          accept=".xlsx,.xlsm"
          className="sr-only"
          onChange={(e) => {
            const picked = e.target.files?.[0]
            if (picked) void loadFile(picked)
          }}
        />
      </div>

      <p className="text-xs text-muted-foreground">
        A corrections file is two columns: the English in the first, the Malayalam in
        the second. Download the current list to see the shape.
      </p>

      {notice && (
        <p role="status" className="text-sm text-[color:var(--warn)]">
          {notice}
        </p>
      )}

      {rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          {query
            ? "Nothing matches that."
            : "Nothing remembered yet. Fix a cell in the Excel translator and export — it will appear here."}
        </p>
      ) : (
        <>
          <div className="overflow-x-auto rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead scope="col">English</TableHead>
                  <TableHead scope="col">Malayalam</TableHead>
                  <TableHead scope="col">Learned</TableHead>
                  <TableHead scope="col" className="w-[110px]" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((row) => (
                  <TableRow key={row.id}>
                    <TableCell className="align-top text-sm">{row.source}</TableCell>
                    <TableCell>
                      <Input
                        className="malayalam h-9"
                        aria-label={`Malayalam for ${row.source}`}
                        value={drafts[row.id] ?? row.target}
                        onChange={(e) =>
                          setDrafts((c) => ({ ...c, [row.id]: e.target.value }))
                        }
                        onBlur={() => void save(row)}
                        onKeyDown={(e) => e.key === "Enter" && void save(row)}
                      />
                    </TableCell>
                    <TableCell className="align-top text-xs text-muted-foreground">
                      {row.updated_at.slice(0, 10)}
                      {row.origin === "claude-kept" && (
                        <Badge variant="outline" className="ml-2 text-[11px]">
                          from a check
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell className="align-top">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => void remove(row)}
                        aria-label={`Remove the correction for ${row.source}`}
                      >
                        {confirming === row.id ? "Click again" : "Remove"}
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>

          <div className="flex items-center gap-3">
            <p className="text-xs text-muted-foreground">
              Showing {rows.length.toLocaleString()} of {total.toLocaleString()}.
            </p>
            {rows.length < total && (
              <Button variant="outline" size="sm" onClick={() => void load(rows.length)}>
                Show more
              </Button>
            )}
          </div>
        </>
      )}
    </section>
  )
}
