/**
 * The only place that talks to the Python API.
 * CLAUDE.md: server state goes through one fetch layer, never scattered `fetch`
 * calls in components.
 */

export type Direction = "to_ascii" | "to_unicode"

/** One character the print font has no glyph for. See features/fonts.py. */
export interface UnconvertibleChar {
  character: string
  count: number
  name: string
  codepoint: string
}

export interface ConvertResult {
  result: string
  direction: Direction
  font: string
  chars_in: number
  chars_out: number
  /** Chiefly emoji — WhatsApp is the primary input to this screen. */
  unconvertible: UnconvertibleChar[]
}

export interface FontInfo {
  font: string
  unicode_entries: number
  ascii_entries: number
  longest_match: number
  warnings: string[]
}

export class ApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = "ApiError"
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...init,
    })
  } catch {
    throw new ApiError("Cannot reach the Focus Toolkit server. Is it running?", 0)
  }

  if (!response.ok) {
    const detail = await response.text()
    let message = `Request failed (${response.status})`
    try {
      const parsed: unknown = JSON.parse(detail)
      if (parsed && typeof parsed === "object" && "detail" in parsed) {
        message = String((parsed as { detail: unknown }).detail)
      }
    } catch {
      // Response was not JSON; keep the generic message.
    }
    throw new ApiError(message, response.status)
  }

  return (await response.json()) as T
}

export function convert(
  text: string,
  direction: Direction,
  signal?: AbortSignal,
): Promise<ConvertResult> {
  return request<ConvertResult>("/api/fonts/convert", {
    method: "POST",
    body: JSON.stringify({ text, direction }),
    signal,
  })
}

export function fontInfo(): Promise<FontInfo> {
  return request<FontInfo>("/api/fonts/info")
}

/** Which features this install can actually serve. See NEXT.md 2.6. */
export interface FeatureAvailability {
  available: string[]
  /** feature name -> why it is unavailable, including the uv command to fix it. */
  unavailable: Record<string, string>
}

export function features(): Promise<FeatureAvailability> {
  return request<FeatureAvailability>("/api/features")
}

// --- Phase 2: images ------------------------------------------------------

export type Verdict = "good" | "caution" | "too_small"
export type PrintUnit = "mm" | "cm" | "inch" | "feet"

export interface PrintClass {
  key: string
  label: string
  viewing: string
  good_dpi: number
  min_dpi: number
}

export interface BestUse {
  print_class: string
  label: string
  viewing: string
  required_dpi: number
  max_w: number
  max_h: number
}

export interface Assessment {
  verdict: Verdict
  headline: string
  detail: string
  effective_dpi: number
  required_dpi: number
  minimum_dpi: number
  print_class: string
  print_class_label: string
  max_w: number
  max_h: number
  unit: string
  crop_fraction: number
  upscale_to: [number, number] | null
  best_use: BestUse[]
}

export interface ImageFacts {
  width: number
  height: number
  mode: string
  format: string | null
  has_alpha: boolean
  embedded_dpi: [number, number] | null
  megapixels: number
}

export type UpscaleScale = "2x" | "4x" | "print"

export interface ScaleOption {
  scale: UpscaleScale
  width: number
  height: number
  megapixels: number
  /** False when the output could not be assembled or written as one file. */
  possible: boolean
  why_not: string | null
}

export interface Inspection {
  facts: ImageFacts
  best_use: BestUse[]
  estimated_upscale_seconds: number
  tiles: number
  scale_options: ScaleOption[]
  device: string
}

export type JobStatus = "queued" | "running" | "done" | "failed" | "cancelled"

export interface Job {
  id: string
  kind: string
  label: string
  status: JobStatus
  step: string
  progress: number
  elapsed: number
  error: string | null
  result: {
    file: string
    media_type: string
    width: number
    height: number
    dpi: number
    bytes: number
    method?: string
    scale?: string
    tiles?: number
    note?: string
  } | null
  /** Cancel was pressed; the current step has not ended yet. */
  cancelling?: boolean
  /** The running step cannot report progress — show motion, not a number. */
  indeterminate?: boolean
  /** The output file has been swept from disk. See SECURITY.md §5. */
  files_deleted?: boolean
  created_at?: number
  finished_at?: number | null
}

export interface ModelRow {
  key: string
  label: string
  licence: string
  kind: string
  available: boolean
  size_mb?: number | null
}

