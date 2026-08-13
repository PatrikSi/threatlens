import { describe, expect, it } from 'vitest'

import { CurrentUser } from '../types/api'
import {
  deriveAiQueryEnablement,
  deriveConfigurationSaveBlockedReason,
  deriveConnectionTestBlockedReason,
  isCandidateItemSearchReady,
  validateDailyBriefReprocessDays,
} from './aiSettingsPageState'

function currentUser(aiEnabled: boolean): CurrentUser {
  return {
    id: 'user-1',
    email: 'admin@example.com',
    role: 'admin',
    is_active: true,
    is_approved: true,
    approved_at: null,
    created_at: '2026-08-11T00:00:00Z',
    features: {
      ai_enabled: aiEnabled,
      ai_configured: aiEnabled,
      ai_summary_enabled: aiEnabled,
      ai_relevance_enabled: aiEnabled,
      ai_daily_brief_enabled: aiEnabled,
    },
  }
}

describe('AI settings page state', () => {
  it('keeps workload queries active while a tab transition settles', () => {
    expect(deriveAiQueryEnablement(currentUser(true), 'overview', 'activity')).toMatchObject({
      aiEnabled: true,
      overview: false,
      activity: true,
      workload: true,
    })
  })

  it('does not activate AI queries when the feature is disabled', () => {
    expect(deriveAiQueryEnablement(currentUser(false), 'configuration', 'configuration')).toEqual({
      aiEnabled: false,
      overview: false,
      activity: false,
      configuration: false,
      workload: false,
    })
  })

  it('requires a meaningful candidate-item scope', () => {
    expect(isCandidateItemSearchReady('a', 0, '', '')).toBe(false)
    expect(isCandidateItemSearchReady('ab', 0, '', '')).toBe(true)
    expect(isCandidateItemSearchReady('', 1, '', '')).toBe(true)
    expect(isCandidateItemSearchReady('', 0, '2026-08-01', '')).toBe(true)
  })

  it('prioritizes save and connection blockers consistently', () => {
    expect(deriveConfigurationSaveBlockedReason('Settings unavailable', true, false)).toBe('Settings unavailable')
    expect(deriveConfigurationSaveBlockedReason(null, true, true)).toContain('connection test')
    expect(deriveConfigurationSaveBlockedReason(null, false, false)).toBe('No AI settings changes to save.')
    expect(deriveConnectionTestBlockedReason(true, true, 2)).toContain('Checking queued')
    expect(deriveConnectionTestBlockedReason(true, false, 2)).toContain('2 AI tasks are')
    expect(deriveConnectionTestBlockedReason(false, true, 2)).toBeNull()
  })

  it('validates daily brief reprocessing against API and retention limits', () => {
    expect(validateDailyBriefReprocessDays('one', 30)).toContain('whole number')
    expect(validateDailyBriefReprocessDays('0', 30)).toContain('between 1 and 90')
    expect(validateDailyBriefReprocessDays('31', 30)).toContain('retained daily briefings')
    expect(validateDailyBriefReprocessDays('30', 30)).toBeNull()
  })
})
