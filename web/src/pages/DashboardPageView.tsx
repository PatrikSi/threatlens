import {
  feedHealthBadgeClass,
  resolveFeedHealth,
} from '../utils/feedHealth'
import {
  formatPlainTextPreview,
  sanitizeHref,
} from './dashboardContent'
import {
  ArticlePreviewDrawer,
  RichContent,
} from './DashboardPageComponents'
import {
  ARTICLE_PREVIEW_MIN_WIDTH,
  aiRelevanceTone,
  countActiveWindowFilters,
  countNewEntriesSince,
  formatAiRelevanceLabel,
  formatClassificationLabel,
  formatDailyBriefOptionLabel,
  formatItemStatusLabel,
  formatPublishedAt,
  formatRollingWindowHint,
  formatWindowSnapLabel,
  formatWindowTimeSummary,
  getArticlePreviewMaxWidth,
  itemStatusTone,
  resolveWindowTimeFilter,
} from './dashboardPageUtils'
import {
  calculateDashboardTotalPages,
  DASHBOARD_TIME_INHERIT_VALUE,
  FILTER_CHIP_CLASS,
  FILTER_SCROLLER_CLASS,
  formatAlertMatchCount,
  MOBILE_DASHBOARD_PAGE_SIZE,
  resolvePanelPageSize,
  resolvePanelRefreshing,
  resolveRssItemDetailClassName,
  ROLLING_WINDOW_FIELD_CLASS,
  selectVisibleItemTags,
  WINDOW_SNAP_OPTIONS,
  WINDOW_TYPE_META,
} from './dashboardPanelPresentation'
import {
  createDefaultAlertWindowFilters,
  createDefaultRssWindowFilters,
  HIDDEN_TAGS,
  type AIRelevanceFilter,
  PAGE_SIZE_OPTIONS,
  resolveWindowRect,
  resolveSavedViewSelectionChange,
  type DashboardAlertWindowFilters,
  type DashboardRssWindowFilters,
  type DashboardWindowSnap,
  type ReadStatusFilter,
  type StarStatusFilter,
  type TimeRangeFilter,
  type TimeSort,
} from './dashboardSavedViews'
import { DashboardDialogs } from './DashboardDialogs'
import type { DashboardPageController } from './useDashboardPageController'