export interface ModelSettings {
  models_dir: string
  free_gb: number
  device: string
  providers: string[]
  models: ModelRow[]
}

export type Preferences = Record<string, string>

export function printClasses(): Promise<PrintClass[]> {
  return request<PrintClass[]>("/api/printsize/classes")
}

export function assessPrint(body: {
  pixels_w: number
  pixels_h: number
  target_w: number
  target_h: number
  unit: PrintUnit
  print_class: string
}): Promise<Assessment> {
  return request<Assessment>("/api/printsize/assess", {
    method: "POST",
    body: JSON.stringify(body),
  })
}

/** Multipart upload — no JSON Content-Type, the browser sets the boundary. */
async function upload<T>(path: string, form: FormData): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, { method: "POST", body: form })
  } catch {
    throw new ApiError("Cannot reach the Focus Toolkit server. Is it running?", 0)
  }
  if (!response.ok) {
    let message = `Upload failed (${response.status})`
    try {
      const body: unknown = await response.json()
      if (body && typeof body === "object" && "detail" in body) {
        message = String((body as { detail: unknown }).detail)
      }
    } catch {
      // keep the generic message
    }
    throw new ApiError(message, response.status)
  }
  return (await response.json()) as T
}

export function inspectImage(file: File, unit: PrintUnit): Promise<Inspection> {
  const form = new FormData()
  form.append("file", file)
  form.append("unit", unit)
  return upload<Inspection>("/api/images/inspect", form)
}

export function startCutout(file: File, dpi: number): Promise<Job> {
  const form = new FormData()
  form.append("file", file)
  form.append("dpi", String(dpi))
  return upload<Job>("/api/images/cutout", form)
}

export function startUpscale(
  file: File,
  scale: UpscaleScale,
  target: [number, number] | null,
  opts: { dpi: number; fmt: string; cmyk: boolean },
): Promise<Job> {
  const form = new FormData()
  form.append("file", file)
  form.append("scale", scale)
  if (target) {
    form.append("target_w", String(target[0]))
    form.append("target_h", String(target[1]))
  }
  form.append("dpi", String(opts.dpi))
  form.append("fmt", opts.fmt)
  form.append("cmyk", String(opts.cmyk))
  return upload<Job>("/api/images/upscale", form)
}

export function jobStatus(id: string): Promise<Job> {
  return request<Job>(`/api/jobs/${id}`)
}

/**
 * Every job this session, newest first.
 *
 * The server has kept 200 of these all along and the UI never asked. An 80-second
 * cutout was reachable at `/api/jobs/{id}/result` long after the screen that
 * started it had been navigated away from and the result apparently "lost"
 * (NEXT.md 0.4).
 */
export function recentJobs(limit = 25): Promise<Job[]> {
  return request<Job[]>(`/api/jobs?limit=${limit}`)
}

export function cancelJob(id: string): Promise<Job> {
  return request<Job>(`/api/jobs/${id}/cancel`, { method: "POST" })
}

export function jobResultUrl(id: string): string {
  return `/api/jobs/${id}/result`
}

export function jobPreviewUrl(id: string): string {
  return `/api/jobs/${id}/preview`
}

export function modelSettings(): Promise<ModelSettings> {
  return request<ModelSettings>("/api/settings/models")
}

export function getPreferences(): Promise<Preferences> {
  return request<Preferences>("/api/settings/preferences")
}

export function setPreferences(updates: Preferences): Promise<Preferences> {
  return request<Preferences>("/api/settings/preferences", {
    method: "PUT",
    body: JSON.stringify(updates),
  })
}

// --- Phase 3: Excel translation -------------------------------------------

export interface ClientRow {
  id: number
  name: string
  archived: number
}

export interface GlossaryTerm {
  id?: number
  source_term: string
  target_term: string
  notes?: string
}

/** What the optional Claude check would cost on this sheet, before running it. */
export interface VerifyQuote {
  rows: number
  model: string
  requests: number
  cost_paise: number
  cost_rupees: number
  configured: boolean
  is_estimate: boolean
}

/** What a column holds. Only FREE_TEXT ever reaches the model (ADR-035). */
export type ColumnClass =
  | "PERSON_NAME"
  | "ADDRESS"
  | "CODE"
  | "NUMERIC_DATE"
  | "CATEGORICAL"
  | "FREE_TEXT"

