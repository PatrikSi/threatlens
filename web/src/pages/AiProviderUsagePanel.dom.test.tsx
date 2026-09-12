// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AiProviderUsagePanel } from './AiProviderUsagePanel'
import type { AIProviderUsageResponse, AIProviderUsageRow } from '../types/aiProviderUsage'

const mocks = vi.hoisted(() => ({ apiFetch: vi.fn() }))
vi.mock('../api/client', () => ({ apiFetch: mocks.apiFetch }))
;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const row = (changes: Partial<AIProviderUsageRow> = {}): AIProviderUsageRow => ({
  provider_id: '00000000-0000-0000-0000-000000000001', provider_version: 2,
  provider_name: 'Local provider', model: 'shared-model', total_requests: 4, successful_requests: 2, failed_requests: 2,
  success_rate_pct: 50, prompt_tokens: 100, completion_tokens: 50, total_tokens: 150, unknown_token_requests: 1,
  average_latency_ms: 10, p95_latency_ms: 19, not_sent_requests: 1, ambiguous_requests: 1, deadline_failures: 1,
  failure_categories: { total_deadline: 1, provider_hourly_token_budget: 1 }, last_request_at: '2026-09-12T12:00:00Z', ...changes,
})
const response = (items: AIProviderUsageRow[], changes: Partial<AIProviderUsageResponse> = {}): AIProviderUsageResponse => ({
  items, total: items.length, limit: 25, offset: 0, days: 30, ...changes,
})
const mounted: Array<{ root: Root; queryClient: QueryClient; host: HTMLDivElement }> = []
function mount(days = 30) {
  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const render = (windowDays: number) => {
    act(() => root.render(<QueryClientProvider client={queryClient}><AiProviderUsagePanel key={windowDays} days={windowDays} /></QueryClientProvider>))
  }
  mounted.push({ root, queryClient, host })
  render(days)
  return { host, render }
}
async function settle(check: () => void) {
  const deadline = Date.now() + 2000
  while (true) {
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 10)) })
    try { check(); return } catch (error) { if (Date.now() >= deadline) throw error }
  }
}
function button(host: HTMLElement, name: string) {
  const result = Array.from(host.querySelectorAll('button')).find((entry) => entry.textContent === name)
  expect(result).toBeDefined()
  return result!
}
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: Error) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
afterEach(() => {
  for (const { root, queryClient, host } of mounted.splice(0)) {
    act(() => root.unmount())
    queryClient.clear()
    host.remove()
  }
  mocks.apiFetch.mockReset()
})

