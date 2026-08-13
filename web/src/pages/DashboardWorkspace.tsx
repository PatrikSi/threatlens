import { ArticlePreviewDrawer } from './DashboardPageComponents'
import {
  ARTICLE_PREVIEW_MIN_WIDTH,
  countActiveWindowFilters,
  countNewEntriesSince,
  formatWindowSnapLabel,
  formatWindowTimeSummary,
  getArticlePreviewMaxWidth,
} from './dashboardPageUtils'
import {
  resolvePanelRefreshing,
  WINDOW_SNAP_OPTIONS,
  WINDOW_TYPE_META,
} from './dashboardPanelPresentation'
import {
  createDefaultAlertWindowFilters,
  createDefaultRssWindowFilters,
  resolveWindowRect,
  type DashboardWindowSnap,
} from './dashboardSavedViews'
import { DashboardAlertPanel } from './DashboardAlertPanel'
import { DashboardDailyBriefPanel } from './DashboardDailyBriefPanel'
import { DashboardNotesPanel } from './DashboardNotesPanel'
import { DashboardRssPanel } from './DashboardRssPanel'
import type { DashboardPageController } from './useDashboardPageController'

export function DashboardWorkspace({ controller }: { controller: DashboardPageController }) {
  const {
    adjustArticlePreviewWidth, articlePreview, articlePreviewFrameState, articlePreviewWidth,
    closeArticlePreview, isArticlePreviewResizing, isWideLayout, mobileActiveWindowIndex,
    renderedWindows, resolvedMobileWindowId, rootRef, setArticlePreviewFrameState,
    setMobileActiveWindowId, startArticlePreviewResize, viewSavePending, windows,
  } = controller

  return (
    <>
      {!isWideLayout && windows.length > 1 && resolvedMobileWindowId && (
        <div className="tl-mobile-panel-switcher border-b border-slate/20 bg-slate-50 px-3 py-2 dark:border-cyan-900/40 dark:bg-[#03130f]">
          <div className="flex items-end justify-between gap-3">
            <label htmlFor="mobile-dashboard-panel" className="min-w-0 flex-1 text-xs font-semibold text-slate dark:text-slate-300">
              <span className="mb-1 block uppercase">Panel {mobileActiveWindowIndex + 1} of {windows.length}</span>
              <select
                id="mobile-dashboard-panel"
                className="w-full rounded border border-slate/20 bg-white px-3 py-2 text-sm font-semibold text-ink dark:border-cyan-900/40 dark:bg-[#041612] dark:text-slate-100"
                value={resolvedMobileWindowId}
                onChange={(event) => setMobileActiveWindowId(event.target.value)}
              >
                {windows.map((windowLayout) => (
                  <option key={windowLayout.id} value={windowLayout.id}>
                    {windowLayout.title} ({WINDOW_TYPE_META[windowLayout.type].label})
                  </option>
                ))}
              </select>
            </label>
          </div>
        </div>
      )}

      <div
        ref={rootRef}
        className={`relative ${isWideLayout ? 'h-[calc(100vh-126px)] min-h-[620px] w-full overflow-hidden bg-slate-100/70 dark:bg-[#02100c]' : 'space-y-0 sm:p-3'}`}
      >
        {viewSavePending && (
          <div className="absolute inset-0 z-30 flex items-start justify-center bg-white/55 px-4 py-6 backdrop-blur-[1px] dark:bg-slate-950/45">
            <div className="rounded-full border border-cyan/30 bg-white/95 px-4 py-2 text-sm font-semibold text-cyan shadow-sm dark:border-cyan-500/35 dark:bg-[#041612]/95 dark:text-cyan-100">
              Saving view changes...
            </div>
          </div>
        )}
        {renderedWindows.map((windowLayout) => (
          <DashboardWindowPanel key={windowLayout.id} controller={controller} windowLayout={windowLayout} />
        ))}
      </div>

      {articlePreview && (
        <ArticlePreviewDrawer
          preview={articlePreview}
          frameState={articlePreviewFrameState}
          width={articlePreviewWidth}
          minWidth={ARTICLE_PREVIEW_MIN_WIDTH}
          maxWidth={getArticlePreviewMaxWidth()}
          onResizeStart={startArticlePreviewResize}
          onResizeBy={adjustArticlePreviewWidth}
          isResizing={isArticlePreviewResizing}
          onFrameLoad={() => setArticlePreviewFrameState('loaded')}
          onClose={closeArticlePreview}
        />
      )}
    </>
  )
}

