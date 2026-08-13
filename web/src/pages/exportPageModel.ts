import type {
  ArticleExportDateBasis,
  ArticleExportFilters,
  ArticleExportFormat,
  ArticleExportOptions,
  ArticleExportPreview,
  ArticleExportSort,
} from '../types/api'

export type ExportDatePreset = 'all' | '7' | '30' | '90' | 'custom'

export interface ExportFilterDraft {
  q: string
  feedIds: string[]
  tagIds: string[]
  tagsMode: ArticleExportFilters['tags_mode']
  classifications: string[]
  relevanceLabels: ArticleExportFilters['ai_relevance_labels']
  scoreMin: string
  scoreMax: string
  isRead: '' | 'true' | 'false'
  isStarred: '' | 'true' | 'false'
  hasArticleText: '' | 'true' | 'false'
  datePreset: ExportDatePreset
  sinceDate: string
  untilDate: string
  dateBasis: ArticleExportDateBasis
  sort: ArticleExportSort
}

export interface ExportDraftValidation {
  filters: ArticleExportFilters | null
  errors: string[]
}

const FORMAT_EXTENSIONS: Record<ArticleExportFormat, string> = {
  csv: 'csv',
  jsonl: 'jsonl',
  threat_bundle: 'zip',
  stix: 'stix.json',
  misp: 'misp.json',
  pdf_bundle: 'pdf.zip',
}

export function createDefaultExportFilterDraft(now = new Date()): ExportFilterDraft {
  const until = startOfLocalDay(now)
  const since = new Date(until)
  since.setDate(since.getDate() - 29)
  return {
    q: '',
    feedIds: [],
    tagIds: [],
    tagsMode: 'any',
    classifications: [],
    relevanceLabels: [],
    scoreMin: '',
    scoreMax: '',
    isRead: '',
    isStarred: '',
    hasArticleText: '',
    datePreset: '30',
    sinceDate: formatDateInput(since),
    untilDate: formatDateInput(until),
    dateBasis: 'published_at_or_first_seen_at',
    sort: 'published_at_desc',
  }
}

export function applyDatePreset(
  draft: ExportFilterDraft,
  preset: ExportDatePreset,
  now = new Date(),
): ExportFilterDraft {
  if (preset === 'custom') {
    return { ...draft, datePreset: preset }
  }
  if (preset === 'all') {
    return { ...draft, datePreset: preset, sinceDate: '', untilDate: '' }
  }

  const until = startOfLocalDay(now)
  const since = new Date(until)
  since.setDate(since.getDate() - (Number(preset) - 1))
  return {
    ...draft,
    datePreset: preset,
    sinceDate: formatDateInput(since),
    untilDate: formatDateInput(until),
  }
}

export function validateExportFilterDraft(draft: ExportFilterDraft): ExportDraftValidation {
  const errors: string[] = []
  const scoreMin = parseOptionalScore(draft.scoreMin, 'Minimum AI score', errors)
  const scoreMax = parseOptionalScore(draft.scoreMax, 'Maximum AI score', errors)

  if (scoreMin !== null && scoreMax !== null && scoreMin > scoreMax) {
    errors.push('Minimum AI score cannot be greater than maximum AI score.')
  }
  if (draft.sinceDate && draft.untilDate && draft.sinceDate > draft.untilDate) {
    errors.push('Start date cannot be later than end date.')
  }

  if (errors.length) {
    return { filters: null, errors }
  }

  return {
    filters: {
      q: draft.q.trim() || null,
      feed_ids: draft.feedIds,
      tag_ids: draft.tagIds,
      tags_mode: draft.tagsMode,
      classifications: draft.classifications,
      ai_relevance_labels: draft.relevanceLabels,
      ai_score_min: scoreMin,
      ai_score_max: scoreMax,
      is_read: parseTriState(draft.isRead),
      is_starred: parseTriState(draft.isStarred),
      has_article_text: parseTriState(draft.hasArticleText),
      since: draft.sinceDate ? localDateBoundaryToIso(draft.sinceDate, 'start') : null,
      until: draft.untilDate ? localDateBoundaryToIso(draft.untilDate, 'end') : null,
      date_basis: draft.dateBasis,
      sort: draft.sort,
    },
    errors: [],
  }
}

