// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import { DashboardPage } from './DashboardPage'
import { buildDashboardSavedViewState, createWindowLayout } from './dashboardSavedViews'
import { intelButton, mountIntel, settle } from './articleIntelligenceTestSupport'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...await original<object>(), apiFetch: vi.fn() }))
vi.mock('../hooks/useCurrentUser', () => ({ useCurrentUser: () => ({ data: { id: 'dashboard-reader', role: 'admin', access: { permissions: ['*:*'] }, features: {} }, isError: false, isLoading: false }) }))
vi.mock('../workspace/useWorkspace', () => ({ useWorkspace: () => ({ isLoading: false, isDegraded: false, effective: { role: 'admin' }, userContext: { role: 'admin' }, model: { dashboardPanelIds: ['rss'] } }) }))

let view: Awaited<ReturnType<typeof mountIntel>> | undefined
afterEach(() => { view?.close(); view = undefined; localStorage.clear(); vi.clearAllMocks() })

it('retires a saved dashboard view delete intent after metadata denial through an outage', async () => {
  const saved = {
    id: 'private-view', user_id: 'dashboard-reader', name: 'Private selected arrangement',
    created_at: '2026-11-01T00:00:00Z', revision: 3, can_edit: true, can_delete: true,
    query_json: buildDashboardSavedViewState([createWindowLayout('rss', 1, 1380, 760, 'full')], {
      time_range: 'all', custom_since_date: '', custom_until_date: '', rolling_days: '7',
    }),
  }
  let status = 200
  const rejected: number[] = []
  vi.mocked(apiFetch).mockImplementation((path) => {
    if (path === '/views') {
      if (status === 200) return Promise.resolve([saved]) as never
      rejected.push(status)
      return Promise.reject(new ApiError('Saved arrangements unavailable', status, path))
    }
    if (path.startsWith('/items?')) return Promise.resolve({ items: [], total: 0, page: 1, page_size: 25 }) as never
    return Promise.resolve([]) as never
  })
  view = await mountIntel(<DashboardPage />, '/')
  await settle()
  act(() => intelButton(view!.host, 'Views').click())
  act(() => document.querySelector<HTMLButtonElement>('[aria-label="Delete saved view Private selected arrangement"]')!.click())
  expect(document.querySelector('[role="alertdialog"]')?.textContent).toContain(saved.name)
  for (const next of [403, 503]) {
    status = next
    await act(async () => { await view!.client.invalidateQueries() })
    await settle()
    expect(rejected).toContain(next)
    expect(document.querySelector('[role="alertdialog"]')).toBeNull()
    expect(document.body.textContent).not.toContain(saved.name)
  }
  expect(vi.mocked(apiFetch).mock.calls.some(([, options]) => options?.method === 'DELETE')).toBe(false)
})
