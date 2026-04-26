import { AISettings, AISettingsUpdateRequest } from '../types/api'

export type AISettingsDraft = {
  base_url: string
  model: string
  temperature: string
  max_completion_tokens: string
  request_timeout_seconds: string
  request_max_retries: string
  summary_enabled: boolean
  relevance_enabled: boolean
  daily_brief_enabled: boolean
  auto_enrich_new_items: boolean
  daily_brief_run_time_utc: string
  daily_brief_window_hours: string
  daily_brief_max_items: string
  daily_brief_history_limit: string
  relevance_medium_threshold: string
  relevance_high_threshold: string
  company_name: string
  company_industry: string
  company_regions: string
  company_stack: string
  company_priority_topics: string
  company_keywords: string
  company_exclusions: string
  company_profile_text: string
  item_enrichment_system_prompt: string
  daily_brief_system_prompt: string
  global_instructions: string
  item_summary_instructions: string
  relevance_instructions: string
  daily_brief_instructions: string
}

export type AISettingsDraftValidation = Partial<Record<keyof AISettingsDraft, string>>

export const DEFAULT_DRAFT: AISettingsDraft = {
  base_url: '',
  model: '',
  temperature: '0.2',
  max_completion_tokens: '5000',
  request_timeout_seconds: '300',
  request_max_retries: '3',
  summary_enabled: true,
  relevance_enabled: true,
  daily_brief_enabled: true,
  auto_enrich_new_items: true,
  daily_brief_run_time_utc: '09:00',
  daily_brief_window_hours: '24',
  daily_brief_max_items: '20',
  daily_brief_history_limit: '7',
  relevance_medium_threshold: '0.55',
  relevance_high_threshold: '0.80',
  company_name: '',
  company_industry: '',
  company_regions: '',
  company_stack: '',
  company_priority_topics: '',
  company_keywords: '',
  company_exclusions: '',
  company_profile_text: '',
  item_enrichment_system_prompt: '',
  daily_brief_system_prompt: '',
  global_instructions: '',
  item_summary_instructions: '',
  relevance_instructions: '',
  daily_brief_instructions: '',
}

const NUMBER_RULES: Array<{
  key: keyof AISettingsDraft
  label: string
  min: number
  max: number
  integer?: boolean
}> = [
  { key: 'temperature', label: 'Temperature', min: 0, max: 2 },
  { key: 'max_completion_tokens', label: 'Max Completion Tokens', min: 128, max: 8192, integer: true },
  { key: 'request_timeout_seconds', label: 'Request Timeout Seconds', min: 5, max: 300, integer: true },
  { key: 'request_max_retries', label: 'Max Retry Attempts', min: 0, max: 5, integer: true },
  { key: 'daily_brief_window_hours', label: 'Daily Brief Window Hours', min: 6, max: 168, integer: true },
  { key: 'daily_brief_max_items', label: 'Daily Brief Max Articles', min: 5, max: 100, integer: true },
  { key: 'daily_brief_history_limit', label: 'Retained Daily Briefings', min: 1, max: 90, integer: true },
  { key: 'relevance_medium_threshold', label: 'Medium Relevance Threshold', min: 0, max: 1 },
  { key: 'relevance_high_threshold', label: 'High Relevance Threshold', min: 0, max: 1 },
]

const TEXT_RULES: Array<{
  key: keyof AISettingsDraft
  label: string
  max: number
}> = [
  { key: 'base_url', label: 'Base URL', max: 4000 },
  { key: 'model', label: 'Model', max: 255 },
  { key: 'company_name', label: 'Company Name', max: 255 },
  { key: 'company_industry', label: 'Industry', max: 255 },
  { key: 'company_profile_text', label: 'Additional Company Context', max: 4000 },
  { key: 'item_enrichment_system_prompt', label: 'Item Enrichment System Prompt', max: 4000 },
  { key: 'daily_brief_system_prompt', label: 'Daily Brief System Prompt', max: 4000 },
  { key: 'global_instructions', label: 'Global Instructions', max: 4000 },
  { key: 'item_summary_instructions', label: 'Item Summary Instructions', max: 4000 },
  { key: 'relevance_instructions', label: 'Relevance Instructions', max: 4000 },
  { key: 'daily_brief_instructions', label: 'Daily Brief Instructions', max: 4000 },
]

