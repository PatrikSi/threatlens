import { resolveApiErrorMessage } from '../api/errors'
import { FeedDetailDialog } from './FeedDetailDialog'
import { FeedManagementDialogs } from './FeedManagementDialogs'
import { feedHealthBadgeClass, resolveFeedHealth } from '../utils/feedHealth'
import { DEFAULT_SCHEDULE_CRON, feedToScheduleDraft, isFeedScheduleDraftDirty, validateFeedScheduleDraft } from './feedScheduleDraft'
import { feedSaveStatusClass, feedSaveStatusText, formatDate, resolveMutationError } from './feedPageUtils'
import {
  type FeedFetchMode,
  type FeedSort,
  type FeedStatusFilter,
  useFeedsPageController,
} from './useFeedsPageController'

function mobileDisclosureClass(open: boolean) {
  return open ? 'block' : 'hidden'
}
function mobileFeedToggleLabel(open: boolean) {
  return open ? 'Hide' : 'New feed'
}
function mobileFeedToggleVisibilityClass(feedCount: number) {
  return feedCount === 0 ? 'hidden' : 'block'
}

function mobileImportActionVisibilityClass(hasImportData: boolean) {
  return hasImportData ? 'block' : 'hidden'
}

export function FeedsPage() {
  const {
    canManage,
    canDelete,
    canBackup,
    name,
    setName,
    url,
    setUrl,
    description,
    setDescription,
    siteUrl,
    setSiteUrl,
    language,
    setLanguage,
    fetchMode,
    setFetchMode,
    interval,
    setInterval,
    scheduleCron,
    setScheduleCron,
    search,
    setSearch,
    sort,
    setSort,
    statusFilter,
    setStatusFilter,
    overwriteExisting,
    setOverwriteExisting,
    importData,
    importFilename,
    importError,
    importWarning,
    lastImportResult,
    managementNotice,
    setManagementNotice,
    exportNotice,
    pendingDeleteFeed,
    setPendingDeleteFeed,
    pendingBulkDeleteFeeds,
    setPendingBulkDeleteFeeds,
    pendingBulkSetEnabled,
    setPendingBulkSetEnabled,
    pendingImportReview,
    setPendingImportReview,
    feedDrafts,
    feedSaveState,
    feedEditDraft,
    mobileAddFeedOpen,
    setMobileAddFeedOpen,
    mobileBulkActionsOpen,
    setMobileBulkActionsOpen,
    mobileScheduleFeedId,
    setMobileScheduleFeedId,
    importFileInputRef,
    feedsQuery,
    encryptedDataHealthQuery,
    feedArticlesQuery,
    detectMetadata,
    createFeed,
    updateFeed,
    updateFeedDetails,
    refreshFeed,
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
    onSubmit,
    onDetectMetadata,
    onConfirmDeleteFeed,
    onConfirmBulkDeleteFeeds,
    onConfirmBulkSetEnabled,
    onImportFile,
    persistFeedSchedule,
    updateFeedDraft,
    resetFeedDraft,
    visibleFeedIds,
    visibleDisabledFeedIds,
    visibleEnabledFeedIds,
    visibleBrokenFeedIds,
    brokenFeeds,
    unreadableFeedInventoryCount,
    hasUnreadableFeedWarning,
    showDerivedKeyWarning,
    importPreviewSummary,
    confirmDiscardUnsavedFeedScheduleChanges,
    onRequestDeleteFeed,
    onRequestBulkDeleteFeeds,
    onRequestBulkDeleteBrokenFeeds,
    onRequestImportReview,
    onConfirmImportReview,
    closeFeedDetail,
    updateFeedEditDraft,
    onSaveFeedDetail,
    openFeedDetail,
    showMobileAddFeedForm,
    managementError,
  } = useFeedsPageController()

  return (
    <div className="grid gap-4 lg:grid-cols-[460px_1fr]">
      <section className="order-2 rounded-xl border border-slate/20 bg-white/80 p-4 sm:order-none dark:border-cyan-900/40 dark:bg-[#041612]/90">
        <div className="flex items-center justify-between gap-3">
          <h2 className="font-display text-xl">Add Feed</h2>
          <button
            type="button"
            className={`${mobileFeedToggleVisibilityClass(feedStats.total)} rounded border border-slate/20 px-3 py-1.5 text-xs font-semibold sm:hidden dark:border-cyan-900/40`}
            aria-expanded={showMobileAddFeedForm}
            aria-controls="add-feed-form"
            onClick={() => setMobileAddFeedOpen((current) => !current)}
          >
            {mobileFeedToggleLabel(mobileAddFeedOpen)}
          </button>
        </div>
        {!canManage && <p className="mt-2 text-sm text-amber-600">Viewer role cannot create or modify feeds.</p>}

        <form
          id="add-feed-form"
          className={`${mobileDisclosureClass(showMobileAddFeedForm)} mt-3 space-y-3 sm:block`}
          onSubmit={onSubmit}
        >
          <div>
            <label htmlFor="feed-rss-url" className="text-sm font-semibold">
              RSS URL
            </label>
            <div className="mt-1 flex gap-2">
              <input
                id="feed-rss-url"
                className="w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={url}
                onChange={(event) => setUrl(event.target.value)}
                required
                disabled={!canManage}
                placeholder="https://example.com/feed.xml"
              />
              <button
                type="button"
                className="rounded border border-slate/30 px-3 py-2 text-xs dark:border-cyan-900/40"
                disabled={!canManage || !url.trim() || detectMetadata.isPending}
                onClick={onDetectMetadata}
              >
                Detect
              </button>
            </div>
            {detectMetadata.isError && (
              <p role="alert" aria-live="assertive" aria-atomic="true" className="mt-1 text-xs text-red-600">
                {resolveApiErrorMessage(detectMetadata.error, 'Feed metadata could not be detected')}
              </p>
            )}
          </div>

          <div>
            <label htmlFor="feed-name" className="text-sm font-semibold">
              Name (auto-filled)
            </label>
            <input
              id="feed-name"
              className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
              value={name}
              onChange={(event) => setName(event.target.value)}
              disabled={!canManage}
              placeholder="Leave blank to auto-detect"
            />
          </div>

          <div>
            <label htmlFor="feed-description" className="text-sm font-semibold">
              Description
            </label>
            <textarea
              id="feed-description"
              className="mt-1 h-20 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              disabled={!canManage}
              placeholder="Detected from feed metadata"
            />
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label htmlFor="feed-site-url" className="text-sm font-semibold">
                Site URL
              </label>
              <input
                id="feed-site-url"
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={siteUrl}
                onChange={(event) => setSiteUrl(event.target.value)}
                disabled={!canManage}
              />
            </div>
            <div>
              <label htmlFor="feed-language" className="text-sm font-semibold">
                Language
              </label>
              <input
                id="feed-language"
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={language}
                onChange={(event) => setLanguage(event.target.value)}
                disabled={!canManage}
                placeholder="en-US"
              />
            </div>
          </div>

          <div>
            <label htmlFor="feed-fetch-mode" className="text-sm font-semibold">
              Fetch Mode
            </label>
            <select
              id="feed-fetch-mode"
              className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
              value={fetchMode}
              onChange={(event) => setFetchMode(event.target.value as FeedFetchMode)}
              disabled={!canManage}
            >
              <option value="interval">Every X seconds</option>
              <option value="schedule">Cron schedule</option>
            </select>
          </div>

          {fetchMode === 'interval' ? (
            <div>
              <label htmlFor="feed-fetch-interval" className="text-sm font-semibold">
                Fetch Interval (seconds)
              </label>
              <input
                id="feed-fetch-interval"
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                type="number"
                min={60}
                value={interval}
                onChange={(event) => setInterval(Number(event.target.value))}
                required
                disabled={!canManage}
              />
            </div>
          ) : (
            <div>
              <label htmlFor="feed-schedule-cron" className="text-sm font-semibold">
                Cron Schedule
              </label>
              <input
                id="feed-schedule-cron"
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={scheduleCron}
                onChange={(event) => setScheduleCron(event.target.value)}
                required
                disabled={!canManage}
                placeholder="0 * * * *"
              />
              <p className="mt-1 text-xs text-slate dark:text-slate-300">Example: <code>*/15 * * * *</code> for every 15 minutes.</p>
            </div>
          )}

          <button
            className="rounded bg-ink px-3 py-2 text-white dark:bg-cyan dark:text-[#053c2e]"
            type="submit"
            disabled={createFeed.isPending || !canManage}
          >
            Add Feed
          </button>
          {createFeed.isError && (
            <p role="alert" aria-live="assertive" aria-atomic="true" className="text-sm text-red-600">
              {resolveApiErrorMessage(createFeed.error, 'Feed could not be added')}
            </p>
          )}
        </form>
      </section>

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

        {importFilename && (
          <p className="mt-2 text-xs text-slate dark:text-slate-300">
            Loaded: {importFilename} ({importData?.length ?? 0} entries)
          </p>
        )}
        {importPreviewSummary && (
          <div className="mt-2 rounded border border-slate/20 bg-white/60 px-2 py-2 text-xs text-slate dark:border-cyan-900/40 dark:bg-[#072019] dark:text-slate-200">
            <p>
              Import preflight: {importPreviewSummary.createCount} new, {importPreviewSummary.overwriteCount} overwrite,{' '}
              {importPreviewSummary.skipCount} skip, {importPreviewSummary.duplicateEntries} duplicate entr
              {importPreviewSummary.duplicateEntries === 1 ? 'y' : 'ies'} ignored from {importPreviewSummary.uniqueEntries} unique URL
              {importPreviewSummary.uniqueEntries === 1 ? '' : 's'}.
            </p>
            {importPreviewSummary.matchingExistingFeeds.length > 0 && (
              <p className="mt-1 text-slate dark:text-slate-300">
                {overwriteExisting
                  ? 'Existing feeds below will be rewritten from the import file after confirmation.'
                  : 'Existing feeds below will be skipped unless overwrite is enabled.'}
              </p>
            )}
          </div>
        )}
        {importError && (
          <p role="alert" aria-live="assertive" aria-atomic="true" className="mt-2 text-xs text-red-600">
            Import parse error: {importError}
          </p>
        )}
        {importWarning && (
          <p role="status" aria-live="polite" aria-atomic="true" className="mt-2 text-xs text-amber-600">
            {importWarning}
          </p>
        )}
        {lastImportResult && (
          <div
            role="status"
            aria-live="polite"
            aria-atomic="true"
            className="mt-2 rounded border border-slate/20 bg-white/60 px-2 py-2 text-xs text-slate dark:border-cyan-900/40 dark:bg-[#072019] dark:text-slate-200"
          >
            <p>
              Import result: created {lastImportResult.created}, updated {lastImportResult.updated}, skipped {lastImportResult.skipped}, errors {lastImportResult.errors.length}
            </p>
            {lastImportResult.created + lastImportResult.updated === 0 && (
              <p className="mt-1 text-amber-600">
                No feeds were created or updated. This usually means all entries already existed and overwrite was disabled, or entries were rejected.
              </p>
            )}
            {lastImportResult.errors.length > 0 && (
              <ul className="mt-1 list-disc pl-4 text-red-600">
                {lastImportResult.errors.map((entry, index) => (
                  <li key={`${entry}-${index}`}>{entry}</li>
                ))}
              </ul>
            )}
          </div>
        )}
        {importFeeds.isError && (
          <p role="alert" aria-live="assertive" aria-atomic="true" className="mt-2 text-xs text-red-600">
            {resolveMutationError(importFeeds.error, 'Feed import could not be completed')}
          </p>
        )}
        {managementNotice && (
          <p role="status" aria-live="polite" aria-atomic="true" className="mt-2 text-xs text-emerald-700 dark:text-emerald-300">
            {managementNotice}
          </p>
        )}
        {exportNotice && (
          <p role="status" aria-live="polite" aria-atomic="true" className="mt-2 text-xs text-amber-700 dark:text-amber-300">
            {exportNotice}
          </p>
        )}
        {exportFeeds.isError && (
          <p role="alert" aria-live="assertive" aria-atomic="true" className="mt-2 text-xs text-red-600">
            {resolveMutationError(exportFeeds.error, 'Feed export could not be completed')}
          </p>
        )}
        {canDelete && encryptedDataHealthQuery.isError && (
          <p role="alert" aria-live="assertive" aria-atomic="true" className="mt-2 text-xs text-red-600">
            {resolveApiErrorMessage(encryptedDataHealthQuery.error, 'Encrypted data health could not be loaded')}
          </p>
        )}
        {managementError && (
          <p role="alert" aria-live="assertive" aria-atomic="true" className="mt-2 text-xs text-red-600">
            {resolveApiErrorMessage(managementError, 'One or more feed management actions could not be completed')}
          </p>
        )}

        <div className="mt-3 space-y-2">
          {filteredFeeds.map((feed) => {
            const health = resolveFeedHealth(feed)
            const draft = feedDrafts[feed.id] ?? feedToScheduleDraft(feed)
            const saveState = feedSaveState[feed.id]?.status ?? 'idle'
            const saveMessage = feedSaveState[feed.id]?.message
            const validationMessage = validateFeedScheduleDraft(draft)
            const isDirty = isFeedScheduleDraftDirty(feed, draft)
            const scheduleNotice =
              validationMessage ?? (saveState !== 'idle' ? saveMessage || feedSaveStatusText(saveState) : null)
            const scheduleHint =
              !scheduleNotice && isDirty ? 'Unsaved schedule changes. Save or reset before leaving this page.' : null
            const displayUrl = feed.url.trim() || 'URL unavailable until the original encryption key is restored.'
            const scheduleExpanded = mobileScheduleFeedId === feed.id || isDirty
            return (
            <div key={feed.id} className="rounded border border-slate/20 p-2.5 sm:p-3 dark:border-cyan-900/40">
              <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                <div>
                  <div className="flex items-center gap-2">
                    <p className="font-semibold">{feed.name}</p>
                    {feed.has_unreadable_url && (
                      <span className="tl-chip tl-chip-danger">
                        Broken URL
                      </span>
                    )}
                    {isDirty && (
                      <span className="tl-chip tl-chip-warning">
                        Unsaved schedule
                      </span>
                    )}
                    <span className={`tl-chip ${feedHealthBadgeClass(health.status)}`}>
                      {health.label}
                    </span>
                  </div>
                  <p className="text-xs text-slate dark:text-slate-300">{displayUrl}</p>
                  {feed.description && <p className="mt-1 line-clamp-2 text-xs text-slate sm:line-clamp-none dark:text-slate-300">{feed.description}</p>}
                  <div className="mt-1 flex flex-wrap gap-2 text-[11px] text-slate dark:text-slate-300">
                    {feed.site_url && <span>Site: {feed.site_url}</span>}
                    {feed.language && <span>Lang: {feed.language}</span>}
                    <span>Last fetch: {formatDate(feed.last_fetch_at)}</span>
                    <span>Last success: {formatDate(feed.last_success_at)}</span>
                  </div>
                </div>
                <div className="grid w-full grid-cols-[repeat(auto-fit,minmax(3.5rem,1fr))] gap-1.5 sm:flex sm:w-auto sm:flex-wrap sm:gap-2">
                  <button
                    className="rounded border border-slate/30 px-1 py-1 text-xs sm:px-2 dark:border-cyan-900/40"
                    onClick={() => openFeedDetail(feed)}
                  >
                    {canManage ? 'Edit' : 'Details'}
                  </button>
                  <button
                    type="button"
                    className="rounded border border-slate/30 px-1 py-1 text-xs sm:hidden sm:px-2 dark:border-cyan-900/40"
                    aria-expanded={scheduleExpanded}
                    aria-controls={`feed-schedule-${feed.id}`}
                    onClick={() => setMobileScheduleFeedId((current) => (current === feed.id ? null : feed.id))}
                  >
                    {scheduleExpanded ? 'Hide schedule' : 'Schedule'}
                  </button>
                  <button
                    className="rounded border border-slate/30 px-1 py-1 text-xs sm:px-2 dark:border-cyan-900/40"
                    onClick={() => refreshFeed.mutate(feed.id)}
                    disabled={!canManage || feed.has_unreadable_url}
                  >
                    Refresh
                  </button>
                  <button
                    className="rounded border border-slate/30 px-1 py-1 text-xs sm:px-2 dark:border-cyan-900/40"
                    onClick={() => updateFeed.mutate({ id: feed.id, body: { enabled: !feed.enabled } })}
                    disabled={!canManage}
                  >
                    {feed.enabled ? 'Disable' : 'Enable'}
                  </button>
                  {canDelete && (
                    <button
                      className="rounded border border-red-300 px-1 py-1 text-xs text-red-700 sm:px-2 dark:border-red-800 dark:text-red-300"
                      onClick={() => onRequestDeleteFeed(feed)}
                      disabled={deleteFeed.isPending || Boolean(pendingDeleteFeed) || Boolean(pendingBulkDeleteFeeds)}
                    >
                      Delete
                    </button>
                  )}
                </div>
              </div>

              <div id={`feed-schedule-${feed.id}`} className={`${scheduleExpanded ? 'block' : 'hidden'} sm:block`}>
              <div className="mt-3 grid gap-2 md:grid-cols-[180px_1fr]">
                <label htmlFor={`feed-fetch-mode-${feed.id}`} className="sr-only">
                  Fetch mode for {feed.name}
                </label>
                <select
                  id={`feed-fetch-mode-${feed.id}`}
                  className="rounded border border-slate/30 bg-white px-2 py-1 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                  value={draft.fetchMode}
                  disabled={!canManage}
                  onChange={(event) => {
                    const nextMode = event.target.value as FeedFetchMode
                    updateFeedDraft(feed, {
                      fetchMode: nextMode,
                      intervalSeconds: draft.intervalSeconds || '1800',
                      scheduleCron: nextMode === 'schedule' ? draft.scheduleCron || DEFAULT_SCHEDULE_CRON : draft.scheduleCron,
                    })
                  }}
                >
                  <option value="interval">Interval</option>
                  <option value="schedule">Schedule</option>
                </select>

                {draft.fetchMode === 'interval' ? (
                  <div className="flex flex-wrap items-center gap-2">
                    <label htmlFor={`feed-interval-seconds-${feed.id}`} className="sr-only">
                      Interval seconds for {feed.name}
                    </label>
                    <label htmlFor={`feed-interval-seconds-${feed.id}`} className="text-xs font-semibold">
                      Every
                    </label>
                    <input
                      id={`feed-interval-seconds-${feed.id}`}
                      className="w-28 rounded border border-slate/30 bg-white px-2 py-1 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                      type="number"
                      min={60}
                      value={draft.intervalSeconds}
                      onChange={(event) => {
                        updateFeedDraft(feed, { fetchMode: 'interval', intervalSeconds: event.target.value })
                      }}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter' && canManage && isDirty && !validationMessage && saveState !== 'saving') {
                          event.preventDefault()
                          void persistFeedSchedule(feed.id, draft)
                        }
                      }}
                      disabled={!canManage}
                    />
                    <span className="text-xs text-slate dark:text-slate-300">seconds</span>
                  </div>
                ) : (
                  <>
                    <label htmlFor={`feed-schedule-cron-${feed.id}`} className="sr-only">
                      Cron schedule for {feed.name}
                    </label>
                    <input
                      id={`feed-schedule-cron-${feed.id}`}
                      className="rounded border border-slate/30 bg-white px-2 py-1 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                      value={draft.scheduleCron}
                      onChange={(event) => {
                        updateFeedDraft(feed, { fetchMode: 'schedule', scheduleCron: event.target.value })
                      }}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter' && canManage && isDirty && !validationMessage && saveState !== 'saving') {
                          event.preventDefault()
                          void persistFeedSchedule(feed.id, draft)
                        }
                      }}
                      disabled={!canManage}
                    />
                  </>
                )}
              </div>

              {canManage && (
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    className="rounded border border-slate/30 px-3 py-1 text-xs font-semibold disabled:opacity-50 dark:border-cyan-900/40"
                    disabled={!isDirty || saveState === 'saving' || Boolean(validationMessage)}
                    onClick={() => void persistFeedSchedule(feed.id, draft)}
                  >
                    Save schedule
                  </button>
                  <button
                    type="button"
                    className="rounded border border-slate/30 px-3 py-1 text-xs disabled:opacity-50 dark:border-cyan-900/40"
                    disabled={!isDirty && saveState === 'idle'}
                    onClick={() => resetFeedDraft(feed)}
                  >
                    Reset draft
                  </button>
                </div>
              )}

              {canManage && scheduleHint && (
                <p className={`mt-1 text-[11px] ${feedSaveStatusClass(saveState, isDirty)}`}>
                  {scheduleHint}
                </p>
              )}

              {canManage && scheduleNotice && (
                <p
                  role={saveState === 'error' ? 'alert' : 'status'}
                  aria-live={saveState === 'error' ? 'assertive' : 'polite'}
                  aria-atomic="true"
                  className={`mt-1 text-[11px] ${validationMessage ? 'text-red-600' : feedSaveStatusClass(saveState, isDirty)}`}
                >
                  {scheduleNotice}
                </p>
              )}
              </div>

              {feed.last_error && <p className="mt-2 text-xs text-red-600">Last error: {feed.last_error}</p>}
            </div>
            )
          })}

          {feedsQuery.isLoading && <p className="text-sm text-slate dark:text-slate-300">Loading feeds...</p>}
          {feedsQuery.isError && (
            <p className="text-sm text-red-600">
              {resolveApiErrorMessage(feedsQuery.error, 'Feeds could not be loaded')}
            </p>
          )}
          {!feedsQuery.isLoading && !filteredFeeds.length && (
            <p className="text-sm text-slate dark:text-slate-300">No feeds match your current filters.</p>
          )}
        </div>
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
