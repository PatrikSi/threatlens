import type {
  ArticleExportFilters,
  ReportPromptConfig,
  ReportSectionConfig,
  ReportTemplate,
} from '../types/api'
import {
  applyDatePreset,
  createDefaultExportFilterDraft,
  type ExportFilterDraft,
  validateExportFilterDraft,
} from './exportPageModel'

export const DEFAULT_REPORT_SECTIONS: ReportSectionConfig[] = [
  { key: 'executive_summary', title: 'Executive Summary', enabled: true },
  { key: 'scope_evidence', title: 'Scope and Evidence', enabled: true },
  { key: 'key_developments', title: 'Key Developments', enabled: true },
  { key: 'threat_landscape', title: 'Threat Landscape', enabled: true },
  { key: 'vulnerabilities', title: 'Vulnerabilities and Exploitation', enabled: true },
  { key: 'campaigns', title: 'Malware, Campaigns, and Infrastructure', enabled: true },
  { key: 'organization_relevance', title: 'Organizational Relevance', enabled: true },
  { key: 'recommended_actions', title: 'Recommended Actions', enabled: true },
  { key: 'observables', title: 'Indicators and Observables', enabled: true },
  { key: 'sources', title: 'Sources', enabled: true },
]

export const DEFAULT_REPORT_PROMPT: ReportPromptConfig = {
  audience: 'security_team',
  objective: 'Explain the most important security developments and the actions they justify.',
  tone: 'analytical',
  detail_level: 'standard',
  use_company_context: true,
  custom_instructions: null,
  focus_topics: [],
  excluded_topics: [],
}

export function reportBuilderFromTemplate(template: ReportTemplate | undefined) {
  return {
    prompt: template ? structuredClone(template.prompt) : structuredClone(DEFAULT_REPORT_PROMPT),
    sections: template ? structuredClone(template.sections) : structuredClone(DEFAULT_REPORT_SECTIONS),
    filterDraft: template
      ? exportFiltersToDraft(template.default_filters)
      : createDefaultExportFilterDraftForReports(),
  }
}

export function createDefaultExportFilterDraftForReports(now = new Date()): ExportFilterDraft {
  return applyDatePreset(createDefaultExportFilterDraft(now), '7', now)
}

export function exportFiltersToDraft(filters: ArticleExportFilters): ExportFilterDraft {
  const fallback = createDefaultExportFilterDraftForReports()
  return {
    ...fallback,
    q: filters.q ?? '',
    feedIds: [...filters.feed_ids],
    tagIds: [...filters.tag_ids],
    tagsMode: filters.tags_mode,
    classifications: [...filters.classifications],
    relevanceLabels: [...filters.ai_relevance_labels],
    scoreMin: filters.ai_score_min === null ? '' : String(filters.ai_score_min),
    scoreMax: filters.ai_score_max === null ? '' : String(filters.ai_score_max),
    isRead: filters.is_read === null ? '' : String(filters.is_read) as 'true' | 'false',
    isStarred: filters.is_starred === null ? '' : String(filters.is_starred) as 'true' | 'false',
    hasArticleText: filters.has_article_text === null ? '' : String(filters.has_article_text) as 'true' | 'false',
    datePreset: filters.since || filters.until ? 'custom' : '7',
    sinceDate: filters.since ? filters.since.slice(0, 10) : fallback.sinceDate,
    untilDate: filters.until ? filters.until.slice(0, 10) : fallback.untilDate,
    dateBasis: filters.date_basis,
    sort: filters.sort,
  }
}

export function reportPeriodFromFilters(filters: ArticleExportFilters) {
  const end = filters.until ? new Date(filters.until) : new Date()
  const start = filters.since ? new Date(filters.since) : new Date(end.getTime() - 7 * 24 * 60 * 60 * 1000)
  return { period_start: start.toISOString(), period_end: end.toISOString() }
}

export function validateReportBuilder(
  filterDraft: ExportFilterDraft,
  prompt: ReportPromptConfig,
  sections: ReportSectionConfig[],
) {
  const filterValidation = validateExportFilterDraft(filterDraft)
  const errors = [...filterValidation.errors]
  if (!prompt.objective.trim()) {
    errors.push('Report objective is required.')
  }
  if (!sections.some((section) => section.enabled)) {
    errors.push('Enable at least one report section.')
  }
  return { filters: filterValidation.filters, errors }
}

export function parseListInput(value: string): string[] {
  return Array.from(new Set(value.split(/[\n,]/).map((entry) => entry.trim()).filter(Boolean)))
}

export function formatReportDate(value: string | null): string {
  if (!value) return 'Not yet'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}
