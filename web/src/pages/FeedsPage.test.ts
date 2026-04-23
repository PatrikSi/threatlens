import { describe, expect, it } from 'vitest'

import { Feed } from '../types/api'
import {
  collectDirtyFeedScheduleDrafts,
  isFeedScheduleDraftDirty,
  normalizeFeedScheduleDraft,
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