export interface SheetColumn {
  /** "Sheet!C" — what the translate call is given back. */
  key: string
  sheet: string
  letter: string
  header: string
  count: number
  sample: string[]
  /** The suggested class. A suggestion, never a decision already taken. */
  cls: ColumnClass
  /** Why, in words the operator can act on. */
  why: string
}

export interface CategoricalColumn {
  key: string
  header: string
  values: { source: string; suggested: string; approved: boolean }[]
}

export interface SheetInfo {
  sheets: string[]
  total_cells: number
  translatable: number
  unique_strings: number
  skipped_formulas: number
  skipped_non_text: number
  engine: string | null
  /** Cells already approved on an earlier sheet — free and offline. */
  from_memory?: number
  /** Cells the client's glossary covers entirely. Also free and offline. */
  from_glossary?: number
  /** Cells the bundled word library answers outright. Free and offline too. */
  from_dictionary?: number
  /** Every column with text in it, its suggested class, and why. */
  columns?: SheetColumn[]
  classes?: ColumnClass[]
  /** How many distinct strings each route would take. */
  routed?: Record<string, number>
  /** Cells nothing could answer, which will be left in English for review. */
  unresolved?: number
  /** Per categorical column, the values to approve once. */
  categorical?: CategoricalColumn[]
  /** What is actually left for the model, and what the quote is based on. */
  to_translate?: number
  verify?: VerifyQuote
}

export interface ReviewRow {
  key: string
  sheet: string
  ref: string
  source: string
  translation: string
  glossary_terms: string[]
  glossary_only: boolean
  /** Filled from a correction the operator approved on an earlier sheet. */
  from_memory?: boolean
  /** Answered outright by the bundled word library — a lookup, not a guess. */
  from_dictionary?: boolean
  /** In a column marked as names, so written by sound rather than translated. */
  from_name?: boolean
  /** Which route answered this cell. */
  cls?: ColumnClass
  /** Nothing could answer it; the English is shown and nothing is guessed. */
  unresolved?: boolean
  /** What the rules would have said. Offered, never presented as the answer. */
  suggestion?: string
  /** The model's own confidence, and how far its four beams disagreed. */
  score?: number | null
  divergence?: number | null
  /** What the round-trip model read the Malayalam back as. */
  back_translation?: string
  lost_terms: string[]
  /** `problems` then `checks`, flat — kept for anything reading the old shape. */
  warnings: string[]
  needs_attention: boolean
  /** Demonstrably wrong: a dropped term, a changed unit, a missing number. */
  problems: string[]
  /** Nothing can vouch for it — chiefly short cells. See features/translate.py. */
  checks: string[]
  must_fix: boolean
  /** What the offline model produced, kept even where Claude replaced it. */
  offline_translation?: string
  /** Whether Claude looked at this row at all. */
  checked?: boolean
  /** Whether Claude replaced what the offline model produced. */
  verify_corrected?: boolean
  verify_note?: string
}

/** How the optional Claude check went. `requested: false` when it was not run. */
export interface ReviewSummary {
  requested: boolean
  model?: string
  checked?: number
  corrected?: number
  unchecked?: number
  cost_paise?: number
  cost_rupees?: number
  error?: string | null
  warnings?: string[]
  /** Rows not sent because they were already exact — glossary or memory. */
  skipped_exact?: number
}

export interface TranslationResult {
  file: string
  rows: ReviewRow[]
  unique_strings: number
  needs_attention: number
  must_fix: number
  from_glossary: number
  /** Rows filled from the corrections memory, so never sent anywhere. */
  from_memory?: number
  /** Rows the word library answered, so never sent anywhere either. */
  from_dictionary?: number
  /** Rows written by sound because they sit in a column marked as names. */
  from_name?: number
  unresolved?: number
  by_class?: Record<string, number>
  /** False when the sheet was translated with no glossary in force. */
  glossary_applied: boolean
  glossary_terms: number
  review?: ReviewSummary
  verify_corrected?: number
}

export interface EngineRow {
  key: string
  label: string
  licence: string
  gated: boolean
  downloaded: boolean
  notes: string
  default: boolean
}

/**
 * Unwrap a list route that may answer either shape.
 *
 * The client and glossary routes returned bare lists while every other route in
 * the app used a wrapped envelope, and they now return `{clients: [...]}` /
 * `{terms: [...]}` (NEXT.md 3.12). This keeps working against either, so a
 * `dist` bundle and a server from different commits do not break the settings
 * screen — the shop PC has exactly that risk.
 */