describe('provider usage with a real QueryClient', () => {
  it('distinguishes provider snapshots sharing a model and exposes semantic records, missing tokens, and typed failures', async () => {
    mocks.apiFetch.mockResolvedValue(response([
      row(), row({ provider_id: '00000000-0000-0000-0000-000000000002', provider_name: 'Retired provider', provider_version: 4 }),
      row({ provider_id: null, provider_name: 'Unknown historical provider', provider_version: null, p95_latency_ms: null }),
      row({ provider_id: null, provider_name: 'Legacy settings', provider_version: null }),
    ]))
    const { host } = mount()
    await settle(() => expect(host.textContent).toContain('Retired provider'))
    expect(host.textContent).toContain('Retired provider')
    expect(host.textContent).toContain('Unknown historical provider')
    expect(host.textContent).toContain('Legacy settings')
    expect(host.textContent).toContain('Version 4')
    expect(host.textContent).toContain('Usage missing for 1 call')
    expect(host.textContent).toContain('total deadline: 1')
    expect(host.textContent).toContain('provider hourly token budget: 1')
    expect(host.textContent).toContain('1 not sent · 1 uncertain outcomes')
    expect(host.textContent).toContain('No measurement')
    expect(host.querySelectorAll('tbody tr')).toHaveLength(4)
    expect([...host.querySelectorAll('th')].every((heading) => heading.getAttribute('scope') === 'col')).toBe(true)
    expect(host.querySelector('[aria-label="Provider usage records"]')?.getAttribute('tabindex')).toBe('0')
    expect(button(host, 'Next provider page').disabled).toBe(true)
  })

  it('paginates separately, resets for a new time window and ignores a late response from the old window', async () => {
    const oldPage = deferred<AIProviderUsageResponse>()
    mocks.apiFetch.mockImplementation((path: string) => {
      if (path.includes('days=7')) return Promise.resolve(response([row({ provider_name: 'New window' })], { days: 7 }))
      if (path.includes('offset=25')) return oldPage.promise
      return Promise.resolve(response(Array.from({ length: 25 }, (_, i) => row({ provider_name: `Page one ${i}` })), { total: 26 }))
    })
    const { host, render } = mount()
    await settle(() => expect(host.textContent).toContain('Page one 0'))
    act(() => button(host, 'Next provider page').click())
    await settle(() => expect(host.textContent).toContain('Loading provider usage'))
    expect(host.textContent).toContain('Loading provider usage')
    expect(host.textContent).not.toContain('Page one 0')
    render(7)
    await settle(() => expect(host.textContent).toContain('New window'))
    expect(host.textContent).toContain('New window')
    await act(async () => oldPage.resolve(response([row({ provider_name: 'Old late page' })], { offset: 25, total: 26 })))
    await settle(() => expect(host.textContent).not.toContain('Old late page'))
    expect(host.textContent).not.toContain('Old late page')
    expect(host.textContent).toContain('1–1 of 1')
    expect(mocks.apiFetch.mock.calls.some(([path]) => path.includes('days=7&limit=25&offset=0'))).toBe(true)
    expect(button(host, 'Previous provider page').disabled).toBe(true)
  })

  it('keeps a failed refresh explicit and lets the user retry without removing cached records', async () => {
    mocks.apiFetch.mockResolvedValueOnce(response([row()])).mockRejectedValueOnce(new Error('synthetic unavailable'))
    const { host } = mount()
    await settle(() => expect(host.textContent).toContain('Local provider'))
    act(() => button(host, 'Refresh provider usage').click())
    await settle(() => expect(host.querySelector('[role="alert"]')?.textContent).toContain('Showing previously loaded records'))
    expect(host.querySelector('[role="alert"]')?.textContent).toContain('Showing previously loaded records')
    expect(host.textContent).toContain('Local provider')
    mocks.apiFetch.mockResolvedValue(response([row({ provider_name: 'Recovered provider' })]))
    act(() => button(host, 'Refresh provider usage').click())
    await settle(() => expect(host.textContent).toContain('Recovered provider'))
    expect(host.querySelector('[role="alert"]')).toBeNull()
    expect(host.textContent).toContain('Recovered provider')
  })

  it('renders initial errors with retry and supports an out-of-range page after history pruning', async () => {
    mocks.apiFetch.mockRejectedValueOnce(new Error('synthetic unavailable'))
    const { host } = mount()
    await settle(() => expect(host.querySelector('[role="alert"]')?.textContent).toContain('Use Refresh provider usage to retry'))
    expect(host.querySelector('[role="alert"]')?.textContent).toContain('Use Refresh provider usage to retry')
    mocks.apiFetch.mockResolvedValueOnce(response(Array.from({ length: 25 }, (_, i) => row({ provider_name: `Record ${i}` })), { total: 26 }))
    act(() => button(host, 'Refresh provider usage').click())
    await settle(() => expect(host.textContent).toContain('Record 0'))
    mocks.apiFetch.mockResolvedValueOnce(response([], { total: 10, offset: 25 }))
    act(() => button(host, 'Next provider page').click())
    await settle(() => expect(host.textContent).toContain('There are no records on this page'))
    expect(host.textContent).toContain('There are no records on this page')
    expect(button(host, 'First provider page').disabled).toBe(false)
    expect(button(host, 'Next provider page').disabled).toBe(true)
  })
})
