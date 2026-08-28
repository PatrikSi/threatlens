import type {
  AlertClosureDisposition,
  AlertOccurrence,
  AlertOccurrenceState,
  AlertSeverity,
} from '../types/alerts'
import { ApiError } from '../api/client'
import { formatAlertPreviewSummary } from './alertPageModel'

export const ALERT_OCCURRENCE_PAGE_SIZES = [25, 50, 100] as const
export const ALERT_OCCURRENCE_ACTIVITY_PAGE_SIZE = 25

export const ALERT_OCCURRENCE_STATES: ReadonlyArray<{
  value: AlertOccurrenceState
  label: string
}> = [
  { value: 'new', label: 'New' },
  { value: 'acknowledged', label: 'Acknowledged' },
  { value: 'investigating', label: 'Investigating' },
  { value: 'closed', label: 'Closed' },
]

export const ALERT_SEVERITIES: ReadonlyArray<{ value: AlertSeverity; label: string }> = [
  { value: 'critical', label: 'Critical' },
  { value: 'high', label: 'High' },
  { value: 'medium', label: 'Medium' },
  { value: 'low', label: 'Low' },
]

export const ALERT_CLOSURE_DISPOSITIONS: ReadonlyArray<{
  value: AlertClosureDisposition
  label: string
}> = [
  { value: 'true_positive', label: 'True positive' },
  { value: 'false_positive', label: 'False positive' },
  { value: 'benign', label: 'Benign' },
  { value: 'duplicate', label: 'Duplicate' },
  { value: 'informational', label: 'Informational' },
  { value: 'other', label: 'Other' },
]

export type AlertBooleanFilter = 'any' | 'yes' | 'no'

export interface AlertOccurrenceFilters {
  lifecycleStates: AlertOccurrenceState[]
  severities: AlertSeverity[]
  ruleId: string
  suppressed: AlertBooleanFilter
  snoozed: AlertBooleanFilter
  since: string
  until: string
}

export const DEFAULT_ALERT_OCCURRENCE_FILTERS: AlertOccurrenceFilters = {
  lifecycleStates: [],
  severities: [],
  ruleId: '',
  suppressed: 'any',
  snoozed: 'any',
  since: '',
  until: '',
}

export interface AlertOccurrenceSource {
  title: string
  summary: string | null
  url: string | null
  feedName: string | null
  classification: string | null
  publishedAt: string | null
  firstSeenAt: string | null
}

export interface AlertOccurrencePageStats {
  matching: number
  newOnPage: number
  activeOnPage: number
  elevatedOnPage: number
}

export type AlertOccurrenceLifecycleAction = 'acknowledge' | 'investigate' | 'close' | 'change_disposition'

export interface AlertBackfillDraft {
  since: string
  until: string
  limit: string
}

export function buildAlertOccurrencesPath(
  filters: AlertOccurrenceFilters,
  page: number,
  pageSize: number,
): string {
  const params = new URLSearchParams({
    page: String(Math.max(1, page)),
    page_size: String(normalizePageSize(pageSize)),
  })
  filters.lifecycleStates.forEach((state) => params.append('lifecycle_states', state))
  filters.severities.forEach((severity) => params.append('severities', severity))
  if (filters.ruleId) params.set('alert_interest_id', filters.ruleId)
  if (filters.suppressed !== 'any') params.set('suppressed', String(filters.suppressed === 'yes'))
  if (filters.snoozed !== 'any') params.set('snoozed', String(filters.snoozed === 'yes'))
  appendIsoDateTime(params, 'since', filters.since)
  appendIsoDateTime(params, 'until', filters.until)
  return `/alerts/occurrences?${params.toString()}`
}

export function filterLoadedOccurrences(
  occurrences: AlertOccurrence[],
  search: string,
): AlertOccurrence[] {
  const needle = search.trim().toLocaleLowerCase()
  if (!needle) return occurrences
  return occurrences.filter((occurrence) => {
    const source = getAlertOccurrenceSource(occurrence)
    return [
      occurrence.id,
      occurrence.item_id,
      occurrence.alert_name_snapshot,
      occurrence.alert_category_snapshot,
      source.title,
      source.feedName,
      ...occurrence.matched_keywords,
      ...occurrence.alert_keywords_snapshot,
    ].some((value) => value?.toLocaleLowerCase().includes(needle))
  })
}