function unwrap<T>(body: unknown, key: string): T[] {
  if (Array.isArray(body)) return body as T[]
  if (body && typeof body === "object" && key in body) {
    const inner = (body as Record<string, unknown>)[key]
    if (Array.isArray(inner)) return inner as T[]
  }
  return []
}

export async function listClients(): Promise<ClientRow[]> {
  return unwrap<ClientRow>(await request<unknown>("/api/clients"), "clients")
}

export function addClient(name: string): Promise<ClientRow> {
  return request<ClientRow>("/api/clients", {
    method: "POST",
    body: JSON.stringify({ name }),
  })
}

export async function getGlossary(clientId: number): Promise<GlossaryTerm[]> {
  return unwrap<GlossaryTerm>(
    await request<unknown>(`/api/clients/${clientId}/glossary`),
    "terms",
  )
}

export async function putGlossary(
  clientId: number,
  terms: GlossaryTerm[],
): Promise<GlossaryTerm[]> {
  return unwrap<GlossaryTerm>(
    await request<unknown>(`/api/clients/${clientId}/glossary`, {
      method: "PUT",
      body: JSON.stringify({ terms }),
    }),
    "terms",
  )
}

export async function deleteTerm(
  clientId: number,
  termId: number,
): Promise<GlossaryTerm[]> {
  return unwrap<GlossaryTerm>(
    await request<unknown>(`/api/clients/${clientId}/glossary/${termId}`, {
      method: "DELETE",
    }),
    "terms",
  )
}

/**
 * What is in this sheet, and what checking it would cost.
 *
 * `clientId` is what makes the quote true: without it the estimate counts rows
 * the glossary and the corrections memory already cover and that never reach
 * the model. Re-inspect when the operator changes the client.
 */
export function inspectSheet(file: File, clientId: number | null = null): Promise<SheetInfo> {
  const form = new FormData()
  form.append("file", file)
  if (clientId != null) form.append("client_id", String(clientId))
  return upload<SheetInfo>("/api/excel/inspect", form)
}

export function startTranslate(
  file: File,
  clientId: number | null,
  options: {
    checkWithClaude?: boolean
    overBudgetOk?: boolean
    /** "Sheet!C" -> class, for every column the operator changed or confirmed. */
    columnClasses?: Record<string, ColumnClass>
  } = {},
): Promise<Job> {
  const form = new FormData()
  form.append("file", file)
  if (clientId != null) form.append("client_id", String(clientId))
  // Only sent when true, so an older server that does not know the field is
  // unaffected by the ordinary offline path.
  if (options.checkWithClaude) form.append("check_with_claude", "true")
  if (options.overBudgetOk) form.append("over_budget_ok", "true")
  const classes = Object.entries(options.columnClasses ?? {})
  if (classes.length) {
    form.append(
      "column_classes",
      classes.map(([key, cls]) => `${key}=${cls}`).join(","),
    )
  }
  return upload<Job>("/api/excel/translate", form)
}

/**
 * Export is a file download, so it bypasses the JSON request helper.
 *
 * Also the moment the shop learns: the server compares the operator's final
 * text against what the offline model produced and remembers the differences.
 * `remembered` is how many corrections that was — read from a response header
 * rather than a second round trip, which could fail and leave the learning
 * unexplained.
 */
export async function exportSheet(
  jobId: string,
  translations: Record<string, string>,
  options: { remember?: boolean; clientId?: number | null } = {},
): Promise<{ blob: Blob; remembered: number }> {
  const response = await fetch("/api/excel/export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      job_id: jobId,
      translations,
      remember: options.remember !== false,
      client_id: options.clientId ?? null,
    }),
  })
  if (!response.ok) {
    throw new ApiError(`Export failed (${response.status})`, response.status)
  }
  const remembered = Number(response.headers.get("X-Corrections-Remembered") ?? "0")
  return { blob: await response.blob(), remembered: Number.isFinite(remembered) ? remembered : 0 }
}

// --- the corrections memory (ADR-029) -------------------------------------

export interface Correction {
  id: number
  client_id: number | null
  source: string
  target: string
  /** "operator" when they typed it, "claude-kept" when they kept a paid one. */
  origin: string
  learned_from: string
  updated_at: string
}

export interface CorrectionsPage {
  client_id: number | null
  corrections: Correction[]
  count: number
  /** Every match, not just this page — the list is always paginated. */
  total: number
}

