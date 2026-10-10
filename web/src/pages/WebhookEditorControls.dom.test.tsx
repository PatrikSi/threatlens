// @vitest-environment jsdom
import { act, useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import { WebhookPayloadFieldPicker } from './WebhookPayloadFieldPicker'
import { WebhookConditionBuilder } from './WebhookConditionBuilder'
import { createDefaultDraft, createRequestFromDraft } from './notificationWebhookDraft'
import type { WebhookConditionGroup } from '../types/webhookAutomation'
import type { NotificationEventType } from '../types/notifications'
import { automationField, editAutomation } from './indicatorAutomationTestSupport'
import { deferred, intelButton, mountIntel, settle } from './articleIntelligenceTestSupport'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...(await original<object>()), apiFetch: vi.fn() }))
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
afterEach(() => { view?.close(); view = undefined; vi.clearAllMocks() })
const variables = [
  { key: 'item.title', description: 'Article title', example: 'Campaign' },
  { key: 'item.full_text', description: 'Full extracted article plain text', example: 'Article' },
  { key: 'item.full_text_status', description: 'Article text availability and truncation status', example: 'available' },
  { key: 'brief.text', description: 'Full report text', example: 'Report' },
]
function PayloadHarness({ scalar = false }: { scalar?: boolean }) {
  const [draft, setDraft] = useState(() => ({ ...createDefaultDraft(), body_fields: scalar ? [{ key: 'item', value: 'custom value' }] : [{ key: 'routing', value: 'soc' }] }))
  return <><WebhookPayloadFieldPicker draft={draft} onChange={setDraft} disabled={false} /><output>{JSON.stringify(createRequestFromDraft(draft))}</output></>
}
function Conditions({ initial }: { initial: WebhookConditionGroup }) {
  const [conditions, setConditions] = useState<WebhookConditionGroup | null>(initial)
  return <><WebhookConditionBuilder value={conditions} onChange={setConditions} disabled={false} /><output>{JSON.stringify(conditions)}</output></>
}
function EventConditions({ initial = null, event = 'article.ai.ready' }: { initial?: WebhookConditionGroup | null; event?: NotificationEventType }) {
  const [eventType, setEventType] = useState(event)
  const [conditions, setConditions] = useState(initial)
  return <>
    <label>Event type<select value={eventType} onChange={(event) => setEventType(event.target.value as NotificationEventType)}>
      <option value="article.ai.ready">AI article analysis ready</option>
      <option value="rss_item_new">New RSS item</option>
      <option value="feed_failing">Feed failing</option>
    </select></label>
    <WebhookConditionBuilder value={conditions} onChange={setConditions} eventType={eventType} disabled={false} />
    <output>{JSON.stringify(conditions)}</output>
  </>
}