type DashboardWindow = DashboardPageController['renderedWindows'][number]

function DashboardWindowPanel({
  controller,
  windowLayout,
}: {
  controller: DashboardPageController
  windowLayout: DashboardWindow
}) {
  const {
    aiRelevanceEnabled, bringWindowToFront, containerDimensions, isEditMode, isWideLayout,
    mobileWindowControlsOpenById, startWindowResize,
  } = controller
  const resolvedRect = resolveWindowRect(windowLayout, containerDimensions.width, containerDimensions.height)
  const windowMeta = WINDOW_TYPE_META[windowLayout.type]
  const rssFilters = windowLayout.rss_filters ?? createDefaultRssWindowFilters()
  const alertFilters = windowLayout.alert_filters ?? createDefaultAlertWindowFilters()
  const activeLocalFilterCount = countActiveWindowFilters(
    windowLayout,
    rssFilters,
    alertFilters,
    aiRelevanceEnabled,
  )
  const windowControlsVisible = isWideLayout
    ? !windowLayout.controls_collapsed
    : mobileWindowControlsOpenById[windowLayout.id] === true
  const snapped = isWideLayout && windowLayout.snap !== 'free'
  const sectionClass = `tl-dashboard-panel ${isWideLayout ? 'absolute' : 'relative'} flex flex-col overflow-hidden border text-[13px] ${windowMeta.shellClassName} ${
    snapped ? 'rounded-none shadow-none' : 'rounded-xl shadow-lg shadow-slate-400/15 dark:shadow-cyan-950/40'
  }`

  return (
<section
  key={windowLayout.id}
  aria-label={`${windowLayout.title} dashboard panel`}
  className={sectionClass}
  style={
    isWideLayout
      ? {
          left: resolvedRect.x,
          top: resolvedRect.y,
          width: resolvedRect.width,
          height: resolvedRect.height,
        }
      : undefined
  }
  onMouseDown={() => bringWindowToFront(windowLayout.id)}
>
  <DashboardPanelHeader
    controller={controller}
    windowLayout={windowLayout}
    windowControlsVisible={windowControlsVisible}
  />

  {windowLayout.type === 'rss' ? (
    <DashboardRssPanel
      controller={controller}
      windowLayout={windowLayout}
      windowControlsVisible={windowControlsVisible}
      activeLocalFilterCount={activeLocalFilterCount}
    />
  ) : windowLayout.type === 'alerts' ? (
    <DashboardAlertPanel
      controller={controller}
      windowLayout={windowLayout}
      windowControlsVisible={windowControlsVisible}
      activeLocalFilterCount={activeLocalFilterCount}
    />
  ) : windowLayout.type === 'daily_brief' ? (
    <DashboardDailyBriefPanel controller={controller} windowLayout={windowLayout} />
  ) : (
    <DashboardNotesPanel controller={controller} windowLayout={windowLayout} />
  )}

  {isEditMode && isWideLayout && windowLayout.snap === 'free' && (
    <button
      type="button"
      className="absolute bottom-1 right-1 h-4 w-4 cursor-se-resize rounded border border-slate/20 bg-white/85 dark:border-cyan-900/40 dark:bg-[#0b2a23]"
      aria-label="Resize panel"
      onMouseDown={(event) => startWindowResize(event, windowLayout.id)}
    />
  )}
</section>
  )
}

