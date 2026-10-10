import { describe, expect, it } from 'vitest'
import { createDefaultDraft, createRequestFromDraft, createDraftFromWebhook } from './notificationWebhookDraft'
import { conditionEventWarning, countConditions, newCondition, normalizeConditions, validateConditions } from './webhookConditionModel'
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
  it('defaults full article text off for legacy destinations and preserves explicit opt-in through saves', () => {
    const legacy = { ...createDefaultDraft(), id: 'hook', user_id: 'user', created_at: '', updated_at: '' } as NotificationWebhook
    delete legacy.include_article_text
    expect(createDraftFromWebhook(legacy).include_article_text).toBe(false)
    const optedIn = createDraftFromWebhook({ ...legacy, include_article_text: true, event_type: 'article.ai.ready' })
    expect(createRequestFromDraft(optedIn)).toMatchObject({ include_article_text: true, event_type: 'article.ai.ready' })
  })
  it('keeps same-indicator predicates explicit and rejects cross-scope fields', () => {
    const scoped: WebhookConditionGroup = { op: 'indicators_any', conditions: [
      { field: 'ioc_type', operator: 'in', value: ['domain'] },
      { field: 'analyst_verdict', operator: 'in', value: [' malicious '] },
    ] }
    expect(validateConditions(scoped)).toBeNull()
    expect(normalizeConditions(scoped)).toMatchObject({ op: 'indicators_any', conditions: [
      { value: ['domain'] }, { value: ['malicious'] },
    ] })
    expect(validateConditions({ ...scoped, conditions: [tree] })).toMatch(/only indicator fields/)
    expect(validateConditions({ ...scoped, conditions: [scoped] })).toMatch(/cannot contain another/)
    const draft = { ...createDefaultDraft(), conditions: scoped }
    expect(createRequestFromDraft(draft).conditions?.op).toBe('indicators_any')
  })
  it('starts with evidence available on the selected event while keeping indicator scope valid', () => {
    expect(newCondition('article.ai.ready')).toEqual({ field: 'ai_relevance_score', operator: 'gte', value: 0.8 })
    for (const event of ['rss_item_new', 'alert_match', 'feed_failing', 'webhook_failed', 'daily_digest', 'report_ready'] as const)
      expect(newCondition(event)).toEqual({ field: 'freshness_seconds', operator: 'lte', value: 86400 })
    for (const event of ['intel.extraction.ready', 'intel.indicators.changed', 'hunt.approved'] as const)
      expect(newCondition(event)).toEqual(newCondition())
    const sameIndicator: WebhookConditionGroup = { op: 'indicators_any', conditions: [newCondition('article.ai.ready', true)] }
    expect(sameIndicator.conditions[0]).toMatchObject({ field: 'ioc_role' })
    expect(validateConditions(sameIndicator)).toBeNull()
  })
  it('warns about incompatible evidence without turning advisory guidance into validation', () => {
    expect(conditionEventWarning('ioc_role', 'article.ai.ready')).toContain('Indicator extraction ready')
    expect(conditionEventWarning('ai_relevance_score', 'feed_failing')).toContain('AI article analysis ready')
    expect(conditionEventWarning('alert_rule_id', 'article.ai.ready')).toContain('Alert match')
    expect(conditionEventWarning('team_id', 'rss_item_new')).toContain('Team ownership')
    expect(conditionEventWarning('hunt_review_status', 'intel.extraction.ready')).toContain('Hunt approved')
    expect(conditionEventWarning('tag', 'daily_digest')).toContain('article tags')
    expect(conditionEventWarning('feed_id', 'report_ready')).toContain('single feed')
    for (const field of ['ai_relevance_score', 'ai_relevance_label', 'tag', 'feed_id', 'freshness_seconds'] as const)
      expect(conditionEventWarning(field, 'article.ai.ready')).toBeNull()
    for (const field of ['ioc_role', 'attack_technique', 'team_id', 'hunt_review_status'] as const)
      expect(conditionEventWarning(field, 'hunt.approved')).toBeNull()
    expect(conditionEventWarning('alert_rule_id', 'alert_match')).toBeNull()
    expect(conditionEventWarning('team_id', 'intel.indicators.changed')).toBeNull()
    expect(conditionEventWarning('hunt_review_status', 'intel.indicators.changed')).toContain('Hunt approved')
    expect(conditionEventWarning('ioc_type')).toBeNull()
    const condition: WebhookConditionGroup = { op: 'any', conditions: [newCondition(), newCondition('article.ai.ready')] }
    expect(validateConditions(condition)).toBeNull()
    expect(createRequestFromDraft({ ...createDefaultDraft(), event_type: 'article.ai.ready', conditions: condition }).conditions).toEqual(condition)
  })
})
