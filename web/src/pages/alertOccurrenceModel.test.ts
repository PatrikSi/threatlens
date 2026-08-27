import { describe, expect, it } from 'vitest'

import { ApiError } from '../api/client'
import type { AlertOccurrence } from '../types/alerts'
import {
  DEFAULT_ALERT_OCCURRENCE_FILTERS,
  alertBackfillRequest,
  alertOccurrencePageCount,
  alertOccurrencePageStats,
  buildAlertOccurrencesPath,
  canBulkAcknowledge,
  canBulkClose,
  createDefaultBackfillDraft,
  filterLoadedOccurrences,
  getAlertOccurrenceLifecycleActions,
  getAlertOccurrenceSource,
  isAlertOccurrenceConflict,
  validateAlertBackfillDraft,
  validateAlertOccurrenceFilters,
} from './alertOccurrenceModel'

function occurrence(overrides: Partial<AlertOccurrence> = {}): AlertOccurrence {
  return {
    id: 'occurrence-1',
    alert_interest_id: 'rule-1',
    rule_id_snapshot: 'rule-1',
    owner_user_id: 'user-1',
    item_id: 'item-1',
    item_id_snapshot: 'item-1',
    integration_event_id: 'event-1',
    rule_revision: 3,
    item_content_hash: 'a'.repeat(64),
    alert_name_snapshot: 'Exchange watch',
    alert_category_snapshot: 'vulnerability',
    alert_keywords_snapshot: ['exchange', 'cve'],
    matched_keywords: ['exchange'],
    source_snapshot_json: {
      item: {
        title: 'Exchange vulnerability report',
        summary: 'A security update is available.',
        url: 'https://example.com/advisory',
        first_seen_at: '2026-08-27T10:00:00Z',
      },
      feed: { name: 'Vendor advisories' },
      classification: { primary_category: 'vulnerability' },
    },
    severity_snapshot: 'high',
    lifecycle_state: 'new',
    is_suppressed: false,
    suppressed_at: null,
    suppression_reason: null,
    is_snoozed: false,
    snoozed_until: null,
    snooze_reason: null,
    closure_disposition: null,
    acknowledged_at: null,
    acknowledged_by_user_id: null,
    investigating_at: null,
    investigating_by_user_id: null,
    closed_at: null,
    closed_by_user_id: null,
    version: 1,
    created_at: '2026-08-27T10:00:00Z',
    updated_at: '2026-08-27T10:00:00Z',
    ...overrides,
  }
}

