// @vitest-environment jsdom
import { act, useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import type { WebhookMatchPreview } from '../types/webhookAutomation'
import { TeamIntegrationConfiguration } from './TeamIntegrationConfiguration'
import { createDefaultDraft, createRequestFromDraft } from './notificationWebhookDraft'
import { automationField, editAutomation } from './indicatorAutomationTestSupport'
import { deferred, intelButton, mountIntel, settle } from './articleIntelligenceTestSupport'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...(await original<object>()), apiFetch: vi.fn() }))
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
afterEach(() => { view?.close(); view = undefined; vi.clearAllMocks() })

const base = '/teams/team/integrations/dest'
const saved = {
  ...createRequestFromDraft({
    ...createDefaultDraft(),
    name: 'Team SIEM',
    event_type: 'article.ai.ready',
    url_template: 'https://receiver.test',
    payload_mode: 'automation_v1',
    include_article_text: true,
    conditions: { op: 'all', conditions: [{ field: 'ai_relevance_score', operator: 'gte', value: 0.8 }] },
  }),
  id: 'dest', user_id: 'custodian', team_id: 'team', ownership_revision: 2,
  created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z',
}
const sample = { events: [{ id: 'event-1', event_type: 'article.ai.ready', label: 'Sensitive article', created_at: '2026-10-01T00:00:00Z' }] }
const match: WebhookMatchPreview = {
  matches: true,
  checks: [],
  missing_fields: [],
  automation_payload: { data: { article_text: { text: 'Bounded article evidence' } } },
}
function Harness() {
  const [unavailable, setUnavailable] = useState(false)
  return <>
    <button onClick={() => setUnavailable(true)}>Disable editor</button>
    <TeamIntegrationConfiguration base={base} unavailable={unavailable} onChanged={vi.fn()} />
  </>
}
function setup({
  preview = () => Promise.resolve(match),
  events = () => Promise.resolve(sample),
  configuration = saved,
}: {
  preview?: () => Promise<WebhookMatchPreview>
  events?: () => Promise<typeof sample>
  configuration?: typeof saved & { secrets_redacted?: boolean }
} = {}) {
  vi.mocked(apiFetch).mockImplementation((path) => {
    if (path === `${base}/configuration`) return Promise.resolve(configuration)
    if (path.includes('/webhooks/events?')) return events()
    if (path === '/notifications/webhooks/preview') return preview()
    if (path === '/feeds' || path === '/notifications/template-variables') return Promise.resolve([])
    throw new Error(`Unexpected request ${path}`)
  })
}
async function evaluate() {
  await vi.waitFor(async () => {
    await settle()
    expect(view!.host.querySelector('option[value="event-1"]')).not.toBeNull()
  })
  editAutomation(view!.host, 'Sample event', 'event-1')
  act(() => intelButton(view!.host, 'Evaluate event').click())
  await settle()
}

describe('team webhook preview', () => {
  it('evaluates only the current draft through the non-sending preview API', async () => {
    setup()
    view = await mountIntel(<Harness />)
    await evaluate()
    await vi.waitFor(async () => {
      await settle()
      expect(view!.host.textContent).toContain('This event matches.')
    })
    expect(view.host.textContent).toContain('Bounded article evidence')
    expect(view.host.textContent).toContain('Nothing is sent to the destination.')
    expect(view.host.textContent).toContain('does not verify destination, team, or credential delivery policies')
    const requests = vi.mocked(apiFetch).mock.calls.filter(([, options]) => options?.method)
    expect(requests).toHaveLength(1)
    expect(requests[0][0]).toBe('/notifications/webhooks/preview')
    expect(requests[0][1]?.method).toBe('POST')
    expect(JSON.parse(String(requests[0][1]?.body))).toMatchObject({
      event_id: 'event-1',
      webhook: {
        name: 'Team SIEM', event_type: 'article.ai.ready', include_article_text: true,
        conditions: { op: 'all', conditions: [{ field: 'ai_relevance_score', operator: 'gte', value: 0.8 }] },
      },
    })
  })

  it('preserves edits and hides a response completed after the draft changes', async () => {
    const pending = deferred<WebhookMatchPreview>()
    setup({ preview: () => pending.promise })
    view = await mountIntel(<Harness />)
    await evaluate()
    expect(intelButton(view.host, 'Evaluating…').disabled).toBe(true)
    editAutomation(view.host, 'Destination name', 'Edited while evaluating')
    await act(async () => pending.resolve(match))
    await settle()
    expect(automationField(view.host, 'Destination name').value).toBe('Edited while evaluating')
    expect(view.host.textContent).toContain('The draft changed. Evaluate again')
    expect(view.host.textContent).not.toContain('This event matches.')
    expect(view.host.textContent).not.toContain('Bounded article evidence')
  })

  it('withholds a previous result and sample after event access fails', async () => {
    let denied = false
    setup({ events: () => denied
      ? Promise.reject(new ApiError('Event access changed', 403, '/notifications/webhooks/events'))
      : Promise.resolve(sample) })
    view = await mountIntel(<Harness />)
    await evaluate()
    expect(view.host.textContent).toContain('Bounded article evidence')
    denied = true
    await act(async () => {
      await view!.client.invalidateQueries({ queryKey: ['notifications', 'preview-events'] })
    })
    await settle()
    expect(view.host.textContent).toContain('Event access changed')
    expect(view.host.textContent).not.toContain('Bounded article evidence')
    expect(view.host.textContent).not.toContain('Sensitive article')
    expect(intelButton(view.host, 'Evaluate event').disabled).toBe(true)
    expect(automationField(view.host, 'Sample event').disabled).toBe(true)
    expect(automationField(view.host, 'Destination name').value).toBe('Team SIEM')
  })

  it('withholds late results and disables every preview action when the editor becomes unavailable', async () => {
    const pending = deferred<WebhookMatchPreview>()
    setup({ preview: () => pending.promise })
    view = await mountIntel(<Harness />)
    await evaluate()
    act(() => intelButton(view!.host, 'Disable editor').click())
    await act(async () => pending.resolve(match))
    await settle()
    expect(view.host.textContent).not.toContain('Bounded article evidence')
    expect(view.host.textContent).not.toContain('Sensitive article')
    expect(intelButton(view.host, 'Evaluate event').disabled).toBe(true)
    expect(intelButton(view.host, 'Refresh sample').disabled).toBe(true)
    expect(automationField(view.host, 'Sample event').disabled).toBe(true)
    const reads = vi.mocked(apiFetch).mock.calls.filter(([path]) => path.includes('/webhooks/events?')).length
    act(() => intelButton(view!.host, 'Refresh sample').click())
    await settle()
    expect(vi.mocked(apiFetch).mock.calls.filter(([path]) => path.includes('/webhooks/events?'))).toHaveLength(reads)
  })

  it('does not fetch a preview for a redacted non-custodian configuration', async () => {
    setup({ configuration: { ...saved, secrets_redacted: true } })
    view = await mountIntel(<Harness />)
    expect(view.host.textContent).toContain('Adopt this destination to edit')
    expect(view.host.querySelector('[aria-label="Webhook matching preview"]')).toBeNull()
    expect(vi.mocked(apiFetch).mock.calls.map(([path]) => path)).toEqual([`${base}/configuration`])
  })
})
