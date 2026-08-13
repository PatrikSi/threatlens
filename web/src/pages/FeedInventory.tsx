import { resolveApiErrorMessage } from '../api/errors'
import { feedHealthBadgeClass, resolveFeedHealth } from '../utils/feedHealth'
import {
  DEFAULT_SCHEDULE_CRON,
  feedToScheduleDraft,
  isFeedScheduleDraftDirty,
  validateFeedScheduleDraft,
} from './feedScheduleDraft'
import { feedSaveStatusClass, feedSaveStatusText, formatDate } from './feedPageUtils'
import { type FeedFetchMode, useFeedsPageController } from './useFeedsPageController'

type FeedsController = ReturnType<typeof useFeedsPageController>
type Feed = FeedsController['filteredFeeds'][number]

export function FeedInventory({ controller }: { controller: FeedsController }) {
  const { filteredFeeds, feedsQuery } = controller
  return (
    <div className="mt-3 space-y-2">
      {filteredFeeds.map((feed) => (
        <FeedInventoryRow key={feed.id} controller={controller} feed={feed} />
      ))}
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
  )
}

function FeedInventoryRow({ controller, feed }: { controller: FeedsController; feed: Feed }) {
  const {
    canManage,
    canDelete,
    deleteFeed,
    feedDrafts,
    mobileScheduleFeedId,
    openFeedDetail,
    onRequestDeleteFeed,
    pendingBulkDeleteFeeds,
    pendingDeleteFeed,
    refreshFeed,
    setMobileScheduleFeedId,
    updateFeed,
  } = controller
  const health = resolveFeedHealth(feed)
  const draft = feedDrafts[feed.id] ?? feedToScheduleDraft(feed)
  const isDirty = isFeedScheduleDraftDirty(feed, draft)
  const scheduleExpanded = mobileScheduleFeedId === feed.id || isDirty
  const displayUrl = feed.url.trim() || 'URL unavailable until the original encryption key is restored.'

  return (
    <div className="rounded border border-slate/20 p-2.5 sm:p-3 dark:border-cyan-900/40">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <p className="font-semibold">{feed.name}</p>
            {feed.has_unreadable_url && <span className="tl-chip tl-chip-danger">Broken URL</span>}
            {isDirty && <span className="tl-chip tl-chip-warning">Unsaved schedule</span>}
            <span className={`tl-chip ${feedHealthBadgeClass(health.status)}`}>{health.label}</span>
          </div>
          <p className="text-xs text-slate dark:text-slate-300">{displayUrl}</p>
          {feed.description && (
            <p className="mt-1 line-clamp-2 text-xs text-slate sm:line-clamp-none dark:text-slate-300">
              {feed.description}
            </p>
          )}
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
      <FeedScheduleEditor
        controller={controller}
        feed={feed}
        scheduleExpanded={scheduleExpanded}
      />
      {feed.last_error && <p className="mt-2 text-xs text-red-600">Last error: {feed.last_error}</p>}
    </div>
  )
}

function FeedScheduleEditor({
  controller,
  feed,
  scheduleExpanded,
}: {
  controller: FeedsController
  feed: Feed
  scheduleExpanded: boolean
}) {
  const {
    canManage,
    feedDrafts,
    feedSaveState,
    persistFeedSchedule,
    resetFeedDraft,
    updateFeedDraft,
  } = controller
  const draft = feedDrafts[feed.id] ?? feedToScheduleDraft(feed)
  const saveState = feedSaveState[feed.id]?.status ?? 'idle'
  const saveMessage = feedSaveState[feed.id]?.message
  const validationMessage = validateFeedScheduleDraft(draft)
  const isDirty = isFeedScheduleDraftDirty(feed, draft)
  const scheduleNotice = validationMessage ?? (saveState !== 'idle' ? saveMessage || feedSaveStatusText(saveState) : null)
  const scheduleHint = !scheduleNotice && isDirty ? 'Unsaved schedule changes. Save or reset before leaving this page.' : null
  const saveOnEnter = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key !== 'Enter' || !canManage || !isDirty || validationMessage || saveState === 'saving') return
    event.preventDefault()
    void persistFeedSchedule(feed.id, draft)
  }

  return (
    <div id={`feed-schedule-${feed.id}`} className={`${scheduleExpanded ? 'block' : 'hidden'} sm:block`}>
      <div className="mt-3 grid gap-2 md:grid-cols-[180px_1fr]">
        <label htmlFor={`feed-fetch-mode-${feed.id}`} className="sr-only">Fetch mode for {feed.name}</label>
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
            <label htmlFor={`feed-interval-seconds-${feed.id}`} className="text-xs font-semibold">Every</label>
            <input
              id={`feed-interval-seconds-${feed.id}`}
              className="w-28 rounded border border-slate/30 bg-white px-2 py-1 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
              type="number"
              min={60}
              value={draft.intervalSeconds}
              onChange={(event) => updateFeedDraft(feed, { fetchMode: 'interval', intervalSeconds: event.target.value })}
              onKeyDown={saveOnEnter}
              disabled={!canManage}
            />
            <span className="text-xs text-slate dark:text-slate-300">seconds</span>
          </div>
        ) : (
          <>
            <label htmlFor={`feed-schedule-cron-${feed.id}`} className="sr-only">Cron schedule for {feed.name}</label>
            <input
              id={`feed-schedule-cron-${feed.id}`}
              className="rounded border border-slate/30 bg-white px-2 py-1 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
              value={draft.scheduleCron}
              onChange={(event) => updateFeedDraft(feed, { fetchMode: 'schedule', scheduleCron: event.target.value })}
              onKeyDown={saveOnEnter}
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
      {canManage && scheduleHint && <p className={`mt-1 text-[11px] ${feedSaveStatusClass(saveState, isDirty)}`}>{scheduleHint}</p>}
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
  )
}
