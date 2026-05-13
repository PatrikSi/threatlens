import { Feed } from '../types/api'
import {
  DEFAULT_SCHEDULE_CRON,
  FeedScheduleDraft,
  feedToScheduleDraft,
  normalizeFeedScheduleDraft,
  validateFeedScheduleDraft,
} from './feedScheduleDraft'

export type FeedEditDraft = {
  name: string
  url: string
  description: string
  siteUrl: string
  language: string
  enabled: boolean
  fetchMode: Feed['fetch_mode']
  intervalSeconds: string
  scheduleCron: string
}

export type FeedUpdatePayload = {
  name?: string
  url?: string
  description?: string | null
  site_url?: string | null
  language?: string | null
  enabled?: boolean
  fetch_mode?: Feed['fetch_mode']
  fetch_interval_seconds?: number
  schedule_cron?: string | null
}

export function feedToEditDraft(feed: Feed): FeedEditDraft {
  return {
    name: feed.name,
    url: feed.url,
    description: feed.description ?? '',
    siteUrl: feed.site_url ?? '',
    language: feed.language ?? '',
    enabled: feed.enabled,
    fetchMode: feed.fetch_mode,
    intervalSeconds: String(feed.fetch_interval_seconds || 1800),
    scheduleCron: feed.schedule_cron || DEFAULT_SCHEDULE_CRON,
  }
}

export function validateFeedEditDraft(feed: Feed, draft: FeedEditDraft): string | null {
  if (!draft.name.trim()) {
    return 'Feed name is required.'
  }

  if (!feed.has_unreadable_url && !draft.url.trim()) {
    return 'RSS URL is required.'
  }

  if (feed.url.includes('REDACTED') && draft.url.trim() !== feed.url.trim() && draft.url.includes('REDACTED')) {
    return 'Enter the full replacement RSS URL; redacted URL values cannot be saved.'
  }

  const scheduleError = validateFeedScheduleDraft(feedEditDraftToScheduleDraft(draft))
  if (scheduleError) {
    return scheduleError
  }

  return null
}

export function isFeedEditDraftDirty(feed: Feed, draft: FeedEditDraft): boolean {
  return Object.keys(buildFeedUpdatePayload(feed, draft)).length > 0
}

export function buildFeedUpdatePayload(feed: Feed, draft: FeedEditDraft): FeedUpdatePayload {
  const payload: FeedUpdatePayload = {}
  const name = draft.name.trim()
  if (name && name !== feed.name) {
    payload.name = name
  }

  const nextUrl = draft.url.trim()
  const currentUrl = feed.url.trim()
  if (nextUrl && nextUrl !== currentUrl) {
    payload.url = nextUrl
  }

  assignOptionalText(payload, 'description', draft.description, feed.description)
  assignOptionalText(payload, 'site_url', draft.siteUrl, feed.site_url)
  assignOptionalText(payload, 'language', draft.language, feed.language)

  if (draft.enabled !== feed.enabled) {
    payload.enabled = draft.enabled
  }

  const persistedSchedule = feedToScheduleDraft(feed)
  const normalizedSchedule = normalizeFeedScheduleDraft(feedEditDraftToScheduleDraft(draft))
  if (normalizedSchedule.fetchMode !== persistedSchedule.fetchMode) {
    payload.fetch_mode = normalizedSchedule.fetchMode
  }

  if (normalizedSchedule.fetchMode === 'interval') {
    if (
      normalizedSchedule.fetchMode !== persistedSchedule.fetchMode ||
      normalizedSchedule.intervalSeconds !== persistedSchedule.intervalSeconds
    ) {
      payload.fetch_interval_seconds = Number(normalizedSchedule.intervalSeconds)
    }
  } else if (
    normalizedSchedule.fetchMode !== persistedSchedule.fetchMode ||
    normalizedSchedule.scheduleCron !== persistedSchedule.scheduleCron
  ) {
    payload.schedule_cron = normalizedSchedule.scheduleCron
  }

  return payload
}

export function feedEditDraftToScheduleDraft(draft: FeedEditDraft): FeedScheduleDraft {
  return {
    fetchMode: draft.fetchMode,
    intervalSeconds: draft.intervalSeconds,
    scheduleCron: draft.scheduleCron,
  }
}

function assignOptionalText(
  payload: FeedUpdatePayload,
  key: 'description' | 'site_url' | 'language',
  draftValue: string,
  currentValue: string | null,
) {
  const nextValue = draftValue.trim()
  const previousValue = currentValue ?? ''
  if (nextValue !== previousValue) {
    payload[key] = nextValue || null
  }
}
