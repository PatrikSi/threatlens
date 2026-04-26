import { Feed } from '../types/api'

export type FeedScheduleDraft = {
  fetchMode: Feed['fetch_mode']
  intervalSeconds: string
  scheduleCron: string
}

export const DEFAULT_SCHEDULE_CRON = '0 * * * *'
export const FEED_SCHEDULE_DRAFT_STORAGE_KEY = 'threatlens.feed-schedule-drafts.v1'

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

  return draft.scheduleCron.trim() ? null : 'Schedule cannot be empty.'
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
