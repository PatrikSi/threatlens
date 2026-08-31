import {
  NotificationEventType,
  NotificationWebhook,
  NotificationWebhookField,
  NotificationWebhookWriteRequest,
} from '../types/api'

export type NotificationWebhookDraft = Omit<NotificationWebhookWriteRequest, 'body_template'> & {
  body_template: string
  content_type: string
}

export const EVENT_OPTIONS: Array<{
  value: NotificationEventType
  label: string
  description: string
}> = [
  { value: 'rss_item_new', label: 'New RSS item', description: 'Fire when a new RSS item is ingested from a feed.' },
  { value: 'alert_match', label: 'Alert match', description: 'Fire when an item matches one or more of your alert interests.' },
  { value: 'feed_failing', label: 'Feed failing', description: 'Fire when a feed hits repeated fetch failures.' },
  { value: 'webhook_failed', label: 'Webhook failed', description: 'Fire when one of your other webhook deliveries fails.' },
  { value: 'daily_digest', label: 'AI daily brief', description: 'Send the generated AI daily brief as soon as it is ready.' },
  { value: 'report_ready', label: 'Intelligence report', description: 'Send a completed intelligence report when delivery is requested.' },
]

const EVENT_DEFAULT_JSON_FIELDS: Record<NotificationEventType, NotificationWebhookField[]> = {
  rss_item_new: [
    { key: 'event.type', value: '{{ event.type }}' },
    { key: 'item.title', value: '{{ item.title }}' },
    { key: 'item.url', value: '{{ item.url }}' },
    { key: 'feed.name', value: '{{ feed.name }}' },
  ],
  alert_match: [
    { key: 'event.type', value: '{{ event.type }}' },
    { key: 'alert.primary_name', value: '{{ alert.primary_name }}' },
    { key: 'alert.matched_keywords', value: '{{ alert.matched_keywords }}' },
    { key: 'item.title', value: '{{ item.title }}' },
  ],
  feed_failing: [
    { key: 'event.type', value: '{{ event.type }}' },
    { key: 'feed.name', value: '{{ feed.name }}' },
    { key: 'feed.error_count', value: '{{ feed.error_count }}' },
    { key: 'feed.last_error', value: '{{ feed.last_error }}' },
  ],
  webhook_failed: [
    { key: 'event.type', value: '{{ event.type }}' },
    { key: 'failed_webhook.name', value: '{{ failed_webhook.name }}' },
    { key: 'failed_webhook.event_type', value: '{{ failed_webhook.event_type }}' },
    { key: 'failed_webhook.error', value: '{{ failed_webhook.error }}' },
  ],
  daily_digest: [
    { key: 'event.type', value: '{{ event.type }}' },
    { key: 'brief.date', value: '{{ brief.date }}' },
    { key: 'brief.title', value: '{{ brief.title }}' },
    { key: 'brief.text', value: '{{ brief.text }}' },
    { key: 'brief.item_count', value: '{{ brief.item_count }}' },
    { key: 'brief.key_points', value: '{{ brief.key_points }}' },
    { key: 'brief.recommended_actions', value: '{{ brief.recommended_actions }}' },
  ],
  report_ready: [
    { key: 'event.type', value: '{{ event.type }}' },
    { key: 'report.url', value: '{{ brief.url }}' },
    { key: 'brief.date', value: '{{ brief.date }}' },
    { key: 'brief.title', value: '{{ brief.title }}' },
    { key: 'brief.text', value: '{{ brief.text }}' },
    { key: 'brief.item_count', value: '{{ brief.item_count }}' },
    { key: 'brief.key_points', value: '{{ brief.key_points }}' },
    { key: 'brief.recommended_actions', value: '{{ brief.recommended_actions }}' },
  ],
}

export function createDefaultDraft(): NotificationWebhookDraft {
  return {
    name: '',
    enabled: true,
    event_type: 'rss_item_new',
    url_template: '',
    method: 'POST',
    feed_scope: 'all',
    feed_ids: [],
    query_params: [],
    headers: [],
    body_mode: 'json',
    body_fields: buildDefaultJsonFields('rss_item_new'),
    body_template: '',
    content_type: '',
    timeout_seconds: 10,
  }
}

