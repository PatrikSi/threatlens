import { describe, expect, it } from 'vitest'

import { AlertInterest } from '../types/api'
import {
  alertSuppressionInputValue,
  alertSuppressionISOString,
  formatAlertPreviewSummary,
  getAlertSaveDisabledReason,
  getAlertSuppressionValidationError,
  groupAlertsByCategory,
  parseAlertKeywords,
} from './alertPageModel'

function createAlert(overrides: Partial<AlertInterest> = {}): AlertInterest {
  return {
    id: 'alert-1',
    user_id: 'user-1',
    name: 'VPN advisories',
    category: 'software',
    keywords: ['vpn'],
    enabled: true,
    created_at: '2026-04-20T10:00:00Z',
    updated_at: '2026-04-21T10:00:00Z',
    ...overrides,
  }
}

describe('alert page model', () => {
  it('normalizes comma-separated keywords and reports missing inputs', () => {
    expect(parseAlertKeywords(' vpn, , gateway ,zero-day ')).toEqual(['vpn', 'gateway', 'zero-day'])
    expect(getAlertSaveDisabledReason('', ['vpn'])).toBe('Enter an interest name.')
    expect(getAlertSaveDisabledReason('VPN', [])).toBe('Enter at least one keyword.')
    expect(getAlertSaveDisabledReason('VPN', ['vpn'])).toBeNull()
  })

  it('groups unknown server categories under Other without dropping alerts', () => {
    const software = createAlert()
    const futureCategory = createAlert({ id: 'alert-2', category: 'future_category' })
    const groups = groupAlertsByCategory([software, futureCategory])

    expect(groups.get('software')).toEqual([software])
    expect(groups.get('other')).toEqual([futureCategory])
  })

  it('turns HTML summaries into readable preview text', () => {
    expect(formatAlertPreviewSummary('<p>VPN &amp; gateway&nbsp;update &#65;</p>')).toBe(
      'VPN & gateway update A',
    )
  })

  it('validates suppression as a future paired timestamp and reason', () => {
    const now = new Date('2026-08-27T12:00:00Z')
    expect(getAlertSuppressionValidationError(false, '', '', now)).toBeNull()
    expect(getAlertSuppressionValidationError(true, '', '', now)).toContain('end time')
    expect(
      getAlertSuppressionValidationError(true, '2026-08-27T11:59', 'Maintenance', now),
    ).toContain('future')
    expect(getAlertSuppressionValidationError(true, '2030-01-01T09:00', '', now)).toContain(
      'reason',
    )
    expect(
      getAlertSuppressionValidationError(true, '2030-01-01T09:00', 'Maintenance', now),
    ).toBeNull()
  })

  it('round-trips suppression timestamps through datetime-local inputs', () => {
    const source = '2030-01-01T09:00:00Z'
    const localInput = alertSuppressionInputValue(source)
    expect(alertSuppressionISOString(localInput)).toBe(source.replace('Z', '.000Z'))
    expect(alertSuppressionInputValue('invalid')).toBe('')
    expect(alertSuppressionISOString('invalid')).toBeNull()
  })
})