function DashboardPanelHeader({
  controller,
  windowLayout,
  windowControlsVisible,
}: {
  controller: DashboardPageController
  windowLayout: DashboardWindow
  windowControlsVisible: boolean
}) {
  const {
    aiRelevanceEnabled, alertQueriesByWindowId, dailyBriefHistoryQuery, dashboardTimeFilter,
    isEditMode, rssLastOpenedAt, rssQueriesByWindowId, startWindowDrag, windowSeenAt,
  } = controller
  const rssFilters = windowLayout.rss_filters ?? createDefaultRssWindowFilters()
  const alertFilters = windowLayout.alert_filters ?? createDefaultAlertWindowFilters()
  const rssQuery = rssQueriesByWindowId[windowLayout.id]
  const alertQuery = alertQueriesByWindowId[windowLayout.id]
  const rssWindowItems = windowLayout.type === 'rss' ? rssQuery?.data?.items ?? [] : []
  const alertWindowItems = windowLayout.type === 'alerts' ? alertQuery?.data?.items ?? [] : []
  const lastSeenAtIso = windowSeenAt[windowLayout.id] ?? ''
  const rssChangedCount = windowLayout.type === 'rss'
    ? countNewEntriesSince(rssWindowItems, rssLastOpenedAt)
    : 0
  const alertChangedCount = windowLayout.type === 'alerts'
    ? countNewEntriesSince(alertWindowItems, lastSeenAtIso)
    : 0
  const windowMeta = WINDOW_TYPE_META[windowLayout.type]
  const windowTimeSummary = formatWindowTimeSummary(windowLayout, dashboardTimeFilter)
  const activeLocalFilterCount = countActiveWindowFilters(
    windowLayout,
    rssFilters,
    alertFilters,
    aiRelevanceEnabled,
  )
  const isPanelRefreshing = resolvePanelRefreshing(windowLayout.type, {
    rss: Boolean(rssQuery?.isFetching && !rssQuery.isLoading),
    alerts: Boolean(alertQuery?.isFetching && !alertQuery.isLoading),
    dailyBrief: Boolean(dailyBriefHistoryQuery.isFetching && !dailyBriefHistoryQuery.isLoading),
  })

  return (
  <div
    className={`tl-dashboard-panel-header flex flex-col gap-2 border-b border-slate/20 px-3 py-2 dark:border-cyan-900/40 ${windowMeta.headerClassName} sm:flex-row sm:items-start sm:justify-between sm:gap-3 sm:py-2.5`}
    data-editing={isEditMode}
    onMouseDown={isEditMode ? (event) => startWindowDrag(event, windowLayout.id) : undefined}
    style={isEditMode ? { cursor: 'grab' } : undefined}
  >
    <div className="min-w-0 flex-1">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`tl-dashboard-panel-kind rounded border px-2 py-0.5 text-[10px] font-semibold ${windowMeta.badgeClassName}`}>
          {windowMeta.label}
        </span>
        <h2 className="tl-dashboard-panel-title text-sm font-semibold leading-tight text-ink sm:text-base dark:text-white">{windowLayout.title}</h2>
      </div>
      <DashboardPanelMetadata
        windowLayout={windowLayout}
        isEditMode={isEditMode}
        windowTimeSummary={windowTimeSummary}
        activeLocalFilterCount={activeLocalFilterCount}
        alertItemCount={alertWindowItems.length}
        rssChangedCount={rssChangedCount}
        alertChangedCount={alertChangedCount}
        isPanelRefreshing={isPanelRefreshing}
      />
    </div>
    <DashboardPanelActions
      controller={controller}
      windowLayout={windowLayout}
      windowControlsVisible={windowControlsVisible}
      activeLocalFilterCount={activeLocalFilterCount}
    />
  </div>
  )
}

function DashboardPanelMetadata({
  windowLayout,
  isEditMode,
  windowTimeSummary,
  activeLocalFilterCount,
  alertItemCount,
  rssChangedCount,
  alertChangedCount,
  isPanelRefreshing,
}: {
  windowLayout: DashboardWindow
  isEditMode: boolean
  windowTimeSummary: string
  activeLocalFilterCount: number
  alertItemCount: number
  rssChangedCount: number
  alertChangedCount: number
  isPanelRefreshing: boolean
}) {
  return (
      <div className="tl-dashboard-panel-meta mt-1.5 flex flex-wrap items-center gap-2 sm:mt-2">
        {isEditMode && (
          <span className="tl-panel-context rounded border border-slate/20 bg-white/70 px-2 py-0.5 text-[10px] font-semibold text-slate-700 dark:border-cyan-900/40 dark:bg-[#041612]/80 dark:text-white/65">
            {formatWindowSnapLabel(windowLayout.snap)}
          </span>
        )}
        <span className="tl-panel-context rounded border border-slate/20 bg-white/70 px-2 py-0.5 text-[10px] font-semibold text-slate-700 dark:border-cyan-900/40 dark:bg-[#041612]/80 dark:text-white/65">
          {windowTimeSummary}
        </span>
        {(windowLayout.type === 'rss' || windowLayout.type === 'alerts') && activeLocalFilterCount > 0 && (
          <span className="tl-panel-context rounded border border-slate/20 bg-white/70 px-2 py-0.5 text-[10px] font-semibold text-slate-700 dark:border-cyan-900/40 dark:bg-[#041612]/80 dark:text-white/65">
            {activeLocalFilterCount} local filters
          </span>
        )}
        {windowLayout.type === 'alerts' && (
          <span className="tl-panel-context rounded border border-slate/20 px-2 py-0.5 text-[10px] font-semibold text-slate dark:border-cyan-900/40 dark:text-slate-300">
            {alertItemCount} shown
          </span>
        )}
        {windowLayout.type === 'notes' && (
          <span className="tl-panel-context rounded border border-slate/20 px-2 py-0.5 text-[10px] font-semibold text-slate dark:border-cyan-900/40 dark:text-slate-300">
            Scratch pad
          </span>
        )}
        {windowLayout.type === 'rss' && rssChangedCount > 0 && (
          <span className="rounded border border-cyan/40 bg-cyan/20 px-2 py-0.5 text-[10px] font-semibold text-cyan">
            +{rssChangedCount} new
          </span>
        )}
        {windowLayout.type === 'alerts' && alertChangedCount > 0 && (
          <span className="rounded border border-cyan/40 bg-cyan/20 px-2 py-0.5 text-[10px] font-semibold text-cyan">
            +{alertChangedCount} new
          </span>
        )}
        {isPanelRefreshing && (
          <span className="tl-panel-context rounded border border-slate/20 bg-white/70 px-2 py-0.5 text-[10px] font-semibold text-slate-700 dark:border-cyan-900/40 dark:bg-[#041612]/80 dark:text-white/65">
            Updating...
          </span>
        )}
      </div>
  )
}

