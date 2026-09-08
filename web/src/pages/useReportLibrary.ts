import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '../api/client'
import type { ReportLibraryPage, ReportListItem } from '../types/api'

export const REPORT_LIBRARY_PAGE_SIZE = 25
export type ReportLibraryFilters = {
  status: '' | ReportListItem['status']; createdFrom: string; createdThrough: string
  q: string; reportType: string; triggerSource: '' | 'manual' | 'scheduled' | 'retry'
}
const EMPTY_FILTERS: ReportLibraryFilters = {
  status: '', createdFrom: '', createdThrough: '', q: '', reportType: '', triggerSource: '',
}

export function reportLibraryPath(filters: ReportLibraryFilters, cursor: string | null = null) {
  const params = new URLSearchParams({ limit: String(REPORT_LIBRARY_PAGE_SIZE) })
  if (cursor) params.set('cursor', cursor)
  if (filters.q.trim()) params.set('q', filters.q.trim())
  if (filters.reportType.trim()) params.set('report_type', filters.reportType.trim())
  if (filters.triggerSource) params.set('trigger_source', filters.triggerSource)
  if (filters.status) params.set('status', filters.status)
  if (filters.createdFrom) params.set('created_from', `${filters.createdFrom}T00:00:00Z`)
  if (filters.createdThrough) {
    const before = new Date(`${filters.createdThrough}T00:00:00Z`)
    before.setUTCDate(before.getUTCDate() + 1)
    params.set('created_before', before.toISOString())
  }
  return `/reports/library?${params}`
}

export function useReportLibrary() {
  const queryClient = useQueryClient()
  const [navigation, setNavigation] = useState<{ index: number; cursors: Array<string | null> }>({ index: 0, cursors: [null] })
  const [filters, setFilters] = useState<ReportLibraryFilters>(EMPTY_FILTERS)
  const page = navigation.index + 1
  const filterError = filters.createdFrom && filters.createdThrough && filters.createdFrom > filters.createdThrough
    ? 'Created through must be on or after created from.' : null
  const path = reportLibraryPath(filters, navigation.cursors[navigation.index])
  const query = useQuery({
    queryKey: ['reports', 'library', path],
    queryFn: ({ signal }) => apiFetch<ReportLibraryPage>(path, { signal }),
    enabled: !filterError,
    refetchInterval: 10_000,
  })
  useEffect(() => {
    if (query.isSuccess && !query.isFetching && query.data.items.length === 0 && page > 1) {
      setNavigation((current) => ({ ...current, index: Math.max(0, current.index - 1) }))
    }
  }, [query.isSuccess, query.isFetching, query.data, page])
  const reports = filterError ? [] : query.data?.items ?? []
  const reset = () => setNavigation({ index: 0, cursors: [null] })
  return {
    query, page, filters, filterError, reports,
    hasNextPage: Boolean(query.data?.next_cursor),
    first: reports.length ? navigation.index * REPORT_LIBRARY_PAGE_SIZE + 1 : 0,
    last: navigation.index * REPORT_LIBRARY_PAGE_SIZE + reports.length,
    asOf: query.data?.as_of,
    setPage: (next: number) => {
      if (query.isFetching) return
      if (next === page - 1 && page > 1) setNavigation((current) => ({ ...current, index: current.index - 1 }))
      else if (next === page + 1 && query.data?.next_cursor) {
        const current = query.data.current_cursor
        const following = query.data.next_cursor
        setNavigation((previous) => ({ index: previous.index + 1,
          cursors: [...previous.cursors.slice(0, previous.index), current, following] }))
      }
    },
    updateFilters: (changes: Partial<ReportLibraryFilters>) => { setFilters((current) => ({ ...current, ...changes })); reset() },
    clearFilters: () => { setFilters(EMPTY_FILTERS); reset() },
    refresh: () => { reset(); void queryClient.invalidateQueries({ queryKey: ['reports', 'library'] }) },
  }
}
