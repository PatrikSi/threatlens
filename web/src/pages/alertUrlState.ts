import { useCallback, useMemo, useRef } from 'react'
import { useLocation, useSearchParams } from 'react-router-dom'
import { ALERT_OCCURRENCE_PAGE_SIZES, ALERT_OCCURRENCE_STATES, ALERT_SEVERITIES, DEFAULT_ALERT_OCCURRENCE_FILTERS, type AlertOccurrenceFilters } from './alertOccurrenceModel'

export type AlertsView = 'rules' | 'occurrences' | 'operations'
export type AlertUrlState = {
  filters: AlertOccurrenceFilters
  page: number
  pageSize: number
  loadedPageSearch: string
  selectedOccurrenceId: string | null
  activityPage: number
}
const FILTER_KEYS = ['lifecycle_states', 'severities', 'alert_interest_id', 'suppressed', 'snoozed', 'since', 'until'] as const
function positiveInteger(value: string | null, fallback: number) {
  const parsed = Number(value)
  return /^\d+$/.test(value ?? '') && Number.isSafeInteger(parsed) && parsed > 0 && parsed <= 2_147_483_647 ? parsed : fallback
}
export function alertsViewFromParams(params: URLSearchParams): AlertsView {
  const value = params.get('view')
  return value === 'rules' || value === 'operations' || value === 'occurrences' ? value : params.has('occurrence') ? 'occurrences' : 'rules'
}
export function readAlertUrlState(params: URLSearchParams): AlertUrlState {
  const pageSize = positiveInteger(params.get('page_size'), 25)
  const booleanFilter = (key: string) => params.get(key) === 'yes' ? 'yes' : params.get(key) === 'no' ? 'no' : 'any'
  return {
    filters: {
      lifecycleStates: ALERT_OCCURRENCE_STATES.map(({ value }) => value).filter((value) => params.getAll('lifecycle_states').includes(value)),
      severities: ALERT_SEVERITIES.map(({ value }) => value).filter((value) => params.getAll('severities').includes(value)),
      ruleId: (params.get('alert_interest_id') ?? '').slice(0, 128),
      suppressed: booleanFilter('suppressed'), snoozed: booleanFilter('snoozed'),
      since: (params.get('since') ?? '').slice(0, 64), until: (params.get('until') ?? '').slice(0, 64),
    },
    page: positiveInteger(params.get('page'), 1),
    pageSize: ALERT_OCCURRENCE_PAGE_SIZES.includes(pageSize as 25 | 50 | 100) ? pageSize : 25,
    loadedPageSearch: (params.get('search') ?? '').slice(0, 255),
    selectedOccurrenceId: params.get('occurrence')?.slice(0, 128) || null,
    activityPage: positiveInteger(params.get('activity_page'), 1),
  }
}
export function writeAlertUrlState(params: URLSearchParams, changes: Partial<AlertUrlState>) {
  const next = new URLSearchParams(params)
  if (!next.has('view')) next.set('view', 'occurrences')
  if (changes.filters) {
    FILTER_KEYS.forEach((key) => next.delete(key))
    const filters = changes.filters
    filters.lifecycleStates.forEach((value) => next.append('lifecycle_states', value))
    filters.severities.forEach((value) => next.append('severities', value))
    if (filters.ruleId) next.set('alert_interest_id', filters.ruleId)
    if (filters.suppressed !== 'any') next.set('suppressed', filters.suppressed)
    if (filters.snoozed !== 'any') next.set('snoozed', filters.snoozed)
    if (filters.since) next.set('since', filters.since)
    if (filters.until) next.set('until', filters.until)
  }
  const values = { page: changes.page, page_size: changes.pageSize, search: changes.loadedPageSearch, occurrence: changes.selectedOccurrenceId, activity_page: changes.activityPage }
  for (const [key, value] of Object.entries(values)) {
    if (value === undefined) continue
    if (value === null || value === '' || (key !== 'page_size' && value === 1) || (key === 'page_size' && value === 25)) next.delete(key)
    else next.set(key, String(value))
  }
  return next
}
export function useAlertUrlState() {
  const [params, setParams] = useSearchParams()
  const location = useLocation()
  const paramsRef = useRef(params)
  paramsRef.current = params
  const state = useMemo(() => readAlertUrlState(params), [params])
  const update = useCallback((changes: Partial<AlertUrlState>, replace = false) => {
    const next = writeAlertUrlState(paramsRef.current, changes)
    paramsRef.current = next
    setParams(next, { replace, preventScrollReset: true })
  }, [setParams])
  const shareParams = new URLSearchParams(params)
  shareParams.set('view', 'occurrences')
  return {
    ...state,
    shareUrl: `${window.location.origin}${location.pathname}?${writeAlertUrlState(shareParams, state)}`,
    update,
    reset: () => setParams(writeAlertUrlState(params, { filters: DEFAULT_ALERT_OCCURRENCE_FILTERS, page: 1, pageSize: 25, loadedPageSearch: '', selectedOccurrenceId: null, activityPage: 1 }), { preventScrollReset: true }),
  }
}
