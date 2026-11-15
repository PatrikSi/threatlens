// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { TestDataRouter, testRouter } from '../../tests/helpers/TestDataRouter'
import { ApiError, apiFetch } from '../api/client'
import type { AITaskRunDetailResponse } from '../types/api'
import { AiSettingsPage } from './AiSettingsPage'
import { deferred, settle } from './articleIntelligenceTestSupport'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: () => {} })
vi.mock('../api/client', async (original) => ({ ...await original<object>(), apiFetch: vi.fn() }))
vi.mock('../hooks/useCurrentUser', () => ({ useCurrentUser: () => ({ data: {
  id: 'admin', role: 'admin', features: { ai_enabled: true }, access: { permissions: ['*:*'] },
} }) }))

const firstId = 'a855d5bd-8cba-41a6-a314-cc18027dfe96'
const secondId = '49f4a711-c280-47e9-bf43-dd1f130289bb'
const link = (id: string) => `/settings/ai?run=${id}`
function detail(id: string): AITaskRunDetailResponse {
  return { run: {
    id, task_type: 'connection_test', trigger_source: 'manual', status: 'ready',
    reason: null, celery_task_id: null, worker_name: null, actor_user_id: null, actor_email: null,
    item_id: null, item_title: null, item_url: null, feed_name: null, item_first_seen_at: null,
    item_published_at: null, daily_brief_id: null, parent_run_id: null, model: 'qualified-model',
    prompt_tokens: 40, completion_tokens: 20, total_tokens: 60, latency_ms: 100, duration_ms: 100,
    prompt_char_count: 100, response_char_count: 50, input_text_chars: 80, error: null, metadata: {},
    target_count: null, processed_count: 0, success_count: 0, error_count: 0, skipped_count: 0,
    skipped_unchanged_count: 0, skipped_ineligible_count: 0,
    queued_at: '2026-09-01T12:00:00Z', started_at: '2026-09-01T12:00:00Z',
    finished_at: '2026-09-01T12:00:01Z', created_at: '2026-09-01T12:00:00Z', updated_at: '2026-09-01T12:00:01Z',
  }, events: [] }
}
let root: Root | undefined
let client: QueryClient
let host: HTMLDivElement
async function mount(entry: string, runResponse = (id: string) => Promise.resolve(detail(id)), listResponse = () => Promise.resolve({ items: [] as AITaskRunDetailResponse['run'][], total: 0, limit: 20, offset: 0 }), dependentResponse?: () => Promise<unknown>) {
  vi.spyOn(HTMLElement.prototype, 'scrollIntoView').mockImplementation(() => {})
  vi.mocked(apiFetch).mockImplementation((path) => {
    if (path.startsWith('/ai/ops/runs/')) return runResponse(path.split('/').at(-1)!) as never
    if (dependentResponse && (path.includes('parent_run_id=') || path.startsWith('/ai/daily-briefs/'))) return dependentResponse() as never
    if (path.startsWith('/ai/ops/runs?')) return listResponse() as never
    if (path === '/feeds') return Promise.resolve([]) as never
    return new Promise(() => {})
  })
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  act(() => root!.render(<QueryClientProvider client={client}>
    <TestDataRouter initialEntries={[entry]}><AiSettingsPage /></TestDataRouter>
  </QueryClientProvider>))
  await settle()
}
afterEach(() => { act(() => root?.unmount()); client?.clear(); host?.remove(); vi.restoreAllMocks(); vi.clearAllMocks() })

