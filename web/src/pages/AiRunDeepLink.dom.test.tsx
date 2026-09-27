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
async function mount(entry: string, runResponse = (id: string) => Promise.resolve(detail(id))) {
  vi.spyOn(HTMLElement.prototype, 'scrollIntoView').mockImplementation(() => {})
  vi.mocked(apiFetch).mockImplementation((path) => {
    if (path.startsWith('/ai/ops/runs/')) return runResponse(path.split('/').at(-1)!) as never
    if (path.startsWith('/ai/ops/runs?')) return Promise.resolve({ items: [], total: 0, limit: 20, offset: 0 }) as never
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
