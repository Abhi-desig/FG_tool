import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { toast } from "sonner"

import { CategoricalApproval } from "@/components/CategoricalApproval"
import { JobProgress } from "@/components/JobProgress"
import { NameColumns } from "@/components/NameColumns"
import { WordLibrary } from "@/components/WordLibrary"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
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
  type ColumnClass,
  type Job,
  type ReviewRow,
  type ReviewSummary,
  type SheetInfo,
  type TranslationResult,
  aiStatus,
  exportSheet,
  inspectSheet,
  listClients,
  startTranslate,
} from "@/lib/api"
import { saveFile } from "@/lib/saveFile"

/**
 * Where the last-used client is remembered.
 *
 * `sessionStorage`, not `localStorage`: the glossary is the shop's guard against
 * a weak model, and a client silently remembered from last week is a worse
 * failure than one that has to be picked again today. It survives navigating
 * between screens, which is the case that actually bit (NEXT.md 1.7).
 */
const LAST_CLIENT_KEY = "focus.lastClientId"

function rememberedClient(): string {
  try {
    return sessionStorage.getItem(LAST_CLIENT_KEY) ?? "none"
  } catch {
    // Private browsing, or storage disabled. The safe default is no glossary.
    return "none"
  }
}

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
  /**
   * What each column holds. Seeded from the server's suggestion the first time a
   * sheet is inspected, then owned by the operator — the sheet is re-inspected
   * on every client change, and re-seeding there would undo their answer under
   * them (ADR-035).
   */
  const [columnClasses, setColumnClasses] = useState<Record<string, ColumnClass>>({})
  const seededFor = useRef<string | null>(null)
  /**
   * Bumped to force a re-inspect of the same file. Approving a categorical
   * column changes what the sheet costs and what the panel should still show,
   * and `setFile(file)` cannot trigger that — it is the same object reference.
   */
  const [refresh, setRefresh] = useState(0)
  const [clients, setClients] = useState<ClientRow[]>([])
  const [clientId, setClientId] = useState<string>(rememberedClient)
  const [job, setJob] = useState<Job | null>(null)
  const [rows, setRows] = useState<ReviewRow[]>([])
  const [edits, setEdits] = useState<Record<string, string>>({})
  const [onlyAttention, setOnlyAttention] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)
  /**
   * Whether to pay Claude to check the offline model's Malayalam.
   *
   * Deliberately **not** remembered between sheets, unlike the client glossary:
   * this one spends money and sends the sheet off the machine, so it is chosen
   * per job or not at all. Off is the safe default and the free one.
   */
  const [checkWithClaude, setCheckWithClaude] = useState(false)
  /** Set when the month's AI budget is already spent — see `AiPhotoEdit`. */
  const [overBudget, setOverBudget] = useState(false)
  const [overBudgetOk, setOverBudgetOk] = useState(false)
  /**
   * Whether the operator's corrections are written to the memory on export.
   *
   * On by default. A memory that only fills when a second button is pressed
   * stays empty, which is the same as not having the feature at all.
   */
  const [remember, setRemember] = useState(true)

  const inputRef = useRef<HTMLInputElement>(null)
  /**
   * Which inspect request is current.
   *
   * The sheet is re-inspected whenever the client changes, and a slow answer
   * for the old client must not overwrite a newer one.
   */
  const inspectRun = useRef(0)

  useEffect(() => {
    listClients()
      .then(setClients)
      .catch(() => setClients([]))
    aiStatus()
      .then((status) => setOverBudget(status.budget.over_budget))
      .catch(() => setOverBudget(false))
  }, [])

  // The grid arrives with the finished job.
  useEffect(() => {
    if (job?.status !== "done" || !job.result) return
    const result = job.result as unknown as TranslationResult
    if (!result.rows) return
    setRows(result.rows)
    setEdits(Object.fromEntries(result.rows.map((r) => [r.key, r.translation])))
  }, [job])

  const accept = useCallback((next: File) => {
    setError(null)
    setJob(null)
    setRows([])
    setEdits({})
    setInfo(null)
    setColumnClasses({})
    seededFor.current = null
    setFile(next)
    setCheckWithClaude(false)
    setOverBudgetOk(false)
  }, [])

  /**
   * Inspect on every (sheet, client) pair, not just on drop.
   *
   * The quote depends on the client: the glossary and the corrections memory
   * decide how many rows actually reach the model. Inspecting only on drop —
   * before the client dropdown has even been touched — meant the estimate could
   * never account for either, and it is the number the operator uses to decide
   * whether to spend.
   *
   * The cost is re-uploading the workbook when the dropdown changes. That
   * happens once per sheet, and `translate` re-parses it anyway.
   */
  useEffect(() => {
    if (!file) return
    const run = ++inspectRun.current
    inspectSheet(file, clientId === "none" ? null : Number(clientId))
      .then((next) => {
        if (inspectRun.current !== run) return
        setInfo(next)
        // Once per file — `accept` clears this, nothing else does.
        if (seededFor.current === null) {
          seededFor.current = file.name
          setColumnClasses(
            Object.fromEntries(
              (next.columns ?? []).map((c) => [c.key, c.cls]),
            ),
          )
        }
      })
      .catch((err: unknown) => {
        if (inspectRun.current !== run) return
        setInfo(null)
        setError(err instanceof ApiError ? err.message : "Could not read that sheet.")
      })
  }, [file, clientId, refresh])

  const chooseClient = useCallback((next: string) => {
    setClientId(next)
    try {
      sessionStorage.setItem(LAST_CLIENT_KEY, next)
    } catch {
      // Not being able to remember it is not a reason to refuse the choice.
    }
  }, [])

  // A client that no longer exists must not silently mean "no glossary".
  useEffect(() => {
    if (clientId === "none" || clients.length === 0) return
    if (!clients.some((c) => String(c.id) === clientId)) setClientId("none")
  }, [clients, clientId])

  const run = useCallback(async () => {
    if (!file) return
    setError(null)
    try {
      setJob(
        await startTranslate(file, clientId === "none" ? null : Number(clientId), {
          checkWithClaude,
          overBudgetOk,
          columnClasses,
        }),
      )
    } catch (err: unknown) {
      setError(err instanceof ApiError ? err.message : "Could not start translating.")
    }
  }, [file, clientId, checkWithClaude, overBudgetOk, columnClasses])

  const download = useCallback(async () => {
    if (!job) return
    try {
      const { blob, remembered } = await exportSheet(job.id, edits, {
        remember,
        clientId: clientId === "none" ? null : Number(clientId),
      })
      saveFile(
        blob,
        (file?.name ?? "sheet").replace(/\.xlsx?$/i, "") + "-malayalam.xlsx",
      )
      toast.success("Exported", {
        description: remembered
          ? `Formatting and formulas untouched. ${remembered} correction${
              remembered === 1 ? "" : "s"
            } remembered — they will fill in by themselves next time.`
          : "Formatting and formulas untouched.",
      })
    } catch {
      toast.error("Could not export that sheet")
    }
  }, [job, edits, file, remember, clientId])

  /**
   * How many cells the operator has changed from what the offline model said.
   *
   * Advisory only, and said so on screen: the server recomputes this from the
   * job's own rows when it exports, because the browser's edit map is seeded
   * with every row and a diff that went wrong here would write thousands of
   * unreviewed machine translations into permanent memory.
   */
  const willRemember = useMemo(
    () =>
      rows.filter((r) => {
        const text = (edits[r.key] ?? "").trim()
        return text && text !== (r.offline_translation ?? r.translation).trim()
      }).length,
    [rows, edits],
  )

  const attention = useMemo(() => rows.filter((r) => r.needs_attention).length, [rows])
  const mustFix = useMemo(() => rows.filter((r) => r.must_fix).length, [rows])
  const visible = useMemo(
    () => (onlyAttention ? rows.filter((r) => r.needs_attention) : rows),
    [rows, onlyAttention],
  )

  /** Whether the finished job actually had a glossary in force (NEXT.md 1.7). */
  const glossaryApplied = useMemo(() => {
    const result = job?.result as unknown as TranslationResult | undefined
    return result?.glossary_applied ?? true
  }, [job])

  /** How the paid check went, when one was asked for. */
  const review = useMemo<ReviewSummary | null>(() => {
    const result = job?.result as unknown as TranslationResult | undefined
    return result?.review?.requested ? result.review : null
  }, [job])

  const corrected = useMemo(
    () => rows.filter((r) => r.verify_corrected).length,
    [rows],
  )

  /**
   * How much of this sheet is already answered, and by which layer.
   *
   * Three sources now, and they are worth naming separately: the operator's own
   * corrections and the client's glossary are their work, while the word
   * library is the tool's. Lumping them together would hide the fact that the
   * shop's own approved terms are being applied at all.
   */
  const covered = useMemo(() => {
    const memory = info?.from_memory ?? 0
    const glossary = info?.from_glossary ?? 0
    const library = info?.from_dictionary ?? 0
    const parts: string[] = []
    if (memory) parts.push(`${memory.toLocaleString()} from your corrections`)
    if (glossary) parts.push(`${glossary.toLocaleString()} from the glossary`)
    if (library) parts.push(`${library.toLocaleString()} from the word library`)
    return { total: memory + glossary + library, detail: parts.join(", ") }
  }, [info])

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
        {/*
          This badge is a promise, so it has to follow the choice below. Every
          other screen in this app is offline unconditionally; this one stops
          being so the moment the Claude check is switched on, and a badge still
          reading "Offline · free" over a job that is spending money and sending
          members' addresses to Anthropic would be the worst kind of wrong.
        */}
        <Badge variant="secondary" className="font-normal">
          {checkWithClaude ? "Checked by Claude · paid" : "Offline · free"}
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

      {/*
        Shown before a sheet is loaded and again beneath the review grid. The
        two moments the operator wants it are "what does this thing already
        know?" and "why did it say that?", and neither is served by hiding it
        in Settings.
      */}
      {!file && <WordLibrary />}

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

      {/* Asked before Translate, because it changes what the run does — and
          hidden once the grid exists, where it would only be confusing. */}
      {file && info && !job && (
        <>
          <NameColumns
            columns={info.columns ?? []}
            picked={columnClasses}
            onChange={setColumnClasses}
          />
          {/* Only shown while there is something left to approve. Once locked,
              these values come from the glossary and this panel disappears. */}
          <CategoricalApproval
            columns={info.categorical ?? []}
            clientId={clientId === "none" ? null : Number(clientId)}
            // Re-inspect so the approved values leave this panel and turn up in
            // the "already known" count above.
            onApproved={() => setRefresh((n) => n + 1)}
          />
        </>
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
            {covered.total > 0 && (
              <p className="mt-1 text-xs text-[color:var(--ok)]">
                {covered.total.toLocaleString()} of these{" "}
                {covered.total === 1 ? "is" : "are"} already known
                {covered.detail && ` (${covered.detail})`} — filled in free, without
                the model.
              </p>
            )}
          </div>

          <div className="w-[220px] space-y-1.5">
            <Label htmlFor="client">Client glossary</Label>
            <Select value={clientId} onValueChange={chooseClient}>
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

          {/*
            The only paid control on this screen, and the only one that sends
            the sheet off the machine. A Select rather than a toggle because a
            mis-click here costs money and the operator's members' privacy —
            both options say plainly which they are.
          */}
          <div className="w-[240px] space-y-1.5">
            <Label htmlFor="check">Malayalam check</Label>
            <Select
              value={checkWithClaude ? "claude" : "offline"}
              onValueChange={(v) => setCheckWithClaude(v === "claude")}
            >
              <SelectTrigger id="check">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="offline">Offline only — free</SelectItem>
                <SelectItem value="claude" disabled={!info.verify?.configured}>
                  {info.verify?.configured
                    ? `Check with Claude — about ₹${info.verify.cost_rupees.toFixed(0)}`
                    : "Check with Claude — no API key yet"}
                </SelectItem>
              </SelectContent>
            </Select>
          </div>

          {/*
            The backend has always accepted this and the fetch layer has always
            sent it; nothing on screen ever set it, so an over-budget operator
            got a grid where nothing was checked and no way to say "do it
            anyway". Same shape as the panel in `AiPhotoEdit`.
          */}
          {checkWithClaude && overBudget && (
            <div className="w-full space-y-2 rounded-lg border border-[color:var(--warn)]/40 bg-[color:var(--warn)]/10 p-3">
              <p className="text-sm text-[color:var(--warn)]">
                This month's AI budget is already spent. The check will be
                refused unless you say otherwise.
              </p>
              <div className="flex items-center gap-2 text-sm">
                <Checkbox
                  id="excel-over-budget"
                  checked={overBudgetOk}
                  onCheckedChange={(v) => setOverBudgetOk(v === true)}
                />
                <Label htmlFor="excel-over-budget" className="font-normal">
                  Spend past the budget anyway
                </Label>
              </div>
            </div>
          )}

          <Button
            onClick={() => void run()}
            disabled={
              job?.status === "running" ||
              job?.status === "queued" ||
              (checkWithClaude && overBudget && !overBudgetOk)
            }
          >
            Translate
          </Button>

          {/*
            NEXT.md 1.7: translating with the glossary off is the most likely
            operator error, and it produces exactly the mistranslations the
            glossary exists to prevent. The safe default stays — but it is no
            longer silent.
          */}
          {clientId === "none" && (
            <p className="w-full text-sm text-[color:var(--warn)]">
              No client selected — no client's locked terms will be applied, and
              the model will guess at product names. Your remembered corrections
              still apply. Pick a client above if this sheet belongs to one.
            </p>
          )}

          {/*
            Said before the money is spent, not after. Everything else in this
            app is offline, so the operator has no reason to assume this is not
            — and on a member list the text being sent is people's names and
            home addresses.
          */}
          {checkWithClaude && info.verify && (
            <Alert className="w-full">
              <AlertTitle>
                This sends {info.verify.rows.toLocaleString()} cells to Anthropic
              </AlertTitle>
              <AlertDescription>
                The English and the offline Malayalam for every word-bearing cell
                leave this machine — on a member list that is names, guardian
                names and home addresses. About ₹
                {info.verify.cost_rupees.toFixed(0)} at today's prices, over{" "}
                {info.verify.requests.toLocaleString()} requests; the real figure
                is charged on what is actually used and appears in Settings when
                the job finishes. Numbers, dates and membership codes are not
                sent. Nothing is written to a file either way until you export.
              </AlertDescription>
            </Alert>
          )}
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
          {!glossaryApplied && (
            <p
              role="alert"
              className="rounded-lg border border-[color:var(--warn)]/50 bg-[color:var(--warn)]/10 p-3 text-sm"
            >
              This sheet was translated <strong>without a glossary</strong>. Product
              names and print terms were guessed by the model — read every row
              marked below before exporting.
            </p>
          )}

          {review && (
            <Alert
              // A check that failed or half-finished must not read as a clean
              // pass. The operator has to know which rows nothing looked at.
              variant={review.error ? "destructive" : "default"}
              className={
                !review.error && (review.unchecked ?? 0) > 0
                  ? "border-[color:var(--warn)]/50 bg-[color:var(--warn)]/10"
                  : undefined
              }
            >
              <AlertTitle>
                {review.error
                  ? "The Malayalam check did not finish"
                  : `${(review.corrected ?? 0).toLocaleString()} rows changed by Claude`}
              </AlertTitle>
              <AlertDescription>
                {review.error ? (
                  <>
                    {review.error} Every row below is exactly as the offline model
                    left it.
                  </>
                ) : (
                  <>
                    {(review.checked ?? 0).toLocaleString()} rows checked with{" "}
                    {review.model}, costing about ₹
                    {(review.cost_rupees ?? 0).toFixed(2)}.
                    {(review.unchecked ?? 0) > 0 && (
                      <>
                        {" "}
                        <strong>
                          {(review.unchecked ?? 0).toLocaleString()} rows were not
                          checked
                        </strong>{" "}
                        and are as the offline model left them — they are marked
                        below.
                      </>
                    )}{" "}
                    A changed row shows what the offline model had said beneath it.
                  </>
                )}
                {(review.warnings ?? []).map((w) => (
                  <span key={w} className="mt-1 block">
                    {w}
                  </span>
                ))}
              </AlertDescription>
            </Alert>
          )}

          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <p className="text-sm">
                <strong>{rows.length}</strong> rows
                {mustFix > 0 && (
                  <>
                    {" · "}
                    <span className="text-destructive">{mustFix} to fix</span>
                  </>
                )}
                {attention - mustFix > 0 && (
                  <>
                    {" · "}
                    <span className="text-[color:var(--warn)]">
                      {attention - mustFix} to check
                    </span>
                  </>
                )}
                {corrected > 0 && (
                  <>
                    {" · "}
                    <span className="text-muted-foreground">
                      {corrected} changed by Claude
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
            <div className="flex items-center gap-3">
              {/*
                On by default, and the count is advisory: the server recomputes
                it from the job's own rows, because the browser's edit map holds
                every row and a diff that went wrong here would make thousands
                of unreviewed machine translations permanent.
              */}
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Checkbox
                  id="excel-remember"
                  checked={remember}
                  onCheckedChange={(v) => setRemember(v === true)}
                />
                <Label htmlFor="excel-remember" className="font-normal">
                  Remember my corrections
                  {remember && willRemember > 0 && (
                    <span className="text-xs"> (about {willRemember.toLocaleString()})</span>
                  )}
                </Label>
              </div>
              <Button onClick={() => void download()}>Export .xlsx</Button>
            </div>
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
                    // A demonstrable error and a cell that merely cannot be
                    // verified are not the same thing, and a grid where
                    // everything is urgent is a grid where nothing is.
                    className={`border-t align-top ${
                      row.must_fix
                        ? "bg-destructive/10"
                        : row.needs_attention
                          ? "bg-[color:var(--warn)]/10"
                          : ""
                    }`}
                  >
                    <td className="px-3 py-2">
                      <code className="text-xs text-muted-foreground">{row.ref}</code>
                      <div className="text-[11px] text-muted-foreground">{row.sheet}</div>
                    </td>
                    <td className="px-3 py-2">
                      {row.source}
                      {/*
                        A remembered row carries no glossary terms, so the
                        badges cannot hang off `glossary_terms.length` — that
                        would leave the operator no way to tell why the cell was
                        already filled in.
                      */}
                      {(row.glossary_terms.length > 0 ||
                        row.from_memory ||
                        row.from_dictionary ||
                        row.from_name ||
                        row.unresolved) && (
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
                          {row.from_memory && (
                            <Badge variant="secondary" className="text-[11px]">
                              from memory
                            </Badge>
                          )}
                          {row.from_dictionary && (
                            <Badge variant="secondary" className="text-[11px]">
                              from word library
                            </Badge>
                          )}
                          {row.from_name && (
                            <Badge variant="secondary" className="text-[11px]">
                              written by sound
                            </Badge>
                          )}
                          {row.unresolved && (
                            <Badge variant="outline" className="text-[11px]">
                              left in English
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
                      {(row.problems ?? row.warnings).map((w) => (
                        <p key={w} className="mt-1 text-xs text-destructive">
                          {w}
                        </p>
                      ))}
                      {(row.checks ?? []).map((w) => (
                        <p key={w} className="mt-1 text-xs text-[color:var(--warn)]">
                          {w}
                        </p>
                      ))}
                      {/*
                        On a sheet of names a "correction" is usually a whole new
                        line rather than a respelling, so what was replaced is
                        shown rather than discarded — the operator is the one who
                        decides which reading is right.
                      */}
                      {row.verify_corrected && (
                        <p className="mt-1 text-xs text-muted-foreground">
                          <Badge variant="secondary" className="mr-1 text-[11px]">
                            Claude changed this
                          </Badge>
                          was <span className="malayalam">{row.offline_translation}</span>
                        </p>
                      )}
                      {review && row.checked === false && (
                        <p className="mt-1 text-xs text-[color:var(--warn)]">
                          {row.verify_note || "Not checked — as the offline model left it."}
                        </p>
                      )}
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

          {/* The other moment it is wanted: a row reads wrong and the operator
              wants to fix the word itself, for every sheet, not just this one. */}
          <WordLibrary />
        </>
      )}
    </div>
  )
}