export interface CorrectionImport {
  added: number
  updated: number
  skipped: number
  rows: number
  truncated: boolean
  blank_rows: number
  total: number
}

export function listCorrections(options: {
  clientId: number | null
  q?: string
  limit?: number
  offset?: number
}): Promise<CorrectionsPage> {
  const params = new URLSearchParams()
  if (options.clientId != null) params.set("client_id", String(options.clientId))
  if (options.q) params.set("q", options.q)
  params.set("limit", String(options.limit ?? 100))
  params.set("offset", String(options.offset ?? 0))
  return request(`/api/corrections?${params}`)
}

export function putCorrections(
  clientId: number | null,
  corrections: { source: string; target: string }[],
): Promise<CorrectionsPage> {
  return request("/api/corrections", {
    method: "PUT",
    body: JSON.stringify({ client_id: clientId, corrections }),
  })
}

export function deleteCorrection(
  id: number,
  clientId: number | null,
): Promise<CorrectionsPage> {
  const params = clientId == null ? "" : `?client_id=${clientId}`
  return request(`/api/corrections/${id}${params}`, { method: "DELETE" })
}

export function importCorrections(
  file: File,
  clientId: number | null,
): Promise<CorrectionImport> {
  const form = new FormData()
  form.append("file", file)
  if (clientId != null) form.append("client_id", String(clientId))
  return upload<CorrectionImport>("/api/corrections/import", form)
}

/**
 * The memory as a spreadsheet.
 *
 * Returns a Blob rather than a URL so this stays the only place that talks to
 * the server — a component with its own `<a href="/api/...">` is the drift the
 * fetch layer exists to prevent.
 */
export async function downloadCorrections(clientId: number | null): Promise<Blob> {
  const params = clientId == null ? "" : `?client_id=${clientId}`
  const response = await fetch(`/api/corrections/export${params}`)
  if (!response.ok) {
    throw new ApiError(`Download failed (${response.status})`, response.status)
  }
  return response.blob()
}

export function forgetJobCorrections(
  jobId: string,
): Promise<{ forgotten: number; total: number }> {
  return request("/api/corrections/forget-job", {
    method: "POST",
    body: JSON.stringify({ job_id: jobId }),
  })
}

/** Lock a categorical column's vocabulary into the client glossary. */
export function approveCategorical(
  clientId: number,
  pairs: { source_term: string; target_term: string }[],
): Promise<{ terms: GlossaryTerm[]; count: number }> {
  return request("/api/excel/categorical/approve", {
    method: "POST",
    body: JSON.stringify({ client_id: clientId, pairs }),
  })
}

export function translationSettings(): Promise<{
  engines: EngineRow[]
  active: string | null
  hf_home: string
}> {
  return request("/api/settings/translation")
}

// --- the word library -----------------------------------------------------

/** Where one row's Malayalam came from. "yours" always wins over the other two. */
export type WordOrigin = "yours" | "trade" | "olam"

export interface WordRow {
  source: string
  target: string
  alternatives: string[]
  origin: WordOrigin
  /** What the bundled library says, when the operator has overridden it. */
  bundled: string
}

export interface WordPage {
  rows: WordRow[]
  total: number
  offset: number
  page: number
  bundled: number
  overrides: number
}

export interface WordImport {
  added: number
  updated: number
  skipped: number
  rows: number
  unchanged: number
  truncated: boolean
  blank_rows: number
  overrides: number
}

export function searchWords(q: string, offset = 0): Promise<WordPage> {
  const params = new URLSearchParams()
  if (q) params.set("q", q)
  params.set("offset", String(offset))
  return request(`/api/dictionary?${params}`)
}

export function putWordOverride(
  source: string,
  target: string,
): Promise<{ added: number; updated: number; skipped: number }> {
  return request("/api/dictionary/override", {
    method: "PUT",
    body: JSON.stringify({ source_term: source, target_term: target }),
  })
}

export function importWords(file: File): Promise<WordImport> {
  const form = new FormData()
  form.append("file", file)
  return upload<WordImport>("/api/dictionary/import", form)
}

/** Same Blob-not-URL rule as `downloadCorrections`, for the same reason. */
export async function downloadWords(): Promise<Blob> {
  const response = await fetch("/api/dictionary/export")
  if (!response.ok) {
    throw new ApiError(`Download failed (${response.status})`, response.status)
  }
  return response.blob()
}

