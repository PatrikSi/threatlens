import { resolveApiErrorMessage } from '../api/errors'
import { DialogSurface } from '../components/ConfirmDialog'
import { DEFAULT_SCHEDULE_CRON } from './feedScheduleDraft'
import { formatDate, resolveMutationError } from './feedPageUtils'
import type { FeedFetchMode, useFeedsPageController } from './useFeedsPageController'

type FeedDetailDialogProps = Pick<
  ReturnType<typeof useFeedsPageController>,
  | 'editingFeed'
  | 'feedEditDraft'
  | 'closeFeedDetail'
  | 'canManage'
  | 'feedEditDirty'
  | 'updateFeedDetails'
  | 'feedEditValidation'
  | 'onSaveFeedDetail'
  | 'updateFeedEditDraft'
  | 'feedArticlesQuery'
>

export function FeedDetailDialog({
  editingFeed,
  feedEditDraft,
  closeFeedDetail,
  canManage,
  feedEditDirty,
  updateFeedDetails,
  feedEditValidation,
  onSaveFeedDetail,
  updateFeedEditDraft,
  feedArticlesQuery,
}: FeedDetailDialogProps) {
  return (
      <DialogSurface
        open={Boolean(editingFeed && feedEditDraft)}
        title={editingFeed?.name ?? 'Feed Details'}
        description="Update feed settings and review the latest ingested articles from this source."
        panelClassName="flex max-h-[90vh] max-w-5xl flex-col overflow-hidden"
        bodyClassName="mt-4 min-h-0 space-y-5 overflow-auto text-sm text-slate dark:text-white/75"
        footerClassName="mt-5 flex flex-wrap items-center justify-between gap-2"
        ariaBusy={updateFeedDetails.isPending}
        dismissDisabled={updateFeedDetails.isPending}
        onClose={closeFeedDetail}
        footer={
          <>
            <div className="min-h-5 text-xs">
              {feedEditValidation && <span className="text-red-600">{feedEditValidation}</span>}
              {!feedEditValidation && feedEditDirty && (
                <span className="text-amber-700 dark:text-amber-300">Unsaved feed edits.</span>
              )}
              {!feedEditValidation && !feedEditDirty && (
                <span className="text-slate dark:text-slate-300">No unsaved feed edits.</span>
              )}
            </div>
            <div className="flex flex-wrap justify-end gap-2">
              <button
                type="button"
                className="rounded border border-slate/20 px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-slate/5 dark:border-cyan-900/40 dark:text-slate-100 dark:hover:bg-white/[0.04]"
                onClick={closeFeedDetail}
                disabled={updateFeedDetails.isPending}
              >
                Cancel
              </button>
              {canManage && (
                <button
                  type="button"
                  className="rounded bg-ink px-3 py-2 text-sm font-semibold text-white hover:bg-slate-900 disabled:cursor-not-allowed disabled:opacity-60 dark:bg-cyan dark:text-[#053c2e] dark:hover:bg-cyan/90"
                  onClick={onSaveFeedDetail}
                  disabled={updateFeedDetails.isPending || Boolean(feedEditValidation) || !feedEditDirty}
                >
                  {updateFeedDetails.isPending ? 'Saving...' : 'Save feed'}
                </button>
              )}
            </div>
          </>
        }
      >
        {editingFeed && feedEditDraft && (
          <>
            <section className="space-y-3 rounded border border-slate/20 bg-slate/5 p-3 dark:border-cyan-900/40 dark:bg-white/[0.03]">
              <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_180px]">
                <div>
                  <label htmlFor="feed-edit-url" className="text-xs font-semibold uppercase text-slate dark:text-slate-300">
                    RSS URL
                  </label>
                  <input
                    id="feed-edit-url"
                    className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 font-mono text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                    value={feedEditDraft.url}
                    onChange={(event) => updateFeedEditDraft({ url: event.target.value })}
                    disabled={!canManage}
                    placeholder={editingFeed.has_unreadable_url ? 'Enter a replacement RSS URL' : 'https://example.com/feed.xml'}
                  />
                  {editingFeed.url.includes('REDACTED') && (
                    <p className="mt-1 text-xs text-amber-700 dark:text-amber-300">
                      Sensitive URL parts are redacted. Leave this field unchanged unless replacing the full feed URL.
                    </p>
                  )}
                </div>
                <label className="flex items-end gap-2 text-sm font-semibold text-slate dark:text-slate-200">
                  <input
                    type="checkbox"
                    checked={feedEditDraft.enabled}
                    onChange={(event) => updateFeedEditDraft({ enabled: event.target.checked })}
                    disabled={!canManage}
                  />
                  Enabled
                </label>
              </div>

              <div className="grid gap-3 md:grid-cols-2">
                <div>
                  <label htmlFor="feed-edit-name" className="text-xs font-semibold uppercase text-slate dark:text-slate-300">
                    Name
                  </label>
                  <input
                    id="feed-edit-name"
                    className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                    value={feedEditDraft.name}
                    onChange={(event) => updateFeedEditDraft({ name: event.target.value })}
                    disabled={!canManage}
                  />
                </div>
                <div>
                  <label htmlFor="feed-edit-site-url" className="text-xs font-semibold uppercase text-slate dark:text-slate-300">
                    Site URL
                  </label>
                  <input
                    id="feed-edit-site-url"
                    className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                    value={feedEditDraft.siteUrl}
                    onChange={(event) => updateFeedEditDraft({ siteUrl: event.target.value })}
                    disabled={!canManage}
                  />
                </div>
              </div>

              <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_140px]">
                <div>
                  <label htmlFor="feed-edit-description" className="text-xs font-semibold uppercase text-slate dark:text-slate-300">
                    Description
                  </label>
                  <textarea
                    id="feed-edit-description"
                    className="mt-1 h-20 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                    value={feedEditDraft.description}
                    onChange={(event) => updateFeedEditDraft({ description: event.target.value })}
                    disabled={!canManage}
                  />
                </div>
                <div>
                  <label htmlFor="feed-edit-language" className="text-xs font-semibold uppercase text-slate dark:text-slate-300">
                    Language
                  </label>
                  <input
                    id="feed-edit-language"
                    className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                    value={feedEditDraft.language}
                    onChange={(event) => updateFeedEditDraft({ language: event.target.value })}
                    disabled={!canManage}
                    placeholder="en-US"
                  />
                </div>
              </div>

              <div className="grid gap-3 md:grid-cols-[180px_1fr]">
                <div>
                  <label htmlFor="feed-edit-fetch-mode" className="text-xs font-semibold uppercase text-slate dark:text-slate-300">
                    Fetch Mode
                  </label>
                  <select
                    id="feed-edit-fetch-mode"
                    className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                    value={feedEditDraft.fetchMode}
                    onChange={(event) => {
                      const nextMode = event.target.value as FeedFetchMode
                      updateFeedEditDraft({
                        fetchMode: nextMode,
                        intervalSeconds: feedEditDraft.intervalSeconds || '1800',
                        scheduleCron:
                          nextMode === 'schedule'
                            ? feedEditDraft.scheduleCron || DEFAULT_SCHEDULE_CRON
                            : feedEditDraft.scheduleCron,
                      })
                    }}
                    disabled={!canManage}
                  >
                    <option value="interval">Interval</option>
                    <option value="schedule">Schedule</option>
                  </select>
                </div>

                {feedEditDraft.fetchMode === 'interval' ? (
                  <div>
                    <label htmlFor="feed-edit-interval" className="text-xs font-semibold uppercase text-slate dark:text-slate-300">
                      Fetch Interval
                    </label>
                    <div className="mt-1 flex items-center gap-2">
                      <input
                        id="feed-edit-interval"
                        className="w-36 rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                        type="number"
                        min={60}
                        value={feedEditDraft.intervalSeconds}
                        onChange={(event) => updateFeedEditDraft({ intervalSeconds: event.target.value })}
                        disabled={!canManage}
                      />
                      <span className="text-xs text-slate dark:text-slate-300">seconds</span>
                    </div>
                  </div>
                ) : (
                  <div>
                    <label htmlFor="feed-edit-cron" className="text-xs font-semibold uppercase text-slate dark:text-slate-300">
                      Cron Schedule
                    </label>
                    <input
                      id="feed-edit-cron"
                      className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 font-mono text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                      value={feedEditDraft.scheduleCron}
                      onChange={(event) => updateFeedEditDraft({ scheduleCron: event.target.value })}
                      disabled={!canManage}
                      placeholder="0 * * * *"
                    />
                  </div>
                )}
              </div>

              {updateFeedDetails.isError && (
                <p role="alert" aria-live="assertive" aria-atomic="true" className="text-sm text-red-600">
                  {resolveMutationError(updateFeedDetails.error, 'Feed details could not be updated')}
                </p>
              )}
            </section>

            <section aria-labelledby="feed-recent-articles-heading" className="space-y-2">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h4 id="feed-recent-articles-heading" className="font-display text-lg text-ink dark:text-white">
                  Recent Articles
                </h4>
                {feedArticlesQuery.data && (
                  <span className="text-xs text-slate dark:text-slate-300">
                    Showing {feedArticlesQuery.data.items.length} of {feedArticlesQuery.data.total}
                  </span>
                )}
              </div>

              {feedArticlesQuery.isLoading && <p className="text-sm text-slate dark:text-slate-300">Loading recent articles...</p>}
              {feedArticlesQuery.isError && (
                <p role="alert" aria-live="assertive" aria-atomic="true" className="text-sm text-red-600">
                  {resolveApiErrorMessage(feedArticlesQuery.error, 'Recent feed articles could not be loaded')}
                </p>
              )}
              {!feedArticlesQuery.isLoading && !feedArticlesQuery.isError && !feedArticlesQuery.data?.items.length && (
                <p className="rounded border border-slate/20 bg-slate/5 p-3 text-sm text-slate dark:border-cyan-900/40 dark:bg-white/[0.03] dark:text-slate-300">
                  No articles have been ingested from this feed yet.
                </p>
              )}
              <div className="space-y-2">
                {(feedArticlesQuery.data?.items ?? []).map((item) => (
                  <article key={item.id} className="rounded border border-slate/20 bg-white/70 p-3 dark:border-cyan-900/40 dark:bg-[#072019]">
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div className="min-w-0">
                        <a
                          className="break-words text-sm font-semibold text-ink hover:text-cyan dark:text-white dark:hover:text-cyan-100"
                          href={item.url}
                          target="_blank"
                          rel="noreferrer"
                        >
                          {item.title}
                        </a>
                        <p className="mt-1 text-xs text-slate dark:text-slate-300">
                          Published {formatDate(item.published_at)} · Seen {formatDate(item.first_seen_at)}
                        </p>
                      </div>
                      <span className="tl-chip">{item.status}</span>
                    </div>
                    {item.summary && <p className="mt-2 line-clamp-2 text-sm text-slate dark:text-slate-300">{item.summary}</p>}
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {item.classification && <span className="tl-chip tl-chip-info">{item.classification}</span>}
                      {item.tags.slice(0, 5).map((tag) => (
                        <span key={tag} className="tl-chip">
                          {tag}
                        </span>
                      ))}
                    </div>
                  </article>
                ))}
              </div>
            </section>
          </>
        )}
      </DialogSurface>

  )
}
