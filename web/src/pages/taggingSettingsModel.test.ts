import { describe, expect, it } from 'vitest'

import {
  createDefaultRuleDraft,
  createRuleRequestFromDraft,
  getRuleDraftValidationError,
  parseTaggingReapplyRequest,
} from './taggingSettingsModel'

describe('tagging settings model', () => {
  it('validates retagging boundaries without accepting partial numbers', () => {
    expect(parseTaggingReapplyRequest('1', '0')).toEqual({ request: { days: 1, limit: 0 }, error: null })
    expect(parseTaggingReapplyRequest('365', '5000')).toEqual({ request: { days: 365, limit: 5000 }, error: null })
    expect(parseTaggingReapplyRequest('1.5', '10').error).toContain('Days Back')
    expect(parseTaggingReapplyRequest('30', '-1').error).toContain('Limit')
  })

  it('requires a feed when selected scope is used', () => {
    const draft = {
      ...createDefaultRuleDraft(),
      name: 'VPN disclosures',
      tag_name: 'vpn',
      pattern: 'vpn',
      feed_scope: 'selected' as const,
    }

    expect(getRuleDraftValidationError(draft)).toBe('Select at least one feed or switch the rule to Any feed.')
    expect(getRuleDraftValidationError({ ...draft, feed_ids: ['feed-1'] })).toBeNull()
  })

  it('trims rule text and omits stale feed IDs for all-feed rules', () => {
    const request = createRuleRequestFromDraft({
      ...createDefaultRuleDraft(),
      name: '  VPN disclosures ',
      tag_name: ' vpn ',
      pattern: ' gateway ',
      feed_scope: 'all',
      feed_ids: ['stale-feed'],
      min_classification_confidence: '0.7',
    })

    expect(request).toMatchObject({
      name: 'VPN disclosures',
      tag_name: 'vpn',
      pattern: 'gateway',
      feed_ids: [],
      min_classification_confidence: 0.7,
    })
  })
})
