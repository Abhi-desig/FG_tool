/**
 * The only place that talks to the Python API.
 * CLAUDE.md: server state goes through one fetch layer, never scattered `fetch`
 * calls in components.
 */

export type Direction = "to_ascii" | "to_unicode"

export interface ConvertResult {
  result: string
  direction: Direction
  font: string
  chars_in: number
  chars_out: number
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

export interface SheetInfo {
  sheets: string[]
  total_cells: number
  translatable: number
  unique_strings: number
  skipped_formulas: number
  skipped_non_text: number
  engine: string | null
}

export interface ReviewRow {
  key: string
  sheet: string
  ref: string
  source: string
  translation: string
  glossary_terms: string[]
  glossary_only: boolean
  lost_terms: string[]
  warnings: string[]
  needs_attention: boolean
}

export interface TranslationResult {
  file: string
  rows: ReviewRow[]
  unique_strings: number
  needs_attention: number
  from_glossary: number
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

export function listClients(): Promise<ClientRow[]> {
  return request<ClientRow[]>("/api/clients")
}

export function addClient(name: string): Promise<ClientRow> {
  return request<ClientRow>("/api/clients", {
    method: "POST",
    body: JSON.stringify({ name }),
  })
}

export function getGlossary(clientId: number): Promise<GlossaryTerm[]> {
  return request<GlossaryTerm[]>(`/api/clients/${clientId}/glossary`)
}

export function putGlossary(
  clientId: number,
  terms: GlossaryTerm[],
): Promise<GlossaryTerm[]> {
  return request<GlossaryTerm[]>(`/api/clients/${clientId}/glossary`, {
    method: "PUT",
    body: JSON.stringify(terms),
  })
}

export function deleteTerm(clientId: number, termId: number): Promise<GlossaryTerm[]> {
  return request<GlossaryTerm[]>(`/api/clients/${clientId}/glossary/${termId}`, {
    method: "DELETE",
  })
}

export function inspectSheet(file: File): Promise<SheetInfo> {
  const form = new FormData()
  form.append("file", file)
  return upload<SheetInfo>("/api/excel/inspect", form)
}

export function startTranslate(file: File, clientId: number | null): Promise<Job> {
  const form = new FormData()
  form.append("file", file)
  if (clientId != null) form.append("client_id", String(clientId))
  return upload<Job>("/api/excel/translate", form)
}

/** Export is a file download, so it bypasses the JSON request helper. */
export async function exportSheet(
  jobId: string,
  translations: Record<string, string>,
): Promise<Blob> {
  const response = await fetch("/api/excel/export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ job_id: jobId, translations }),
  })
  if (!response.ok) {
    throw new ApiError(`Export failed (${response.status})`, response.status)
  }
  return response.blob()
}

export function translationSettings(): Promise<{
  engines: EngineRow[]
  active: string | null
  hf_home: string
}> {
  return request("/api/settings/translation")
}

// --- Phase 4: posters -----------------------------------------------------

export type BlockSize = "small" | "medium" | "large" | "huge"
export type TextMode = "unicode" | "ascii"
export type BlockAlign = "left" | "centre" | "right"

export interface CanvasPreset {
  key: string
  label: string
  width_mm: number
  height_mm: number
  width_px: number
  height_px: number
  dpi: number
  safe_mm: number
  aspect: number
}

/** What a line *is* on the poster. A style colours the headline, not block `t3`. */
export type BlockRole = "headline" | "offer" | "occasion" | "phone" | "free"

export interface PosterBlock {
  id: string
  text: string
  x: number
  y: number
  width: number
  size: BlockSize
  weight: "regular" | "bold"
  colour: string
  align: BlockAlign
  mode: TextMode
  shadow: boolean
  role: BlockRole
}

export interface PosterLayout {
  canvas: string
  blocks: PosterBlock[]
  background_colour: string
}

export interface PosterCheck {
  canvas: { key: string; width_px: number; height_px: number; safe_fraction: [number, number] }
  safe_zone: {
    id: string
    outside_safe_zone: boolean
    extent: { x0: number; y0: number; x1: number; y1: number }
  }[]
  overflow: { id: string; message: string; measured: boolean }[]
  /** What auto-fit changed. Not a warning — but the operator must be told. */
  fitted: { id: string; message: string; scale: number; lines: number }[]
  blocks: { id: string; font_px: number; scale: number; lines: string[]; overflows: boolean }[]
}

export function posterPresets(): Promise<{
  canvases: CanvasPreset[]
  sizes: Record<BlockSize, number>
  fonts: { unicode: string; ascii: string }
}> {
  return request("/api/posters/presets")
}

/** One line of a pasted message, and the part of the poster it was guessed to be. */
export interface CopyLine {
  text: string
  role: BlockRole
}

/**
 * Sort one pasted WhatsApp message into the lines a poster is made of.
 * Offline and free. Every line comes back, in order — nothing is dropped.
 */
export function splitCopy(text: string): Promise<{ lines: CopyLine[] }> {
  return request("/api/posters/split-copy", {
    method: "POST",
    body: JSON.stringify({ text }),
  })
}

/**
 * `measured` carries each block's real width at 1 em, measured in the browser
 * with the poster font loaded. The server has no shaping engine, so without
 * these it can only estimate — see `lib/textFit`.
 */
export function checkPoster(
  layout: PosterLayout,
  measured: Record<string, number> = {},
): Promise<PosterCheck> {
  return request<PosterCheck>("/api/posters/check", {
    method: "POST",
    body: JSON.stringify({ layout, measured }),
  })
}

export function autoLayout(
  layout: PosterLayout,
  bg: File,
  place: boolean,
): Promise<{ blocks: PosterBlock[] }> {
  const form = new FormData()
  form.append("layout", JSON.stringify(layout))
  form.append("background", bg)
  form.append("place", String(place))
  return upload("/api/posters/auto", form)
}

/** SVG is a file download, so it bypasses the JSON request helper. */
export async function posterSvg(
  layout: PosterLayout,
  bg: File | null,
  safeZone: boolean,
  measured: Record<string, number> = {},
): Promise<Blob> {
  const form = new FormData()
  form.append("layout", JSON.stringify(layout))
  form.append("safe_zone", String(safeZone))
  // Without this the export re-fits from the server's estimate and could break
  // the text differently from the preview the operator just approved.
  form.append("measured", JSON.stringify(measured))
  if (bg) form.append("background", bg)
  const response = await fetch("/api/posters/svg", { method: "POST", body: form })
  if (!response.ok) throw new ApiError(`Export failed (${response.status})`, response.status)
  return response.blob()
}

// --- Phase 5: AI ----------------------------------------------------------

export type AiFeature = "photo-edit" | "poster-artwork" | "poster-layout"

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
  layout?: { blocks: Record<string, unknown>[] }
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
): Promise<{ cost_rupees: number; cost_paise: number; model: string }> {
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
): Promise<AiResult> {
  const form = new FormData()
  form.append("file", file)
  form.append("instruction", instruction)
  form.append("preserve", preserve)
  return upload<AiResult>("/api/ai/photo-edit", form)
}

