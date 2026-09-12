// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import { AiReliabilityStatistics, AiStatisticsWorkspace } from './AiStatisticsWorkspace'
import type { AIStatisticsResponse } from '../types/aiStatistics'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...(await original<object>()), apiFetch: vi.fn() }))
const user = vi.hoisted(() => ({ role: 'analyst', access: { permissions: ['read:ai'] }, features: { ai_enabled: true } }))
vi.mock('../hooks/useCurrentUser', () => ({ useCurrentUser: () => ({ data: user, isLoading: false }) }))
let root: Root | null = null
let host: HTMLDivElement | null = null
let client: QueryClient | null = null
const response: AIStatisticsResponse = {
  since: '2026-09-01T00:00:00Z', until: '2026-09-12T00:00:00Z', days: 30,
  features: [{ feature: 'report', requests: 3, successful: 1, failed: 2, known_usage_requests: 1,
    unknown_usage_requests: 2, prompt_tokens: 10, completion_tokens: 20, total_tokens: 30,
    not_sent: 1, ambiguous: 1, deadline_failures: 1, timeout_failures: 1, truncated_outputs: 0,
    budget_rejections: 1, latency_samples: 1, p50_latency_ms: 500, p95_latency_ms: 500, p99_latency_ms: 500 }],
  queues: [{ feature: 'report', queued: 1, running: 0, oldest_queued_at: '2026-09-11T23:00:00Z', oldest_running_at: null }],
  provider_retry_attempts: 2, recovered_pre_io_failures: 1, latency_histogram: { under_1s: 1 },
}
function render(permissions = false) {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  act(() => root!.render(<QueryClientProvider client={client!}>{permissions ? <AiStatisticsWorkspace /> : <AiReliabilityStatistics days={30} />}</QueryClientProvider>))
}
async function settle() { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 15)) }) }
afterEach(() => { act(() => root?.unmount()); client?.clear(); host?.remove(); vi.resetAllMocks(); user.role = 'analyst'; user.access.permissions = ['read:ai'] })

describe('AI statistics workspace', () => {
  it('does not request protected metrics for a nonadministrator or missing AI permission', async () => {
    render(true); await settle()
    expect(host!.textContent).toContain('require the administrator role')
    expect(apiFetch).not.toHaveBeenCalled()
    user.role = 'admin'; user.access.permissions = []
    act(() => root!.render(<QueryClientProvider client={client!}><AiStatisticsWorkspace /></QueryClientProvider>))
    await settle(); expect(apiFetch).not.toHaveBeenCalled()
  })

  it.each([403, 503])('distinguishes a successful snapshot followed by HTTP %s', async (status) => {
    vi.mocked(apiFetch).mockResolvedValue(response)
    render(); await settle()
    expect(host!.textContent).toContain('1 successful / 3 recorded')
    expect(host!.textContent).toContain('2 calls')
    expect(host!.textContent).toContain('60 min')
    expect([...host!.querySelectorAll('th')].every((th) => th.hasAttribute('scope'))).toBe(true)
    vi.mocked(apiFetch).mockRejectedValue(new ApiError('Statistics unavailable', status, '/ai/ops/statistics'))
    await act(async () => { await client!.invalidateQueries() }); await settle()
    expect(host!.textContent).toContain('Statistics unavailable')
    expect(host!.textContent!.includes('1 successful / 3 recorded')).toBe(status === 503)
    vi.mocked(apiFetch).mockResolvedValue({ ...response, features: [], queues: [] })
    act(() => host!.querySelector('button')!.click()); await settle()
    expect(host!.textContent).toContain('No provider requests recorded')
    expect(host!.textContent).not.toContain('Statistics unavailable')
  })
})
