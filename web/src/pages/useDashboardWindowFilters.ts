import { type Dispatch, type SetStateAction, useMemo } from 'react'

import { DASHBOARD_TIME_INHERIT_VALUE } from './dashboardPanelPresentation'
import {
  createDefaultAlertWindowFilters,
  createDefaultRssWindowFilters,
  isTimeRangeFilter,
  normalizeRollingDaysInput,
  type DashboardAlertWindowFilters,
  type DashboardRssWindowFilters,
  type DashboardWindow,
  type TimeRangeFilter,
  type WindowTimeFilter,
} from './dashboardSavedViews'
import { summarizeGlobalSearchAcrossWindows } from './dashboardState'

type DashboardWindowFiltersOptions = {
  dashboardTimeFilter: WindowTimeFilter
  setDashboardCustomSinceDate: Dispatch<SetStateAction<string>>
  setDashboardCustomUntilDate: Dispatch<SetStateAction<string>>
  setDashboardRollingDays: Dispatch<SetStateAction<string>>
  setDashboardTimeRange: Dispatch<SetStateAction<TimeRangeFilter>>
  setWindowSeenAt: Dispatch<SetStateAction<Record<string, string>>>
  setWindows: Dispatch<SetStateAction<DashboardWindow[]>>
  windows: DashboardWindow[]
}