/**
 * A picture request described by the poster's own copy.
 *
 * With `style_key`, the shop's saved prompt structure for that look is filled
 * in with the copy below — which is why the picture ends up about the message
 * rather than about whatever the operator managed to describe in a hurry.
 */
export interface ArtworkRequest {
  style_key?: string | null
  headline?: string
  offer?: string
  occasion?: string
  phone?: string
  /** The operator's own idea for the picture, when they have one. */
  idea?: string
  subject?: string
  style?: string
  palette?: string
  aspect?: string
  batch?: boolean
}

export function generateArtwork(body: ArtworkRequest): Promise<AiResult> {
  return request<AiResult>("/api/ai/artwork", {
    method: "POST",
    body: JSON.stringify(body),
  })
}

/** Free. The exact words that would be sent, so nothing is hidden. */
export function artworkPrompt(body: ArtworkRequest): Promise<{
  prompt: string
  style_key: string | null
  estimate: { cost_rupees: number; cost_paise: number; model: string }
}> {
  return request("/api/ai/artwork/prompt", {
    method: "POST",
    body: JSON.stringify(body),
  })
}

// --- design styles --------------------------------------------------------

/** How the words are styled when a look is chosen. Half a style is not a style. */
export interface StyleTextDefault {
  colour?: string
  size?: BlockSize
  weight?: "regular" | "bold"
  align?: BlockAlign
}

export interface StyleTextDefaults {
  background_colour?: string
  headline?: StyleTextDefault
  offer?: StyleTextDefault
  occasion?: StyleTextDefault
  phone?: StyleTextDefault
}

export interface DesignStyle {
  id: number
  key: string
  name: string
  description: string
  body: string
  palette: string
  swatches: string[]
  text_defaults: StyleTextDefaults
  is_default: boolean
  sort_order: number
  updated_at: string
}

export function listStyles(): Promise<{
  styles: DesignStyle[]
  variables: string[]
  required: string[]
}> {
  return request("/api/styles")
}

export function validateStyle(body: string): Promise<{ problems: string[] }> {
  return request("/api/styles/validate", {
    method: "POST",
    body: JSON.stringify({ key: "check", name: "check", body }),
  })
}

export function saveStyle(
  style: Omit<DesignStyle, "id" | "is_default" | "sort_order" | "updated_at">,
  styleId?: number,
): Promise<{ style: DesignStyle; problems: string[] }> {
  return request("/api/styles", {
    method: "PUT",
    body: JSON.stringify({ ...style, style_id: styleId ?? null }),
  })
}

export function restoreStyleDefault(id: number): Promise<{ style: DesignStyle }> {
  return request(`/api/styles/${id}/restore-default`, { method: "POST" })
}

export function deleteStyle(id: number): Promise<{ styles: DesignStyle[] }> {
  return request(`/api/styles/${id}`, { method: "DELETE" })
}

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