describe('AI run deep links with a real router and query cache', () => {
  it.each(['children', 'daily brief sources'] as const)('keeps parent-denied %s hidden until their fresh dependent read succeeds', async (kind) => {
    let parentStatus = 200
    let dependentReads = 0
    let recovered = false
    const pending = deferred<unknown>()
    const parent = detail(firstId)
    parent.run.task_type = kind === 'children' ? 'reprocess' : 'daily_brief'
    parent.run.daily_brief_id = kind === 'daily brief sources' ? 'private-brief' : null
    const child = { ...detail(secondId).run, task_type: 'item_enrichment', item_title: 'Old dependent private title' }
    const sources = [{ id: 'source-1', daily_brief_id: 'private-brief', item_id: null, included: true, rank: 1, exclusion_reason: null, title_snapshot: 'Old dependent private title', feed_name_snapshot: null, url_snapshot: 'https://evidence.invalid/private', classification_snapshot: null, relevance_score_snapshot: null, relevance_label_snapshot: null, published_at_snapshot: null, first_seen_at_snapshot: null, created_at: parent.run.created_at }]
    const response = () => kind === 'children'
      ? { items: [{ ...child, item_title: recovered ? 'Fresh dependent private title' : child.item_title }], total: 1, limit: 50, offset: 0 }
      : sources.map((source) => ({ ...source, title_snapshot: recovered ? 'Fresh dependent private title' : source.title_snapshot }))
    await mount(link(firstId), () => parentStatus === 200 ? Promise.resolve(parent) : Promise.reject(new ApiError('Parent access withdrawn', parentStatus, '/ai/ops/runs')), undefined, () => {
      dependentReads += 1
      return dependentReads === 1 || recovered ? Promise.resolve(response()) : pending.promise
    })
    await settle()
    expect(host.textContent).toContain('Old dependent private title')
    parentStatus = 403
    await act(async () => client.invalidateQueries({ queryKey: ['ai', 'ops', 'run', firstId] }))
    await settle()
    expect(host.textContent).not.toContain('Old dependent private title')
    parentStatus = 200
    await act(async () => client.invalidateQueries({ queryKey: ['ai', 'ops', 'run', firstId] }))
    await settle()
    expect(host.textContent).not.toContain('Old dependent private title')
    expect(dependentReads).toBe(2)
    await act(async () => pending.reject(new ApiError('Dependent read temporarily unavailable', 503, '/dependent')))
    await settle()
    expect(host.textContent).not.toContain('Old dependent private title')
    recovered = true
    await act(async () => client.invalidateQueries({ queryKey: kind === 'children' ? ['ai', 'ops', 'child-runs', firstId] : ['ai', 'daily-brief-sources', 'private-brief'] }))
    await settle()
    expect(host.textContent).toContain('Fresh dependent private title')
  })

  it('keeps denied run metadata and timeline hidden through an outage and recovers only from a fresh read', async () => {
    let status = 200
    const privateDetail = detail(firstId)
    privateDetail.run.metadata = { scope: 'Private run evidence' }
    privateDetail.events = [{ id: 'private-event', task_run_id: firstId, event_type: 'progress', message: 'Private timeline evidence', payload: {}, created_at: privateDetail.run.created_at }]
    await mount(link(firstId), () => status === 200 ? Promise.resolve(privateDetail) : Promise.reject(new ApiError('Run read unavailable', status, '/ai/ops/runs')))
    expect(host.textContent).toContain('Private timeline evidence')
    for (const next of [403, 503]) {
      status = next
      await act(async () => client.invalidateQueries({ queryKey: ['ai', 'ops', 'run', firstId] }))
      await settle()
      expect(host.textContent).not.toContain('Private run evidence')
      expect(host.textContent).not.toContain('Private timeline evidence')
    }
    status = 200
    await act(async () => client.invalidateQueries({ queryKey: ['ai', 'ops', 'run', firstId] }))
    await settle()
    expect(host.textContent).toContain('Private timeline evidence')
  })

  it('retains ordinary outage detail while pausing cancellation and retiring a pending cancellation intent', async () => {
    let status = 200
    const running = detail(firstId)
    running.run.status = 'running'
    running.run.finished_at = null
    await mount(link(firstId), () => status === 200 ? Promise.resolve(running) : Promise.reject(new ApiError('Run temporarily unavailable', status, '/ai/ops/runs')))
    const stop = [...host.querySelectorAll('button')].find((button) => button.textContent === 'Stop Task')!
    act(() => stop.click())
    expect(document.querySelector('[role="alertdialog"]')).not.toBeNull()
    status = 503
    await act(async () => client.invalidateQueries({ queryKey: ['ai', 'ops', 'run', firstId] }))
    await settle()
    expect(host.textContent).toContain(`Run ${firstId}`)
    const confirm = [...document.querySelectorAll('button')].filter((button) => button.textContent === 'Stop Task')
    expect(confirm.every((button) => button.disabled)).toBe(true)
    expect(vi.mocked(apiFetch).mock.calls.some(([, options]) => options?.method === 'POST')).toBe(false)
  })

  it('keeps denied history titles hidden across a later outage', async () => {
    let status = 200
    const privateRun = detail(firstId).run
    privateRun.item_title = 'Private history title'
    await mount(link(firstId), (id) => Promise.resolve(detail(id)), () => status === 200
      ? Promise.resolve({ items: [privateRun], total: 1, limit: 20, offset: 0 })
      : Promise.reject(new ApiError('History read unavailable', status, '/ai/ops/runs')))
    expect(host.textContent).toContain('Private history title')
    for (const next of [403, 503]) {
      status = next
      await act(async () => client.invalidateQueries({ queryKey: ['ai', 'ops', 'runs'] }))
      await settle()
      expect(host.textContent).not.toContain('Private history title')
    }
  })

  it('retires a cached inspection of the same run when its selected detail is denied', async () => {
    let status = 200
    const privateDetail = detail(firstId)
    privateDetail.run.item_title = 'Private inspected run'
    privateDetail.events = [{ id: 'exchange', task_run_id: firstId, event_type: 'provider_exchange', message: null, payload: { request_payload: { prompt: 'Private request payload' }, response_body: 'Private provider response' }, created_at: privateDetail.run.created_at }]
    await mount(link(firstId), () => status === 200 ? Promise.resolve(privateDetail) : Promise.reject(new ApiError('Selected run access withdrawn', status, '/ai/ops/runs')))
    act(() => [...host.querySelectorAll('button')].find((button) => button.textContent === 'View Request / Response')!.click())
    await settle()
    expect(document.querySelector('[role="dialog"]')?.textContent).toContain('Private provider response')
    status = 403
    await act(async () => client.invalidateQueries({ queryKey: ['ai', 'ops', 'run', firstId] }))
    await settle()
    expect(document.querySelector('[role="dialog"]')).toBeNull()
    expect(document.body.textContent).toContain('Selected run access withdrawn')
    for (const privateText of ['Private inspected run', 'Private request payload', 'Private provider response']) {
      expect(document.body.textContent).not.toContain(privateText)
    }
  })

  it('opens an exact off-page run and follows browser navigation without dropping the pin', async () => {
    await mount(link(firstId))
    expect(host.textContent).toContain(`Run ${firstId}`)
    expect(host.querySelector(`a[href="${link(firstId)}"]`)).not.toBeNull()
    await act(async () => testRouter().navigate(link(secondId)))
    await settle()
    expect(host.textContent).toContain(`Run ${secondId}`)
    expect(host.textContent).not.toContain(`Run ${firstId}`)
    await act(async () => testRouter().navigate(-1))
    await settle()
    expect(host.textContent).toContain(`Run ${firstId}`)
  })

  it('ignores a late response for the previous linked run', async () => {
    const previous = deferred<AITaskRunDetailResponse>()
    await mount(link(firstId), (id) => id === firstId ? previous.promise : Promise.resolve(detail(id)))
    await act(async () => testRouter().navigate(link(secondId)))
    await settle()
    await act(async () => previous.resolve(detail(firstId)))
    await settle()
    expect(host.textContent).toContain(`Run ${secondId}`)
    expect(host.textContent).not.toContain(`Run ${firstId}`)
  })

  it('hides cached detail after explicit access denial', async () => {
    let revoked = false
    await mount(link(firstId), (id) => revoked
      ? Promise.reject(new ApiError('Run access revoked', 403, `/ai/ops/runs/${id}`)) : Promise.resolve(detail(id)))
    expect(host.textContent).toContain(`Run ${firstId}`)
    revoked = true
    await act(async () => client.invalidateQueries({ queryKey: ['ai', 'ops', 'run', firstId] }))
    await settle()
    expect(host.textContent).not.toContain(`Run ${firstId}`)
    expect(host.textContent).toContain('Run access revoked')
  })

  it('rejects malformed run links without issuing a detail request', async () => {
    await mount('/settings/ai?run=..%2Fsettings')
    expect(host.textContent).toContain('This AI run link is invalid')
    expect(vi.mocked(apiFetch).mock.calls.some(([path]) => path.startsWith('/ai/ops/runs/'))).toBe(false)
    await act(async () => testRouter().navigate(link(firstId)))
    await settle()
    expect(host.textContent).toContain(`Run ${firstId}`)
    expect(host.textContent).not.toContain('This AI run link is invalid')
  })

  it('reopens the same linked run after a local tab switch and returns to overview when the link is cleared', async () => {
    await mount(link(firstId))
    act(() => [...host.querySelectorAll('button')].find((button) => button.textContent?.trim() === 'Configuration')!.click())
    expect(host.querySelector('[aria-selected="true"]')?.textContent).toContain('Configuration')
    await act(async () => testRouter().navigate(link(firstId)))
    await settle()
    expect(host.querySelector('[aria-selected="true"]')?.id).toContain('activity')
    await act(async () => testRouter().navigate('/settings/ai'))
    await settle()
    expect(host.querySelector('[aria-selected="true"]')?.id).toContain('overview')
  })

  it('retains dirty scope through same-page run links and still guards leaving the page', async () => {
    await mount(link(firstId))
    const field = [...host.querySelectorAll('label')].find((node) => node.textContent?.startsWith('Reprocess lookback (days)'))!.querySelector('input')!
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(field, '14')
      field.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await act(async () => testRouter().navigate(link(secondId)))
    await settle()
    expect(host.textContent).toContain(`Run ${secondId}`)
    expect(host.querySelector('[role="dialog"]')).toBeNull()
    expect(field.value).toBe('14')
    await act(async () => testRouter().navigate(-1))
    await settle()
    expect(host.textContent).toContain(`Run ${firstId}`)
    expect(field.value).toBe('14')
    await act(async () => testRouter().navigate('/test-away'))
    await settle()
    expect(document.querySelector('[role="alertdialog"]')?.textContent).toContain('Discard unsaved changes?')
    expect(testRouter().state.location.pathname).toBe('/settings/ai')
  })
})
