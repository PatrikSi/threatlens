import { formatRollingWindowHint } from './dashboardPageUtils'
import { ROLLING_WINDOW_FIELD_CLASS } from './dashboardPanelPresentation'
import {
  resolveSavedViewSelectionChange,
  type TimeRangeFilter,
} from './dashboardSavedViews'
import type { DashboardPageController } from './useDashboardPageController'

export function DashboardToolbar({ controller }: { controller: DashboardPageController }) {
  const {
    activeSavedViewId, addWindow, addWindowActionRefs, addWindowMenuId, addWindowMenuRef, addWindowTriggerRef,
    aiDailyBriefEnabled, alertWindowCount, applyDashboardSavedViewState, applyGlobalSearch, canAddWindow,
    captureCurrentDashboardViewState, clearActiveSavedViewSelection, closeAddWindowMenu,
    confirmDiscardUnsavedDashboardChanges, dailyBriefWindowCount, dashboardCustomSinceDate,
    dashboardCustomUntilDate, dashboardRollingDays, dashboardTimeRange, editSessionSnapshot,
    globalSearchState, handleAddWindowMenuKeyDown, handleAddWindowTriggerKeyDown, hasUnsavedDashboardChanges,
    isEditMode, mobileDashboardViewsOpen, notesWindowCount, openAddWindowMenu,
    requestSavedViewLoad, rssWindowCount,
    saveCurrentView, saveView, savedViewName, setEditSessionSnapshot, setIsEditMode, setOpenWindowMenuId,
    setMobileDashboardViewsOpen, setSavedViewName, setShowManageViewsModal, setShowSaveAsNew,
    setViewSaveError, showAddWindowMenu,
    showSaveAsNew, updateActiveView, updateDashboardCustomSinceDate, updateDashboardCustomUntilDate,
    updateDashboardRollingDaysValue, updateDashboardTimeRange, updateExistingView, viewSaveError,
    viewSavePending, viewsQuery,
  } = controller

  return (
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
                  className="h-8 w-full rounded border border-slate/20 px-3 text-xs disabled:opacity-50 sm:w-auto dark:border-cyan-900/40"
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
                  disabled={!canAddWindow}
                  title={!canAddWindow ? 'Dashboard panel limit reached.' : undefined}
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
  )
}
