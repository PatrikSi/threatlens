import { AddFeedPanel } from './AddFeedPanel'
import { FeedDetailDialog } from './FeedDetailDialog'
import { FeedInventory } from './FeedInventory'
import { FeedManagementStatus } from './FeedManagementStatus'
import { FeedManagementDialogs } from './FeedManagementDialogs'
import {
  type FeedSort,
  type FeedStatusFilter,
  useFeedsPageController,
} from './useFeedsPageController'

function mobileDisclosureClass(open: boolean) {
  return open ? 'block' : 'hidden'
}
function mobileImportActionVisibilityClass(hasImportData: boolean) {
  return hasImportData ? 'block' : 'hidden'
}

export function FeedsPage() {
  const controller = useFeedsPageController()
  const {
    canManage,
    canDelete,
    canBackup,
    search,
    setSearch,
    sort,
    setSort,
    statusFilter,
    setStatusFilter,
    overwriteExisting,
    setOverwriteExisting,
    importData,
    setManagementNotice,
    pendingDeleteFeed,
    setPendingDeleteFeed,
    pendingBulkDeleteFeeds,
    setPendingBulkDeleteFeeds,
    pendingBulkSetEnabled,
    setPendingBulkSetEnabled,
    pendingImportReview,
    setPendingImportReview,
    feedEditDraft,
    mobileBulkActionsOpen,
    setMobileBulkActionsOpen,
    importFileInputRef,
    feedArticlesQuery,
    updateFeedDetails,
    deleteFeed,
    bulkRefreshFeeds,
    bulkSetEnabled,
    bulkDeleteFeeds,
    importFeeds,
    exportFeeds,
    filteredFeeds,
    feedStats,
    editingFeed,
    feedEditValidation,
    feedEditDirty,
    onConfirmDeleteFeed,
    onConfirmBulkDeleteFeeds,
    onConfirmBulkSetEnabled,
    onImportFile,
    visibleFeedIds,
    visibleDisabledFeedIds,
    visibleEnabledFeedIds,
    visibleBrokenFeedIds,
    brokenFeeds,
    unreadableFeedInventoryCount,
    hasUnreadableFeedWarning,
    showDerivedKeyWarning,
    confirmDiscardUnsavedFeedScheduleChanges,
    onRequestBulkDeleteFeeds,
    onRequestBulkDeleteBrokenFeeds,
    onRequestImportReview,
    onConfirmImportReview,
    closeFeedDetail,
    updateFeedEditDraft,
    onSaveFeedDetail,
  } = controller

  return (
    <div className="grid gap-4 lg:grid-cols-[460px_1fr]">
      <AddFeedPanel controller={controller} />

      <section className="order-1 rounded-xl border border-slate/20 bg-white/80 p-4 sm:order-none dark:border-cyan-900/40 dark:bg-[#041612]/90">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="font-display text-xl">Configured Feeds ({feedStats.total})</h2>
          <div className="grid w-full grid-cols-2 gap-2 sm:flex sm:w-auto sm:flex-wrap sm:items-center">
            <button
              type="button"
              className="rounded border border-slate/30 px-3 py-1.5 text-xs disabled:opacity-50 dark:border-cyan-900/40"
              onClick={() => exportFeeds.mutate()}
              disabled={!canBackup || exportFeeds.isPending}
              title="Export full feed URLs for backup and restore"
            >
              Export JSON
            </button>
            <button
              type="button"
              className="rounded border border-slate/30 px-3 py-1.5 text-xs disabled:opacity-50 dark:border-cyan-900/40"
              onClick={() => importFileInputRef.current?.click()}
              disabled={!canManage}
            >
              Import JSON
            </button>
            <input
              ref={importFileInputRef}
              type="file"
              accept="application/json"
              className="sr-only"
              onChange={onImportFile}
              disabled={!canManage}
              tabIndex={-1}
            />
            <button
              type="button"
              className={`${mobileImportActionVisibilityClass(Boolean(importData))} col-span-2 rounded bg-ink px-3 py-1.5 text-xs text-white disabled:opacity-50 sm:col-auto sm:block dark:bg-cyan dark:text-[#053c2e]`}
              disabled={!canManage || !importData || importFeeds.isPending}
              onClick={onRequestImportReview}
            >
              Run Import
            </button>
          </div>
        </div>

        <p className="mt-2 text-xs text-slate dark:text-slate-300">
          Showing {filteredFeeds.length} of {feedStats.total} feeds · {feedStats.enabled} enabled · {feedStats.disabled} disabled
          · {feedStats.unhealthy} with errors · {feedStats.broken} unreadable URL
        </p>

        {canDelete && hasUnreadableFeedWarning && (
          <div className="mt-3 rounded-lg border border-amber-300/70 bg-amber-50 px-3 py-3 text-sm text-amber-950 dark:border-amber-800/60 dark:bg-amber-950/30 dark:text-amber-100">
            <p className="font-semibold">
              {unreadableFeedInventoryCount} stored feed{unreadableFeedInventoryCount === 1 ? ' has' : 's have'} unreadable
              encrypted URLs.
            </p>
            <p className="mt-1 text-xs text-amber-900/90 dark:text-amber-200/90">
              ThreatLens can keep running, but those feeds cannot refresh until the original `APP_DATA_ENCRYPTION_KEY` is
              restored through `APP_DATA_ENCRYPTION_PREVIOUS_KEYS` or the feeds are recreated.
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                className="rounded border border-amber-400/80 bg-white px-3 py-1.5 text-xs font-semibold text-amber-900 dark:border-amber-700 dark:bg-transparent dark:text-amber-100"
                onClick={() => setStatusFilter('broken')}
              >
                Show Broken Feeds
              </button>
              <button
                type="button"
                className="rounded border border-red-400 px-3 py-1.5 text-xs font-semibold text-red-700 disabled:opacity-50 dark:border-red-700 dark:text-red-300"
                disabled={!brokenFeeds.length || bulkDeleteFeeds.isPending || Boolean(pendingDeleteFeed) || Boolean(pendingBulkDeleteFeeds)}
                onClick={() => onRequestBulkDeleteBrokenFeeds(brokenFeeds)}
              >
                Delete Broken Feeds
              </button>
            </div>
          </div>
        )}

        {showDerivedKeyWarning && (
          <div className="mt-3 rounded-lg border border-sky-300/70 bg-sky-50 px-3 py-3 text-sm text-sky-950 dark:border-sky-900/60 dark:bg-sky-950/25 dark:text-sky-100">
            <p className="font-semibold">This deployment is using a derived development encryption key.</p>
            <p className="mt-1 text-xs text-sky-900/90 dark:text-sky-200/90">
              Set an explicit persistent `APP_DATA_ENCRYPTION_KEY` before relying on durable data. The bundled compose
              deployment now expects that key to be configured on purpose.
            </p>
          </div>
        )}

        <div className="mt-2">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-1 sm:gap-3 md:grid-cols-[minmax(0,1fr)_180px_180px_auto]">
            <div className="col-span-2 sm:col-span-1">
              <label htmlFor="feed-search" className="text-xs font-semibold uppercase text-slate dark:text-slate-300">
                Search
              </label>
              <input
                id="feed-search"
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Name, URL, language, or error"
              />
            </div>
            <div>
              <label htmlFor="feed-status-filter" className="text-xs font-semibold uppercase text-slate dark:text-slate-300">
                Status
              </label>
              <select
                id="feed-status-filter"
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                value={statusFilter}
                onChange={(event) => setStatusFilter(event.target.value as FeedStatusFilter)}
              >
                <option value="all">All feeds</option>
                <option value="enabled">Enabled only</option>
                <option value="disabled">Disabled only</option>
                <option value="broken">Unreadable URL only</option>
              </select>
            </div>
            <div>
              <label htmlFor="feed-sort" className="text-xs font-semibold uppercase text-slate dark:text-slate-300">
                Sort
              </label>
              <select
                id="feed-sort"
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                value={sort}
                onChange={(event) => setSort(event.target.value as FeedSort)}
              >
                <option value="created_desc">Newest created</option>
                <option value="name_asc">Name A-Z</option>
                <option value="name_desc">Name Z-A</option>
                <option value="last_fetch_desc">Last fetched newest</option>
                <option value="last_fetch_asc">Last fetched oldest</option>
              </select>
            </div>
            <label className="col-span-2 flex items-end gap-2 text-xs text-slate sm:col-span-1 dark:text-slate-300">
              <input
                type="checkbox"
                checked={overwriteExisting}
                onChange={(event) => setOverwriteExisting(event.target.checked)}
                disabled={!canManage}
              />
              Overwrite existing on import
            </label>
          </div>
        </div>

        <button
          type="button"
          className="mt-2 flex w-full items-center justify-between rounded border border-slate/30 px-3 py-2 text-left text-sm font-semibold sm:hidden dark:border-cyan-900/40"
          aria-expanded={mobileBulkActionsOpen}
          aria-controls="feed-bulk-actions"
          onClick={() => setMobileBulkActionsOpen((current) => !current)}
        >
          <span>Bulk actions</span>
          <span className="text-xs font-normal text-slate dark:text-slate-300">Filtered feeds</span>
        </button>

        <div
          id="feed-bulk-actions"
          className={`${mobileDisclosureClass(mobileBulkActionsOpen)} mt-2 grid gap-2 sm:flex sm:flex-wrap sm:items-center`}
        >
          <button
            type="button"
            className="rounded border border-slate/30 px-3 py-1.5 text-xs dark:border-cyan-900/40"
            disabled={!canManage || !visibleFeedIds.length || bulkRefreshFeeds.isPending}
            onClick={() => {
              setManagementNotice('')
              bulkRefreshFeeds.mutate(filteredFeeds)
            }}
          >
            Refresh Filtered
          </button>
          <button
            type="button"
            className="rounded border border-slate/30 px-3 py-1.5 text-xs dark:border-cyan-900/40"
            disabled={!canManage || !visibleDisabledFeedIds.length || bulkSetEnabled.isPending}
            onClick={() => {
              setManagementNotice('')
              setPendingBulkSetEnabled({
                enabled: true,
                feeds: filteredFeeds.filter((feed) => !feed.enabled),
              })
            }}
          >
            Enable Disabled (Filtered)
          </button>
          <button
            type="button"
            className="rounded border border-slate/30 px-3 py-1.5 text-xs dark:border-cyan-900/40"
            disabled={!canManage || !visibleEnabledFeedIds.length || bulkSetEnabled.isPending}
            onClick={() => {
              setManagementNotice('')
              setPendingBulkSetEnabled({
                enabled: false,
                feeds: filteredFeeds.filter((feed) => feed.enabled),
              })
            }}
          >
            Disable Enabled (Filtered)
          </button>
          {canDelete && (
            <button
              type="button"
              className="rounded border border-red-300 px-3 py-1.5 text-xs text-red-700 dark:border-red-800 dark:text-red-300"
              disabled={
                !visibleDisabledFeedIds.length ||
                bulkDeleteFeeds.isPending ||
                Boolean(pendingDeleteFeed) ||
                Boolean(pendingBulkDeleteFeeds)
              }
              onClick={() => onRequestBulkDeleteFeeds(filteredFeeds.filter((feed) => !feed.enabled))}
            >
              Delete Disabled (Filtered)
            </button>
          )}
          {canDelete && (
            <button
              type="button"
              className="rounded border border-red-300 px-3 py-1.5 text-xs text-red-700 dark:border-red-800 dark:text-red-300"
              disabled={
                !visibleBrokenFeedIds.length ||
                bulkDeleteFeeds.isPending ||
                Boolean(pendingDeleteFeed) ||
                Boolean(pendingBulkDeleteFeeds)
              }
              onClick={() => onRequestBulkDeleteBrokenFeeds(filteredFeeds.filter((feed) => feed.has_unreadable_url))}
            >
              Delete Broken (Filtered)
            </button>
          )}
        </div>

        <FeedManagementStatus controller={controller} />
        <FeedInventory controller={controller} />
      </section>


      <FeedDetailDialog
        editingFeed={editingFeed}
        feedEditDraft={feedEditDraft}
        closeFeedDetail={closeFeedDetail}
        canManage={canManage}
        feedEditDirty={feedEditDirty}
        updateFeedDetails={updateFeedDetails}
        feedEditValidation={feedEditValidation}
        onSaveFeedDetail={onSaveFeedDetail}
        updateFeedEditDraft={updateFeedEditDraft}
        feedArticlesQuery={feedArticlesQuery}
      />
      <FeedManagementDialogs
        pendingImportReview={pendingImportReview}
        setPendingImportReview={setPendingImportReview}
        onConfirmImportReview={onConfirmImportReview}
        importFeeds={importFeeds}
        pendingBulkSetEnabled={pendingBulkSetEnabled}
        setPendingBulkSetEnabled={setPendingBulkSetEnabled}
        onConfirmBulkSetEnabled={onConfirmBulkSetEnabled}
        bulkSetEnabled={bulkSetEnabled}
        pendingDeleteFeed={pendingDeleteFeed}
        setPendingDeleteFeed={setPendingDeleteFeed}
        onConfirmDeleteFeed={onConfirmDeleteFeed}
        deleteFeed={deleteFeed}
        pendingBulkDeleteFeeds={pendingBulkDeleteFeeds}
        setPendingBulkDeleteFeeds={setPendingBulkDeleteFeeds}
        onConfirmBulkDeleteFeeds={onConfirmBulkDeleteFeeds}
        bulkDeleteFeeds={bulkDeleteFeeds}
      />
      {confirmDiscardUnsavedFeedScheduleChanges.discardDialog}
    </div>
  )
}