export function useDashboardWindowFilters({
  dashboardTimeFilter,
  setDashboardCustomSinceDate,
  setDashboardCustomUntilDate,
  setDashboardRollingDays,
  setDashboardTimeRange,
  setWindowSeenAt,
  setWindows,
  windows,
}: DashboardWindowFiltersOptions) {
  const updateWindowRssFilters = (
    windowId: string,
    updater: (current: DashboardRssWindowFilters) => DashboardRssWindowFilters,
    resetPage = true,
  ) => {
    setWindows((current) =>
      current.map((window) => {
        if (window.id !== windowId || window.type !== 'rss') {
          return window
        }

        const nextFilters = updater(window.rss_filters ?? createDefaultRssWindowFilters())
        return {
          ...window,
          rss_filters: {
            ...nextFilters,
            page: resetPage ? 1 : nextFilters.page,
          },
        }
      }),
    )
  }

  const updateWindowAlertFilters = (
    windowId: string,
    updater: (current: DashboardAlertWindowFilters) => DashboardAlertWindowFilters,
    resetPage = true,
  ) => {
    setWindows((current) =>
      current.map((window) => {
        if (window.id !== windowId || window.type !== 'alerts') {
          return window
        }

        const nextFilters = updater(window.alert_filters ?? createDefaultAlertWindowFilters())
        return {
          ...window,
          alert_filters: {
            ...nextFilters,
            page: resetPage ? 1 : nextFilters.page,
          },
        }
      }),
    )
  }

  const updateWindowDailyBriefSelection = (windowId: string, selectedDailyBriefId: string) => {
    setWindows((current) =>
      current.map((window) =>
        window.id === windowId && window.type === 'daily_brief'
          ? { ...window, selected_daily_brief_id: selectedDailyBriefId || null }
          : window,
      ),
    )
  }

  const markWindowSeen = (windowId: string) => {
    setWindowSeenAt((current) => ({
      ...current,
      [windowId]: new Date().toISOString(),
    }))
  }

  const resetAllWindowPages = () => {
    setWindows((current) =>
      current.map((window) => {
        if (window.type === 'rss') {
          return {
            ...window,
            rss_filters: {
              ...(window.rss_filters ?? createDefaultRssWindowFilters()),
              page: 1,
            },
          }
        }

        if (window.type === 'alerts') {
          return {
            ...window,
            alert_filters: {
              ...(window.alert_filters ?? createDefaultAlertWindowFilters()),
              page: 1,
            },
          }
        }

        return window
      }),
    )
  }

  const updateDashboardTimeRange = (nextRange: TimeRangeFilter) => {
    resetAllWindowPages()
    setDashboardTimeRange(nextRange)
  }

  const updateDashboardCustomSinceDate = (nextDate: string) => {
    resetAllWindowPages()
    setDashboardCustomSinceDate(nextDate)
  }

  const updateDashboardCustomUntilDate = (nextDate: string) => {
    resetAllWindowPages()
    setDashboardCustomUntilDate(nextDate)
  }

  const updateDashboardRollingDaysValue = (nextValue: string) => {
    resetAllWindowPages()
    setDashboardTimeRange('days')
    setDashboardRollingDays(normalizeRollingDaysInput(nextValue))
  }

  const updateWindowTimeRange = (windowId: string, nextValue: string) => {
    setWindows((current) =>
      current.map((window) => {
        if (window.id !== windowId || window.type === 'notes' || window.type === 'daily_brief') {
          return window
        }

        if (nextValue === DASHBOARD_TIME_INHERIT_VALUE) {
          return {
            ...window,
            time_override: null,
          }
        }

        if (!isTimeRangeFilter(nextValue)) {
          return window
        }

        const base = window.time_override ?? dashboardTimeFilter
        if (window.type === 'rss') {
          return {
            ...window,
            rss_filters: {
              ...(window.rss_filters ?? createDefaultRssWindowFilters()),
              page: 1,
            },
            time_override: {
              ...base,
              time_range: nextValue,
            },
          }
        }

        return {
          ...window,
          alert_filters: {
            ...(window.alert_filters ?? createDefaultAlertWindowFilters()),
            page: 1,
          },
          time_override: {
            ...base,
            time_range: nextValue,
          },
        }
      }),
    )
  }

  const updateWindowCustomTimeDate = (
    windowId: string,
    key: 'custom_since_date' | 'custom_until_date',
    value: string,
  ) => {
    setWindows((current) =>
      current.map((window) => {
        if (window.id !== windowId || window.type === 'notes' || window.type === 'daily_brief') {
          return window
        }

        const base = window.time_override ?? dashboardTimeFilter
        if (window.type === 'rss') {
          return {
            ...window,
            rss_filters: {
              ...(window.rss_filters ?? createDefaultRssWindowFilters()),
              page: 1,
            },
            time_override: {
              ...base,
              time_range: 'custom',
              [key]: value,
            },
          }
        }

        return {
          ...window,
          alert_filters: {
            ...(window.alert_filters ?? createDefaultAlertWindowFilters()),
            page: 1,
          },
          time_override: {
            ...base,
            time_range: 'custom',
            [key]: value,
          },
        }
      }),
    )
  }

  const updateWindowRollingDays = (windowId: string, value: string) => {
    const normalized = normalizeRollingDaysInput(value)
    setWindows((current) =>
      current.map((window) => {
        if (window.id !== windowId || window.type === 'notes' || window.type === 'daily_brief') {
          return window
        }

        const base = window.time_override ?? dashboardTimeFilter
        if (window.type === 'rss') {
          return {
            ...window,
            rss_filters: {
              ...(window.rss_filters ?? createDefaultRssWindowFilters()),
              page: 1,
            },
            time_override: {
              ...base,
              time_range: 'days',
              rolling_days: normalized,
            },
          }
        }

        return {
          ...window,
          alert_filters: {
            ...(window.alert_filters ?? createDefaultAlertWindowFilters()),
            page: 1,
          },
          time_override: {
            ...base,
            time_range: 'days',
            rolling_days: normalized,
          },
        }
      }),
    )
  }

  const applyGlobalSearch = (query: string) => {
    setWindows((current) =>
      current.map((window) => {
        if (window.type === 'rss') {
          return {
            ...window,
            rss_filters: {
              ...(window.rss_filters ?? createDefaultRssWindowFilters()),
              q: query,
              page: 1,
            },
          }
        }
        if (window.type === 'alerts') {
          return {
            ...window,
            alert_filters: {
              ...(window.alert_filters ?? createDefaultAlertWindowFilters()),
              q: query,
              page: 1,
            },
          }
        }
        return window
      }),
    )
  }

  const globalSearchState = useMemo(() => summarizeGlobalSearchAcrossWindows(windows), [windows])

  return {
    applyGlobalSearch,
    globalSearchState,
    markWindowSeen,
    updateDashboardCustomSinceDate,
    updateDashboardCustomUntilDate,
    updateDashboardRollingDaysValue,
    updateDashboardTimeRange,
    updateWindowAlertFilters,
    updateWindowCustomTimeDate,
    updateWindowDailyBriefSelection,
    updateWindowRollingDays,
    updateWindowRssFilters,
    updateWindowTimeRange,
  }
}
