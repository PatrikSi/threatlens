// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import { StatsPage } from './StatsPage'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...(await original<object>()), apiFetch: vi.fn() }))
const user = vi.hoisted(() => ({ role: 'analyst', access: { permissions: ['read:stats'] }, features: { ai_enabled: true } }))
vi.mock('../hooks/useCurrentUser', () => ({ useCurrentUser: () => ({ data: user, isLoading: false, isError: false }) }))
let root: Root | null = null
let host: HTMLDivElement | null = null
let client: QueryClient | null = null
async function settle() { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)) }) }
async function mount(path = '/stats', initialFailure = true) {
  if (initialFailure) vi.mocked(apiFetch).mockRejectedValue(new ApiError('Metrics temporarily unavailable', 503, '/statistics'))
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  act(() => root!.render(<MemoryRouter initialEntries={[path]}><QueryClientProvider client={client!}><StatsPage /></QueryClientProvider></MemoryRouter>))
  await settle()
}
const tab = (text: string) => Array.from(host!.querySelectorAll('nav button')).find((button) => button.textContent?.startsWith(text)) as HTMLButtonElement

afterEach(() => { act(() => root?.unmount()); client?.clear(); host?.remove(); vi.clearAllMocks(); vi.unstubAllGlobals(); user.role = 'analyst'; user.access.permissions = ['read:stats']; user.features.ai_enabled = true })
describe('statistics section access and navigation', () => {
  it.each([
    ['/feeds', 'Private feed', 403], ['/feeds', 'Private feed', 503],
    ['/stats/overview', 'private.example', 403], ['/stats/overview', 'private.example', 503],
    ['/stats/feed-timeseries', 'Private series', 403], ['/stats/feed-timeseries', 'Private series', 503],
    ['/stats/activity-heatmap', 'Last 30 Days (Hourly)', 403], ['/stats/activity-heatmap', 'Last 30 Days (Hourly)', 503],
    ['/stats/signal-radar', 'Ransomware', 403], ['/stats/signal-radar', 'Ransomware', 503],
  ])('hides only withdrawn ingestion snapshots while preserving transient failures (%s, %s, %s)', async (deniedPath, marker, status) => {
    vi.mocked(apiFetch).mockImplementation((path) => Promise.resolve(ingestionSnapshot(path)) as never)
    await mount('/stats', false)
    expect(host!.textContent).toContain(marker)
    vi.mocked(apiFetch).mockImplementation((path) => path.startsWith(String(deniedPath))
      ? Promise.reject(new ApiError('Statistics access check failed', Number(status), path))
      : Promise.resolve(ingestionSnapshot(path)) as never)
    await act(async () => { await client!.invalidateQueries() }); await settle()
    expect(host!.textContent!.includes(String(marker))).toBe(status === 503)
    vi.mocked(apiFetch).mockImplementation((path) => Promise.resolve(ingestionSnapshot(path)) as never)
    await act(async () => { await client!.invalidateQueries() }); await settle()
    expect(host!.textContent).toContain(marker)
  })

  it('keeps ingestion navigation usable on a restricted AI deep link without polling AI', async () => {
    await mount('/stats?section=ai')
    expect(host!.textContent).toContain('AI statistics require the administrator role')
    expect(tab('AI statistics').disabled).toBe(true)
    expect(tab('Ingestion statistics').disabled).toBe(false)
    expect(apiFetch).not.toHaveBeenCalled()
    act(() => tab('Ingestion statistics').click()); await settle()
    expect(vi.mocked(apiFetch).mock.calls.some(([path]) => path.startsWith('/stats/'))).toBe(true)
    expect(vi.mocked(apiFetch).mock.calls.some(([path]) => path.startsWith('/ai/'))).toBe(false)
  })
  it('opens AI-only administrators on their allowed statistics section', async () => {
    user.role = 'admin'; user.access.permissions = ['read:ai']
    await mount()
    expect(tab('AI statistics').getAttribute('aria-pressed')).toBe('true')
    expect(tab('Ingestion statistics').disabled).toBe(true)
    expect(apiFetch).toHaveBeenCalled()
    expect(vi.mocked(apiFetch).mock.calls.every(([path]) => path.startsWith('/ai/'))).toBe(true)
  })
  it('keeps an allowed escape from an ingestion deep link for AI-only administrators', async () => {
    user.role = 'admin'; user.access.permissions = ['read:ai']
    await mount('/stats?section=ingestion')
    expect(apiFetch).not.toHaveBeenCalled()
    expect(host!.textContent).toContain('Ingestion statistics require read:stats')
    act(() => tab('AI statistics').click()); await settle()
    expect(vi.mocked(apiFetch).mock.calls.every(([path]) => path.startsWith('/ai/'))).toBe(true)
  })
  it('keeps AI statistics sealed for additive analyst permissions and for disabled installations', async () => {
    user.access.permissions = ['read:ai']
    await mount('/stats?section=ai')
    expect(apiFetch).not.toHaveBeenCalled()
    user.role = 'admin'; user.features.ai_enabled = false
    act(() => root!.render(<MemoryRouter initialEntries={['/stats?section=ai']}><QueryClientProvider client={client!}><StatsPage /></QueryClientProvider></MemoryRouter>))
    await settle()
    expect(host!.textContent).toContain('AI is disabled')
    expect(apiFetch).not.toHaveBeenCalled()
  })
})

function ingestionSnapshot(path: string) {
  const timeWindow = { generated_at: '2026-09-12T12:00:00Z', window_days: 30,
    window_start_at: '2026-08-13T12:00:00Z', window_end_at: '2026-09-12T12:00:00Z' }
  if (path === '/feeds') return [{ id: 'private-feed', name: 'Private feed' }]
  if (path.startsWith('/stats/feed-timeseries')) return { ...timeWindow,
    series: [{ feed_id: 'private-feed', feed_name: 'Private series', points: [{ date: '2026-09-12', count: 1 }] }] }
  if (path.startsWith('/stats/activity-heatmap')) return { ...timeWindow, bucket_unit: 'hour', bucket_labels: [], rows: [], max_count: 0 }
  if (path.startsWith('/stats/signal-radar')) return { ...timeWindow, total: 1, max_count: 1, axes: [{ category: 'ransomware', count: 1, pct: 100 }] }
  return { ...timeWindow, totals: { feeds_total: 1, feeds_enabled: 1, feeds_disabled: 0, items_total: 1,
    items_new: 1, items_content_fetched: 0, items_error: 0, articles_total: 0 },
    activity: { items_last_24h: 1, items_last_7d: 1, items_last_30d: 1 },
    derived: { extraction_success_rate_pct: 0, error_rate_pct: 0, avg_items_per_day_window: 1 },
    status_breakdown: [], daily_volume: [], feed_breakdown: [], top_domains: [{ domain: 'private.example', count: 1 }] }
}