export function createDraftFromWebhook(webhook: NotificationWebhook): NotificationWebhookDraft {
  const { contentType, headers } = splitContentTypeHeader(webhook.headers)
  return {
    name: webhook.name,
    enabled: webhook.enabled,
    event_type: webhook.event_type,
    url_template: webhook.url_template,
    method: webhook.method,
    feed_scope: webhook.feed_scope,
    feed_ids: [...webhook.feed_ids],
    query_params: webhook.query_params.map((field) => ({ ...field })),
    headers: headers.map((field) => ({ ...field })),
    body_mode: webhook.body_mode,
    body_fields: webhook.body_fields.map((field) => ({ ...field })),
    body_template: webhook.body_template ?? '',
    content_type: contentType,
    timeout_seconds: webhook.timeout_seconds,
  }
}

export function createRequestFromDraft(draft: NotificationWebhookDraft): NotificationWebhookWriteRequest {
  const normalizedDraft = normalizeDraftUrlQuery(draft)
  return {
    name: normalizedDraft.name.trim(),
    enabled: normalizedDraft.enabled,
    event_type: normalizedDraft.event_type,
    url_template: normalizedDraft.url_template.trim(),
    method: normalizedDraft.method,
    feed_scope: normalizedDraft.feed_scope,
    feed_ids: normalizedDraft.feed_scope === 'selected' ? normalizedDraft.feed_ids : [],
    query_params: sanitizeFields(normalizedDraft.query_params),
    headers: buildRequestHeaders(normalizedDraft),
    body_mode: normalizedDraft.body_mode,
    body_fields:
      normalizedDraft.body_mode === 'json' || normalizedDraft.body_mode === 'form'
        ? sanitizeFields(normalizedDraft.body_fields)
        : [],
    body_template: normalizedDraft.body_mode === 'raw' ? normalizedDraft.body_template : null,
    timeout_seconds: normalizedDraft.timeout_seconds,
  }
}

export function applyBodyMode(
  current: NotificationWebhookDraft,
  nextBodyMode: NotificationWebhookDraft['body_mode'],
): NotificationWebhookDraft {
  if (nextBodyMode === 'json') {
    return {
      ...current,
      body_mode: 'json',
      body_fields: current.body_fields.length ? current.body_fields : buildDefaultJsonFields(current.event_type),
      body_template: '',
    }
  }
  if (nextBodyMode === 'form') {
    return {
      ...current,
      body_mode: 'form',
      body_fields: current.body_fields.length ? current.body_fields : [emptyWebhookField()],
      body_template: '',
    }
  }
  if (nextBodyMode === 'raw') {
    return { ...current, body_mode: 'raw', body_template: current.body_template }
  }
  return { ...current, body_mode: 'none', body_fields: [], body_template: '' }
}

export function applyEventType(
  current: NotificationWebhookDraft,
  eventType: NotificationEventType,
): NotificationWebhookDraft {
  const next = { ...current, event_type: eventType }
  if (current.body_mode !== 'json') {
    return next
  }
  const currentFields = JSON.stringify(current.body_fields)
  const currentDefaultFields = JSON.stringify(buildDefaultJsonFields(current.event_type))
  if (!current.body_fields.length || currentFields === currentDefaultFields) {
    return { ...next, body_fields: buildDefaultJsonFields(eventType) }
  }
  return next
}

export function normalizeDraftUrlQuery(current: NotificationWebhookDraft): NotificationWebhookDraft {
  const extracted = extractUrlQueryParams(current.url_template)
  if (extracted == null) {
    return current
  }
  return {
    ...current,
    url_template: extracted.baseUrl,
    query_params: mergeQueryParams(current.query_params, extracted.queryParams),
  }
}

export function toggleFeedSelection(current: NotificationWebhookDraft, feedId: string): NotificationWebhookDraft {
  const alreadySelected = current.feed_ids.includes(feedId)
  return {
    ...current,
    feed_scope: 'selected',
    feed_ids: alreadySelected ? current.feed_ids.filter((candidate) => candidate !== feedId) : [...current.feed_ids, feedId],
  }
}

export function updateWebhookField(
  fields: NotificationWebhookField[],
  index: number,
  patch: Partial<NotificationWebhookField>,
): NotificationWebhookField[] {
  return fields.map((field, candidateIndex) => (candidateIndex === index ? { ...field, ...patch } : field))
}

export function emptyWebhookField(): NotificationWebhookField {
  return { key: '', value: '' }
}