// --- Posters --------------------------------------------------------------
//
// Gemini draws the whole poster from one of the shop's own designs in
// `data/poster_prompts/`. The canvas editor, layout presets and SVG export that
// used to live here are gone with ADR-034 — and with them the guarantee that
// Malayalam was spelled correctly and every line stayed editable.

/** The tags the operator writes in front of their copy. */
export type CopyTag = "main" | "h1" | "h2"

/** One design from the prompt folder. The prompt body itself never leaves the server. */
export interface PosterDesign {
  key: string
  name: string
  description: string
  /**
   * The shape the design asks for, e.g. `"4:5"`. Empty when it does not say,
   * in which case nothing is forced. Sent to Google as a request parameter and
   * not only written in the prompt, because a ratio in prose drifts.
   */
  aspect: string
}

export interface PosterCopy {
  main?: string
  h1?: string
  h2?: string
}

export function posterDesigns(): Promise<{
  designs: PosterDesign[]
  count: number
  /** Shown in the empty state, so the operator knows where their files go. */
  folder: string
  tags: CopyTag[]
  /**
   * Whether the server will accept a pinned style. The operator never chooses
   * one — the model does — so the override only appears when someone started
   * the server with `DEV_TOOLS=true`.
   */
  dev_tools: boolean
}> {
  return request("/api/posters/designs")
}

/** What the app read out of the pasted copy. Free, offline, no model. */
export function parsePosterCopy(copy: string): Promise<{
  copy: PosterCopy
  tags: CopyTag[]
  missing: CopyTag[]
}> {
  const form = new FormData()
  form.append("copy", copy)
  return upload("/api/posters/parse", form)
}

export function generatePoster(body: {
  copy: string
  reference?: File | null
  batch?: boolean
  overBudgetOk?: boolean
  /**
   * Pin the style instead of letting the model choose. Developer control only —
   * the server refuses it unless it was started with `DEV_TOOLS=true`, rather
   * than ignoring it, so a pinned run can never be mistaken for agreement.
   */
  forceStyle?: string
}): Promise<AiResult> {
  const form = new FormData()
  form.append("copy", body.copy)
  if (body.reference) form.append("reference", body.reference)
  form.append("batch", body.batch === false ? "false" : "true")
  if (body.overBudgetOk) form.append("over_budget_ok", "true")
  if (body.forceStyle) form.append("force_style", body.forceStyle)
  return upload<AiResult>("/api/posters/generate", form)
}

/**
 * Change the poster that was just made.
 *
 * The poster goes back up from the browser rather than being held on the
 * server: it is already in the page, and the operator may have stepped back to
 * an earlier attempt.
 */
export function refinePoster(body: {
  poster: Blob
  note: string
  overBudgetOk?: boolean
}): Promise<AiResult> {
  const form = new FormData()
  form.append("poster", body.poster, "poster.png")
  form.append("note", body.note)
  if (body.overBudgetOk) form.append("over_budget_ok", "true")
  return upload<AiResult>("/api/posters/refine", form)
}

// --- Phase 5: AI ----------------------------------------------------------

export type AiFeature =
  | "photo-edit"
  | "poster"
  | "poster-concept"
  // Retired, and still readable: `ai_spend` holds real rows under these names
  // and the Settings spend view must render the shop's own history (ADR-034).
  | "poster-artwork"
  | "poster-layout"
  | "poster-copy"

export interface KeyRow {
  name: string
  is_set: boolean
  hint: string | null
  last_tested_at: string | null
  last_test_ok: boolean | null
}

export interface Budget {
  month: string
  total_paise: number
  runs: number
  failed_runs: number
  budget_paise: number
  spent_rupees: number
  budget_rupees: number
  fraction_used: number
  over_budget: boolean
  is_estimate: boolean
}

export interface PromptScope {
  key: string
  label: string
  description: string
  variables: string[]
  required: string[]
}

export interface PromptRow {
  id: number
  scope: string
  name: string
  body: string
  is_default: boolean
  is_active: boolean
  updated_at: string
}

export interface AiResult {
  ok: boolean
  feature: string
  model: string
  batch: boolean
  cost_paise: number
  cost_rupees: number
  error: string | null
  warnings: string[]
  prompt_used: string
  budget: Budget
  image?: string
  media_type?: string
  /**
   * Which style the model chose, and the one line it gave for why. Present on
   * poster generation only, and only when the reply named a style that really
   * exists — an empty `style` with a warning means the poster is fine but
   * cannot be reproduced from the log.
   */
  style?: string
  style_reason?: string
}

