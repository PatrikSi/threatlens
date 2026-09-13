// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiFetch } from '../api/client'
import { alertsViewFromParams, readAlertUrlState, writeAlertUrlState } from './alertUrlState'
import { useAlertOccurrencesController } from './useAlertOccurrencesController'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...(await original<typeof import('../api/client')>()), apiFetch: vi.fn() }))
vi.mock('../hooks/useCurrentUser', () => ({ useCurrentUser: () => ({ data: { id: 'user', role: 'analyst', access: { permissions: ['read:alerts', 'write:alerts'] }, features: {} }, isLoading: false, isError: false }) }))
let root: Root | undefined
let router: ReturnType<typeof createMemoryRouter>
let client: QueryClient
let controller: ReturnType<typeof useAlertOccurrencesController>
function Harness() { controller = useAlertOccurrencesController(); return null }
function render(path: string) {
  vi.mocked(apiFetch).mockImplementation((url) => {
    if (url === '/alerts?include_disabled=true') return Promise.resolve([]) as never
    if (url.startsWith('/alerts/occurrences?')) {
      const params = new URLSearchParams(url.split('?')[1])
      return Promise.resolve({ items: [], total: 100, page: Number(params.get('page')), page_size: Number(params.get('page_size')) }) as never
    }
    if (url.includes('/activity?')) return Promise.resolve({ items: [], total: 0, page: 1, page_size: 25 }) as never
    return Promise.resolve(null) as never
  })
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  router = createMemoryRouter([{ path: '/alerts', element: <Harness /> }], { initialEntries: [path] })
  root = createRoot(document.createElement('div'))
  act(() => root!.render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>))
}
async function settle() { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 15)) }) }
afterEach(() => { act(() => root?.unmount()); root = undefined; router?.dispose(); client?.clear(); vi.clearAllMocks(); vi.unstubAllGlobals() })
describe('alert triage URL restoration', () => {
  it('restores filter scope, pages and selected occurrence on direct load and through browser history', async () => {
    render('/alerts?view=occurrences&lifecycle_states=new&severities=high&suppressed=no&page=2&page_size=25&occurrence=private-id&activity_page=3&search=Exchange')
    await settle()
    expect(controller.filters.lifecycleStates).toEqual(['new'])
    expect(controller.filters.severities).toEqual(['high'])
    expect(controller.filters.suppressed).toBe('no')
    expect(controller.page).toBe(2)
    expect(controller.selectedOccurrenceId).toBe('private-id')
    expect(controller.activityPage).toBe(3)
    expect(controller.loadedPageSearch).toBe('Exchange')
    expect(apiFetch).toHaveBeenCalledWith('/alerts/occurrences/private-id')
    act(() => controller.setPage(3))
    await settle()
    expect(controller.selectedOccurrenceId).toBeNull()
    expect(controller.page).toBe(3)
    await act(async () => { await router.navigate(-1) })
    expect(controller.page).toBe(2)
    expect(controller.selectedOccurrenceId).toBe('private-id')
    expect(controller.activityPage).toBe(3)
    await act(async () => { await router.navigate(1) })
    expect(controller.page).toBe(3)
    expect(controller.selectedOccurrenceId).toBeNull()
    act(() => controller.selectOccurrence('other/id'))
    await settle()
    expect(apiFetch).toHaveBeenCalledWith('/alerts/occurrences/other%2Fid')
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { clipboard: { writeText } })
    await act(async () => { await controller.copyTriageLink() })
    const link = new URL(writeText.mock.calls[0][0])
    expect(link.pathname).toBe('/alerts')
    expect(link.searchParams.get('occurrence')).toBe('other/id')
    expect(link.searchParams.get('page')).toBe('3')
    expect(controller.shareFeedback).toContain('copied')
    const reloaded = link.pathname + link.search
    act(() => root!.unmount()); router.dispose(); client.clear()
    render(reloaded)
    await settle()
    expect(controller.page).toBe(3)
    expect(controller.selectedOccurrenceId).toBe('other/id')
    expect(controller.filters.severities).toEqual(['high'])
  })

  it('normalizes malformed values and preserves an explicit rules view with retained occurrence context', () => {
    const params = new URLSearchParams('view=rules&occurrence=id&page=NaN&page_size=3&activity_page=-4&lifecycle_states=invalid&lifecycle_states=new&lifecycle_states=new&suppressed=bad')
    expect(alertsViewFromParams(params)).toBe('rules')
    const state = readAlertUrlState(params)
    expect(state.page).toBe(1)
    expect(state.activityPage).toBe(1)
    expect(state.pageSize).toBe(25)
    expect(state.filters.lifecycleStates).toEqual(['new'])
    expect(state.filters.suppressed).toBe('any')
    expect(writeAlertUrlState(params, { activityPage: 1 }).get('view')).toBe('rules')
  })
})