describe('alert occurrence API model', () => {
  it('builds the exact repeated filter and stable pagination parameters expected by the API', () => {
    const filters = {
      ...DEFAULT_ALERT_OCCURRENCE_FILTERS,
      lifecycleStates: ['new', 'investigating'] as const,
      severities: ['critical', 'high'] as const,
      ruleId: 'rule-7',
      suppressed: 'no' as const,
      snoozed: 'yes' as const,
      since: '2026-08-01T09:30',
      until: '2026-08-27T17:45',
    }
    const path = buildAlertOccurrencesPath(
      {
        ...filters,
        lifecycleStates: [...filters.lifecycleStates],
        severities: [...filters.severities],
      },
      3,
      50,
    )
    const url = new URL(path, 'https://threatlens.local')

    expect(url.pathname).toBe('/alerts/occurrences')
    expect(url.searchParams.getAll('lifecycle_states')).toEqual(['new', 'investigating'])
    expect(url.searchParams.getAll('severities')).toEqual(['critical', 'high'])
    expect(url.searchParams.get('alert_interest_id')).toBe('rule-7')
    expect(url.searchParams.get('suppressed')).toBe('false')
    expect(url.searchParams.get('snoozed')).toBe('true')
    expect(url.searchParams.get('since')).toBe(new Date(filters.since).toISOString())
    expect(url.searchParams.get('until')).toBe(new Date(filters.until).toISOString())
    expect(url.searchParams.get('page')).toBe('3')
    expect(url.searchParams.get('page_size')).toBe('50')
  })

  it('limits free-text matching to explicit loaded-page fields and rejects unsafe source URLs', () => {
    const candidate = occurrence()
    expect(filterLoadedOccurrences([candidate], 'vendor advisories')).toEqual([candidate])
    expect(filterLoadedOccurrences([candidate], 'item-1')).toEqual([candidate])
    expect(filterLoadedOccurrences([candidate], 'not-present')).toEqual([])

    const unsafe = occurrence({
      source_snapshot_json: {
        item: {
          title: 'Unsafe source',
          summary: '<p>Vendor &amp; product update</p>',
          url: 'https://user:secret@example.com/advisory',
        },
      },
    })
    expect(getAlertOccurrenceSource(unsafe)).toMatchObject({
      title: 'Unsafe source',
      summary: 'Vendor & product update',
      url: null,
    })
  })

  it('exposes only supported lifecycle and all-or-nothing bulk transitions', () => {
    expect(getAlertOccurrenceLifecycleActions('new')).toEqual(['acknowledge', 'investigate', 'close'])
    expect(getAlertOccurrenceLifecycleActions('acknowledged')).toEqual(['investigate', 'close'])
    expect(getAlertOccurrenceLifecycleActions('investigating')).toEqual(['close'])
    expect(getAlertOccurrenceLifecycleActions('closed')).toEqual(['change_disposition'])
    expect(canBulkAcknowledge([occurrence(), occurrence({ id: 'occurrence-2' })])).toBe(true)
    expect(canBulkAcknowledge([occurrence({ lifecycle_state: 'investigating' })])).toBe(false)
    expect(canBulkClose([occurrence({ lifecycle_state: 'acknowledged' })])).toBe(true)
    expect(canBulkClose([occurrence({ lifecycle_state: 'closed' })])).toBe(false)
  })

  it('calculates honest page-scoped stats and bounded page counts', () => {
    const page = [
      occurrence(),
      occurrence({ id: 'occurrence-2', lifecycle_state: 'acknowledged', severity_snapshot: 'medium' }),
      occurrence({ id: 'occurrence-3', lifecycle_state: 'investigating', severity_snapshot: 'critical' }),
      occurrence({ id: 'occurrence-4', lifecycle_state: 'closed', severity_snapshot: 'low' }),
    ]
    expect(alertOccurrencePageStats(page, 137)).toEqual({
      matching: 137,
      newOnPage: 1,
      activeOnPage: 2,
      elevatedOnPage: 2,
    })
    expect(alertOccurrencePageCount(137, 50)).toBe(3)
    expect(alertOccurrencePageCount(0, 50)).toBe(1)
  })

  it('validates collection time ranges and bounded non-notifying backfill inputs', () => {
    expect(validateAlertOccurrenceFilters({
      ...DEFAULT_ALERT_OCCURRENCE_FILTERS,
      since: '2026-08-28T10:00',
      until: '2026-08-27T10:00',
    })).toContain('must be before')

    const draft = createDefaultBackfillDraft(new Date('2026-08-27T12:00:00Z'))
    expect(validateAlertBackfillDraft(draft)).toBeNull()
    expect(alertBackfillRequest(draft)).toEqual({
      since: new Date(draft.since).toISOString(),
      until: new Date(draft.until).toISOString(),
      limit: 100,
    })
    expect(validateAlertBackfillDraft({ ...draft, limit: '501' })).toContain('between 1 and 500')
    expect(validateAlertBackfillDraft({
      since: '2026-01-01T00:00',
      until: '2026-08-27T00:00',
      limit: '100',
    })).toContain('cannot exceed 90 days')
  })

  it('recognizes optimistic concurrency conflicts without treating generic 409 responses as stale data', () => {
    expect(isAlertOccurrenceConflict(new ApiError(
      'Alert occurrence changed since it was loaded: expected version 1, current version is 2.',
      409,
      '/alerts/occurrences/occurrence-1/lifecycle',
      null,
      { code: 'alert_occurrence_version_conflict' },
    ))).toBe(true)
    expect(isAlertOccurrenceConflict(new ApiError('A duplicate already exists.', 409, '/alerts'))).toBe(false)
  })
})
