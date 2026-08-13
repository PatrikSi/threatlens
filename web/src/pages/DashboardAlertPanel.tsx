import { resolveApiErrorMessage } from '../api/errors'
import { formatPlainTextPreview, sanitizeHref } from './dashboardContent'
import {
  formatClassificationLabel,
  formatPublishedAt,
  resolveWindowTimeFilter,
} from './dashboardPageUtils'
import {
  calculateDashboardTotalPages,
  DASHBOARD_TIME_INHERIT_VALUE,
  formatAlertMatchCount,
  MOBILE_DASHBOARD_PAGE_SIZE,
  resolvePanelPageSize,
  ROLLING_WINDOW_FIELD_CLASS,
  WINDOW_TYPE_META,
} from './dashboardPanelPresentation'
import {
  createDefaultAlertWindowFilters,
  PAGE_SIZE_OPTIONS,
  type DashboardAlertWindowFilters,
  type TimeSort,
} from './dashboardSavedViews'
import type { DashboardPageController } from './useDashboardPageController'

type DashboardWindow = DashboardPageController['renderedWindows'][number]

interface DashboardAlertPanelProps {
  controller: DashboardPageController
  windowLayout: DashboardWindow
  windowControlsVisible: boolean
  activeLocalFilterCount: number
}

export function DashboardAlertPanel({
  controller,
  windowLayout,
  windowControlsVisible,
  activeLocalFilterCount,
}: DashboardAlertPanelProps) {
  const {
    alertInterestsQuery, alertQueriesByWindowId, availableAlertCategories, dashboardTimeFilter,
    toggleMobileWindowControls, updateWindowAlertFilters, updateWindowCustomTimeDate,
    updateWindowRollingDays, updateWindowTimeRange,
  } = controller
  const windowMeta = WINDOW_TYPE_META[windowLayout.type]
  const effectiveWindowTimeFilter = resolveWindowTimeFilter(windowLayout, dashboardTimeFilter)
  const alertFilters = windowLayout.alert_filters ?? createDefaultAlertWindowFilters()
  const alertQuery = alertQueriesByWindowId[windowLayout.id]
  const alertWindowItems = alertQuery?.data?.items ?? []
  const alertTotalPages = calculateDashboardTotalPages(
    alertQuery?.data?.total,
    resolvePanelPageSize(alertQuery?.data?.page_size, alertFilters.page_size),
  )

  return (
    <>
                  {windowControlsVisible && (
                    <div className={`tl-mobile-filter-sheet border-b border-slate/20 px-3 py-2 dark:border-cyan-900/40 ${windowMeta.panelClassName}`}>
                    <div className="tl-mobile-filter-sheet-header -mx-3 -mt-2 mb-2 flex items-center justify-between border-b border-slate/20 bg-white px-3 py-2 sm:hidden dark:border-cyan-900/40 dark:bg-[#041612]">
                      <div>
                        <p className="text-sm font-semibold">Alert filters</p>
                        <p className="text-xs text-slate dark:text-slate-300">{activeLocalFilterCount} active</p>
                      </div>
                      <button
                        type="button"
                        className="rounded border border-slate/20 px-3 py-1.5 text-xs font-semibold dark:border-cyan-900/40"
                        onClick={() => toggleMobileWindowControls(windowLayout.id)}
                      >
                        Done
                      </button>
                    </div>
                    <div
                      className="flex max-h-24 min-w-0 flex-wrap items-center gap-2 overflow-y-auto pb-1"
                      role="group"
                      aria-label={`${windowLayout.title} alert category filters`}
                    >
                      <button
                        type="button"
                        aria-pressed={alertFilters.selected_categories.length === 0}
                        className={`rounded border px-3 py-1 text-xs font-semibold ${
                          alertFilters.selected_categories.length === 0
                            ? 'tl-chip-filter-active'
                            : 'tl-chip-neutral'
                        }`}
                        onClick={() => updateWindowAlertFilters(windowLayout.id, (current) => ({ ...current, selected_categories: [] }))}
                      >
                        All Categories
                      </button>
                      {availableAlertCategories.map((category) => {
                        const active = alertFilters.selected_categories.includes(category)
                        return (
                          <button
                            key={category}
                            type="button"
                            aria-pressed={active}
                            className={`whitespace-nowrap rounded border px-3 py-1 text-xs font-semibold ${
                              active ? 'tl-chip-filter-active' : 'tl-chip-neutral'
                            }`}
                            onClick={() =>
                              updateWindowAlertFilters(windowLayout.id, (current) => ({
                                ...current,
                                selected_categories: current.selected_categories.includes(category)
                                  ? current.selected_categories.filter((entry: string) => entry !== category)
                                  : [...current.selected_categories, category],
                              }))
                            }
                          >
                            {formatClassificationLabel(category)}
                          </button>
                        )
                      })}
                    </div>

                    <div
                      className="mt-2 flex max-h-24 min-w-0 flex-wrap items-center gap-2 overflow-y-auto pb-1"
                      role="group"
                      aria-label={`${windowLayout.title} alert interest filters`}
                    >
                      <button
                        type="button"
                        aria-pressed={alertFilters.selected_alert_ids.length === 0}
                        className={`rounded border px-3 py-1 text-xs font-semibold ${
                          alertFilters.selected_alert_ids.length === 0
                            ? 'tl-chip-filter-active'
                            : 'tl-chip-neutral'
                        }`}
                        onClick={() => updateWindowAlertFilters(windowLayout.id, (current) => ({ ...current, selected_alert_ids: [] }))}
                      >
                        All Interests
                      </button>
                      {alertInterestsQuery.data?.map((interest) => {
                        const active = alertFilters.selected_alert_ids.includes(interest.id)
                        return (
                          <button
                            key={interest.id}
                            type="button"
                            aria-pressed={active}
                            className={`whitespace-nowrap rounded border px-3 py-1 text-xs font-semibold ${
                              active
                                ? 'tl-chip-filter-active'
                                : 'tl-chip-neutral'
                            }`}
                            onClick={() =>
                              updateWindowAlertFilters(windowLayout.id, (current) => ({
                                ...current,
                                selected_alert_ids: current.selected_alert_ids.includes(interest.id)
                                  ? current.selected_alert_ids.filter((entry: string) => entry !== interest.id)
                                  : [...current.selected_alert_ids, interest.id],
                              }))
                            }
                          >
                            {interest.name}
                          </button>
                        )
                      })}
                    </div>

                    <div className="tl-dashboard-filter-controls mt-2 grid grid-cols-2 items-center gap-1.5 sm:flex sm:flex-wrap sm:gap-2">
                      <input
                        value={alertFilters.q}
                        onChange={(event) => updateWindowAlertFilters(windowLayout.id, (current) => ({ ...current, q: event.target.value }))}
                        aria-label={`${windowLayout.title} search query`}
                        placeholder="Search matched alert items"
                        className="col-span-2 w-full rounded border border-slate/20 bg-white px-2 py-1.5 text-sm sm:min-w-64 sm:flex-1 dark:border-cyan-900/40 dark:bg-[#072019]"
                      />
                      <select
                        className="w-full rounded border border-slate/20 bg-white px-2 py-1.5 text-sm sm:w-auto dark:border-cyan-900/40 dark:bg-[#072019]"
                        value={windowLayout.time_override?.time_range ?? DASHBOARD_TIME_INHERIT_VALUE}
                        onChange={(event) => updateWindowTimeRange(windowLayout.id, event.target.value)}
                        aria-label={`${windowLayout.title} time range`}
                      >
                        <option value={DASHBOARD_TIME_INHERIT_VALUE}>Dashboard Time</option>
                        <option value="all">All time</option>
                        <option value="24h">24h</option>
                        <option value="7d">7d</option>
                        <option value="30d">30d</option>
                        <option value="days">Last X days</option>
                        <option value="custom">Custom</option>
                      </select>
                      {effectiveWindowTimeFilter.time_range === 'days' && (
                        <label className={`${ROLLING_WINDOW_FIELD_CLASS} sm:w-[150px] dark:bg-[#072019]`}>
                          <span className="mr-2 text-xs text-slate dark:text-white/60">Last</span>
                          <input
                            type="number"
                            min={1}
                            max={365}
                            value={effectiveWindowTimeFilter.rolling_days}
                            onChange={(event) => updateWindowRollingDays(windowLayout.id, event.target.value)}
                            aria-label={`${windowLayout.title} rolling time window in days`}
                            className="w-full bg-transparent focus-visible:outline-none"
                          />
                          <span className="ml-2 text-xs text-slate dark:text-white/60">days</span>
                        </label>
                      )}
                      <select
                        className="w-full rounded border border-slate/20 bg-white px-2 py-1.5 text-sm sm:w-auto dark:border-cyan-900/40 dark:bg-[#072019]"
                        value={alertFilters.sort}
                        onChange={(event) =>
                          updateWindowAlertFilters(windowLayout.id, (current) => ({ ...current, sort: event.target.value as TimeSort }))
                        }
                        aria-label={`${windowLayout.title} sort order`}
                      >
                        <option value="published_at_desc">Newest</option>
                        <option value="published_at_asc">Oldest</option>
                        <option value="first_seen_desc">Seen newest</option>
                        <option value="first_seen_asc">Seen oldest</option>
                      </select>
                      <div
                        className="flex w-full rounded border border-slate/20 p-0.5 sm:w-auto dark:border-cyan-900/40"
                        role="group"
                        aria-label={`${windowLayout.title} alert view mode`}
                      >
                        <button
                          type="button"
                          aria-pressed={alertFilters.view_mode === 'expanded'}
                          className={`flex-1 rounded px-2 py-1 text-xs font-semibold sm:flex-none ${alertFilters.view_mode === 'expanded' ? 'bg-cyan/15 text-cyan' : ''}`}
                          onClick={() => updateWindowAlertFilters(windowLayout.id, (current) => ({ ...current, view_mode: 'expanded' }), false)}
                        >
                          Expanded
                        </button>
                        <button
                          type="button"
                          aria-pressed={alertFilters.view_mode === 'compact'}
                          className={`flex-1 rounded px-2 py-1 text-xs font-semibold sm:flex-none ${alertFilters.view_mode === 'compact' ? 'bg-cyan/15 text-cyan' : ''}`}
                          onClick={() => updateWindowAlertFilters(windowLayout.id, (current) => ({ ...current, view_mode: 'compact' }), false)}
                        >
                          Compact
                        </button>
                      </div>
                      <input
                        type="date"
                        className="tl-custom-date-control w-full rounded border border-slate/20 bg-white px-2 py-1.5 text-sm disabled:opacity-50 sm:w-auto dark:border-cyan-900/40 dark:bg-[#072019]"
                        value={effectiveWindowTimeFilter.custom_since_date}
                        onChange={(event) => updateWindowCustomTimeDate(windowLayout.id, 'custom_since_date', event.target.value)}
                        disabled={effectiveWindowTimeFilter.time_range !== 'custom'}
                        aria-label={`${windowLayout.title} custom start date`}
                      />
                      <input
                        type="date"
                        className="tl-custom-date-control w-full rounded border border-slate/20 bg-white px-2 py-1.5 text-sm disabled:opacity-50 sm:w-auto dark:border-cyan-900/40 dark:bg-[#072019]"
                        value={effectiveWindowTimeFilter.custom_until_date}
                        onChange={(event) => updateWindowCustomTimeDate(windowLayout.id, 'custom_until_date', event.target.value)}
                        disabled={effectiveWindowTimeFilter.time_range !== 'custom'}
                        aria-label={`${windowLayout.title} custom end date`}
                      />
                    </div>
                    </div>
                  )}

                  <div className="tl-dashboard-panel-body flex-1 overflow-auto p-2 sm:p-3">
                    <div className="space-y-1.5 sm:space-y-2">
                      {alertWindowItems.map((item) => {
                        const compactAlerts = alertFilters.view_mode === 'compact'
                        const itemHref = sanitizeHref(item.canonical_url || item.url)
                        return (
                        <article key={item.id} className={`tl-dashboard-alert-card rounded border border-slate/20 ${compactAlerts ? 'p-2' : 'p-2.5 sm:p-3'} dark:border-cyan-900/40`}>
                          <div className="flex items-start justify-between gap-2">
                            <div className="min-w-0 flex-1">
                              <h3 className={`font-semibold leading-snug ${compactAlerts ? 'text-[13px]' : ''}`}>
                                {itemHref ? (
                                  <a
                                    href={itemHref}
                                    target="_blank"
                                    rel="noreferrer"
                                    className="hover:text-cyan hover:underline"
                                  >
                                    {item.title}
                                  </a>
                                ) : (
                                  <span>{item.title}</span>
                                )}
                              </h3>
                              <p className={`text-xs text-slate dark:text-slate-300 ${compactAlerts ? 'mt-0.5' : 'mt-1'}`}>
                                {item.feed_name} • Published {formatPublishedAt(item.published_at)}
                              </p>
                            </div>
                            <span className="shrink-0 rounded border border-slate/20 px-2 py-0.5 text-[11px] dark:border-cyan-900/40">
                              {formatAlertMatchCount(item.matches.length)}
                            </span>
                          </div>
                          <div className="mt-2 flex flex-wrap gap-1.5">
                            {item.matches.map((match) => (
                              <span
                                key={`${item.id}-${match.alert_id}`}
                                className="tl-chip tl-chip-neutral"
                              >
                                {match.alert_name} ({formatClassificationLabel(match.category)})
                              </span>
                            ))}
                          </div>
                          {!compactAlerts && (
                            <p className="mt-2 line-clamp-2 text-[13px] leading-5 text-slate dark:text-slate-300">
                              {formatPlainTextPreview(item.summary, 'No summary available.')}
                            </p>
                          )}
                        </article>
                      )})}

                      {alertQuery?.isLoading && <p className="text-sm text-slate dark:text-slate-300">Loading alert matches...</p>}
                      {alertQuery?.isFetching && !alertQuery.isLoading && (
                        <p className="text-xs text-slate dark:text-white/60">Refreshing matches...</p>
                      )}
                      {alertQuery?.isError && (
                        <p className="text-sm text-red-600">
                          {resolveApiErrorMessage(alertQuery.error, 'Alert matches could not be loaded')}
                        </p>
                      )}
                      {!alertQuery?.isLoading && !alertWindowItems.length && (
                        <p className="text-sm text-slate dark:text-slate-300">No items matched current alert filters.</p>
                      )}
                    </div>
                  </div>

                  <div className="grid grid-cols-[auto_1fr_auto] items-center gap-2 border-t border-slate/20 px-2 py-2 text-xs sm:flex sm:flex-wrap sm:justify-between sm:px-3 dark:border-cyan-900/40">
                    <button
                      className="min-w-11 rounded border border-slate/20 px-2 py-1 disabled:opacity-50 sm:min-w-0 dark:border-cyan-900/40"
                      disabled={alertFilters.page <= 1}
                      onClick={() =>
                        updateWindowAlertFilters(windowLayout.id, (current) => ({ ...current, page: current.page - 1 }), false)
                      }
                    >
                      Prev
                    </button>
                    <span className="text-center sm:w-auto">
                      <span className="sm:hidden">Page {alertFilters.page} of {alertTotalPages} · {MOBILE_DASHBOARD_PAGE_SIZE} per page</span>
                      <span className="hidden sm:inline">Page {alertFilters.page} / {alertTotalPages}</span>
                    </span>
                    <button
                      className="min-w-11 rounded border border-slate/20 px-2 py-1 disabled:opacity-50 sm:order-4 sm:min-w-0 dark:border-cyan-900/40"
                      disabled={alertFilters.page >= alertTotalPages}
                      onClick={() =>
                        updateWindowAlertFilters(windowLayout.id, (current) => ({ ...current, page: current.page + 1 }), false)
                      }
                    >
                      Next
                    </button>
                    <div className="hidden items-center justify-center gap-1 sm:order-3 sm:ml-auto sm:flex sm:gap-2">
                      <label className="hidden text-xs text-slate sm:inline dark:text-slate-300">Per page</label>
                      <select
                        className="hidden rounded border border-slate/20 bg-white px-2 py-1 text-xs sm:block dark:border-cyan-900/40 dark:bg-[#072019]"
                        value={alertFilters.page_size}
                        onChange={(event) =>
                          updateWindowAlertFilters(windowLayout.id, (current) => ({
                            ...current,
                            page_size: Number(event.target.value) as DashboardAlertWindowFilters['page_size'],
                          }))
                        }
                        aria-label={`${windowLayout.title} results per page`}
                      >
                        {PAGE_SIZE_OPTIONS.map((option) => (
                          <option key={option} value={option}>
                            {option}
                          </option>
                        ))}
                      </select>
                    </div>
                  </div>
    </>
  )
}
