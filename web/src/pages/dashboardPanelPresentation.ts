import type { DashboardWindowSnap, DashboardWindowType } from './dashboardSavedViews'

export const MOBILE_DASHBOARD_PAGE_SIZE = 10
export const DASHBOARD_TIME_INHERIT_VALUE = '__dashboard_time__'
export const ROLLING_WINDOW_FIELD_CLASS =
  'flex w-full items-center rounded border border-slate/20 bg-white px-2 py-1.5 text-sm focus-within:border-cyan/60 focus-within:ring-2 focus-within:ring-cyan/60 focus-within:ring-offset-1 dark:border-cyan-900/40 dark:bg-[#072019] dark:focus-within:border-cyan-400/60 dark:focus-within:ring-cyan-300/60 dark:focus-within:ring-offset-[var(--tl-input-bg)]'
export const FILTER_SCROLLER_CLASS =
  'tl-dashboard-filter-scroller flex min-w-0 flex-nowrap items-center gap-1.5 overflow-x-auto overflow-y-hidden pb-1'
export const FILTER_CHIP_CLASS =
  'inline-flex h-7 shrink-0 items-center whitespace-nowrap rounded border px-3 py-1 text-xs font-semibold'

export const WINDOW_SNAP_OPTIONS: Array<{ value: DashboardWindowSnap; label: string }> = [
  { value: 'free', label: 'Floating' },
  { value: 'full', label: 'Full' },
  { value: 'left', label: 'Left Half' },
  { value: 'right', label: 'Right Half' },
  { value: 'top_left', label: 'Top Left' },
  { value: 'top_right', label: 'Top Right' },
  { value: 'bottom_left', label: 'Bottom Left' },
  { value: 'bottom_right', label: 'Bottom Right' },
]

export function resolvePanelPageSize(queryPageSize: number | undefined, configuredPageSize: number) {
  return queryPageSize ?? configuredPageSize
}

export function selectVisibleItemTags(tags: string[], wideLayout: boolean) {
  return tags.slice(0, wideLayout ? 3 : 1)
}

export function resolveRssItemDetailClassName(wideLayout: boolean) {
  return wideLayout
    ? 'tl-rss-item-detail mt-3 border-t border-slate/20 pt-3 dark:border-cyan-900/40'
    : 'tl-mobile-rss-detail fixed inset-0 z-50 overflow-y-auto bg-white px-3 pb-6 dark:bg-[#03130f]'
}

export function formatAlertMatchCount(count: number) {
  return `${count} ${count === 1 ? 'alert' : 'alerts'}`
}

export function calculateDashboardTotalPages(total: number | undefined, pageSize: number) {
  return Math.max(1, Math.ceil((total ?? 0) / Math.max(1, pageSize)))
}

export function resolvePanelRefreshing(
  type: DashboardWindowType,
  states: { rss: boolean; alerts: boolean; dailyBrief: boolean },
) {
  if (type === 'rss') return states.rss
  if (type === 'alerts') return states.alerts
  if (type === 'daily_brief') return states.dailyBrief
  return false
}

export const WINDOW_TYPE_META: Record<
  DashboardWindowType,
  {
    label: string
    description: string
    badgeClassName: string
    headerClassName: string
    shellClassName: string
    panelClassName: string
  }
> = {
  rss: {
    label: 'RSS Triage',
    description: 'Track feeds, pivot by tags, and expand into article detail.',
    badgeClassName: 'tl-chip-info',
    headerClassName: 'bg-white/92 dark:bg-[#041612]/90',
    shellClassName: 'border-slate/20 bg-white/95 dark:border-cyan-900/45 dark:bg-[#041612]/96',
    panelClassName: 'bg-white/90 dark:bg-[#03130f]/84',
  },
  alerts: {
    label: 'Alert Matches',
    description: 'Watch keyword-driven matches across your configured interests.',
    badgeClassName: 'tl-chip-neutral',
    headerClassName: 'bg-white/92 dark:bg-[#041612]/90',
    shellClassName: 'border-slate/20 bg-white/95 dark:border-cyan-900/45 dark:bg-[#041612]/96',
    panelClassName: 'bg-white/90 dark:bg-[#03130f]/84',
  },
  notes: {
    label: 'Notes',
    description: 'Keep scratch notes, pivots, and hypotheses attached to this view.',
    badgeClassName: 'tl-chip-neutral',
    headerClassName: 'bg-white/92 dark:bg-[#041612]/90',
    shellClassName: 'border-slate/20 bg-white/95 dark:border-cyan-900/45 dark:bg-[#041612]/96',
    panelClassName: 'bg-white/90 dark:bg-[#03130f]/84',
  },
  daily_brief: {
    label: 'Daily Brief',
    description: 'Review retained AI briefings and the items that shaped them.',
    badgeClassName: 'tl-chip-neutral',
    headerClassName: 'bg-white/92 dark:bg-[#041612]/90',
    shellClassName: 'border-slate/20 bg-white/95 dark:border-cyan-900/45 dark:bg-[#041612]/96',
    panelClassName: 'bg-white/90 dark:bg-[#03130f]/84',
  },
}