export function getAlertOccurrenceSource(occurrence: AlertOccurrence): AlertOccurrenceSource {
  const snapshot = asRecord(occurrence.source_snapshot_json)
  const item = asRecord(snapshot.item)
  const feed = asRecord(snapshot.feed)
  const classification = asRecord(snapshot.classification)
  return {
    title: readString(item.title) ?? `Occurrence ${shortIdentifier(occurrence.id)}`,
    summary: normalizeSummary(readString(item.summary)),
    url: safeExternalUrl(readString(item.canonical_url) ?? readString(item.url)),
    feedName: readString(feed.name),
    classification: readString(classification.primary_category),
    publishedAt: readString(item.published_at),
    firstSeenAt: readString(item.first_seen_at),
  }
}

export function getAlertOccurrenceLifecycleActions(
  state: AlertOccurrenceState,
): AlertOccurrenceLifecycleAction[] {
  if (state === 'new') return ['acknowledge', 'investigate', 'close']
  if (state === 'acknowledged') return ['investigate', 'close']
  if (state === 'investigating') return ['close']
  return ['change_disposition']
}

export function canBulkAcknowledge(occurrences: AlertOccurrence[]): boolean {
  return occurrences.length > 0 && occurrences.every((occurrence) => occurrence.lifecycle_state === 'new')
}

export function canBulkClose(occurrences: AlertOccurrence[]): boolean {
  return occurrences.length > 0 && occurrences.every((occurrence) => occurrence.lifecycle_state !== 'closed')
}

export function alertOccurrencePageStats(
  occurrences: AlertOccurrence[],
  total: number,
): AlertOccurrencePageStats {
  return {
    matching: total,
    newOnPage: occurrences.filter((occurrence) => occurrence.lifecycle_state === 'new').length,
    activeOnPage: occurrences.filter((occurrence) =>
      occurrence.lifecycle_state === 'acknowledged' || occurrence.lifecycle_state === 'investigating',
    ).length,
    elevatedOnPage: occurrences.filter((occurrence) =>
      occurrence.severity_snapshot === 'critical' || occurrence.severity_snapshot === 'high',
    ).length,
  }
}

export function alertOccurrenceResultRange(
  total: number,
  page: number,
  pageSize: number,
  count: number,
): string {
  if (total === 0 || count === 0) return '0 results'
  const first = (page - 1) * pageSize + 1
  return `${first}-${Math.min(total, first + count - 1)} of ${total}`
}

export function alertOccurrencePageCount(total: number, pageSize: number): number {
  return Math.max(1, Math.ceil(Math.max(0, total) / normalizePageSize(pageSize)))
}

export function alertOccurrenceActiveFilterCount(filters: AlertOccurrenceFilters): number {
  return filters.lifecycleStates.length
    + filters.severities.length
    + Number(Boolean(filters.ruleId))
    + Number(filters.suppressed !== 'any')
    + Number(filters.snoozed !== 'any')
    + Number(Boolean(filters.since))
    + Number(Boolean(filters.until))
}

export function validateAlertOccurrenceFilters(filters: AlertOccurrenceFilters): string | null {
  const since = parseLocalDateTime(filters.since)
  const until = parseLocalDateTime(filters.until)
  if (filters.since && !since) return 'Choose a valid start time.'
  if (filters.until && !until) return 'Choose a valid end time.'
  if (since && until && since.getTime() > until.getTime()) {
    return 'The created-since time must be before or equal to the created-until time.'
  }
  return null
}

export function formatAlertOccurrenceState(state: AlertOccurrenceState): string {
  return ALERT_OCCURRENCE_STATES.find((entry) => entry.value === state)?.label ?? state
}

export function formatAlertSeverity(severity: AlertSeverity): string {
  return ALERT_SEVERITIES.find((entry) => entry.value === severity)?.label ?? severity
}

export function formatAlertDisposition(disposition: string | null): string {
  if (!disposition) return 'Not set'
  return ALERT_CLOSURE_DISPOSITIONS.find((entry) => entry.value === disposition)?.label
    ?? sentenceCase(disposition)
}

