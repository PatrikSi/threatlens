// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiFetch } from '../api/client'
import { reportLibraryPath, useReportLibrary } from './useReportLibrary'
import type { ReportListItem } from '../types/api'
;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', () => ({ apiFetch: vi.fn() }))
let root: Root
let client: QueryClient
let library: ReturnType<typeof useReportLibrary>
function Harness() { library = useReportLibrary(); return null }
async function settle() { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 15)) }) }
afterEach(() => { act(() => root?.unmount()); client?.clear(); vi.clearAllMocks() })
describe('report library scope and paging', () => {
  it('reaches records older than the first hundred, uses opaque cursors and resets filters to page one', async () => {
    const dataset = Array.from({ length: 121 }, (_, index) => ({ id: `report-${index + 1}`, status: 'ready' })) as ReportListItem[]
    vi.mocked(apiFetch).mockImplementation((path) => {
      const params = new URLSearchParams(path.split('?')[1])
      const cursor = params.get('cursor')
      const offset = cursor && cursor !== 'first' ? dataset.findIndex((row) => row.id === cursor) + 1 : 0
      const items = params.get('status') === 'error' ? [] : dataset.slice(offset, offset + Number(params.get('limit')))
      return Promise.resolve({ items, current_cursor: cursor ?? 'first',
        next_cursor: offset + items.length < dataset.length && items.length ? items.at(-1)!.id : null,
        as_of: '2026-09-08T00:00:00Z' }) as never
    })
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    root = createRoot(document.createElement('div'))
    act(() => root.render(<QueryClientProvider client={client}><Harness /></QueryClientProvider>))
    await settle()
    expect(library.reports).toHaveLength(25)
    expect(library.hasNextPage).toBe(true)
    for (let page = 2; page <= 5; page++) { act(() => library.setPage(page)); await settle() }
    expect(library.reports[0].id).toBe('report-101')
    expect(library.first).toBe(101)
    expect(library.last).toBe(121)
    expect(library.hasNextPage).toBe(false)
    act(() => library.updateFilters({ status: 'error', createdFrom: '2026-09-01', createdThrough: '2026-09-08' }))
    await settle()
    expect(library.page).toBe(1)
    expect(library.reports).toEqual([])
    const path = vi.mocked(apiFetch).mock.lastCall![0]
    expect(path).not.toContain('offset=')
    expect(path).not.toContain('cursor=')
    expect(path).toContain('status=error')
    const params = new URLSearchParams(path.split('?')[1])
    expect(params.get('created_from')).toBe('2026-09-01T00:00:00Z')
    expect(params.get('created_before')).toBe('2026-09-09T00:00:00.000Z')
    vi.mocked(apiFetch).mockClear()
    act(() => library.updateFilters({ createdThrough: '2026-08-01' }))
    await settle()
    expect(library.filterError).toBeTruthy()
    expect(apiFetch).not.toHaveBeenCalled()
    act(() => library.clearFilters())
    await settle()
    expect(library.page).toBe(1)
    expect(library.filters.status).toBe('')
  })

  it('uses the next UTC day for inclusive date input across a year boundary', () => {
    const params = new URLSearchParams(reportLibraryPath({ status: '', createdFrom: '', createdThrough: '2026-12-31', q: '', reportType: '', triggerSource: '' }).split('?')[1])
    expect(params.get('created_before')).toBe('2027-01-01T00:00:00.000Z')
  })

  it('keeps the first-page cutoff when going back and refresh deliberately releases it', async () => {
    let newest = 'original'
    vi.mocked(apiFetch).mockImplementation((path) => {
      const cursor = new URLSearchParams(path.split('?')[1]).get('cursor')
      return Promise.resolve({ items: [{ id: cursor === 'second' ? 'older' : cursor ? 'original' : newest }],
        current_cursor: cursor ?? 'anchored-first', next_cursor: cursor === 'second' ? null : 'second',
        as_of: '2026-09-08T00:00:00Z' }) as never
    })
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    root = createRoot(document.createElement('div'))
    act(() => root.render(<QueryClientProvider client={client}><Harness /></QueryClientProvider>))
    await settle()
    act(() => library.setPage(2)); await settle()
    expect(library.reports[0].id).toBe('older')
    newest = 'new arrival'
    act(() => library.setPage(1)); await settle()
    expect(library.reports[0].id).toBe('original')
    expect(vi.mocked(apiFetch).mock.lastCall![0]).toContain('cursor=anchored-first')
    act(() => library.refresh()); await settle()
    expect(library.reports[0].id).toBe('new arrival')
    act(() => library.updateFilters({ q: '"supply chain" OR phishing -test', reportType: 'weekly', triggerSource: 'scheduled' }))
    await settle()
    const params = new URLSearchParams(vi.mocked(apiFetch).mock.lastCall![0].split('?')[1])
    expect(params.has('cursor')).toBe(false)
    expect(params.get('q')).toBe('"supply chain" OR phishing -test')
    expect(params.get('report_type')).toBe('weekly')
    expect(params.get('trigger_source')).toBe('scheduled')
  })
})
