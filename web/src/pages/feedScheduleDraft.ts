import { Feed } from '../types/api'

export type FeedScheduleDraft = {
  fetchMode: Feed['fetch_mode']
  intervalSeconds: string
  scheduleCron: string
}

export const DEFAULT_SCHEDULE_CRON = '0 * * * *'
export const FEED_SCHEDULE_DRAFT_STORAGE_KEY = 'threatlens.feed-schedule-drafts.v1'

const CRON_FIELD_RANGES = [
  { min: 0, max: 59 },
  { min: 0, max: 23 },
  { min: 1, max: 31 },
  { min: 1, max: 12, names: ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'] },
  { min: 0, max: 7, names: ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'] },
]

export function getFeedScheduleDraftStorageKey(userId: string): string {
  return `${FEED_SCHEDULE_DRAFT_STORAGE_KEY}:${userId}`
}

export function migrateLegacyFeedScheduleDraftStorage(
  storage: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>,
  userId: string,
) {
  try {
    const scopedKey = getFeedScheduleDraftStorageKey(userId)
    const scopedValue = storage.getItem(scopedKey)
    const legacyValue = storage.getItem(FEED_SCHEDULE_DRAFT_STORAGE_KEY)
    if (scopedValue === null && legacyValue !== null) {
      storage.setItem(scopedKey, legacyValue)
    }
    if (legacyValue !== null) {
      storage.removeItem(FEED_SCHEDULE_DRAFT_STORAGE_KEY)
    }
  } catch {
    // Storage can be unavailable in private or restricted browser contexts.
  }
}

export function readPersistedFeedScheduleDrafts(
  storage: Pick<Storage, 'getItem'>,
  storageKey: string,
): Record<string, FeedScheduleDraft> {
  try {
    const raw = storage.getItem(storageKey)
    if (!raw) {
      return {}
    }

    const parsed = JSON.parse(raw) as unknown
    if (typeof parsed !== 'object' || parsed === null) {
      return {}
    }

    const next: Record<string, FeedScheduleDraft> = {}
    for (const [feedId, entry] of Object.entries(parsed as Record<string, unknown>)) {
      if (typeof entry !== 'object' || entry === null) {
        continue
      }

      const draft = entry as Record<string, unknown>
      const fetchMode = draft.fetchMode
      const intervalSeconds = draft.intervalSeconds
      const scheduleCron = draft.scheduleCron
      if (fetchMode !== 'interval' && fetchMode !== 'schedule') {
        continue
      }
      if (typeof intervalSeconds !== 'string' || typeof scheduleCron !== 'string') {
        continue
      }

      next[feedId] = {
        fetchMode,
        intervalSeconds,
        scheduleCron,
      }
    }

    return next
  } catch {
    return {}
  }
}

export function feedToScheduleDraft(feed: Feed): FeedScheduleDraft {
  return {
    fetchMode: feed.fetch_mode,
    intervalSeconds: String(feed.fetch_interval_seconds || 1800),
    scheduleCron: feed.schedule_cron || DEFAULT_SCHEDULE_CRON,
  }
}

export function normalizeFeedScheduleDraft(draft: FeedScheduleDraft): FeedScheduleDraft {
  const trimmedInterval = draft.intervalSeconds.trim()
  const parsedInterval = Number(trimmedInterval)

  return {
    fetchMode: draft.fetchMode,
    intervalSeconds: Number.isFinite(parsedInterval) ? String(Math.floor(parsedInterval)) : trimmedInterval,
    scheduleCron: draft.scheduleCron.trim(),
  }
}

export function validateFeedScheduleDraft(draft: FeedScheduleDraft): string | null {
  if (draft.fetchMode === 'interval') {
    const trimmedInterval = draft.intervalSeconds.trim()
    const parsedInterval = Number(trimmedInterval)
    if (!trimmedInterval) {
      return 'Interval is required.'
    }
    if (!Number.isFinite(parsedInterval) || parsedInterval < 60) {
      return 'Interval must be at least 60 seconds.'
    }
    return null
  }

  const scheduleCron = draft.scheduleCron.trim()
  if (!scheduleCron) {
    return 'Schedule cannot be empty.'
  }
  if (!isValidCronExpression(scheduleCron)) {
    return 'Schedule must be a valid five-field cron expression.'
  }
  return null
}

export function isFeedScheduleDraftDirty(feed: Feed, draft: FeedScheduleDraft): boolean {
  const persistedDraft = feedToScheduleDraft(feed)
  const normalizedDraft = normalizeFeedScheduleDraft(draft)

  if (normalizedDraft.fetchMode !== persistedDraft.fetchMode) {
    return true
  }

  if (normalizedDraft.fetchMode === 'interval') {
    return normalizedDraft.intervalSeconds !== persistedDraft.intervalSeconds
  }

  return normalizedDraft.scheduleCron !== persistedDraft.scheduleCron
}

export function collectDirtyFeedScheduleDrafts(
  feeds: Feed[],
  drafts: Record<string, FeedScheduleDraft>,
): Record<string, FeedScheduleDraft> {
  const dirtyDrafts: Record<string, FeedScheduleDraft> = {}

  for (const feed of feeds) {
    const draft = drafts[feed.id]
    if (!draft || !isFeedScheduleDraftDirty(feed, draft)) {
      continue
    }
    dirtyDrafts[feed.id] = draft
  }

  return dirtyDrafts
}

function isValidCronExpression(value: string): boolean {
  const fields = value.trim().split(/\s+/)
  if (fields.length !== 5) {
    return false
  }

  return fields.every((field, index) => isValidCronField(field, CRON_FIELD_RANGES[index]))
}

function isValidCronField(
  field: string,
  range: { min: number; max: number; names?: string[] },
): boolean {
  if (!field) {
    return false
  }

  return field.split(',').every((segment) => isValidCronFieldSegment(segment, range))
}

function isValidCronFieldSegment(
  segment: string,
  range: { min: number; max: number; names?: string[] },
): boolean {
  const [base, step, extra] = segment.split('/')
  if (extra !== undefined || !base) {
    return false
  }
  if (step !== undefined && !isValidCronNumber(step, 1, range.max)) {
    return false
  }
  if (base === '*' || base === '?') {
    return true
  }

  const [start, end, trailing] = base.split('-')
  if (trailing !== undefined || !start) {
    return false
  }

  const startValue = parseCronFieldValue(start, range)
  if (startValue == null) {
    return false
  }
  if (end === undefined) {
    return true
  }

  const endValue = parseCronFieldValue(end, range)
  return endValue != null && startValue <= endValue
}

function parseCronFieldValue(value: string, range: { min: number; max: number; names?: string[] }): number | null {
  const normalized = value.toUpperCase()
  const namedIndex = range.names?.indexOf(normalized) ?? -1
  if (namedIndex >= 0) {
    return range.names?.[0] === 'SUN' ? namedIndex : namedIndex + 1
  }
  if (!isValidCronNumber(value, range.min, range.max)) {
    return null
  }
  return Number(value)
}

function isValidCronNumber(value: string, min: number, max: number): boolean {
  if (!/^\d+$/.test(value)) {
    return false
  }
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed >= min && parsed <= max
}
