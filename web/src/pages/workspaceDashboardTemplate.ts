import type { SavedViewQueryPayload } from '../types/savedViews'
import { parseDashboardSavedView, type DashboardWindow } from './dashboardSavedViews'

export function workspaceTemplateWindows(template: SavedViewQueryPayload, width: number, height: number): DashboardWindow[] {
  const parsed = parseDashboardSavedView(template, width, height)
  return parsed.windows.map((window) => {
    if (window.type !== 'rss' && window.type !== 'alerts') return window
    const filters = window.type === 'rss' ? parsed.rss_filters : parsed.alert_filters
    return { ...window, time_override: window.time_override ?? {
      time_range: filters.time_range, custom_since_date: filters.custom_since_date,
      custom_until_date: filters.custom_until_date, rolling_days: filters.rolling_days,
    } }
  })
}
