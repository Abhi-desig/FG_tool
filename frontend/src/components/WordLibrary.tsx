import { useCallback, useEffect, useRef, useState } from "react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  ApiError,
  type WordOrigin,
  type WordRow,
  downloadWords,
  importWords,
  putWordOverride,
  searchWords,
} from "@/lib/api"
import { saveFile } from "@/lib/saveFile"

/** What the badge in the last column says, and how loud it is. */
const ORIGIN: Record<WordOrigin, { label: string; variant: "default" | "secondary" | "outline" }> = {
  yours: { label: "Yours", variant: "default" },
  trade: { label: "Print trade", variant: "secondary" },
  olam: { label: "Olam", variant: "outline" },
}

/**
 * The offline word library, on the Excel tab because that is where it is used.
 *
 * Deliberately not in Settings beside the glossary. The glossary is a decision
 * the operator makes about one client; this is the vocabulary the tool already
 * has, and the question it answers — "does it know this word, and does it know
 * it correctly?" — is asked in the middle of translating a sheet.
 *
 * Nothing here is editable in place. The panel searches and downloads; a
 * correction is made either on one word, or by editing the spreadsheet and
 * loading it back. Fifty-nine thousand rows is not a table to maintain by hand.
 */
export function WordLibrary() {
  const [query, setQuery] = useState("")
  const [rows, setRows] = useState<WordRow[]>([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [bundled, setBundled] = useState(0)
  const [overrides, setOverrides] = useState(0)
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState(false)

  const fileRef = useRef<HTMLInputElement>(null)

  const load = useCallback(async (search: string, from: number) => {
    try {
      const page = await searchWords(search, from)
      setRows(page.rows)
      setTotal(page.total)
      setOffset(page.offset)
      setBundled(page.bundled)
      setOverrides(page.overrides)
      setDrafts({})
    } catch {
      setRows([])
      setTotal(0)
    }
  }, [])

  // Nothing is fetched until the operator opens the panel: the first call reads
  // a 1.7 MB file off disk, and a translator screen must not pay for something
  // nobody looked at. Debounced so typing "visiting card" is one search rather
  // than thirteen — including the first one, where 200ms is imperceptible.
  useEffect(() => {
    if (!open) return
    const timer = setTimeout(() => void load(query, 0), 200)
    return () => clearTimeout(timer)
  }, [query, open, load])

  const save = async (source: string) => {
    const target = (drafts[source] ?? "").trim()
    if (!target) return
    try {
      await putWordOverride(source, target)
      toast.success(`“${source}” is now ${target}`, {
        description: "Every later sheet uses this, offline and free.",
      })
      await load(query, offset)
    } catch {
      toast.error("Could not save that word")
    }
  }

  const download = async () => {
    try {
      saveFile(await downloadWords(), "word-library.xlsx")
    } catch {
      toast.error("Could not download the word library")
    }
  }

  const upload = async (file: File) => {
    setBusy(true)
    setNotice(null)
    try {
      const result = await importWords(file)
      // Says what actually changed, not how many rows were in the file. A
      // round trip of the whole library reports "nothing changed", which is
      // the correct and reassuring answer.
      const changed = result.added + result.updated
      setNotice(
        changed === 0
          ? `Read ${result.rows.toLocaleString()} rows — nothing had been changed, so nothing was saved.`
          : `${changed.toLocaleString()} word${changed === 1 ? "" : "s"} corrected` +
            ` · ${result.unchanged.toLocaleString()} left as they were` +
            (result.skipped ? ` · ${result.skipped.toLocaleString()} skipped` : ""),
      )
      await load(query, 0)
    } catch (err: unknown) {
      setNotice(err instanceof ApiError ? err.message : "Could not read that file.")
    } finally {
      setBusy(false)
      if (fileRef.current) fileRef.current.value = ""
    }
  }

  if (!open) {
    return (
      <section className="rounded-xl border bg-card p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-medium">Word library</h2>
            <p className="mt-1 text-xs text-muted-foreground">
              The English → Malayalam words this tool already knows. Search it, correct
              it, or download it as a spreadsheet.
            </p>
          </div>
          <Button variant="outline" onClick={() => setOpen(true)}>
            Open
          </Button>
        </div>
      </section>
    )
  }

  return (
    <section className="space-y-4 rounded-xl border bg-card p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">Word library</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            {bundled.toLocaleString()} words, on this machine. A cell that is one of
            these is answered from here and never sent to the model — so it is exact,
            free, and the same every time.
          </p>
        </div>
        <Button variant="ghost" size="sm" onClick={() => setOpen(false)}>
          Close
        </Button>
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-[220px] flex-1 space-y-1.5">
          <Label htmlFor="word-search">Search</Label>
          <Input
            id="word-search"
            value={query}
            placeholder="standee, visiting card, matte…"
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <Button variant="outline" onClick={() => void download()}>
          Download as Excel
        </Button>
        <Button
          variant="outline"
          disabled={busy}
          onClick={() => fileRef.current?.click()}
        >
          {busy ? "Loading…" : "Load edited file"}
        </Button>
        <input
          ref={fileRef}
          type="file"
          accept=".xlsx,.xlsm"
          className="sr-only"
          onChange={(e) => {
            const picked = e.target.files?.[0]
            if (picked) void upload(picked)
          }}
        />
      </div>

      {notice && <p className="text-xs text-muted-foreground">{notice}</p>}

      {overrides > 0 && (
        <p className="text-xs text-muted-foreground">
          {overrides.toLocaleString()} word{overrides === 1 ? " has" : "s have"} been
          corrected by you. Those corrections survive a future update to the library.
        </p>
      )}

      {rows.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          No word matches that. Type it with its Malayalam into this client&rsquo;s
          glossary instead, and it will be locked wherever it appears.
        </p>
      ) : (
        <div className="overflow-x-auto">
          {/*
            `table-fixed` is doing real work, not tidying. Under the default
            auto layout a single long Olam gloss claimed 940px of a 958px table
            and squeezed the Malayalam box — the one field that has to be
            readable and typeable — down to 70. Fixed layout makes the widths
            below binding and lets the gloss wrap inside its own column.
          */}
          <Table className="min-w-[880px] table-fixed">
            <TableHeader>
              {/*
                Fixed widths, not proportional. Olam's "other meanings" can be a
                paragraph, and a percentage layout let it squeeze the Malayalam
                box down to about fifty pixels — the one column the operator has
                to be able to read and type in.
              */}
              <TableRow>
                <TableHead className="w-[180px]">English</TableHead>
                <TableHead className="w-[260px]">Malayalam</TableHead>
                <TableHead className="w-[320px]">Other meanings</TableHead>
                <TableHead className="w-[110px]">Where from</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={row.source}>
                  <TableCell className="align-top">{row.source}</TableCell>
                  <TableCell className="align-top">
                    <div className="flex items-center gap-2">
                      <Input
                        value={drafts[row.source] ?? row.target}
                        className="malayalam h-8 py-1"
                        aria-label={`Malayalam for ${row.source}`}
                        onChange={(e) =>
                          setDrafts((prev) => ({ ...prev, [row.source]: e.target.value }))
                        }
                        onKeyDown={(e) => e.key === "Enter" && void save(row.source)}
                      />
                      {drafts[row.source] !== undefined &&
                        drafts[row.source] !== row.target && (
                          <Button
                            size="sm"
                            className="h-8 px-2 text-xs"
                            onClick={() => void save(row.source)}
                          >
                            Save
                          </Button>
                        )}
                    </div>
                    {row.bundled && (
                      <p className="mt-1 text-xs text-muted-foreground">
                        was <span className="malayalam">{row.bundled}</span>
                      </p>
                    )}
                  </TableCell>
                  <TableCell className="align-top">
                    {/* Reference, not the answer. Kept quiet and clipped to two
                        lines so a long Olam gloss cannot own the row. */}
                    <span className="malayalam line-clamp-2 text-xs text-muted-foreground">
                      {row.alternatives.join(" · ")}
                    </span>
                  </TableCell>
                  <TableCell className="align-top">
                    <Badge variant={ORIGIN[row.origin].variant} className="text-[11px]">
                      {ORIGIN[row.origin].label}
                    </Badge>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      {total > rows.length && (
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span>
            {offset + 1}–{offset + rows.length} of {total.toLocaleString()}
          </span>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={offset === 0}
              onClick={() => void load(query, Math.max(0, offset - rows.length))}
            >
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={offset + rows.length >= total}
              onClick={() => void load(query, offset + rows.length)}
            >
              Next
            </Button>
          </div>
        </div>
      )}
    </section>
  )
}
