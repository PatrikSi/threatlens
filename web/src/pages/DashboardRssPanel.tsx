import { resolveApiErrorMessage } from '../api/errors'
import { feedHealthBadgeClass, resolveFeedHealth } from '../utils/feedHealth'
import {
  resolveWindowTimeFilter,
} from './dashboardPageUtils'
import {
  calculateDashboardTotalPages,
  DASHBOARD_TIME_INHERIT_VALUE,
  FILTER_CHIP_CLASS,
  FILTER_SCROLLER_CLASS,
  MOBILE_DASHBOARD_PAGE_SIZE,
  resolvePanelPageSize,
  ROLLING_WINDOW_FIELD_CLASS,
  WINDOW_TYPE_META,
} from './dashboardPanelPresentation'
import {
  createDefaultRssWindowFilters,
  HIDDEN_TAGS,
  type AIRelevanceFilter,
  PAGE_SIZE_OPTIONS,
  type DashboardRssWindowFilters,
  type ReadStatusFilter,
  type StarStatusFilter,
  type TimeSort,
} from './dashboardSavedViews'
import { DashboardRssItem } from './DashboardRssItem'
import type { DashboardPageController } from './useDashboardPageController'

type DashboardWindow = DashboardPageController['renderedWindows'][number]

interface DashboardRssPanelProps {
  controller: DashboardPageController
  windowLayout: DashboardWindow
  windowControlsVisible: boolean
  activeLocalFilterCount: number
}
export function DashboardRssPanel({
  controller,
  windowLayout,
  windowControlsVisible,
  activeLocalFilterCount,
}: DashboardRssPanelProps) {
  const {
    aiRelevanceEnabled, dashboardTimeFilter, feedsQuery, rssQueriesByWindowId, tagsQuery,
    toggleMobileWindowControls, updateWindowCustomTimeDate, updateWindowRollingDays,
    updateWindowRssFilters,
    updateWindowTimeRange,
  } = controller
  const windowMeta = WINDOW_TYPE_META[windowLayout.type]
  const effectiveWindowTimeFilter = resolveWindowTimeFilter(windowLayout, dashboardTimeFilter)
  const rssFilters = windowLayout.rss_filters ?? createDefaultRssWindowFilters()
  const rssQuery = rssQueriesByWindowId[windowLayout.id]
  const rssWindowItems = rssQuery?.data?.items ?? []
  const rssTotalPages = calculateDashboardTotalPages(
    rssQuery?.data?.total,
    resolvePanelPageSize(rssQuery?.data?.page_size, rssFilters.page_size),
  )

  return (
    <>
                  {windowControlsVisible && (
                    <div className={`tl-mobile-filter-sheet border-b border-slate/20 px-3 py-2 dark:border-cyan-900/40 ${windowMeta.panelClassName}`}>
                      <div className="tl-mobile-filter-sheet-header -mx-3 -mt-2 mb-2 flex items-center justify-between border-b border-slate/20 bg-white px-3 py-2 sm:hidden dark:border-cyan-900/40 dark:bg-[#041612]">
                        <div>
                          <p className="text-sm font-semibold">RSS filters</p>
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
                      <div className={FILTER_SCROLLER_CLASS} role="group" aria-label={`${windowLayout.title} feed filters`}>
                        <button
                          type="button"
                          aria-pressed={rssFilters.selected_feed_ids.length === 0}
                          aria-label={`${windowLayout.title} all feeds`}
                          className={`${FILTER_CHIP_CLASS} ${
                            rssFilters.selected_feed_ids.length === 0 ? 'tl-chip-filter-active' : 'tl-chip-neutral'
                          }`}
                          onClick={() => updateWindowRssFilters(windowLayout.id, (current) => ({ ...current, selected_feed_ids: [] }))}
                        >
                          All
                        </button>
                        {feedsQuery.data?.map((feed) => {
                          const active = rssFilters.selected_feed_ids.includes(feed.id)
                          const health = resolveFeedHealth(feed)
                          return (
                            <button
                              key={feed.id}
                              type="button"
                              aria-pressed={active}
                              className={`${FILTER_CHIP_CLASS} ${active ? 'tl-chip-filter-active' : 'tl-chip-neutral'}`}
                              aria-label={`${windowLayout.title} ${feed.name} feed, ${health.label}`}
                              onClick={() =>
                                updateWindowRssFilters(windowLayout.id, (current) => ({
                                  ...current,
                                  selected_feed_ids: current.selected_feed_ids.includes(feed.id)
                                    ? current.selected_feed_ids.filter((id: string) => id !== feed.id)
                                    : [...current.selected_feed_ids, feed.id],
                                }))
                              }
                            >
                              {feed.name}
                              {health.status !== 'healthy' && (
                                <span className={`tl-chip ml-1.5 ${feedHealthBadgeClass(health.status)}`}>{health.label}</span>
                              )}
                            </button>
                          )
                        })}
                      </div>

                      <div className={`mt-1 ${FILTER_SCROLLER_CLASS}`} role="group" aria-label={`${windowLayout.title} tag filters`}>
                        <button
                          type="button"
                          aria-pressed={rssFilters.selected_tags.length === 0}
                          aria-label={`${windowLayout.title} all tags`}
                          className={`${FILTER_CHIP_CLASS} ${
                            rssFilters.selected_tags.length === 0 ? 'tl-chip-filter-active' : 'tl-chip-neutral'
                          }`}
                          onClick={() => updateWindowRssFilters(windowLayout.id, (current) => ({ ...current, selected_tags: [] }))}
                        >
                          All
                        </button>
                        {tagsQuery.data
                          ?.filter((tag) => !HIDDEN_TAGS.has(tag.name))
                          .map((tag) => {
                            const active = rssFilters.selected_tags.includes(tag.name)
                            return (
                              <button
                                key={tag.id}
                                type="button"
                                aria-pressed={active}
                                className={`${FILTER_CHIP_CLASS} ${active ? 'tl-chip-filter-active' : 'tl-chip-neutral'}`}
                                onClick={() =>
                                  updateWindowRssFilters(windowLayout.id, (current) => ({
                                    ...current,
                                    selected_tags: current.selected_tags.includes(tag.name)
                                      ? current.selected_tags.filter((entry: string) => entry !== tag.name)
                                      : [...current.selected_tags, tag.name],
                                  }))
                                }
                              >
                                #{tag.name}
                              </button>
                            )
                          })}
                      </div>
                      {tagsQuery.isError && (
                        <p className="mt-0.5 text-xs text-red-600">
                          {resolveApiErrorMessage(tagsQuery.error, 'Tags could not be loaded')}
                        </p>
                      )}

                      <div className="tl-dashboard-filter-controls mt-1 grid grid-cols-2 items-center gap-1.5 sm:flex sm:flex-wrap">
                      <input
                        value={rssFilters.q}
                        onChange={(event) => updateWindowRssFilters(windowLayout.id, (current) => ({ ...current, q: event.target.value }))}
                        aria-label={`${windowLayout.title} search query`}
                        placeholder="Search title, summary, URL"
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
                        value={rssFilters.sort}
                        onChange={(event) =>
                          updateWindowRssFilters(windowLayout.id, (current) => ({ ...current, sort: event.target.value as TimeSort }))
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
                        aria-label={`${windowLayout.title} view mode`}
                      >
                        <button
                          type="button"
                          aria-pressed={rssFilters.view_mode === 'expanded'}
                          className={`flex-1 rounded px-2 py-1 text-xs font-semibold sm:flex-none ${rssFilters.view_mode === 'expanded' ? 'bg-cyan/15 text-cyan' : ''}`}
                          onClick={() => updateWindowRssFilters(windowLayout.id, (current) => ({ ...current, view_mode: 'expanded' }), false)}
                        >
                          Expanded
                        </button>
                        <button
                          type="button"
                          aria-pressed={rssFilters.view_mode === 'compact'}
                          className={`flex-1 rounded px-2 py-1 text-xs font-semibold sm:flex-none ${rssFilters.view_mode === 'compact' ? 'bg-cyan/15 text-cyan' : ''}`}
                          onClick={() => updateWindowRssFilters(windowLayout.id, (current) => ({ ...current, view_mode: 'compact' }), false)}
                        >
                          Compact
                        </button>
                      </div>
                      <button
                        type="button"
                        className="w-full rounded border border-slate/20 px-3 py-1.5 text-xs font-semibold sm:w-auto dark:border-cyan-900/40"
                        onClick={() =>
                          updateWindowRssFilters(
                            windowLayout.id,
                            (current) => ({ ...current, show_advanced_filters: !current.show_advanced_filters }),
                            false,
                          )
                        }
                      >
                        {rssFilters.show_advanced_filters ? 'Hide Filters' : 'More Filters'}
                      </button>
                      </div>

                      {rssFilters.show_advanced_filters && (
                        <div className="mt-1 grid gap-2 rounded border border-slate/20 bg-white/90 p-2 dark:border-cyan-900/40 dark:bg-[#072019]/70 md:grid-cols-2 lg:grid-cols-3">
                        <select
                          className="rounded border border-slate/20 bg-white px-2 py-1.5 text-sm dark:border-cyan-900/40 dark:bg-[#041612]"
                          value={rssFilters.read_status}
                          onChange={(event) =>
                            updateWindowRssFilters(windowLayout.id, (current) => ({
                              ...current,
                              read_status: event.target.value as ReadStatusFilter,
                            }))
                          }
                          aria-label={`${windowLayout.title} read status filter`}
                        >
                          <option value="all">Read: All</option>
                          <option value="unread">Read: Unread</option>
                          <option value="read">Read: Read</option>
                        </select>
                        <select
                          className="rounded border border-slate/20 bg-white px-2 py-1.5 text-sm dark:border-cyan-900/40 dark:bg-[#041612]"
                          value={rssFilters.star_status}
                          onChange={(event) =>
                            updateWindowRssFilters(windowLayout.id, (current) => ({
                              ...current,
                              star_status: event.target.value as StarStatusFilter,
                            }))
                          }
                          aria-label={`${windowLayout.title} star filter`}
                        >
                          <option value="all">Stars: All</option>
                          <option value="starred">Stars: Starred</option>
                          <option value="unstarred">Stars: Unstarred</option>
                        </select>
                        {aiRelevanceEnabled && (
                          <select
                            className="rounded border border-slate/20 bg-white px-2 py-1.5 text-sm dark:border-cyan-900/40 dark:bg-[#041612]"
                            value={rssFilters.ai_relevance}
                            onChange={(event) =>
                              updateWindowRssFilters(windowLayout.id, (current) => ({
                                ...current,
                                ai_relevance: event.target.value as AIRelevanceFilter,
                              }))
                            }
                            aria-label={`${windowLayout.title} AI relevance filter`}
                          >
                            <option value="all">AI Relevance: All</option>
                            <option value="high">AI Relevance: High</option>
                            <option value="medium">AI Relevance: Medium</option>
                            <option value="low">AI Relevance: Low</option>
                          </select>
                        )}
                        <div className="flex flex-col gap-2 sm:flex-row">
                          <input
                            type="date"
                            className="tl-custom-date-control w-full rounded border border-slate/20 bg-white px-2 py-1.5 text-sm disabled:opacity-50 dark:border-cyan-900/40 dark:bg-[#041612]"
                            value={effectiveWindowTimeFilter.custom_since_date}
                            onChange={(event) => updateWindowCustomTimeDate(windowLayout.id, 'custom_since_date', event.target.value)}
                            disabled={effectiveWindowTimeFilter.time_range !== 'custom'}
                            aria-label={`${windowLayout.title} custom start date`}
                          />
                          <input
                            type="date"
                            className="tl-custom-date-control w-full rounded border border-slate/20 bg-white px-2 py-1.5 text-sm disabled:opacity-50 dark:border-cyan-900/40 dark:bg-[#041612]"
                            value={effectiveWindowTimeFilter.custom_until_date}
                            onChange={(event) => updateWindowCustomTimeDate(windowLayout.id, 'custom_until_date', event.target.value)}
                            disabled={effectiveWindowTimeFilter.time_range !== 'custom'}
                            aria-label={`${windowLayout.title} custom end date`}
                          />
                        </div>
                        </div>
                      )}
                    </div>
                  )}

                  <div className="tl-dashboard-panel-body flex-1 overflow-auto p-2 sm:p-3">
                    <div className="tl-dashboard-rss-list space-y-1.5 sm:space-y-2">
                      {rssWindowItems.map((item) => (
                        <DashboardRssItem
                          key={item.id}
                          controller={controller}
                          windowLayout={windowLayout}
                          rssFilters={rssFilters}
                          item={item}
                        />
                      ))}

                      {rssQuery?.isLoading && <p className="text-sm text-slate dark:text-slate-300">Loading items...</p>}
                      {rssQuery?.isFetching && !rssQuery.isLoading && (
                        <p className="text-xs text-slate dark:text-white/60">Refreshing items...</p>
                      )}
                      {rssQuery?.isError && (
                        <p className="text-sm text-red-600">
                          {resolveApiErrorMessage(rssQuery.error, 'RSS items could not be loaded')}
                        </p>
                      )}
                      {!rssQuery?.isLoading && !rssWindowItems.length && (
                        <p className="text-sm text-slate dark:text-slate-300">No items match current filters.</p>
                      )}
                    </div>
                  </div>

                  <div className="grid grid-cols-[auto_1fr_auto] items-center gap-2 border-t border-slate/20 px-2 py-2 text-xs sm:flex sm:flex-wrap sm:justify-between sm:px-3 dark:border-cyan-900/40">
                    <button
                      className="min-w-11 rounded border border-slate/20 px-2 py-1 disabled:opacity-50 sm:min-w-0 dark:border-cyan-900/40"
                      disabled={rssFilters.page <= 1}
                      onClick={() =>
                        updateWindowRssFilters(windowLayout.id, (current) => ({ ...current, page: current.page - 1 }), false)
                      }
                    >
                      Prev
                    </button>
                    <span className="text-center sm:w-auto">
                      <span className="sm:hidden">Page {rssFilters.page} of {rssTotalPages} · {MOBILE_DASHBOARD_PAGE_SIZE} per page</span>
                      <span className="hidden sm:inline">Page {rssFilters.page} / {rssTotalPages}</span>
                    </span>
                    <button
                      className="min-w-11 rounded border border-slate/20 px-2 py-1 disabled:opacity-50 sm:order-4 sm:min-w-0 dark:border-cyan-900/40"
                      disabled={rssFilters.page >= rssTotalPages}
                      onClick={() =>
                        updateWindowRssFilters(windowLayout.id, (current) => ({ ...current, page: current.page + 1 }), false)
                      }
                    >
                      Next
                    </button>
                    <div className="hidden items-center justify-center gap-1 sm:order-3 sm:ml-auto sm:flex sm:gap-2">
                      <label className="hidden text-xs text-slate sm:inline dark:text-slate-300">Per page</label>
                      <select
                        className="hidden rounded border border-slate/20 bg-white px-2 py-1 text-xs sm:block dark:border-cyan-900/40 dark:bg-[#072019]"
                        value={rssFilters.page_size}
                        onChange={(event) =>
                          updateWindowRssFilters(windowLayout.id, (current) => ({
                            ...current,
                            page_size: Number(event.target.value) as DashboardRssWindowFilters['page_size'],
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
