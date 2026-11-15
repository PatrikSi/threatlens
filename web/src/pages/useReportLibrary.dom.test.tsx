// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import { reportLibraryPath, useReportLibrary } from './useReportLibrary'
import type { ReportLibraryPage, ReportListItem } from '../types/api'
;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...(await original<object>()), apiFetch: vi.fn() }))
let root: Root
let client: QueryClient
let library: ReturnType<typeof useReportLibrary>
function Harness() { library = useReportLibrary(); return null }
async function settle() { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 15)) }) }
afterEach(() => { act(() => root?.unmount()); client?.clear(); vi.clearAllMocks(); vi.restoreAllMocks() })
function libraryPage(id: string, cursor = 'anchored-first'): ReportLibraryPage {
  return { items: [{ id, title: id, status: 'ready' } as ReportListItem], current_cursor: cursor,
    next_cursor: cursor === 'second' ? null : 'second', as_of: '2026-09-08T00:00:00Z' }
}
function heldLibraryPage() {
  let resolve!: (value: ReportLibraryPage) => void
  let reject!: (reason: Error) => void
  const promise = new Promise<ReportLibraryPage>((done, fail) => { resolve = done; reject = fail })
  return { promise, resolve, reject }
}
async function primeLibraryPages() {
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } })
  root = createRoot(document.createElement('div'))
  act(() => root.render(<QueryClientProvider client={client}><Harness /></QueryClientProvider>))
  await settle()
  act(() => library.updateFilters({ q: 'cached-filter' })); await settle()
  act(() => library.clearFilters()); await settle()
  act(() => library.setPage(2)); await settle()
  act(() => library.setPage(1)); await settle()
  act(() => library.setPage(2)); await settle()
}
describe('report library scope and paging', () => {
  it.each(['previous', 'refresh', 'filters', 'remount'])('keeps a denied library fenced through cached %s re-entry', async (entry) => {
    vi.spyOn(Date, 'now').mockReturnValue(Date.parse('2026-11-15T12:00:00Z'))
    const held = heldLibraryPage()
    let phase = 'seed'
    vi.mocked(apiFetch).mockImplementation((path) => {
      const params = new URLSearchParams(path.split('?')[1])
      if (phase === 'denied') return Promise.reject(new ApiError('Library access removed', 403, path))
      if (phase === 'held') return held.promise as never
      return Promise.resolve(libraryPage(params.get('q') ? 'private-filtered' : params.get('cursor') === 'second'
        ? 'private-second' : 'private-first', params.get('cursor') ?? 'anchored-first')) as never
    })
    await primeLibraryPages()
    expect(library.reports[0]?.id).toBe('private-second')
    phase = 'denied'
    await act(async () => { await library.query.refetch() }); await settle()
    expect(library.reports).toEqual([])
    phase = 'held'
    if (entry === 'previous') act(() => library.setPage(1))
    else if (entry === 'refresh') act(() => library.refresh())
    else if (entry === 'filters') act(() => library.updateFilters({ q: 'cached-filter' }))
    else {
      act(() => root.unmount())
      root = createRoot(document.createElement('div'))
      act(() => root.render(<QueryClientProvider client={client}><Harness /></QueryClientProvider>))
    }
    await settle()
    expect(library.reports).toEqual([])
    expect(library.hasNextPage).toBe(false)
    expect(library.asOf).toBeUndefined()
    await act(async () => { held.resolve(libraryPage('freshly-authorized')); await Promise.resolve() }); await settle()
    expect(library.reports[0]?.id).toBe('freshly-authorized')
  })

  it('keeps the denial fence after a same-key retry returns an outage', async () => {
    vi.mocked(apiFetch).mockResolvedValue(libraryPage('private-report'))
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    root = createRoot(document.createElement('div'))
    act(() => root.render(<QueryClientProvider client={client}><Harness /></QueryClientProvider>))
    await settle()
    for (const status of [403, 503]) {
      vi.mocked(apiFetch).mockRejectedValue(new ApiError('Read unavailable', status, '/reports/library'))
      await act(async () => { await library.query.refetch() }); await settle()
      expect(library.reports).toEqual([])
    }
    vi.mocked(apiFetch).mockResolvedValue(libraryPage('freshly-authorized'))
    await act(async () => { await library.query.refetch() }); await settle()
    expect(library.reports[0]?.id).toBe('freshly-authorized')
  })

  it('does not let an older in-flight sibling read release a later denial fence', async () => {
    const older = heldLibraryPage()
    const current = heldLibraryPage()
    let phase = 'seed'
    vi.mocked(apiFetch).mockImplementation((path) => {
      const params = new URLSearchParams(path.split('?')[1])
      if (params.get('q') === 'cached-filter' && phase === 'older') return older.promise as never
      if (phase === 'denied') return Promise.reject(new ApiError('Access removed', 403, path))
      if (phase === 'held') return current.promise as never
      return Promise.resolve(libraryPage(params.get('q') ? 'private-filtered' : 'private-page', params.get('cursor') ?? 'anchored-first')) as never
    })
    await primeLibraryPages()
    phase = 'older'
    const sibling = ['reports', 'library', reportLibraryPath({ ...library.filters, q: 'cached-filter' })]
    let oldRequest!: Promise<void>
    act(() => { oldRequest = client.refetchQueries({ queryKey: sibling, exact: true }) })
    await settle()
    phase = 'denied'
    await act(async () => { await library.query.refetch() }); await settle()
    phase = 'held'
    act(() => library.updateFilters({ q: 'cached-filter' })); await settle()
    await act(async () => { older.resolve(libraryPage('late-private-result')); await oldRequest }); await settle()
    expect(library.reports).toEqual([])
    let freshRequest!: Promise<unknown>
    act(() => { freshRequest = library.query.refetch() }); await settle()
    expect(library.reports).toEqual([])
    await act(async () => { current.resolve(libraryPage('freshly-authorized')); await freshRequest }); await settle()
    expect(library.reports[0]?.id).toBe('freshly-authorized')
  })

  it('does not let an older in-flight sibling failure retire a fresh authorized library read', async () => {
    const older = heldLibraryPage()
    let phase = 'seed'
    vi.mocked(apiFetch).mockImplementation((path) => {
      const params = new URLSearchParams(path.split('?')[1])
      if (params.get('q') === 'cached-filter' && phase === 'older') return older.promise as never
      if (phase === 'denied') return Promise.reject(new ApiError('Access removed', 403, path))
      return Promise.resolve(libraryPage(phase === 'fresh' ? 'freshly-authorized' : 'private-page', params.get('cursor') ?? 'anchored-first')) as never
    })
    await primeLibraryPages()
    phase = 'older'
    let oldRequest!: Promise<void>
    act(() => { oldRequest = client.refetchQueries({ queryKey: ['reports', 'library', reportLibraryPath({ ...library.filters, q: 'cached-filter' })], exact: true }) })
    await settle()
    phase = 'denied'
    await act(async () => { await library.query.refetch() }); await settle()
    phase = 'fresh'
    await act(async () => { await library.query.refetch() }); await settle()
    expect(library.reports[0]?.id).toBe('freshly-authorized')
    await act(async () => { older.reject(new ApiError('Old denied read', 403, '/reports/library')); await oldRequest }); await settle()
    expect(library.reports[0]?.id).toBe('freshly-authorized')
  })

  it.each([401, 403, 404, 503])('handles retained library results after a %s refresh', async (status) => {
    vi.mocked(apiFetch).mockResolvedValue({ items: [{ id: 'private-report', title: 'Private report' }],
      current_cursor: 'first', next_cursor: 'next', as_of: '2026-09-08T00:00:00Z' })
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    root = createRoot(document.createElement('div'))
    act(() => root.render(<QueryClientProvider client={client}><Harness /></QueryClientProvider>))
    await settle()
    expect(library.reports).toHaveLength(1)
    vi.mocked(apiFetch).mockRejectedValue(new ApiError('Library unavailable', status, '/reports/library'))
    await act(async () => { await client.invalidateQueries({ queryKey: ['reports', 'library'] }) })
    await settle()
    expect(library.query.isError).toBe(true)
    if (status === 503) expect(library.reports[0]?.id).toBe('private-report')
    else {
      expect(library.reports).toEqual([])
      expect(library.hasNextPage).toBe(false)
      expect(library.asOf).toBeUndefined()
    }
  })

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
