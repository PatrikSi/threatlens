import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../api/client'
import type { ReportListItem } from '../types/api'

export const REPORT_LIBRARY_PAGE_SIZE = 25
export type ReportLibraryFilters = { status: '' | ReportListItem['status']; createdFrom: string; createdThrough: string }
const EMPTY_FILTERS: ReportLibraryFilters = { status: '', createdFrom: '', createdThrough: '' }

export function reportLibraryPath(filters: ReportLibraryFilters, page: number) {
  const params = new URLSearchParams({ limit: String(REPORT_LIBRARY_PAGE_SIZE + 1), offset: String((page - 1) * REPORT_LIBRARY_PAGE_SIZE) })
  if (filters.status) params.set('status', filters.status)
  if (filters.createdFrom) params.set('created_from', `${filters.createdFrom}T00:00:00Z`)
  if (filters.createdThrough) {
    const before = new Date(`${filters.createdThrough}T00:00:00Z`)
    before.setUTCDate(before.getUTCDate() + 1)
    params.set('created_before', before.toISOString())
  }
  return `/reports?${params}`
}

export function useReportLibrary() {
  const [page, setPage] = useState(1)
  const [filters, setFilters] = useState<ReportLibraryFilters>(EMPTY_FILTERS)
  const filterError = filters.createdFrom && filters.createdThrough && filters.createdFrom > filters.createdThrough
    ? 'Created through must be on or after created from.' : null
  const path = reportLibraryPath(filters, page)
  const query = useQuery({
    queryKey: ['reports', 'library', path],
    queryFn: ({ signal }) => apiFetch<ReportListItem[]>(path, { signal }),
    enabled: !filterError,
    refetchInterval: 10_000,
  })
  useEffect(() => {
    if (query.isSuccess && !query.isFetching && query.data.length === 0 && page > 1) setPage((current) => Math.max(1, current - 1))
  }, [query.isSuccess, query.isFetching, query.data, page])
  const reports = filterError ? [] : (query.data ?? []).slice(0, REPORT_LIBRARY_PAGE_SIZE)
  return {
    query, page, filters, filterError, reports,
    hasNextPage: (query.data?.length ?? 0) > REPORT_LIBRARY_PAGE_SIZE,
    first: reports.length ? (page - 1) * REPORT_LIBRARY_PAGE_SIZE + 1 : 0,
    last: (page - 1) * REPORT_LIBRARY_PAGE_SIZE + reports.length,
    setPage: (next: number) => setPage(Math.max(1, next)),
    updateFilters: (changes: Partial<ReportLibraryFilters>) => { setFilters((current) => ({ ...current, ...changes })); setPage(1) },
    clearFilters: () => { setFilters(EMPTY_FILTERS); setPage(1) },
  }
}
