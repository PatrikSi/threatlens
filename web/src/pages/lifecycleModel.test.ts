import { describe, expect, it } from 'vitest'

import type { LifecyclePolicy, LifecyclePreview } from '../types/lifecycle'
import {
  lifecycleChangeIsDestructive,
  lifecycleDraftError,
  lifecycleDraftFromPolicy,
  lifecycleDraftIsEqual,
  lifecycleNextScheduleAt,
  lifecyclePreviewCountLabel,
  lifecyclePreviewIsFreshForDraft,
  lifecycleScheduleLabel,
} from './lifecycleModel'

const policy: LifecyclePolicy = {
  target_key: 'article_content',
  enabled: false,
  retention_days: 365,
  schedule_cadence: 'daily',
  schedule_hour_utc: 2,
  schedule_weekday: null,
  max_records_per_run: 10_000,
  options: { protect_starred: true },
  revision: 1,
  next_run_at: null,
  last_run_at: null,
  last_run_status: null,
  configuration_updated_at: '2026-09-02T10:00:00Z',
  updated_at: '2026-09-02T10:00:00Z',
  updated_by: null,
}

describe('lifecycle policy model', () => {
  it('maps backend weekday zero to Monday and six to Sunday', () => {
    expect(lifecycleScheduleLabel({
      ...policy,
      schedule_cadence: 'weekly',
      schedule_weekday: 0,
    })).toBe('Monday at 02:00 UTC')
    expect(lifecycleScheduleLabel({
      ...policy,
      schedule_cadence: 'weekly',
      schedule_weekday: 6,
    })).toBe('Sunday at 02:00 UTC')
  })

  it('calculates the next weekly schedule using backend Monday-zero weekdays', () => {
    const next = lifecycleNextScheduleAt({
      schedule_cadence: 'weekly',
      schedule_hour_utc: 2,
      schedule_weekday: 0,
    }, new Date('2026-09-06T12:00:00Z'))

    expect(next.toISOString()).toBe('2026-09-07T02:00:00.000Z')
  })

  it('compares safeguards independently of object insertion order', () => {
    const left = lifecycleDraftFromPolicy({
      ...policy,
      options: { protect_starred: true, protect_notes: false },
    })
    const right = {
      ...left,
      options: { protect_notes: false, protect_starred: true },
    }
    expect(lifecycleDraftIsEqual(left, right)).toBe(true)
  })

  it('validates target retention bounds and bounded batch sizes', () => {
    const target = {
      key: 'article_content' as const,
      label: 'Fetched article content',
      category: 'intelligence' as const,
      description: 'Extracted payloads.',
      action_description: 'Purges extracted payloads.',
      cutoff_description: 'Publication time.',
      min_retention_days: 7,
      max_retention_days: 3650,
      default_retention_days: 365,
      safeguards: [],
      policy,
      latest_preview: null,
    }
    expect(lifecycleDraftError(target, {
      ...lifecycleDraftFromPolicy(policy),
      retention_days: 6,
    })).toContain('7 to 3,650 days')
    expect(lifecycleDraftError(target, {
      ...lifecycleDraftFromPolicy(policy),
      max_records_per_run: 100_001,
    })).toContain('100 to 100,000')
  })

  it('treats disabling a safeguard as a destructive change', () => {
    const baseline = lifecycleDraftFromPolicy({
      ...policy,
      enabled: true,
      options: { protect_starred: true, protect_notes: true },
    })
    expect(lifecycleChangeIsDestructive(baseline, {
      ...baseline,
      options: { ...baseline.options, protect_starred: false },
    })).toBe(true)
    expect(lifecycleChangeIsDestructive(baseline, {
      ...baseline,
      schedule_hour_utc: 3,
    })).toBe(false)
    expect(lifecycleChangeIsDestructive(baseline, {
      ...baseline,
      max_records_per_run: baseline.max_records_per_run + 1_000,
    })).toBe(true)
  })

  it('requires a matching unexpired preview from the current revision', () => {
    const preview = {
      policy_revision: 3,
      expires_at: '2026-09-02T10:15:00Z',
      count_is_lower_bound: true,
      is_partial: false,
    } as LifecyclePreview

    expect(lifecyclePreviewIsFreshForDraft({
      preview,
      previewMatchesDraft: true,
      policyRevision: 3,
      now: Date.parse('2026-09-02T10:00:00Z'),
    })).toBe(true)
    expect(lifecyclePreviewIsFreshForDraft({
      preview,
      previewMatchesDraft: true,
      policyRevision: 3,
      now: Date.parse('2026-09-02T10:16:00Z'),
    })).toBe(false)
    expect(lifecyclePreviewCountLabel(24, preview)).toBe('At least 24')
  })
})
