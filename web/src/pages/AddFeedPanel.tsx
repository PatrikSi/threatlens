import { resolveApiErrorMessage } from '../api/errors'
import { type FeedFetchMode, useFeedsPageController } from './useFeedsPageController'

type FeedsController = ReturnType<typeof useFeedsPageController>

function mobileDisclosureClass(open: boolean) {
  return open ? 'block' : 'hidden'
}

function mobileFeedToggleLabel(open: boolean) {
  return open ? 'Hide' : 'New feed'
}

function mobileFeedToggleVisibilityClass(feedCount: number) {
  return feedCount === 0 ? 'hidden' : 'block'
}

export function AddFeedPanel({ controller }: { controller: FeedsController }) {
  const {
    canManage,
    createFeed,
    description,
    detectMetadata,
    feedStats,
    fetchMode,
    interval,
    language,
    mobileAddFeedOpen,
    name,
    onDetectMetadata,
    onSubmit,
    scheduleCron,
    setDescription,
    setFetchMode,
    setInterval,
    setLanguage,
    setMobileAddFeedOpen,
    setName,
    setScheduleCron,
    setSiteUrl,
    setUrl,
    showMobileAddFeedForm,
    siteUrl,
    url,
  } = controller

  return (
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
      {!canManage && <p className="mt-2 text-sm text-amber-600">Your current access is read-only for feeds.</p>}
      <form
        id="add-feed-form"
        className={`${mobileDisclosureClass(showMobileAddFeedForm)} mt-3 space-y-3 sm:block`}
        onSubmit={onSubmit}
      >
        <div>
          <label htmlFor="feed-rss-url" className="text-sm font-semibold">RSS URL</label>
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
          <label htmlFor="feed-name" className="text-sm font-semibold">Name (auto-filled)</label>
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
          <label htmlFor="feed-description" className="text-sm font-semibold">Description</label>
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
            <label htmlFor="feed-site-url" className="text-sm font-semibold">Site URL</label>
            <input
              id="feed-site-url"
              className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
              value={siteUrl}
              onChange={(event) => setSiteUrl(event.target.value)}
              disabled={!canManage}
            />
          </div>
          <div>
            <label htmlFor="feed-language" className="text-sm font-semibold">Language</label>
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
          <label htmlFor="feed-fetch-mode" className="text-sm font-semibold">Fetch Mode</label>
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
            <label htmlFor="feed-fetch-interval" className="text-sm font-semibold">Fetch Interval (seconds)</label>
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
            <label htmlFor="feed-schedule-cron" className="text-sm font-semibold">Cron Schedule</label>
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
  )
}