export function resolveDefaultContentTypeLabel(bodyMode: NotificationWebhookDraft['body_mode']): string {
  if (bodyMode === 'json') return 'application/json'
  if (bodyMode === 'form') return 'application/x-www-form-urlencoded'
  if (bodyMode === 'raw') return 'auto detect'
  return 'none'
}

export function describeEventType(eventType: NotificationEventType): string {
  return EVENT_OPTIONS.find((option) => option.value === eventType)?.label ?? eventType
}

export function describeEventDescription(eventType: NotificationEventType): string {
  return EVENT_OPTIONS.find((option) => option.value === eventType)?.description ?? eventType
}

export function resolveNotificationEventAvailability(
  aiDailyBriefAvailable: boolean,
  selectedEventType: NotificationEventType,
  aiReportingAvailable = true,
) {
  return {
    availableEventOptions: EVENT_OPTIONS.filter(
      (option) => (option.value !== 'daily_digest' || aiDailyBriefAvailable) &&
        (option.value !== 'report_ready' || aiReportingAvailable),
    ),
    unavailableDailyBriefSelected: !aiDailyBriefAvailable && selectedEventType === 'daily_digest',
    unavailableReportSelected: !aiReportingAvailable && selectedEventType === 'report_ready',
  }
}

function buildDefaultJsonFields(eventType: NotificationEventType): NotificationWebhookField[] {
  return EVENT_DEFAULT_JSON_FIELDS[eventType].map((field) => ({ ...field }))
}

function sanitizeFields(fields: NotificationWebhookField[]): NotificationWebhookField[] {
  return fields
    .map((field) => ({ key: field.key.trim(), value: field.value }))
    .filter((field) => field.key.length > 0)
}

function splitContentTypeHeader(
  headers: NotificationWebhookField[],
): { contentType: string; headers: NotificationWebhookField[] } {
  let contentType = ''
  const remainingHeaders: NotificationWebhookField[] = []
  for (const header of headers) {
    if (header.key.trim().toLowerCase() === 'content-type') {
      if (!contentType) contentType = header.value
      continue
    }
    remainingHeaders.push({ ...header })
  }
  return { contentType, headers: remainingHeaders }
}

function buildRequestHeaders(draft: NotificationWebhookDraft): NotificationWebhookField[] {
  const headers = sanitizeFields(draft.headers).filter((header) => header.key.trim().toLowerCase() !== 'content-type')
  const contentType = draft.content_type.trim()
  if (contentType) headers.push({ key: 'Content-Type', value: contentType })
  return headers
}

function extractUrlQueryParams(
  urlTemplate: string,
): { baseUrl: string; queryParams: NotificationWebhookField[] } | null {
  const trimmedUrl = urlTemplate.trim()
  const queryIndex = trimmedUrl.indexOf('?')
  if (queryIndex === -1) return null

  const hashIndex = trimmedUrl.indexOf('#', queryIndex)
  const baseUrl =
    hashIndex === -1
      ? trimmedUrl.slice(0, queryIndex)
      : `${trimmedUrl.slice(0, queryIndex)}${trimmedUrl.slice(hashIndex)}`
  const rawQuery =
    hashIndex === -1 ? trimmedUrl.slice(queryIndex + 1) : trimmedUrl.slice(queryIndex + 1, hashIndex)
  const queryParams = rawQuery
    .split('&')
    .map((segment) => segment.trim())
    .filter(Boolean)
    .map((segment) => {
      const separatorIndex = segment.indexOf('=')
      const rawKey = separatorIndex === -1 ? segment : segment.slice(0, separatorIndex)
      const rawValue = separatorIndex === -1 ? '' : segment.slice(separatorIndex + 1)
      return { key: decodeQueryComponent(rawKey), value: decodeQueryComponent(rawValue) }
    })
    .filter((field) => field.key.trim().length > 0)
  return { baseUrl, queryParams }
}

function mergeQueryParams(
  existing: NotificationWebhookField[],
  extracted: NotificationWebhookField[],
): NotificationWebhookField[] {
  if (!extracted.length) return existing
  const extractedKeys = new Set(extracted.map((field) => field.key))
  return [...existing.filter((field) => !extractedKeys.has(field.key)), ...extracted]
}

function decodeQueryComponent(value: string): string {
  const normalized = value.replace(/\+/g, ' ')
  try {
    return decodeURIComponent(normalized)
  } catch {
    return normalized
  }
}