export function validateAISettingsDraft(draft: AISettingsDraft): AISettingsDraftValidation {
  const errors: AISettingsDraftValidation = {}

  for (const rule of TEXT_RULES) {
    const value = draft[rule.key]
    if (typeof value !== 'string') {
      continue
    }
    if (value.trim().length > rule.max) {
      errors[rule.key] = `${rule.label} cannot exceed ${rule.max} characters.`
    }
  }

  for (const rule of NUMBER_RULES) {
    const value = draft[rule.key]
    if (typeof value !== 'string') {
      continue
    }
    const trimmed = value.trim()
    const parsed = Number(trimmed)
    if (!trimmed || !Number.isFinite(parsed)) {
      errors[rule.key] = `${rule.label} must be a number.`
      continue
    }
    if (rule.integer && !Number.isInteger(parsed)) {
      errors[rule.key] = `${rule.label} must be a whole number.`
      continue
    }
    if (parsed < rule.min || parsed > rule.max) {
      errors[rule.key] = `${rule.label} must be between ${rule.min} and ${rule.max}.`
    }
  }

  const medium = Number(draft.relevance_medium_threshold)
  const high = Number(draft.relevance_high_threshold)
  if (
    Number.isFinite(medium) &&
    Number.isFinite(high) &&
    !errors.relevance_medium_threshold &&
    !errors.relevance_high_threshold &&
    medium >= high
  ) {
    errors.relevance_high_threshold = 'High Relevance Threshold must be greater than Medium Relevance Threshold.'
  }

  if (!isValidUtcTimeInput(draft.daily_brief_run_time_utc)) {
    errors.daily_brief_run_time_utc = 'Daily Brief Run Time must be a valid UTC time.'
  }

  return errors
}

export function getFirstAISettingsDraftValidationError(validation: AISettingsDraftValidation): string | null {
  return Object.values(validation).find((message): message is string => Boolean(message)) ?? null
}

export function createDraftFromSettings(settings: AISettings): AISettingsDraft {
  return {
    base_url: settings.base_url ?? '',
    model: settings.model ?? '',
    temperature: String(settings.temperature),
    max_completion_tokens: String(settings.max_completion_tokens),
    request_timeout_seconds: String(settings.request_timeout_seconds),
    request_max_retries: String(settings.request_max_retries),
    summary_enabled: settings.summary_enabled,
    relevance_enabled: settings.relevance_enabled,
    daily_brief_enabled: settings.daily_brief_enabled,
    auto_enrich_new_items: settings.auto_enrich_new_items,
    daily_brief_run_time_utc: formatUtcTimeInput(settings.daily_brief_schedule_hour_utc, settings.daily_brief_schedule_minute_utc),
    daily_brief_window_hours: String(settings.daily_brief_window_hours),
    daily_brief_max_items: String(settings.daily_brief_max_items),
    daily_brief_history_limit: String(settings.daily_brief_history_limit),
    relevance_medium_threshold: String(settings.relevance_medium_threshold),
    relevance_high_threshold: String(settings.relevance_high_threshold),
    company_name: settings.company_name ?? '',
    company_industry: settings.company_industry ?? '',
    company_regions: settings.company_regions.join('\n'),
    company_stack: settings.company_stack.join('\n'),
    company_priority_topics: settings.company_priority_topics.join('\n'),
    company_keywords: settings.company_keywords.join('\n'),
    company_exclusions: settings.company_exclusions.join('\n'),
    company_profile_text: settings.company_profile_text ?? '',
    item_enrichment_system_prompt: settings.item_enrichment_system_prompt ?? '',
    daily_brief_system_prompt: settings.daily_brief_system_prompt ?? '',
    global_instructions: settings.global_instructions ?? '',
    item_summary_instructions: settings.item_summary_instructions ?? '',
    relevance_instructions: settings.relevance_instructions ?? '',
    daily_brief_instructions: settings.daily_brief_instructions ?? '',
  }
}

