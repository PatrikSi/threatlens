import { describe, expect, it } from 'vitest'
import { createDefaultDraft, createRequestFromDraft, createDraftFromWebhook } from './notificationWebhookDraft'
import { countConditions, normalizeConditions, validateConditions } from './webhookConditionModel'
import type { NotificationWebhook } from '../types/notifications'
import type { WebhookConditionGroup } from '../types/webhookAutomation'

describe('bounded webhook condition drafts', () => {
  const tree: WebhookConditionGroup = { op: 'all', conditions: [
    { field: 'ioc_type', operator: 'in', value: ['domain', ' url '] },
    { op: 'not', conditions: [{ field: 'tag', operator: 'in', value: [' example '] }] },
  ] }
  it('normalizes comma-separated values without mutating the draft or changing exclusions', () => {
    expect(countConditions(tree)).toBe(4)
    expect(validateConditions(tree)).toBeNull()
    expect(normalizeConditions(tree)?.conditions[0]).toMatchObject({ value: ['domain', 'url'] })
    expect(tree.conditions[0]).toMatchObject({ value: ['domain', ' url '] })
    expect(createRequestFromDraft({ ...createDefaultDraft(), conditions: tree }).conditions).toEqual(normalizeConditions(tree))
  })
  it('rejects invalid trees and backend limit violations before submission', () => {
    const leaf = tree.conditions[0]
    expect(validateConditions({ op: 'all', conditions: [] })).toMatch(/at least one/)
    expect(validateConditions({ op: 'not', conditions: [leaf, leaf] })).toMatch(/exactly one/)
    expect(validateConditions({ op: 'all', conditions: Array.from({ length: 32 }, () => leaf) })).toMatch(/32/)
    expect(validateConditions({ op: 'all', conditions: [{ field: 'freshness_seconds', operator: 'lte', value: 31_536_001 }] })).toMatch(/365/)
    expect(validateConditions({ op: 'all', conditions: [{ field: 'extraction_confidence', operator: 'gte', value: 1.1 }] })).toMatch(/between/)
    expect(validateConditions({ op: 'all', conditions: [{ field: 'tag', operator: 'in', value: ['ok', ''] }] })).toMatch(/empty/)
    expect(validateConditions({ op: 'all', conditions: [{ field: 'tag', operator: 'in', value: Array(51).fill('x') }] })).toMatch(/50/)
    const nested = (child: WebhookConditionGroup): WebhookConditionGroup => ({ op: 'all', conditions: [child] })
    expect(validateConditions(nested(nested(tree)))).toMatch(/four levels/)
  })
  it('keeps existing notification settings compatible and round-trips new automation settings', () => {
    const legacy = { ...createDefaultDraft(), id: 'hook', headers: [], created_at: '', updated_at: '', user_id: 'user' } as NotificationWebhook
    delete legacy.payload_mode; delete legacy.conditions; delete legacy.credential_profile_id
    expect(createDraftFromWebhook(legacy)).toMatchObject({ payload_mode: 'template', conditions: null, credential_profile_id: null })
    const saved = { ...legacy, payload_mode: 'automation_v1' as const, conditions: tree, credential_profile_id: 'profile' }
    const draft = createDraftFromWebhook(saved)
    expect(createRequestFromDraft(draft)).toMatchObject({ payload_mode: 'automation_v1', conditions: normalizeConditions(tree), credential_profile_id: 'profile' })
    expect(draft.conditions).not.toBe(tree)
  })
})