function DashboardPanelActions({
  controller,
  windowLayout,
  windowControlsVisible,
  activeLocalFilterCount,
}: {
  controller: DashboardPageController
  windowLayout: DashboardWindow
  windowControlsVisible: boolean
  activeLocalFilterCount: number
}) {
  const {
    isEditMode, isWideLayout, markWindowSeen, openRenameWindow, removeWindow, setWindowSnap,
    toggleMobileWindowControls, toggleWindowControls, windows,
  } = controller

  return (
    <div
      className="tl-dashboard-panel-actions flex flex-wrap items-center gap-2 sm:shrink-0 sm:border-l sm:border-slate/15 sm:pl-3 dark:sm:border-cyan-900/30"
      onMouseDown={(event) => event.stopPropagation()}
    >
        {windowLayout.type === 'alerts' && (
          <button
            type="button"
            className="rounded border border-slate/20 px-2 py-1 text-xs dark:border-cyan-900/40"
            onClick={() => markWindowSeen(windowLayout.id)}
          >
            Mark Seen
          </button>
        )}
        {(windowLayout.type === 'rss' || windowLayout.type === 'alerts') && (
          <button
            type="button"
            className="rounded border border-slate/20 px-2 py-1 text-xs dark:border-cyan-900/40"
            onClick={() => {
              if (isWideLayout) {
                toggleWindowControls(windowLayout.id)
                return
              }
              toggleMobileWindowControls(windowLayout.id)
            }}
          >
            {windowControlsVisible
              ? isWideLayout
                ? 'Hide Filters'
                : 'Done'
              : activeLocalFilterCount > 0 && !isWideLayout
                ? `Filters (${activeLocalFilterCount})`
                : 'Show Filters'}
          </button>
        )}
        {isEditMode && (
          <div className="grid w-full grid-cols-2 gap-1.5 sm:contents">
            <select
              className="hidden rounded border border-slate/20 bg-white px-2 py-1 text-xs sm:block dark:border-cyan-900/40 dark:bg-[#072019]"
              value={windowLayout.snap}
              onChange={(event) => setWindowSnap(windowLayout.id, event.target.value as DashboardWindowSnap)}
              onMouseDown={(event) => event.stopPropagation()}
              aria-label={`${windowLayout.title} panel layout`}
            >
              {WINDOW_SNAP_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="rounded border border-slate/20 px-2 py-1 text-xs dark:border-cyan-900/40"
              onClick={() => openRenameWindow(windowLayout.id)}
            >
              Rename
            </button>
            <button
              type="button"
              className="rounded border border-slate/20 px-2 py-1 text-xs text-red-600 disabled:opacity-40 dark:border-cyan-900/40"
              disabled={windows.length <= 1}
              onClick={() => removeWindow(windowLayout.id)}
            >
              <span className="sm:hidden">Remove panel</span>
              <span className="hidden sm:inline">Close</span>
            </button>
          </div>
        )}
    </div>
  )
}