export function listKeys(): Promise<{
  keys: KeyRow[]
  encryption_key_location: string
  configured: boolean
}> {
  return request("/api/settings/keys")
}

export function saveKey(
  name: string,
  value: string,
): Promise<{ keys: KeyRow[]; warning: string | null }> {
  return request(`/api/settings/keys/${name}`, {
    method: "PUT",
    body: JSON.stringify({ value }),
  })
}

export function removeKey(name: string): Promise<{ keys: KeyRow[] }> {
  return request(`/api/settings/keys/${name}`, { method: "DELETE" })
}

export function testKey(name: string): Promise<{
  ok: boolean
  message: string
  keys: KeyRow[]
  /** What testing repaired — a model name Google has since retired. */
  notes?: string[]
}> {
  return request(`/api/settings/keys/${name}/test`, { method: "POST" })
}

export function aiStatus(): Promise<{
  configured: boolean
  budget: Budget
  rates_paise: Record<string, number>
}> {
  return request("/api/ai/status")
}

export function aiEstimate(
  feature: AiFeature,
  batch: boolean,
): Promise<{
  cost_rupees: number
  cost_paise: number
  model: string
  /** False when waiting saves nothing on this feature — hide the toggle. */
  batch_discount: boolean
  instant_paise: number
  batch_paise: number | null
}> {
  return request(`/api/ai/estimate?feature=${feature}&batch=${batch}`)
}

export function listPrompts(
  scope?: string,
): Promise<{ scopes: PromptScope[]; prompts: PromptRow[] }> {
  return request(`/api/settings/prompts${scope ? `?scope=${scope}` : ""}`)
}

export function validatePrompt(
  scope: string,
  body: string,
): Promise<{ problems: string[]; variables: string[] }> {
  return request("/api/settings/prompts/validate", {
    method: "POST",
    body: JSON.stringify({ scope, name: "check", body }),
  })
}

export function savePrompt(
  scope: string,
  name: string,
  body: string,
  promptId?: number,
): Promise<{ prompt: PromptRow; problems: string[] }> {
  return request("/api/settings/prompts", {
    method: "PUT",
    body: JSON.stringify({ scope, name, body, prompt_id: promptId ?? null }),
  })
}

export function restorePromptDefault(id: number): Promise<{ prompt: PromptRow }> {
  return request(`/api/settings/prompts/${id}/restore-default`, { method: "POST" })
}

export function activatePrompt(id: number): Promise<{ prompt: PromptRow }> {
  return request(`/api/settings/prompts/${id}/activate`, { method: "POST" })
}

export function editPhoto(
  file: File,
  instruction: string,
  preserve: string,
  overBudgetOk = false,
): Promise<AiResult> {
  const form = new FormData()
  form.append("file", file)
  form.append("instruction", instruction)
  form.append("preserve", preserve)
  form.append("over_budget_ok", String(overBudgetOk))
  return upload<AiResult>("/api/ai/photo-edit", form)
}

/**
 * A picture request described by the poster's own copy.
 *
 * With `style_key`, the shop's saved prompt structure for that look is filled
 * in with the copy below — which is why the picture ends up about the message
 * rather than about whatever the operator managed to describe in a hurry.
 */
// --- which model does which job -------------------------------------------

export interface AiModel {
  name: string
  label: string
  description: string
  image_output: boolean
  input_token_limit: number | null
}

export interface AiRole {
  key: "artwork" | "photo" | "layout"
  label: string
  description: string
  needs_image: boolean
  chosen: string
  default: string
  is_default: boolean
  /** null until a live list has been fetched — never "missing" on no evidence. */
  confirmed: boolean | null
  cost_rupees: number
}

export interface AiModelSettings {
  roles: AiRole[]
  models: AiModel[]
  error?: string | null
}

export function aiModels(refresh = false): Promise<AiModelSettings> {
  return request(`/api/ai/models${refresh ? "?refresh=true" : ""}`)
}

export function setAiModels(
  choices: Partial<Record<AiRole["key"], string>>,
): Promise<AiModelSettings> {
  return request("/api/ai/models", {
    method: "PUT",
    body: JSON.stringify(choices),
  })
}