describe('SOC webhook payload selection', () => {
  it('searches fields, adds full text without replacing custom fields, and prevents duplicate keys', async () => {
    vi.mocked(apiFetch).mockResolvedValue(variables)
    view = await mountIntel(<PayloadHarness />)
    editAutomation(view.host, 'Search payload fields', 'full text')
    expect(automationField(view.host, 'Payload field').querySelector('option[value="brief.text"]')).toBeNull()
    editAutomation(view.host, 'Payload field', 'item.full_text')
    act(() => intelButton(view!.host, 'Add payload field').click())
    await settle()
    expect(JSON.parse(view.host.querySelector('output')!.textContent!)).toMatchObject({ body_fields: [
      { key: 'routing', value: 'soc' }, { key: 'item.full_text', value: '{{ item.full_text }}' },
    ] })
    expect(intelButton(view.host, 'Add payload field').disabled).toBe(true)
    expect(view.host.textContent).toContain('already configured')
    expect(view.host.textContent).toContain('128 KiB')
    editAutomation(view.host, 'Search payload fields', 'availability')
    editAutomation(view.host, 'Payload field', 'item.full_text_status')
    act(() => intelButton(view!.host, 'Add payload field').click())
    await settle()
    expect(view.host.querySelector('output')!.textContent).toContain('{{ item.full_text_status }}')
  })
  it('rejects overlapping JSON paths and allows an explicit alternate output key', async () => {
    vi.mocked(apiFetch).mockResolvedValue(variables)
    view = await mountIntel(<PayloadHarness scalar />)
    editAutomation(view.host, 'Payload field', 'item.full_text')
    expect(intelButton(view.host, 'Add payload field').disabled).toBe(true)
    expect(view.host.textContent).toContain('overlaps an existing JSON path')
    editAutomation(view.host, 'Output key', 'source_text')
    act(() => intelButton(view!.host, 'Add payload field').click())
    await settle()
    expect(JSON.parse(view.host.querySelector('output')!.textContent!)).toMatchObject({ body_fields: [
      { key: 'item', value: 'custom value' }, { key: 'source_text', value: '{{ item.full_text }}' },
    ] })
  })
  it('keeps drafts during catalog failure and exposes retry', async () => {
    vi.mocked(apiFetch).mockRejectedValue(new ApiError('Field catalog unavailable', 503, '/notifications/template-variables'))
    view = await mountIntel(<PayloadHarness />)
    expect(view.host.textContent).toContain('Field catalog unavailable')
    expect(view.host.querySelector('output')!.textContent).toContain('soc')
    expect(intelButton(view.host, 'Add payload field').disabled).toBe(true)
    vi.mocked(apiFetch).mockResolvedValue(variables)
    act(() => intelButton(view!.host, 'Retry payload fields').click())
    await settle()
    expect(automationField(view.host, 'Payload field').querySelector('option[value="item.full_text"]')).not.toBeNull()
  })
})

