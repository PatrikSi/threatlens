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

it('does not arm a saved-view delete intent during denied metadata before observer delivery', async () => {
  const saved = {
    id: 'private-view', user_id: 'dashboard-reader', name: 'Private arrangement intent',
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
    return Promise.resolve(path.startsWith('/items?') ? { items: [], total: 0, page: 1, page_size: 25 } : []) as never
  })
  view = await mountIntel(<DashboardPage />, '/')
  await settle()
  act(() => intelButton(view!.host, 'Views').click())
  const button = document.querySelector<HTMLButtonElement>('[aria-label="Delete saved view Private arrangement intent"]')!
  expect(button.disabled).toBe(false)
  const actualErrors: number[] = []
  await act(async () => {
    status = 403
    try {
      await view!.client.fetchQuery({ queryKey: ['dashboard', 'views'], staleTime: 0, retry: false })
    } catch (error) { actualErrors.push((error as ApiError).status) }
    // Keep the actual request/interaction/recovery within one observer batch.
    button.click()
    status = 200
    await view!.client.fetchQuery({ queryKey: ['dashboard', 'views'], staleTime: 0, retry: false })
  })
  await settle()
  expect(rejected).toContain(403)
  expect(actualErrors).toEqual([403])
  expect(view.client.getQueryState(['dashboard', 'views'])?.status).toBe('success')
  expect(document.body.textContent).toContain(saved.name)
  expect(document.querySelector('[role="alertdialog"]')).toBeNull()
  expect(vi.mocked(apiFetch).mock.calls.some(([, options]) => options?.method === 'DELETE')).toBe(false)
})