export function formatAlertOccurrenceActivity(action: string): string {
  const labels: Record<string, string> = {
    created: 'Occurrence created',
    lifecycle_changed: 'Lifecycle changed',
    disposition_changed: 'Disposition changed',
    snoozed: 'Occurrence snoozed',
    snooze_cleared: 'Snooze cleared',
  }
  return labels[action] ?? sentenceCase(action)
}

export function formatActivityDetailKey(key: string): string {
  return sentenceCase(key)
}

export function formatActivityDetailValue(value: unknown): string {
  if (value === null || value === undefined) return 'None'
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  try {
    return JSON.stringify(value)
  } catch {
    return 'Unavailable'
  }
}

export function isAlertOccurrenceConflict(error: unknown): boolean {
  return error instanceof ApiError
    && error.status === 409
    && error.code === 'alert_occurrence_version_conflict'
}

export function isAlertOccurrencePermissionError(error: unknown): boolean {
  if (!(error instanceof Error)) return false
  const candidate = error as Error & { status?: unknown }
  return candidate.status === 403 && !/csrf|security token/i.test(error.message)
}

export function shortIdentifier(value: string | null): string {
  if (!value) return 'Unavailable'
  return value.length > 12 ? `${value.slice(0, 8)}...${value.slice(-4)}` : value
}

export function createDefaultBackfillDraft(now = new Date()): AlertBackfillDraft {
  const until = new Date(now)
  const since = new Date(now.getTime() - 24 * 60 * 60 * 1000)
  return {
    since: toLocalDateTimeInput(since),
    until: toLocalDateTimeInput(until),
    limit: '100',
  }
}

export function validateAlertBackfillDraft(draft: AlertBackfillDraft): string | null {
  const since = parseLocalDateTime(draft.since)
  const until = parseLocalDateTime(draft.until)
  const limit = Number(draft.limit)
  if (!since || !until) return 'Choose a valid start and end time.'
  if (since.getTime() > until.getTime()) return 'The start time must be before or equal to the end time.'
  if (until.getTime() - since.getTime() > 90 * 24 * 60 * 60 * 1000) {
    return 'The backfill window cannot exceed 90 days.'
  }
  if (!Number.isInteger(limit) || limit < 1 || limit > 500) {
    return 'The item limit must be a whole number between 1 and 500.'
  }
  return null
}

export function alertBackfillRequest(
  draft: AlertBackfillDraft,
  cursor?: { firstSeenAt: string; itemId: string } | null,
) {
  const since = parseLocalDateTime(draft.since)
  const until = parseLocalDateTime(draft.until)
  if (!since || !until) throw new Error('A valid backfill time window is required.')
  return {
    since: since.toISOString(),
    until: until.toISOString(),
    limit: Number(draft.limit),
    ...(cursor
      ? {
          cursor_first_seen_at: cursor.firstSeenAt,
          cursor_item_id: cursor.itemId,
        }
      : {}),
  }
}

export function alertBackfillDraftKey(draft: AlertBackfillDraft): string {
  return `${draft.since}|${draft.until}|${draft.limit}`
}

function appendIsoDateTime(params: URLSearchParams, key: string, value: string): void {
  const parsed = parseLocalDateTime(value)
  if (parsed) params.set(key, parsed.toISOString())
}

function parseLocalDateTime(value: string): Date | null {
  if (!value) return null
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

function toLocalDateTimeInput(value: Date): string {
  const local = new Date(value.getTime() - value.getTimezoneOffset() * 60_000)
  return local.toISOString().slice(0, 16)
}

function normalizePageSize(value: number): number {
  return ALERT_OCCURRENCE_PAGE_SIZES.includes(value as (typeof ALERT_OCCURRENCE_PAGE_SIZES)[number])
    ? value
    : ALERT_OCCURRENCE_PAGE_SIZES[0]
}

function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

function readString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null
}

function safeExternalUrl(value: string | null): string | null {
  if (!value) return null
  try {
    const url = new URL(value)
    return (url.protocol === 'http:' || url.protocol === 'https:') && !url.username && !url.password
      ? url.toString()
      : null
  } catch {
    return null
  }
}

function sentenceCase(value: string): string {
  const readable = value.replaceAll('_', ' ').trim()
  return readable ? `${readable.charAt(0).toUpperCase()}${readable.slice(1)}` : 'Recorded activity'
}

function normalizeSummary(value: string | null): string | null {
  if (!value) return null
  return formatAlertPreviewSummary(value) || null
}