describe('SOC webhook condition selection', () => {
  it('creates usable AI conditions and preserves them with guidance when the event changes', async () => {
    vi.mocked(apiFetch).mockResolvedValue([])
    view = await mountIntel(<EventConditions />)
    act(() => intelButton(view!.host, 'Add event conditions').click())
    expect(automationField(view.host, 'Field').value).toBe('ai_relevance_score')
    expect(automationField(view.host, 'Threshold').value).toBe('0.8')
    editAutomation(view.host, 'Threshold', '0.93')
    act(() => intelButton(view!.host, 'Add group').click())
    const accepted = JSON.parse(view.host.querySelector('output')!.textContent!)
    expect(accepted).toMatchObject({ conditions: [
      { field: 'ai_relevance_score', value: 0.93 },
      { op: 'all', conditions: [{ field: 'ai_relevance_score', value: 0.8 }] },
    ] })
    editAutomation(view.host, 'Event type', 'feed_failing')
    expect(view.host.querySelector('[role="status"]')?.textContent).toContain('Choose AI article analysis ready')
    expect(view.host.textContent).toContain('Your condition is preserved.')
    expect(JSON.parse(view.host.querySelector('output')!.textContent!)).toEqual(accepted)
    expect(view.host.querySelector('[role="alert"]')).toBeNull()
    editAutomation(view.host, 'Event type', 'article.ai.ready')
    expect(view.host.textContent).not.toContain('This event does not carry shared article relevance')
  })
  it('starts RSS conditions with event age and explains unsupported indicator evidence', async () => {
    vi.mocked(apiFetch).mockResolvedValue([])
    view = await mountIntel(<EventConditions event="rss_item_new" />)
    act(() => intelButton(view!.host, 'Add event conditions').click())
    expect(automationField(view.host, 'Field').value).toBe('freshness_seconds')
    expect(automationField(view.host, 'Comparison').value).toBe('lte')
    expect(automationField(view.host, 'Threshold').value).toBe('86400')
    editAutomation(view.host, 'Field', 'ioc_role')
    expect(view.host.querySelector('[role="status"]')?.textContent).toContain('does not carry indicator or ATT&CK evidence')
    expect(automationField(view.host, 'Field').value).toBe('ioc_role')
  })
  it('keeps new nested predicates inside a same-indicator group in indicator scope', async () => {
    vi.mocked(apiFetch).mockResolvedValue([])
    view = await mountIntel(<EventConditions initial={{ op: 'indicators_any', conditions: [{ field: 'ioc_type', operator: 'in', value: ['domain'] }] }} />)
    act(() => intelButton(view!.host, 'Add group').click())
    expect(JSON.parse(view.host.querySelector('output')!.textContent!)).toMatchObject({ conditions: [
      { field: 'ioc_type', value: ['domain'] },
      { op: 'all', conditions: [{ field: 'ioc_role', value: ['malicious_infrastructure'] }] },
    ] })
    expect(view.host.querySelector('[role="alert"]')).toBeNull()
    expect(view.host.textContent).toContain('This event does not carry indicator or ATT&CK evidence')
  })
  it('selects shared AI relevance with a bounded numeric threshold and named levels', async () => {
    vi.mocked(apiFetch).mockResolvedValue([])
    view = await mountIntel(<Conditions initial={{ op: 'all', conditions: [{ field: 'ioc_role', operator: 'in', value: ['unknown'] }] }} />)
    editAutomation(view.host, 'Field', 'ai_relevance_score')
    expect(automationField(view.host, 'Threshold').value).toBe('0.8')
    expect(view.host.textContent).toContain('independent of team assessments')
    editAutomation(view.host, 'Threshold', '1.1')
    expect(view.host.querySelector('[role="alert"]')?.textContent).toContain('AI relevance score must be between 0 and 1')
    editAutomation(view.host, 'Field', 'ai_relevance_label')
    editAutomation(view.host, 'Add a value', 'high')
    expect(JSON.parse(view.host.querySelector('output')!.textContent!)).toMatchObject({ conditions: [{ field: 'ai_relevance_label', operator: 'in', value: ['high'] }] })
    expect(view.host.querySelector('[role="alert"]')).toBeNull()
  })
  it('searches accessible names without removing unavailable saved IDs', async () => {
    const pending = deferred<unknown>()
    vi.mocked(apiFetch).mockReturnValue(pending.promise)
    view = await mountIntel(<Conditions initial={{ op: 'all', conditions: [{ field: 'feed_id', operator: 'in', value: ['removed-feed'] }] }} />)
    expect(view.host.textContent).toContain('removed-feed')
    await act(async () => pending.resolve([{ id: 'feed-1', name: 'Endpoint intelligence' }, { id: 'feed-2', name: 'Cloud intelligence' }]))
    await settle()
    editAutomation(view.host, 'Search values', 'endpoint')
    expect(automationField(view.host, 'Add a value').querySelector('option[value="feed-2"]')).toBeNull()
    editAutomation(view.host, 'Add a value', 'feed-1')
    expect(JSON.parse(view.host.querySelector('output')!.textContent!)).toMatchObject({ conditions: [{ value: ['removed-feed', 'feed-1'] }] })
    expect(view.host.textContent).toContain('Endpoint intelligence')
  })
  it('allows editing raw values after lookup denial without discarding the saved scope', async () => {
    vi.mocked(apiFetch).mockRejectedValue(new ApiError('Access denied', 403, '/feeds'))
    view = await mountIntel(<Conditions initial={{ op: 'all', conditions: [{ field: 'feed_id', operator: 'not_in', value: ['private-feed'] }] }} />)
    expect(view.host.textContent).toContain('Access denied')
    expect(view.host.querySelector('output')!.textContent).toContain('private-feed')
    editAutomation(view.host, 'Values (comma-separated)', 'private-feed,another-feed')
    expect(JSON.parse(view.host.querySelector('output')!.textContent!)).toMatchObject({ conditions: [{ operator: 'not_in', value: ['private-feed', 'another-feed'] }] })
  })
})
