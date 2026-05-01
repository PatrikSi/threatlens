import { describe, expect, it } from 'vitest'

import { Feed } from '../types/api'
import {
  collectDirtyFeedScheduleDrafts,
  FEED_SCHEDULE_DRAFT_STORAGE_KEY,
  getFeedScheduleDraftStorageKey,
  isFeedScheduleDraftDirty,
  migrateLegacyFeedScheduleDraftStorage,
  normalizeFeedScheduleDraft,
  readPersistedFeedScheduleDrafts,
  validateFeedScheduleDraft,
} from './feedScheduleDraft'

function createFeed(overrides: Partial<Feed> = {}): Feed {
  return {
    id: 'feed-1',
    name: 'Edge Advisories',
    url: 'https://example.com/feed.xml',
    description: null,
    site_url: null,
    language: null,
    enabled: true,
    fetch_mode: 'schedule',
    fetch_interval_seconds: 1800,
    schedule_cron: '0 * * * *',
    etag: null,
    last_modified: null,
    last_fetch_at: null,
    last_success_at: null,
    error_count: 0,
    last_error: null,
    has_unreadable_url: false,
    created_at: '2026-04-18T00:00:00Z',
    ...overrides,
  }
}

describe('validateFeedScheduleDraft', () => {
  it('rejects interval drafts below the minimum fetch cadence', () => {
    expect(
      validateFeedScheduleDraft({
        fetchMode: 'interval',
        intervalSeconds: '59',
        scheduleCron: '0 * * * *',
      }),
    ).toBe('Interval must be at least 60 seconds.')
  })

  it('rejects blank cron schedules', () => {
    expect(
      validateFeedScheduleDraft({
        fetchMode: 'schedule',
        intervalSeconds: '1800',
        scheduleCron: '   ',
      }),
    ).toBe('Schedule cannot be empty.')
  })

  it('rejects invalid cron schedules before submit', () => {
    expect(
      validateFeedScheduleDraft({
        fetchMode: 'schedule',
        intervalSeconds: '1800',
        scheduleCron: 'not a cron',
      }),
    ).toBe('Schedule must be a valid five-field cron expression.')

    expect(
      validateFeedScheduleDraft({
        fetchMode: 'schedule',
        intervalSeconds: '1800',
        scheduleCron: '*/15 * * * MON-FRI',
      }),
    ).toBeNull()
  })
})

describe('normalizeFeedScheduleDraft', () => {
  it('trims schedule input and normalizes interval values before save', () => {
    expect(
      normalizeFeedScheduleDraft({
        fetchMode: 'interval',
        intervalSeconds: ' 1800.9 ',
        scheduleCron: '  */15 * * * *  ',
      }),
    ).toEqual({
      fetchMode: 'interval',
      intervalSeconds: '1800',
      scheduleCron: '*/15 * * * *',
    })
  })
})

describe('isFeedScheduleDraftDirty', () => {
  it('treats equivalent trimmed schedule values as unchanged', () => {
    expect(
      isFeedScheduleDraftDirty(createFeed(), {
        fetchMode: 'schedule',
        intervalSeconds: '1800',
        scheduleCron: ' 0 * * * * ',
      }),
    ).toBe(false)
  })

  it('detects a real fetch-mode change that still needs to be saved', () => {
    expect(
      isFeedScheduleDraftDirty(
        createFeed({
          fetch_mode: 'interval',
          fetch_interval_seconds: 900,
          schedule_cron: null,
        }),
        {
          fetchMode: 'schedule',
          intervalSeconds: '900',
          scheduleCron: '*/30 * * * *',
        },
      ),
    ).toBe(true)
  })
})

describe('collectDirtyFeedScheduleDrafts', () => {
  it('persists only rows that still differ from the server copy', () => {
    const feeds = [
      createFeed({ id: 'feed-1' }),
      createFeed({
        id: 'feed-2',
        fetch_mode: 'interval',
        fetch_interval_seconds: 900,
        schedule_cron: null,
      }),
    ]

    expect(
      collectDirtyFeedScheduleDrafts(feeds, {
        'feed-1': {
          fetchMode: 'schedule',
          intervalSeconds: '1800',
          scheduleCron: '0 * * * *',
        },
        'feed-2': {
          fetchMode: 'interval',
          intervalSeconds: '1200',
          scheduleCron: '0 * * * *',
        },
      }),
    ).toEqual({
      'feed-2': {
        fetchMode: 'interval',
        intervalSeconds: '1200',
        scheduleCron: '0 * * * *',
      },
    })
  })
})

describe('feed schedule draft storage', () => {
  it('migrates legacy global feed schedule drafts into a user-scoped key once', () => {
    const store = new Map<string, string>()
    const storage = {
      getItem: (key: string) => (store.has(key) ? store.get(key)! : null),
      setItem: (key: string, value: string) => {
        store.set(key, value)
      },
      removeItem: (key: string) => {
        store.delete(key)
      },
    }
    const legacyDrafts = {
      'feed-1': {
        fetchMode: 'schedule',
        intervalSeconds: '1800',
        scheduleCron: '0 * * * *',
      },
    }

    store.set(FEED_SCHEDULE_DRAFT_STORAGE_KEY, JSON.stringify(legacyDrafts))
    migrateLegacyFeedScheduleDraftStorage(storage, 'alice')

    expect(store.has(FEED_SCHEDULE_DRAFT_STORAGE_KEY)).toBe(false)
    expect(readPersistedFeedScheduleDrafts(storage, getFeedScheduleDraftStorageKey('alice'))).toEqual(legacyDrafts)
    expect(readPersistedFeedScheduleDrafts(storage, getFeedScheduleDraftStorageKey('bob'))).toEqual({})

    store.set(FEED_SCHEDULE_DRAFT_STORAGE_KEY, JSON.stringify({ 'feed-2': legacyDrafts['feed-1'] }))
    migrateLegacyFeedScheduleDraftStorage(storage, 'alice')

    expect(readPersistedFeedScheduleDrafts(storage, getFeedScheduleDraftStorageKey('alice'))).toEqual(legacyDrafts)
    expect(store.has(FEED_SCHEDULE_DRAFT_STORAGE_KEY)).toBe(false)
  })

  it('ignores storage failures during legacy draft migration', () => {
    const storage = {
      getItem: () => {
        throw new Error('storage unavailable')
      },
      setItem: () => {
        throw new Error('storage unavailable')
      },
      removeItem: () => {
        throw new Error('storage unavailable')
      },
    }

    expect(() => migrateLegacyFeedScheduleDraftStorage(storage, 'alice')).not.toThrow()
  })
})
