import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { toast } from "sonner"

import { JobProgress } from "@/components/JobProgress"
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
  ApiError,
  type ClientRow,
  type Job,
  type ReviewRow,
  type SheetInfo,
  type TranslationResult,
  exportSheet,
  inspectSheet,
  listClients,
  startTranslate,
} from "@/lib/api"

/**
 * Phase 3. Translate a client spreadsheet, review every row, then export.
 *
 * ROADMAP.md: nothing is written to a file until the operator accepts, and the
 * review grid exists because the model is imperfect — it will drop a word. Rows
 * that need attention are surfaced first rather than left to be noticed.
 */
export function ExcelTranslator() {
  const [file, setFile] = useState<File | null>(null)
  const [info, setInfo] = useState<SheetInfo | null>(null)
  const [clients, setClients] = useState<ClientRow[]>([])
  const [clientId, setClientId] = useState<string>("none")
  const [job, setJob] = useState<Job | null>(null)
  const [rows, setRows] = useState<ReviewRow[]>([])
  const [edits, setEdits] = useState<Record<string, string>>({})
  const [onlyAttention, setOnlyAttention] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)

  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    listClients()
      .then(setClients)
      .catch(() => setClients([]))
  }, [])

  // The grid arrives with the finished job.
  useEffect(() => {
    if (job?.status !== "done" || !job.result) return
    const result = job.result as unknown as TranslationResult
    if (!result.rows) return
    setRows(result.rows)
    setEdits(Object.fromEntries(result.rows.map((r) => [r.key, r.translation])))
  }, [job])

  const accept = useCallback(async (next: File) => {
    setError(null)
    setJob(null)
    setRows([])
    setEdits({})
    setFile(next)
    try {
      setInfo(await inspectSheet(next))
    } catch (err: unknown) {
      setInfo(null)
      setError(err instanceof ApiError ? err.message : "Could not read that sheet.")
    }
  }, [])

  const run = useCallback(async () => {
    if (!file) return
    setError(null)
    try {
      setJob(await startTranslate(file, clientId === "none" ? null : Number(clientId)))
    } catch (err: unknown) {
      setError(err instanceof ApiError ? err.message : "Could not start translating.")
    }
  }, [file, clientId])

  const download = useCallback(async () => {
    if (!job) return
    try {
      const blob = await exportSheet(job.id, edits)
      const url = URL.createObjectURL(blob)
      const link = document.createElement("a")
      link.href = url
      link.download = (file?.name ?? "sheet").replace(/\.xlsx?$/i, "") + "-malayalam.xlsx"
      link.click()
      URL.revokeObjectURL(url)
      toast.success("Exported", { description: "Formatting and formulas untouched." })
    } catch {
      toast.error("Could not export that sheet")
    }
  }, [job, edits, file])

  const attention = useMemo(() => rows.filter((r) => r.needs_attention).length, [rows])
  const visible = useMemo(
    () => (onlyAttention ? rows.filter((r) => r.needs_attention) : rows),
    [rows, onlyAttention],
  )

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Excel translator</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            English → Malayalam, with your approved terms locked. Review every row before
            anything is written.
          </p>
        </div>
        <Badge variant="secondary" className="font-normal">
          Offline · free
        </Badge>
      </header>

      {!file && (
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          onDragOver={(e) => {
            e.preventDefault()
            setDragging(true)
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault()
            setDragging(false)
            const dropped = e.dataTransfer.files[0]
            if (dropped) void accept(dropped)
          }}
          className={`flex min-h-[240px] w-full flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed transition-colors ${
            dragging
              ? "border-primary bg-accent"
              : "border-border bg-card hover:border-primary/50"
          }`}
        >
          <span className="text-base font-medium">Drop a client spreadsheet here</span>
          <span className="text-sm text-muted-foreground">
            or click to browse — .xlsx
          </span>
        </button>
      )}

      <input
        ref={inputRef}
        type="file"
        accept=".xlsx,.xlsm"
        className="sr-only"
        onChange={(e) => {
          const picked = e.target.files?.[0]
          if (picked) void accept(picked)
        }}
      />

      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}

      {file && info && (
        <div className="flex flex-wrap items-end gap-4 rounded-xl border bg-card p-4">
          <div className="min-w-[200px] flex-1">
            <p className="text-sm font-medium">{file.name}</p>
            <p className="mt-1 text-xs text-muted-foreground">
              {info.sheets.length} sheet{info.sheets.length === 1 ? "" : "s"} ·{" "}
              {info.translatable} cells to translate · {info.unique_strings} distinct
              {info.skipped_formulas > 0 &&
                ` · ${info.skipped_formulas} formulas left alone`}
            </p>
          </div>

          <div className="w-[220px] space-y-1.5">
            <Label htmlFor="client">Client glossary</Label>
            <Select value={clientId} onValueChange={setClientId}>
              <SelectTrigger id="client">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none">No glossary</SelectItem>
                {clients.map((c) => (
                  <SelectItem key={c.id} value={String(c.id)}>
                    {c.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <Button
            onClick={() => void run()}
            disabled={job?.status === "running" || job?.status === "queued"}
          >
            Translate
          </Button>
          <Button
            variant="ghost"
            onClick={() => {
              setFile(null)
              setInfo(null)
              setJob(null)
              setRows([])
            }}
          >
            Choose another
          </Button>
        </div>
      )}

      {job && job.status !== "done" && (
        <JobProgress job={job} onUpdate={setJob} />
      )}

      {rows.length > 0 && (
        <>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <p className="text-sm">
                <strong>{rows.length}</strong> rows
                {attention > 0 && (
                  <>
                    {" · "}
                    <span className="text-[color:var(--warn)]">
                      {attention} need attention
                    </span>
                  </>
                )}
              </p>
              {attention > 0 && (
                <Button
                  variant={onlyAttention ? "secondary" : "outline"}
                  size="sm"
                  onClick={() => setOnlyAttention((v) => !v)}
                >
                  {onlyAttention ? "Show all rows" : "Show only these"}
                </Button>
              )}
            </div>
            <Button onClick={() => void download()}>Export .xlsx</Button>
          </div>

          {/* Wide content scrolls inside its own container. */}
          <div className="overflow-x-auto rounded-xl border">
            <table className="w-full min-w-[720px] text-sm">
              <thead className="bg-muted/60">
                <tr className="text-left">
                  <th className="w-24 px-3 py-2 font-medium">Cell</th>
                  <th className="px-3 py-2 font-medium">English</th>
                  <th className="px-3 py-2 font-medium">Malayalam</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((row) => (
                  <tr
                    key={row.key}
                    className={`border-t align-top ${
                      row.needs_attention ? "bg-[color:var(--warn)]/10" : ""
                    }`}
                  >
                    <td className="px-3 py-2">
                      <code className="text-xs text-muted-foreground">{row.ref}</code>
                      <div className="text-[11px] text-muted-foreground">{row.sheet}</div>
                    </td>
                    <td className="px-3 py-2">
                      {row.source}
                      {row.glossary_terms.length > 0 && (
                        <div className="mt-1 flex flex-wrap gap-1">
                          {row.glossary_terms.map((t) => (
                            <Badge key={t} variant="outline" className="text-[11px]">
                              {t}
                            </Badge>
                          ))}
                          {row.glossary_only && (
                            <Badge variant="secondary" className="text-[11px]">
                              from glossary
                            </Badge>
                          )}
                        </div>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      <Input
                        value={edits[row.key] ?? ""}
                        onChange={(e) =>
                          setEdits((prev) => ({ ...prev, [row.key]: e.target.value }))
                        }
                        className="malayalam h-auto py-1.5"
                        aria-label={`Malayalam for ${row.source}`}
                      />
                      {row.warnings.map((w) => (
                        <p key={w} className="mt-1 text-xs text-[color:var(--warn)]">
                          {w}
                        </p>
                      ))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <p className="text-xs text-muted-foreground">
            Nothing has been written yet. Your edits are applied only when you export, and
            formatting, formulas and numbers are left untouched.
          </p>
        </>
      )}
    </div>
  )
}
