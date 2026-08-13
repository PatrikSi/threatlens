import { TaggingRule, TaggingRuleWriteRequest, TaggingSettingsBundleResponse } from '../types/api'
import { formatDateTime } from '../utils/datetime'

export const BUILTIN_CATEGORIES = [
  'vulnerability',
  'apt_campaign',
  'malware_ransomware',
  'phishing_social_engineering',
  'supply_chain',
  'incident_breach',
  'threat_intelligence_research',
  'defensive_guidance',
  'technology_ai',
  'multi',
] as const

export type TaggingRuleField = TaggingRuleWriteRequest['applies_to'][number]

export const RULE_FIELDS = [
  { value: 'title', label: 'Title' },
  { value: 'summary', label: 'Summary' },
  { value: 'article_text', label: 'Article Text' },
  { value: 'feed_name', label: 'Feed Name' },
] as const satisfies ReadonlyArray<{ value: TaggingRuleField; label: string }>

export type TaggingSettingsDraft = {
  enabled_categories: string[]
  min_auto_tag_confidence: string
  secondary_tag_limit: string
}

export type TaggingRuleDraft = Omit<TaggingRuleWriteRequest, 'min_classification_confidence'> & {
  min_classification_confidence: string
}

export type TaggingReapplyRequest = {
  days: number
  limit: number
}

export type TaggingNotice = {
  tone: 'success' | 'error'
  message: string
}

export const DEFAULT_TAGGING_SETTINGS_DRAFT: TaggingSettingsDraft = {
  enabled_categories: [...BUILTIN_CATEGORIES],
  min_auto_tag_confidence: '0.45',
  secondary_tag_limit: '2',
}

export function createDefaultRuleDraft(): TaggingRuleDraft {
  return {
    name: '',
    tag_name: '',
    enabled: true,
    match_type: 'contains',
    pattern: '',
    case_sensitive: false,
    applies_to: ['title', 'summary'],
    required_categories: [],
    feed_scope: 'all',
    feed_ids: [],
    min_classification_confidence: '',
  }
}

export function createSettingsDraft(settings: TaggingSettingsBundleResponse['settings']): TaggingSettingsDraft {
  return {
    enabled_categories: [...settings.enabled_categories],
    min_auto_tag_confidence: String(settings.min_auto_tag_confidence),
    secondary_tag_limit: String(settings.secondary_tag_limit),
  }
}

export function parseTaggingReapplyRequest(
  daysInput: string,
  limitInput: string,
): { request: TaggingReapplyRequest | null; error: string | null } {
  const trimmedDays = daysInput.trim()
  const trimmedLimit = limitInput.trim()
  const days = Number(trimmedDays)
  const limit = Number(trimmedLimit)

  if (!trimmedDays || !Number.isInteger(days) || days < 1 || days > 365) {
    return { request: null, error: 'Days Back must be a whole number between 1 and 365.' }
  }
  if (!trimmedLimit || !Number.isInteger(limit) || limit < 0 || limit > 5000) {
    return { request: null, error: 'Limit must be a whole number between 0 and 5000.' }
  }
  return { request: { days, limit }, error: null }
}

export function createDraftFromRule(rule: TaggingRule): TaggingRuleDraft {
  return {
    name: rule.name,
    tag_name: rule.tag_name,
    enabled: rule.enabled,
    match_type: rule.match_type,
    pattern: rule.pattern,
    case_sensitive: rule.case_sensitive,
    applies_to: [...rule.applies_to],
    required_categories: [...rule.required_categories],
    feed_scope: rule.feed_scope,
    feed_ids: [...rule.feed_ids],
    min_classification_confidence: rule.min_classification_confidence != null ? String(rule.min_classification_confidence) : '',
  }
}

export function createRuleRequestFromDraft(draft: TaggingRuleDraft): TaggingRuleWriteRequest {
  return {
    name: draft.name.trim(),
    tag_name: draft.tag_name.trim(),
    enabled: draft.enabled,
    match_type: draft.match_type,
    pattern: draft.pattern.trim(),
    case_sensitive: draft.case_sensitive,
    applies_to: [...draft.applies_to],
    required_categories: [...draft.required_categories],
    feed_scope: draft.feed_scope,
    feed_ids: draft.feed_scope === 'selected' ? [...draft.feed_ids] : [],
    min_classification_confidence:
      draft.min_classification_confidence.trim().length > 0 ? Number(draft.min_classification_confidence) : null,
  }
}

export function getRuleDraftValidationError(draft: TaggingRuleDraft): string | null {
  if (!draft.name.trim()) {
    return 'Rule name is required.'
  }
  if (!draft.tag_name.trim()) {
    return 'Tag name is required.'
  }
  if (!draft.pattern.trim()) {
    return 'Pattern is required.'
  }
  if (draft.applies_to.length === 0) {
    return 'Choose at least one field to inspect.'
  }
  if (draft.feed_scope === 'selected' && draft.feed_ids.length === 0) {
    return 'Select at least one feed or switch the rule to Any feed.'
  }
  if (draft.min_classification_confidence.trim()) {
    const parsed = Number(draft.min_classification_confidence)
    if (Number.isNaN(parsed) || parsed < 0 || parsed > 1) {
      return 'Minimum classification confidence must be between 0 and 1.'
    }
  }
  return null
}

export function formatTaggingCategory(value: string): string {
  return value
    .split('_')
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

export function formatTaggingField(value: string): string {
  const labels: Record<string, string> = { article_text: 'Article Text', feed_name: 'Feed Name' }
  return labels[value] ?? value.charAt(0).toUpperCase() + value.slice(1)
}

export function formatTaggingTimestamp(value: string): string {
  return formatDateTime(value)
}