export function createDefaultExportOptions(format: ArticleExportFormat): ArticleExportOptions {
  const structured = format === 'jsonl' || format === 'threat_bundle'
  return {
    include_article_text: structured,
    include_ai_details: true,
    include_tag_metadata: true,
    include_iocs: true,
    include_ioc_csv: format === 'threat_bundle',
    include_user_state: false,
    include_user_notes: false,
    pdf_include_article_text: format === 'pdf_bundle',
    stix_marking: 'TLP:WHITE',
    misp_distribution: 0,
    filename_prefix: null,
  }
}

export function changeExportFormat(
  format: ArticleExportFormat,
  previous: ArticleExportOptions,
): ArticleExportOptions {
  return { ...createDefaultExportOptions(format), filename_prefix: previous.filename_prefix }
}

export function exportBlockingReason(
  preview: ArticleExportPreview | undefined,
  format: ArticleExportFormat,
  validationErrors: string[],
): string | null {
  if (validationErrors.length) {
    return validationErrors[0]
  }
  if (!preview) {
    return 'Wait for the matching article preview to finish.'
  }
  if (preview.total_matches === 0) {
    return 'No articles match the current filters.'
  }
  if (format === 'pdf_bundle' && preview.exceeds_pdf_limit) {
    return 'The readable PDF bundle exceeds its article limit. Narrow the filters.'
  }
  if (preview.exceeds_export_limit) {
    return 'The export exceeds its article limit. Narrow the filters.'
  }
  return null
}

export function defaultExportFilename(format: ArticleExportFormat, prefix: string | null): string {
  const safePrefix = (prefix?.trim() || 'threatlens-export').replace(/[^a-zA-Z0-9._-]+/g, '-').replace(/^-+|-+$/g, '')
  return `${safePrefix || 'threatlens-export'}.${FORMAT_EXTENSIONS[format]}`
}

export function triggerBrowserDownload(blob: Blob, filename: string) {
  const objectUrl = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = objectUrl
  anchor.download = filename
  anchor.style.display = 'none'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0)
}

export function formatByteSize(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`
  }
  const units = ['KiB', 'MiB', 'GiB']
  let value = bytes / 1024
  let unit = units[0]
  for (let index = 1; index < units.length && value >= 1024; index += 1) {
    value /= 1024
    unit = units[index]
  }
  return `${value >= 10 ? value.toFixed(0) : value.toFixed(1)} ${unit}`
}

function parseOptionalScore(value: string, label: string, errors: string[]): number | null {
  if (!value.trim()) {
    return null
  }
  const parsed = Number(value)
  if (!Number.isFinite(parsed) || parsed < 0 || parsed > 1) {
    errors.push(`${label} must be a number from 0 to 1.`)
    return null
  }
  return parsed
}

function parseTriState(value: ExportFilterDraft['isRead']): boolean | null {
  return value === '' ? null : value === 'true'
}

function localDateBoundaryToIso(value: string, boundary: 'start' | 'end'): string {
  const [year, month, day] = value.split('-').map(Number)
  const date = new Date(
    year,
    month - 1,
    day,
    boundary === 'start' ? 0 : 23,
    boundary === 'start' ? 0 : 59,
    boundary === 'start' ? 0 : 59,
    boundary === 'start' ? 0 : 999,
  )
  return date.toISOString()
}

function startOfLocalDay(value: Date): Date {
  return new Date(value.getFullYear(), value.getMonth(), value.getDate())
}

function formatDateInput(value: Date): string {
  const year = value.getFullYear()
  const month = String(value.getMonth() + 1).padStart(2, '0')
  const day = String(value.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}