export function createRequestFromDraft(draft: AISettingsDraft): AISettingsUpdateRequest {
  const dailyBriefSchedule = parseUtcTimeInput(draft.daily_brief_run_time_utc)
  return {
    provider_type: 'openai_compatible',
    base_url: normalizeOptionalText(draft.base_url),
    model: normalizeOptionalText(draft.model),
    temperature: parseNumberOrDefault(draft.temperature, 0.2),
    max_completion_tokens: parseNumberOrDefault(draft.max_completion_tokens, 5000),
    request_timeout_seconds: parseNumberOrDefault(draft.request_timeout_seconds, 300),
    request_max_retries: Math.max(0, parseNumberOrDefault(draft.request_max_retries, 3)),
    summary_enabled: draft.summary_enabled,
    relevance_enabled: draft.relevance_enabled,
    daily_brief_enabled: draft.daily_brief_enabled,
    auto_enrich_new_items: draft.auto_enrich_new_items,
    daily_brief_schedule_hour_utc: dailyBriefSchedule.hour,
    daily_brief_schedule_minute_utc: dailyBriefSchedule.minute,
    daily_brief_window_hours: parseNumberOrDefault(draft.daily_brief_window_hours, 24),
    daily_brief_max_items: parseNumberOrDefault(draft.daily_brief_max_items, 20),
    daily_brief_history_limit: parseNumberOrDefault(draft.daily_brief_history_limit, 7),
    relevance_medium_threshold: parseNumberOrDefault(draft.relevance_medium_threshold, 0.55),
    relevance_high_threshold: parseNumberOrDefault(draft.relevance_high_threshold, 0.8),
    company_name: normalizeOptionalText(draft.company_name),
    company_industry: normalizeOptionalText(draft.company_industry),
    company_regions: parseListText(draft.company_regions),
    company_stack: parseListText(draft.company_stack),
    company_priority_topics: parseListText(draft.company_priority_topics),
    company_keywords: parseListText(draft.company_keywords),
    company_exclusions: parseListText(draft.company_exclusions),
    company_profile_text: normalizeOptionalText(draft.company_profile_text),
    item_enrichment_system_prompt: normalizeOptionalText(draft.item_enrichment_system_prompt),
    daily_brief_system_prompt: normalizeOptionalText(draft.daily_brief_system_prompt),
    global_instructions: normalizeOptionalText(draft.global_instructions),
    item_summary_instructions: normalizeOptionalText(draft.item_summary_instructions),
    relevance_instructions: normalizeOptionalText(draft.relevance_instructions),
    daily_brief_instructions: normalizeOptionalText(draft.daily_brief_instructions),
  }
}

function parseListText(value: string): string[] {
  return value
    .split(/[\n,]/)
    .map((entry) => entry.trim())
    .filter((entry, index, array) => entry.length > 0 && array.indexOf(entry) === index)
}

function normalizeOptionalText(value: string): string | null {
  const normalized = value.trim()
  return normalized ? normalized : null
}

function parseNumberOrDefault(value: string, fallback: number) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

function formatUtcTimeInput(hour: number, minute: number) {
  return `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`
}

function isValidUtcTimeInput(value: string) {
  const matched = /^(\d{1,2}):(\d{2})$/.exec(value.trim())
  if (!matched) {
    return false
  }
  const hour = Number(matched[1])
  const minute = Number(matched[2])
  return Number.isFinite(hour) && hour >= 0 && hour <= 23 && Number.isFinite(minute) && minute >= 0 && minute <= 59
}

function parseUtcTimeInput(value: string) {
  const matched = /^(\d{1,2}):(\d{2})$/.exec(value.trim())
  if (!matched) {
    return { hour: 9, minute: 0 }
  }
  const hour = Number(matched[1])
  const minute = Number(matched[2])
  if (!Number.isFinite(hour) || hour < 0 || hour > 23 || !Number.isFinite(minute) || minute < 0 || minute > 59) {
    return { hour: 9, minute: 0 }
  }
  return { hour, minute }
}
