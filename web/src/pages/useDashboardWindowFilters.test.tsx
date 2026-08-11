// @vitest-environment jsdom

import { act, useState } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import {
  createDefaultAlertWindowFilters,
  createDefaultRssWindowFilters,
  type DashboardWindow,
  type TimeRangeFilter,
  type WindowTimeFilter,
} from './dashboardSavedViews'
import { useDashboardWindowFilters } from './useDashboardWindowFilters'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const defaultTimeFilter: WindowTimeFilter = {
  time_range: 'all',
  custom_since_date: '',
  custom_until_date: '',
  rolling_days: '7',
}

function createWindows(): DashboardWindow[] {
  return [
    {
      id: 'rss-1',
      type: 'rss',
      title: 'RSS',
      snap: 'full',
      rect: { x: 0, y: 0, width: 1200, height: 720 },
      controls_collapsed: false,
      scratch_note: '',
      time_override: null,
      rss_filters: { ...createDefaultRssWindowFilters(), q: 'old', page: 4 },
      alert_filters: null,
      selected_daily_brief_id: null,
    },
    {
      id: 'alerts-1',
      type: 'alerts',
      title: 'Alerts',
      snap: 'full',
      rect: { x: 0, y: 0, width: 1200, height: 720 },
      controls_collapsed: false,
      scratch_note: '',
      time_override: null,
      rss_filters: null,
      alert_filters: { ...createDefaultAlertWindowFilters(), q: 'old', page: 3 },
      selected_daily_brief_id: null,
    },
    {
      id: 'notes-1',
      type: 'notes',
      title: 'Notes',
      snap: 'full',
      rect: { x: 0, y: 0, width: 1200, height: 720 },
      controls_collapsed: false,
      scratch_note: 'unchanged',
      time_override: null,
      rss_filters: null,
      alert_filters: null,
      selected_daily_brief_id: null,
    },
  ]
}

type HarnessValue = ReturnType<typeof useDashboardWindowFilters> & {
  dashboardRollingDays: string
  dashboardTimeRange: TimeRangeFilter
  windows: DashboardWindow[]
}

let root: Root
let container: HTMLDivElement
let latest: HarnessValue | null

function Harness() {
  const [windows, setWindows] = useState(createWindows)
  const [dashboardTimeRange, setDashboardTimeRange] = useState<TimeRangeFilter>('all')
  const [, setDashboardCustomSinceDate] = useState('')
  const [, setDashboardCustomUntilDate] = useState('')
  const [dashboardRollingDays, setDashboardRollingDays] = useState('7')
  const [, setWindowSeenAt] = useState<Record<string, string>>({})
  const actions = useDashboardWindowFilters({
    dashboardTimeFilter: { ...defaultTimeFilter, time_range: dashboardTimeRange, rolling_days: dashboardRollingDays },
    setDashboardCustomSinceDate,
    setDashboardCustomUntilDate,
    setDashboardRollingDays,
    setDashboardTimeRange,
    setWindowSeenAt,
    setWindows,
    windows,
  })

  latest = { ...actions, dashboardRollingDays, dashboardTimeRange, windows }
  return null
}

beforeEach(() => {
  latest = null
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => root.render(<Harness />))
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
})

describe('useDashboardWindowFilters', () => {
  it('applies global search only to searchable panels and resets their pages', () => {
    act(() => latest?.applyGlobalSearch('cve-2026'))

    expect(latest?.windows[0].rss_filters).toMatchObject({ q: 'cve-2026', page: 1 })
    expect(latest?.windows[1].alert_filters).toMatchObject({ q: 'cve-2026', page: 1 })
    expect(latest?.windows[2].scratch_note).toBe('unchanged')
    expect(latest?.globalSearchState).toEqual({ value: 'cve-2026', isMixed: false })
  })

  it('normalizes rolling days and resets every searchable page', () => {
    act(() => latest?.updateDashboardRollingDaysValue('9999 days'))

    expect(latest?.dashboardTimeRange).toBe('days')
    expect(latest?.dashboardRollingDays).toBe('365')
    expect(latest?.windows[0].rss_filters?.page).toBe(1)
    expect(latest?.windows[1].alert_filters?.page).toBe(1)
  })
})