export function DashboardPageView({ controller }: { controller: DashboardPageController }) {
  const {
    activeSavedViewId, addWindow, addWindowActionRefs, addWindowMenuId, addWindowMenuRef,
    addWindowTriggerRef,
    adjustArticlePreviewWidth, aiDailyBriefEnabled, aiRelevanceEnabled, aiSummaryEnabled, alertInterestsQuery,
    alertQueriesByWindowId, alertWindowCount, applyDashboardSavedViewState, applyGlobalSearch, articlePreview,
    articlePreviewFrameState, articlePreviewWidth, articleRetryFeedbackByItemId, availableAlertCategories, bringWindowToFront,
    canManage, captureCurrentDashboardViewState, clearActiveSavedViewSelection, closeAddWindowMenu, closeArticlePreview,
    confirmDiscardUnsavedDashboardChanges, containerDimensions, dailyBriefHistoryQuery, dailyBriefWindowCount, dashboardCustomSinceDate,
    dashboardCustomUntilDate, dashboardRollingDays, dashboardTimeFilter, dashboardTimeRange, detailQueriesByWindowId,
    editSessionSnapshot, expandedItemIdsByWindowId, feedsQuery, globalSearchState, handleAddWindowMenuKeyDown,
    handleAddWindowTriggerKeyDown, handleOpenArticlePreview, handleToggleItem, hasUnsavedDashboardChanges,
    isArticlePreviewResizing, isEditMode, isWideLayout, itemActionFeedbackByItemId, markWindowSeen,
    mobileActiveWindowIndex, mobileDashboardViewsOpen, mobileWindowControlsOpenById, noteDraftsByItemId, notesWindowCount,
    openAddWindowMenu, openRenameWindow, removeWindow, renderedWindows, requestSavedViewLoad,
    resolvedMobileWindowId, retryArticleFetch, rootRef, rssLastOpenedAt, rssQueriesByWindowId,
    rssWindowCount, saveCurrentView, saveView, savedViewName, setArticlePreviewFrameState,
    setEditSessionSnapshot, setIsEditMode, setMobileActiveWindowId, setMobileDashboardViewsOpen, setNoteDraftsByItemId,
    setOpenWindowMenuId, setSavedViewName, setShowManageViewsModal, setShowSaveAsNew, setViewSaveError,
    setWindowSnap, showAddWindowMenu, showSaveAsNew, startArticlePreviewResize, startWindowDrag,
    startWindowResize, tagsQuery, toggleMobileWindowControls, toggleWindowControls, updateActiveView,
    updateDashboardCustomSinceDate, updateDashboardCustomUntilDate, updateDashboardRollingDaysValue, updateDashboardTimeRange, updateExistingView,
    updateNote, updateRead, updateStar, updateWindowAlertFilters, updateWindowCustomTimeDate,
    updateWindowDailyBriefSelection, updateWindowRollingDays, updateWindowRssFilters, updateWindowScratchNote, updateWindowTimeRange,
    viewSaveError, viewSavePending, viewsQuery, windowSeenAt, windows,
  } = controller

  return (
    <div className="w-full">
      <div className="border-b border-slate/20 bg-white/85 px-3 py-1.5 shadow-sm dark:border-cyan-900/40 dark:bg-[#041612]/92">
        <div className="grid grid-cols-[minmax(0,1fr)_112px_auto] items-center gap-1.5 sm:hidden">
          <input
            value={globalSearchState.value}
            onChange={(event) => applyGlobalSearch(event.target.value)}
            aria-label="Search across all dashboard panels"
            placeholder={
              globalSearchState.isMixed
                ? 'Panels have different searches. Type here to overwrite all panel searches...'
                : 'Search across all panels...'
            }
            className="h-10 w-full rounded border border-slate/20 bg-white px-3 text-sm dark:border-cyan-900/40 dark:bg-[#041612]"
          />
          <select
            className="h-10 w-full rounded border border-slate/20 bg-white px-3 text-sm dark:border-cyan-900/40 dark:bg-[#041612]"
            value={dashboardTimeRange}
            onChange={(event) => updateDashboardTimeRange(event.target.value as TimeRangeFilter)}
            aria-label="Dashboard time range"
          >
            <option value="all">All time</option>
            <option value="24h">Last 24h</option>
            <option value="7d">Last 7d</option>
            <option value="30d">Last 30d</option>
            <option value="days">Last X days</option>
            <option value="custom">Custom</option>
          </select>
          <button
            type="button"
            className="h-10 rounded border border-slate/20 px-2 text-xs dark:border-cyan-900/40"
            onClick={() => setMobileDashboardViewsOpen((current) => !current)}
            aria-expanded={mobileDashboardViewsOpen}
            aria-controls="dashboard-view-toolbar"
          >
            {mobileDashboardViewsOpen ? 'Hide' : 'Tools'}
          </button>
          {dashboardTimeRange === 'days' && (
            <label className={`${ROLLING_WINDOW_FIELD_CLASS} col-span-3 h-10 text-sm dark:bg-[#041612]`}>
              <span className="mr-2 text-xs text-slate dark:text-white/60">Last</span>
              <input
                type="number"
                min={1}
                max={365}
                value={dashboardRollingDays}
                onChange={(event) => updateDashboardRollingDaysValue(event.target.value)}
                aria-label="Dashboard rolling time window in days"
                className="w-full bg-transparent text-sm focus-visible:outline-none"
              />
              <span className="ml-2 text-xs text-slate dark:text-white/60">days</span>
            </label>
          )}
          {dashboardTimeRange === 'custom' && (
            <div className="col-span-3 grid grid-cols-2 gap-1.5">
              <input
                type="date"
                className="h-10 w-full rounded border border-slate/20 bg-white px-2 text-sm dark:border-cyan-900/40 dark:bg-[#041612]"
                value={dashboardCustomSinceDate}
                onChange={(event) => updateDashboardCustomSinceDate(event.target.value)}
                aria-label="Dashboard custom start date"
              />
              <input
                type="date"
                className="h-10 w-full rounded border border-slate/20 bg-white px-2 text-sm dark:border-cyan-900/40 dark:bg-[#041612]"
                value={dashboardCustomUntilDate}
                onChange={(event) => updateDashboardCustomUntilDate(event.target.value)}
                aria-label="Dashboard custom end date"
              />
            </div>
          )}
        </div>
        <div
          id="dashboard-view-toolbar"
          className={`${mobileDashboardViewsOpen ? 'flex' : 'hidden'} flex-col gap-1.5 sm:flex sm:flex-row sm:flex-wrap sm:items-center lg:flex-nowrap`}
        >
          <input
            value={globalSearchState.value}
            onChange={(event) => applyGlobalSearch(event.target.value)}
            aria-label="Search across all dashboard panels"
            placeholder={
              globalSearchState.isMixed
                ? 'Panels have different searches. Type here to overwrite all panel searches...'
                : 'Search across all panels...'
            }
            className="hidden h-8 w-full min-w-[180px] rounded border border-slate/20 bg-white px-2.5 text-xs sm:block sm:flex-1 dark:border-cyan-900/40 dark:bg-[#041612]"
          />
          <div className="hidden flex-col gap-1.5 sm:flex sm:flex-row sm:flex-wrap sm:items-center">
            <select
              className="h-8 w-full rounded border border-slate/20 bg-white px-2 text-xs sm:w-auto dark:border-cyan-900/40 dark:bg-[#041612]"
              value={dashboardTimeRange}
              onChange={(event) => updateDashboardTimeRange(event.target.value as TimeRangeFilter)}
              aria-label="Dashboard time range"
            >
              <option value="all">All time</option>
              <option value="24h">Last 24h</option>
              <option value="7d">Last 7d</option>
              <option value="30d">Last 30d</option>
              <option value="days">Last X days</option>
              <option value="custom">Custom</option>
            </select>
            {dashboardTimeRange === 'days' && (
              <>
                <label className={`${ROLLING_WINDOW_FIELD_CLASS} h-8 text-xs sm:w-[138px] dark:bg-[#041612]`}>
                  <span className="mr-2 text-xs text-slate dark:text-white/60">Last</span>
                  <input
                    type="number"
                    min={1}
                    max={365}
                    value={dashboardRollingDays}
                    onChange={(event) => updateDashboardRollingDaysValue(event.target.value)}
                    aria-label="Dashboard rolling time window in days"
                    className="w-full bg-transparent text-xs focus-visible:outline-none"
                  />
                  <span className="ml-2 text-xs text-slate dark:text-white/60">days</span>
                </label>
                <div className="flex h-8 w-full items-center rounded border border-slate/20 bg-slate/10 px-2 text-xs text-slate sm:w-auto dark:border-cyan-900/40 dark:bg-[#041612] dark:text-white/65">
                  {formatRollingWindowHint(dashboardRollingDays)}
                </div>
              </>
            )}
            {dashboardTimeRange === 'custom' && (
              <>
                <input
                  type="date"
                  className="h-8 w-full rounded border border-slate/20 bg-white px-2 text-xs sm:w-auto dark:border-cyan-900/40 dark:bg-[#041612]"
                  value={dashboardCustomSinceDate}
                  onChange={(event) => updateDashboardCustomSinceDate(event.target.value)}
                  aria-label="Dashboard custom start date"
                />
                <input
                  type="date"
                  className="h-8 w-full rounded border border-slate/20 bg-white px-2 text-xs sm:w-auto dark:border-cyan-900/40 dark:bg-[#041612]"
                  value={dashboardCustomUntilDate}
                  onChange={(event) => updateDashboardCustomUntilDate(event.target.value)}
                  aria-label="Dashboard custom end date"
                />
              </>
            )}
          </div>
          <select
            className="h-8 w-full rounded border border-slate/20 bg-white px-2 text-xs xl:w-auto dark:border-cyan-900/40 dark:bg-[#041612]"
            value={activeSavedViewId ?? ''}
            aria-label="Load saved dashboard view"
            onChange={(event) => {
              const change = resolveSavedViewSelectionChange({
                currentActiveSavedViewId: activeSavedViewId,
                nextValue: event.target.value,
                hasProtectedEditSession: hasUnsavedDashboardChanges,
              })

              if (change.kind === 'clear') {
                if (hasUnsavedDashboardChanges) {
                  confirmDiscardUnsavedDashboardChanges(() => {
                    clearActiveSavedViewSelection()
                  })
                } else {
                  clearActiveSavedViewSelection()
                }
                return
              }

              if (change.kind === 'load' || change.kind === 'confirm_load') {
                requestSavedViewLoad(change.viewId)
              }
            }}
          >
            <option value="">Load View</option>
            {viewsQuery.data?.map((view) => (
              <option key={view.id} value={view.id}>
                {view.name}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="h-8 w-full rounded border border-slate/20 px-3 text-xs sm:w-auto dark:border-cyan-900/40"
            onClick={() => setShowManageViewsModal(true)}
          >
            Views
          </button>
          {!isEditMode ? (
            <button
              type="button"
              className="h-8 w-full rounded border border-slate/20 px-3 text-xs font-semibold sm:w-auto dark:border-cyan-900/40"
              onClick={() => {
                setEditSessionSnapshot({
                  activeSavedViewId,
                  savedViewName,
                  state: captureCurrentDashboardViewState(),
                })
                setIsEditMode(true)
                setShowSaveAsNew(false)
                setSavedViewName('')
              }}
            >
              Edit Layout
            </button>
          ) : (
            <>
              <div className="relative">
                <button
                  type="button"
                  ref={addWindowTriggerRef}
                  className="h-8 w-full rounded border border-slate/20 px-3 text-xs sm:w-auto dark:border-cyan-900/40"
                  onClick={() => {
                    if (showAddWindowMenu) {
                      closeAddWindowMenu()
                      return
                    }
                    openAddWindowMenu()
                  }}
                  onKeyDown={handleAddWindowTriggerKeyDown}
                  aria-haspopup="menu"
                  aria-expanded={showAddWindowMenu}
                  aria-controls={showAddWindowMenu ? addWindowMenuId : undefined}
                >
                  Add Panel
                </button>
                {showAddWindowMenu && (
                  <div
                    ref={addWindowMenuRef}
                    id={addWindowMenuId}
                    role="menu"
                    aria-label="Add dashboard panel"
                    onKeyDown={handleAddWindowMenuKeyDown}
                    className="absolute right-0 top-[calc(100%+6px)] z-30 w-56 max-w-[calc(100vw-2rem)] rounded border border-slate/20 bg-white p-1 shadow-lg dark:border-cyan-900/40 dark:bg-[#041612]"
                  >
                    <button
                      ref={(node) => {
                        addWindowActionRefs.current[0] = node
                      }}
                      type="button"
                      role="menuitem"
                      className="tl-menu-item w-full rounded px-2 py-1.5 text-left text-xs"
                      onClick={() => addWindow('rss')}
                    >
                      RSS Panel ({rssWindowCount})
                    </button>
                    <button
                      ref={(node) => {
                        addWindowActionRefs.current[1] = node
                      }}
                      type="button"
                      role="menuitem"
                      className="tl-menu-item w-full rounded px-2 py-1.5 text-left text-xs"
                      onClick={() => addWindow('alerts')}
                    >
                      Alerts Panel ({alertWindowCount})
                    </button>
                    <button
                      ref={(node) => {
                        addWindowActionRefs.current[2] = node
                      }}
                      type="button"
                      role="menuitem"
                      className="tl-menu-item w-full rounded px-2 py-1.5 text-left text-xs"
                      onClick={() => addWindow('notes')}
                    >
                      Notes Panel ({notesWindowCount})
                    </button>
                    {aiDailyBriefEnabled && (
                      <button
                        ref={(node) => {
                          addWindowActionRefs.current[3] = node
                        }}
                        type="button"
                        role="menuitem"
                        className="tl-menu-item w-full rounded px-2 py-1.5 text-left text-xs"
                        onClick={() => addWindow('daily_brief')}
                      >
                        Daily Brief Panel ({dailyBriefWindowCount})
                      </button>
                    )}
                  </div>
                )}
              </div>
              {activeSavedViewId ? (
                <>
                  <span className="hidden items-center rounded border border-cyan/30 bg-cyan/8 px-2.5 text-xs font-semibold text-cyan sm:flex dark:border-cyan-800/40 dark:bg-cyan-950/40 dark:text-cyan-200">
                    Editing &ldquo;{viewsQuery.data?.find((v) => v.id === activeSavedViewId)?.name}&rdquo;
                  </span>
                  <button
                    type="button"
                    className="h-8 w-full rounded bg-ink px-3 text-xs font-semibold text-white disabled:opacity-50 sm:w-auto dark:bg-cyan dark:text-slate-950"
                    onClick={() => {
                      updateActiveView()
                    }}
                    disabled={viewSavePending}
                  >
                    {updateExistingView.isPending ? 'Saving...' : 'Save'}
                  </button>
                  {!showSaveAsNew ? (
                    <button
                      type="button"
                      className="h-8 w-full rounded border border-slate/20 px-3 text-xs sm:w-auto dark:border-cyan-900/40"
                      disabled={viewSavePending}
                      onClick={() => setShowSaveAsNew(true)}
                    >
                      Save as New...
                    </button>
                  ) : (
                    <>
                      <input
                        autoFocus
                        value={savedViewName}
                        onChange={(event) => setSavedViewName(event.target.value)}
                        aria-label="New saved dashboard view name"
                        placeholder="New view name..."
                        disabled={viewSavePending}
                        className="h-8 w-full min-w-[140px] rounded border border-slate/20 bg-white px-2.5 text-xs sm:w-auto sm:min-w-[160px] dark:border-cyan-900/40 dark:bg-[#041612]"
                      />
                      <button
                        type="button"
                        className="h-8 w-full rounded border border-slate/20 px-3 text-xs font-semibold disabled:opacity-50 sm:w-auto dark:border-cyan-900/40"
                        onClick={() => {
                          saveCurrentView()
                        }}
                        disabled={viewSavePending || !savedViewName.trim()}
                      >
                        {saveView.isPending ? 'Creating...' : 'Create'}
                      </button>
                    </>
                  )}
                </>
              ) : (
                <>
                  <input
                    value={savedViewName}
                    onChange={(event) => setSavedViewName(event.target.value)}
                    aria-label="Saved dashboard view name"
                    placeholder="View name..."
                    disabled={viewSavePending}
                    className="h-8 w-full min-w-[140px] rounded border border-slate/20 bg-white px-2.5 text-xs sm:w-auto sm:min-w-[160px] dark:border-cyan-900/40 dark:bg-[#041612]"
                  />
                  <button
                    type="button"
                    className="h-8 w-full rounded bg-ink px-3 text-xs font-semibold text-white disabled:opacity-50 sm:w-auto dark:bg-cyan dark:text-slate-950"
                    onClick={() => {
                      saveCurrentView()
                    }}
                    disabled={viewSavePending || !savedViewName.trim()}
                  >
                    {saveView.isPending ? 'Saving...' : 'Save New View'}
                  </button>
                </>
              )}
              <button
                type="button"
                className="h-8 w-full rounded border border-slate/20 px-3 text-xs sm:w-auto dark:border-cyan-900/40"
                disabled={viewSavePending}
                onClick={() => {
                  if (editSessionSnapshot) {
                    applyDashboardSavedViewState(editSessionSnapshot.state, editSessionSnapshot.activeSavedViewId)
                    setSavedViewName(editSessionSnapshot.savedViewName)
                  }
                  setIsEditMode(false)
                  closeAddWindowMenu()
                  setOpenWindowMenuId(null)
                  setShowSaveAsNew(false)
                  setEditSessionSnapshot(null)
                  setViewSaveError('')
                }}
              >
                Cancel
              </button>
            </>
          )}
        </div>
        {viewSaveError && (
          <p role="alert" aria-live="assertive" aria-atomic="true" className="mt-2 text-sm text-red-600 dark:text-red-300">
            {viewSaveError}
          </p>
        )}
        {viewSavePending && (
          <p role="status" aria-live="polite" aria-atomic="true" className="mt-2 text-sm text-cyan-700 dark:text-cyan-300">
            Saving the current layout. Editing is temporarily locked until the request finishes.
          </p>
        )}
      </div>

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
        {renderedWindows.map((windowLayout) => {
          const resolvedRect = resolveWindowRect(windowLayout, containerDimensions.width, containerDimensions.height)
          const effectiveWindowTimeFilter = resolveWindowTimeFilter(windowLayout, dashboardTimeFilter)
          const rssFilters = windowLayout.rss_filters ?? createDefaultRssWindowFilters()
          const alertFilters = windowLayout.alert_filters ?? createDefaultAlertWindowFilters()
          const rssQuery = rssQueriesByWindowId[windowLayout.id]
          const alertQuery = alertQueriesByWindowId[windowLayout.id]
          const rssWindowItems =
            windowLayout.type === 'rss'
              ? rssQuery?.data?.items ?? []
              : []
          const alertWindowItems =
            windowLayout.type === 'alerts'
              ? alertQuery?.data?.items ?? []
              : []
          const lastSeenAtIso = windowSeenAt[windowLayout.id] ?? ''
          const rssChangedCount = windowLayout.type === 'rss' ? countNewEntriesSince(rssWindowItems, rssLastOpenedAt) : 0
          const alertChangedCount = windowLayout.type === 'alerts' ? countNewEntriesSince(alertWindowItems, lastSeenAtIso) : 0
          const rssTotalPages = calculateDashboardTotalPages(
            rssQuery?.data?.total,
            resolvePanelPageSize(rssQuery?.data?.page_size, rssFilters.page_size),
          )
          const alertTotalPages = calculateDashboardTotalPages(
            alertQuery?.data?.total,
            resolvePanelPageSize(alertQuery?.data?.page_size, alertFilters.page_size),
          )
          const windowMeta = WINDOW_TYPE_META[windowLayout.type]
          const windowTimeSummary = formatWindowTimeSummary(windowLayout, dashboardTimeFilter)
          const activeLocalFilterCount = countActiveWindowFilters(windowLayout, rssFilters, alertFilters, aiRelevanceEnabled)
          const windowControlsVisible = isWideLayout
            ? !windowLayout.controls_collapsed
            : mobileWindowControlsOpenById[windowLayout.id] === true
          const isPanelRefreshing = resolvePanelRefreshing(windowLayout.type, {
            rss: Boolean(rssQuery?.isFetching && !rssQuery.isLoading),
            alerts: Boolean(alertQuery?.isFetching && !alertQuery.isLoading),
            dailyBrief: Boolean(dailyBriefHistoryQuery.isFetching && !dailyBriefHistoryQuery.isLoading),
          })

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
                        {alertWindowItems.length} shown
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
                </div>
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
              </div>

              {windowLayout.type === 'rss' ? (
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
                                    ? current.selected_feed_ids.filter((id) => id !== feed.id)
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
                                      ? current.selected_tags.filter((entry) => entry !== tag.name)
                                      : [...current.selected_tags, tag.name],
                                  }))
                                }
                              >
                                #{tag.name}
                              </button>
                            )
                          })}
                      </div>
                      {tagsQuery.isError && <p className="mt-0.5 text-xs text-red-600">Failed to load tags.</p>}

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
                      {rssWindowItems.map((item) => {
                        const expanded = expandedItemIdsByWindowId[windowLayout.id] === item.id
                        const detailQuery = detailQueriesByWindowId[windowLayout.id]
                        const detail = expanded ? detailQuery?.data : null
                        const compact = rssFilters.view_mode === 'compact'
                        const itemHref = sanitizeHref(item.canonical_url || item.url)
                        const detailHref = sanitizeHref(detail?.article?.final_url || detail?.url || null)
                        const displayableItemTags = item.tags.filter((tagName) => !HIDDEN_TAGS.has(tagName))
                        const visibleItemTags = selectVisibleItemTags(displayableItemTags, isWideLayout)
                        const hiddenItemTagCount = Math.max(0, displayableItemTags.length - visibleItemTags.length)

                        return (
                          <article
                            key={item.id}
                            className={`tl-dashboard-rss-card relative rounded border text-slate-900 dark:text-slate-100 ${compact ? 'p-2' : 'p-2.5 sm:p-3'} transition ${
                              expanded ? 'tl-row-selected' : 'tl-article-row'
                            } ${item.is_read ? 'tl-article-row-read' : ''}`}
                          >
                            <div className="w-full text-left">
                              <div className="flex flex-col gap-1.5 sm:flex-row sm:items-start sm:justify-between sm:gap-3">
                                <h3 className={`${compact ? 'text-[14px]' : 'text-[14px] sm:text-[15px]'} line-clamp-2 pr-16 font-semibold leading-snug sm:pr-0`}>
                                  {itemHref ? (
                                    <a
                                      href={itemHref}
                                      target="_blank"
                                      rel="noreferrer"
                                      className="hover:text-cyan hover:underline"
                                      onClick={(event) => event.stopPropagation()}
                                    >
                                      {item.title}
                                    </a>
                                  ) : (
                                    <span>{item.title}</span>
                                  )}
                                </h3>
                                <div className="grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-2 sm:relative sm:flex sm:w-auto sm:shrink-0 sm:flex-nowrap sm:justify-end">
                                  <span className="hidden tl-source-text text-left text-xs font-semibold dark:text-slate-300 sm:inline sm:text-right">{item.feed_name}</span>
                                  <div className="min-w-0 pr-16 sm:hidden sm:pr-0">
                                    <p className="truncate text-[11px] leading-4">
                                      <span className="tl-source-text font-semibold dark:text-slate-300">{item.feed_name}</span>
                                      <span className="mx-1 text-slate/55" aria-hidden="true">·</span>
                                      <span className="text-slate dark:text-slate-300">{formatPublishedAt(item.published_at)}</span>
                                    </p>
                                  </div>
                                  {itemHref && (
                                    <button
                                      type="button"
                                      className="tl-dashboard-rss-preview absolute right-2 top-2 rounded border border-transparent bg-transparent px-1.5 py-1 text-xs font-semibold text-cyan hover:text-cyan dark:bg-transparent sm:right-0 sm:top-5 sm:whitespace-nowrap sm:border-slate/20 sm:bg-white sm:px-2 sm:font-normal sm:text-inherit sm:hover:border-cyan dark:sm:border-cyan-900/40 dark:sm:bg-[#041612]"
                                      onClick={() =>
                                        handleOpenArticlePreview(
                                          {
                                            itemId: item.id,
                                            url: itemHref,
                                            title: item.title,
                                            sourceLabel: item.feed_name,
                                          },
                                          item.is_read,
                                        )
                                      }
                                    >
                                      <span className="sm:hidden">Preview</span>
                                      <span className="hidden sm:inline">Preview Original</span>
                                    </button>
                                  )}
                                </div>
                              </div>
                              <button
                                type="button"
                                className="tl-dashboard-rss-toggle mt-1 w-full text-left text-slate-900 dark:text-slate-100"
                                onClick={() => handleToggleItem(windowLayout.id, item.id, item.is_read)}
                                aria-expanded={expanded}
                                aria-controls={`rss-item-detail-${item.id}`}
                              >
                                <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-slate sm:gap-2 dark:text-slate-300">
                                  <span className="hidden sm:inline">Published {formatPublishedAt(item.published_at)}</span>
                                  {item.status !== 'content_fetched' && (
                                    <span className={`tl-chip ${itemStatusTone(item.status)}`}>{formatItemStatusLabel(item.status)}</span>
                                  )}
                                  {!item.is_read && <span className="tl-chip tl-chip-unread">Unread</span>}
                                  {item.is_starred && <span className="tl-chip tl-chip-starred">Starred</span>}
                                  {aiRelevanceEnabled && item.ai_relevance_label && (
                                    <span className={`tl-chip ${aiRelevanceTone(item.ai_relevance_label)}`}>
                                      AI {formatAiRelevanceLabel(item.ai_relevance_label)}
                                    </span>
                                  )}
                                  {visibleItemTags.map((tagName) => (
                                    <span
                                      key={`${item.id}-${tagName}`}
                                      className="tl-chip tl-chip-tag"
                                    >
                                      #{tagName}
                                    </span>
                                  ))}
                                  {!isWideLayout && hiddenItemTagCount > 0 && (
                                    <span className="tl-chip tl-chip-neutral">+{hiddenItemTagCount}</span>
                                  )}
                                </div>
                                {!compact && (
                                  <p className="mt-1 line-clamp-1 text-xs leading-[1.45] text-slate sm:mt-2 sm:line-clamp-2 sm:text-[13px] sm:leading-5 dark:text-slate-300">
                                    {item.summary || 'No summary available.'}
                                  </p>
                                )}
                              </button>
                            </div>

                            {expanded && (
                              <div
                                id={`rss-item-detail-${item.id}`}
                                className={resolveRssItemDetailClassName(isWideLayout)}
                              >
                                <div className="sticky top-0 z-10 -mx-3 mb-3 flex items-center gap-3 border-b border-slate/20 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#03130f] lg:hidden">
                                    <button
                                      type="button"
                                      className="shrink-0 rounded border border-slate/20 px-3 py-1.5 text-xs font-semibold dark:border-cyan-900/40"
                                      onClick={() => handleToggleItem(windowLayout.id, item.id, true)}
                                    >
                                      Back
                                    </button>
                                    <div className="min-w-0">
                                      <p className="text-[11px] font-semibold uppercase text-slate dark:text-slate-300">RSS item</p>
                                      <p className="truncate text-sm font-semibold">{item.title}</p>
                                    </div>
                                  </div>
                                {detailQuery?.isLoading && <p className="text-sm text-slate dark:text-slate-300">Loading article content...</p>}
                                {detailQuery?.isError && <p className="text-sm text-red-600">Failed to load item details.</p>}

                                {detail && detail.id === item.id && (
                                  <>
                                    <div className="flex flex-wrap items-center gap-2">
                                      {detailHref ? (
                                        <a
                                          className="rounded border border-slate/20 px-2 py-1 text-xs hover:border-cyan hover:text-cyan dark:border-cyan-900/40"
                                          href={detailHref}
                                          target="_blank"
                                          rel="noreferrer"
                                        >
                                          Open Source Link
                                        </a>
                                      ) : (
                                        <span className="rounded border border-slate/20 px-2 py-1 text-xs text-slate dark:border-cyan-900/40 dark:text-slate-400">
                                          Source link unavailable
                                        </span>
                                      )}
                                      <button
                                        className="rounded border border-slate/20 px-2 py-1 text-xs dark:border-cyan-900/40"
                                        disabled={!canManage || (updateRead.isPending && updateRead.variables?.itemId === detail.id)}
                                        onClick={() =>
                                          updateRead.mutate({
                                            itemId: detail.id,
                                            isRead: !detail.state.is_read,
                                          })
                                        }
                                      >
                                        {updateRead.isPending && updateRead.variables?.itemId === detail.id
                                          ? 'Saving...'
                                          : detail.state.is_read
                                            ? 'Mark Unread'
                                            : 'Mark Read'}
                                      </button>
                                      <button
                                        className="rounded border border-slate/20 px-2 py-1 text-xs dark:border-cyan-900/40"
                                        disabled={!canManage || (updateStar.isPending && updateStar.variables?.itemId === detail.id)}
                                        onClick={() =>
                                          updateStar.mutate({
                                            itemId: detail.id,
                                            isStarred: !detail.state.is_starred,
                                          })
                                        }
                                      >
                                        {updateStar.isPending && updateStar.variables?.itemId === detail.id
                                          ? 'Saving...'
                                          : detail.state.is_starred
                                            ? 'Unstar'
                                            : 'Star'}
                                      </button>
                                      {!canManage && <span className="text-xs text-amber-600 dark:text-amber-300">Viewer role is read-only.</span>}
                                    </div>
                                    {itemActionFeedbackByItemId[detail.id] && (
                                      <p
                                        role={itemActionFeedbackByItemId[detail.id]?.tone === 'error' ? 'alert' : 'status'}
                                        aria-live={itemActionFeedbackByItemId[detail.id]?.tone === 'error' ? 'assertive' : 'polite'}
                                        aria-atomic="true"
                                        className={`mt-2 text-xs ${
                                          itemActionFeedbackByItemId[detail.id]?.tone === 'success'
                                            ? 'text-emerald-700 dark:text-emerald-300'
                                            : 'text-red-600 dark:text-red-300'
                                        }`}
                                      >
                                        {itemActionFeedbackByItemId[detail.id]?.message}
                                      </p>
                                    )}

                                    <div className="tl-rss-detail-section tl-surface-muted mt-3 rounded p-3">
                                      <p className="text-xs font-semibold text-slate dark:text-slate-300">RSS summary</p>
                                      {detail.classification && (
                                        <p className="mt-1 text-xs text-slate dark:text-slate-300">
                                          Classification:{' '}
                                          <span className="font-semibold">
                                            {formatClassificationLabel(detail.classification.primary_category)}
                                          </span>{' '}
                                          ({Math.round(detail.classification.confidence * 100)}% confidence)
                                        </p>
                                      )}
                                      <div className="tl-rss-detail-reader rss-reader tl-reader-surface mt-2 rounded p-3">
                                        <RichContent content={detail.summary || 'No summary.'} itemId={detail.id} section="summary" />
                                      </div>
                                    </div>

                                    {(aiSummaryEnabled || aiRelevanceEnabled) && detail.ai_insight?.status === 'ready' && (
                                      <div className="tl-rss-detail-section tl-surface-muted mt-3 rounded p-3">
                                        <p className="text-xs font-semibold text-slate dark:text-slate-300">AI insight</p>
                                        {aiRelevanceEnabled && detail.ai_insight.relevance_label && (
                                          <div className="mt-2 flex flex-wrap items-center gap-2">
                                            <span className={`tl-chip tl-chip-md ${aiRelevanceTone(detail.ai_insight.relevance_label)}`}>
                                              {formatAiRelevanceLabel(detail.ai_insight.relevance_label)} Relevance
                                            </span>
                                            {typeof detail.ai_insight.relevance_score === 'number' && (
                                              <span className="text-xs text-slate dark:text-white/65">
                                                Score {Math.round(detail.ai_insight.relevance_score * 100)}%
                                              </span>
                                            )}
                                          </div>
                                        )}
                                        {aiRelevanceEnabled && detail.ai_insight.relevance_reasons.length > 0 && (
                                          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-slate dark:text-white/75">
                                            {detail.ai_insight.relevance_reasons.map((reason, index) => (
                                              <li key={`${detail.id}-ai-reason-${index}`}>{reason}</li>
                                            ))}
                                          </ul>
                                        )}
                                        {aiSummaryEnabled && detail.ai_insight.summary_text && (
                                          <div className="tl-rss-detail-reader tl-reader-surface mt-3 rounded p-3">
                                            <RichContent
                                              content={detail.ai_insight.summary_text}
                                              itemId={detail.id}
                                              section="ai-summary"
                                            />
                                          </div>
                                        )}
                                        <p className="mt-2 text-xs text-slate dark:text-white/60">
                                          Generated {formatPublishedAt(detail.ai_insight.generated_at)}{detail.ai_insight.model ? ` via ${detail.ai_insight.model}` : ''}.
                                        </p>
                                      </div>
                                    )}
                                    {(aiSummaryEnabled || aiRelevanceEnabled) && detail.ai_insight?.status === 'error' && detail.ai_insight.error && (
                                      <div className="mt-3 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-950/20 dark:text-red-200">
                                        AI enrichment failed: {detail.ai_insight.error}
                                      </div>
                                    )}

                                    <div className="tl-rss-detail-section tl-surface-muted mt-3 rounded p-3">
                                      <p className="text-xs font-semibold text-slate dark:text-slate-300">Full article</p>
                                      {detail.article?.text && detail.article.extraction_method === 'rss_summary_fallback' && (
                                        <p className="mt-2 text-xs text-amber-700 dark:text-amber-200">
                                          Showing RSS summary because full article extraction returned {detail.article.error ?? 'an error'}.
                                        </p>
                                      )}
                                      {detail.article?.text ? (
                                        <div className="tl-rss-detail-reader rss-reader tl-reader-surface mt-2 rounded p-3">
                                          <RichContent content={detail.article.text} itemId={detail.id} section="article" />
                                        </div>
                                      ) : (
                                        <p className="mt-2 text-sm text-slate dark:text-slate-300">No extracted article text available yet.</p>
                                      )}
                                      {detail.article?.error && detail.article.extraction_method !== 'rss_summary_fallback' && (
                                        <p className="mt-2 text-sm text-red-600">Extraction error: {detail.article.error}</p>
                                      )}
                                      {(!detail.article?.text || detail.article?.error) && (
                                        <div className="mt-3 flex flex-wrap items-center gap-2">
                                          <button
                                            className="rounded border border-slate/20 px-2 py-1 text-xs dark:border-cyan-900/40 disabled:opacity-50"
                                            disabled={
                                              !canManage ||
                                              (retryArticleFetch.isPending && retryArticleFetch.variables?.itemId === detail.id)
                                            }
                                            onClick={() => retryArticleFetch.mutate({ itemId: detail.id })}
                                          >
                                            {retryArticleFetch.isPending && retryArticleFetch.variables?.itemId === detail.id
                                              ? 'Queueing...'
                                              : detail.article?.error
                                                ? 'Retry Article Fetch'
                                                : 'Queue Article Fetch'}
                                          </button>
                                          {articleRetryFeedbackByItemId[detail.id] && (
                                            <span
                                              role={articleRetryFeedbackByItemId[detail.id]?.tone === 'error' ? 'alert' : 'status'}
                                              aria-live={articleRetryFeedbackByItemId[detail.id]?.tone === 'error' ? 'assertive' : 'polite'}
                                              aria-atomic="true"
                                              className={`text-xs ${
                                                articleRetryFeedbackByItemId[detail.id]?.tone === 'success'
                                                  ? 'text-emerald-700 dark:text-emerald-300'
                                                  : 'text-red-600 dark:text-red-300'
                                              }`}
                                            >
                                              {articleRetryFeedbackByItemId[detail.id]?.message}
                                            </span>
                                          )}
                                          {!canManage && (
                                            <span className="text-xs text-slate dark:text-slate-300">Read-only for viewer role.</span>
                                          )}
                                        </div>
                                      )}
                                    </div>

                                    <div className="tl-rss-detail-section tl-surface-muted mt-3 rounded p-3">
                                      <label className="text-xs font-semibold text-slate dark:text-slate-300">Notes</label>
                                      <textarea
                                        className="mt-1 h-20 w-full rounded border border-slate/20 bg-white px-2 py-1.5 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                                        value={noteDraftsByItemId[detail.id] ?? detail.state.note ?? ''}
                                        onChange={(event) =>
                                          setNoteDraftsByItemId((current) => ({
                                            ...current,
                                            [detail.id]: event.target.value,
                                          }))
                                        }
                                        disabled={!canManage}
                                        aria-label={`Analyst notes for ${detail.title}`}
                                      />
                                      <div className="mt-2 flex items-center gap-2">
                                        <button
                                          className="rounded bg-ink px-2.5 py-1 text-xs font-semibold text-white disabled:opacity-50 dark:border dark:border-cyan-500/35 dark:bg-[var(--tl-accent-bg-strong)] dark:text-[var(--tl-accent-soft)]"
                                          onClick={() =>
                                            updateNote.mutate({
                                              itemId: detail.id,
                                              note: (noteDraftsByItemId[detail.id] ?? detail.state.note ?? '') || null,
                                            })
                                          }
                                          disabled={!canManage || (updateNote.isPending && updateNote.variables?.itemId === detail.id)}
                                        >
                                          {updateNote.isPending && updateNote.variables?.itemId === detail.id ? 'Saving...' : 'Save Notes'}
                                        </button>
                                        {!canManage && <span className="text-xs text-slate dark:text-slate-300">Read-only for viewer role.</span>}
                                      </div>
                                    </div>
                                  </>
                                )}
                              </div>
                            )}
                          </article>
                        )
                      })}

                      {rssQuery?.isLoading && <p className="text-sm text-slate dark:text-slate-300">Loading items...</p>}
                      {rssQuery?.isFetching && !rssQuery.isLoading && (
                        <p className="text-xs text-slate dark:text-white/60">Refreshing items...</p>
                      )}
                      {rssQuery?.isError && (
                        <p className="text-sm text-red-600">
                          Failed to load items. {(rssQuery.error as Error | undefined)?.message ?? ''}
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
              ) : windowLayout.type === 'alerts' ? (
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
                                  ? current.selected_categories.filter((entry) => entry !== category)
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
                                  ? current.selected_alert_ids.filter((entry) => entry !== interest.id)
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
                          Failed to load alert matches. {(alertQuery.error as Error | undefined)?.message ?? ''}
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
              ) : windowLayout.type === 'daily_brief' ? (
                <div className={`flex min-h-0 flex-1 flex-col p-2 sm:p-3 ${windowMeta.panelClassName}`}>
                  {dailyBriefHistoryQuery.isLoading && <p className="text-sm text-slate dark:text-white/75">Loading daily brief...</p>}
                  {dailyBriefHistoryQuery.isError && (
                    <p className="text-sm text-red-600">
                      Failed to load the daily brief. {(dailyBriefHistoryQuery.error as Error | undefined)?.message ?? ''}
                    </p>
                  )}
                  {!dailyBriefHistoryQuery.isLoading && !(dailyBriefHistoryQuery.data?.length ?? 0) && (
                    <p className="text-sm text-slate dark:text-white/75">
                      No AI daily brief is available yet. An admin can generate one from the AI page after the endpoint is configured.
                    </p>
                  )}
                  {(dailyBriefHistoryQuery.data?.length ?? 0) > 0 && (() => {
                    const availableBriefs = dailyBriefHistoryQuery.data ?? []
                    const selectedBrief =
                      availableBriefs.find((brief) => brief.id === windowLayout.selected_daily_brief_id) ?? availableBriefs[0]

                    if (!selectedBrief) {
                      return null
                    }

                    return (
                    <div className="min-h-0 flex-1 space-y-3 overflow-auto">
                      <div className="rounded border border-slate/20 bg-white/92 p-2.5 sm:p-3 dark:border-cyan-900/40 dark:bg-[#041612]/92">
                        <div className="flex flex-wrap items-center justify-between gap-3">
                          <label className="flex min-w-0 flex-1 flex-col items-stretch gap-1 text-sm sm:min-w-[220px] sm:flex-row sm:items-center sm:gap-2">
                            <span className="text-xs font-semibold text-slate dark:text-white/55">Briefing</span>
                            <select
                              className="w-full rounded border border-slate/20 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#041612]"
                              value={selectedBrief.id}
                              onChange={(event) => updateWindowDailyBriefSelection(windowLayout.id, event.target.value)}
                              aria-label={`${windowLayout.title} briefing selection`}
                            >
                              {availableBriefs.map((brief) => (
                                <option key={brief.id} value={brief.id}>
                                  {formatDailyBriefOptionLabel(brief)}
                                </option>
                              ))}
                            </select>
                          </label>
                        </div>
                        <p className="mt-3 text-xs text-slate dark:text-white/60">
                          Generated {formatPublishedAt(selectedBrief.generated_at)} for {selectedBrief.item_count} items covering{' '}
                          {formatPublishedAt(selectedBrief.window_start)} to {formatPublishedAt(selectedBrief.window_end)}.
                        </p>
                      </div>

                      {selectedBrief.key_points.length > 0 && (
                        <div className="tl-daily-brief-section rounded border border-slate/20 bg-white/90 p-2.5 sm:p-3 dark:border-cyan-900/40 dark:bg-[#041612]/92">
                          <p className="text-xs font-semibold text-slate dark:text-slate-300">Key points</p>
                          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-slate dark:text-white/75">
                            {selectedBrief.key_points.map((point, index) => (
                              <li key={`${windowLayout.id}-brief-point-${index}`}>{point}</li>
                            ))}
                          </ul>
                        </div>
                      )}

                      {selectedBrief.recommended_actions.length > 0 && (
                        <div className="tl-daily-brief-section rounded border border-slate/20 bg-white/90 p-2.5 sm:p-3 dark:border-cyan-900/40 dark:bg-[#041612]/92">
                          <p className="text-xs font-semibold text-slate dark:text-slate-300">Recommended actions</p>
                          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-slate dark:text-white/75">
                            {selectedBrief.recommended_actions.map((action, index) => (
                              <li key={`${windowLayout.id}-brief-action-${index}`}>{action}</li>
                            ))}
                          </ul>
                        </div>
                      )}

                      {selectedBrief.items.length > 0 && (
                        <div className="tl-daily-brief-section rounded border border-slate/20 bg-white/90 p-2.5 sm:p-3 dark:border-cyan-900/40 dark:bg-[#041612]/92">
                          <p className="text-xs font-semibold text-slate dark:text-slate-300">Referenced items</p>
                          <div className="mt-2 space-y-2">
                            {selectedBrief.items.map((item) => (
                              <article key={item.id} className="tl-daily-brief-item rounded border border-slate/20 p-2 dark:border-cyan-900/40">
                                <div className="flex items-start justify-between gap-2">
                                  {sanitizeHref(item.url) ? (
                                    <a
                                      href={sanitizeHref(item.url) ?? undefined}
                                      target="_blank"
                                      rel="noreferrer"
                                      className="text-sm font-semibold hover:text-cyan hover:underline dark:hover:text-cyan-200"
                                    >
                                      {item.title}
                                    </a>
                                  ) : (
                                    <span className="text-sm font-semibold">{item.title}</span>
                                  )}
                                  {item.relevance_label && (
                                    <span className={`tl-chip shrink-0 ${aiRelevanceTone(item.relevance_label)}`}>
                                      {formatAiRelevanceLabel(item.relevance_label)}
                                    </span>
                                  )}
                                </div>
                                <p className="mt-1 text-xs text-slate dark:text-white/65">
                                  {item.feed_name} • Published {formatPublishedAt(item.published_at)}
                                </p>
                              </article>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                    )
                  })()}
                </div>
              ) : (
                <div className={`flex flex-1 flex-col p-2.5 sm:p-3 ${windowMeta.panelClassName}`}>
                  <label className="text-xs font-semibold text-slate dark:text-slate-300">Scratch notes</label>
                  <textarea
                    className="tl-dashboard-notes-editor mt-2 h-full min-h-[180px] w-full flex-1 rounded border border-slate/20 bg-white px-3 py-2 text-sm leading-6 dark:border-cyan-900/40 dark:bg-[#072019]"
                    placeholder="Use this space for quick notes, pivots, and hypotheses..."
                    value={windowLayout.scratch_note}
                    onChange={(event) => updateWindowScratchNote(windowLayout.id, event.target.value)}
                    aria-label={`${windowLayout.title} scratch notes`}
                  />
                  <p className="mt-2 text-xs text-slate dark:text-slate-300">Saved in this panel and in saved views.</p>
                </div>
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
        })}
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

      <DashboardDialogs controller={controller} />
    </div>
  )
}
