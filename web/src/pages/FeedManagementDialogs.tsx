import { ConfirmDialog } from '../components/ConfirmDialog'
import type { useFeedsPageController } from './useFeedsPageController'

type FeedManagementDialogsProps = Pick<
  ReturnType<typeof useFeedsPageController>,
  | 'pendingImportReview'
  | 'setPendingImportReview'
  | 'onConfirmImportReview'
  | 'importFeeds'
  | 'pendingBulkSetEnabled'
  | 'setPendingBulkSetEnabled'
  | 'onConfirmBulkSetEnabled'
  | 'bulkSetEnabled'
  | 'pendingDeleteFeed'
  | 'setPendingDeleteFeed'
  | 'onConfirmDeleteFeed'
  | 'deleteFeed'
  | 'pendingBulkDeleteFeeds'
  | 'setPendingBulkDeleteFeeds'
  | 'onConfirmBulkDeleteFeeds'
  | 'bulkDeleteFeeds'
>

export function FeedManagementDialogs({
  pendingImportReview,
  setPendingImportReview,
  onConfirmImportReview,
  importFeeds,
  pendingBulkSetEnabled,
  setPendingBulkSetEnabled,
  onConfirmBulkSetEnabled,
  bulkSetEnabled,
  pendingDeleteFeed,
  setPendingDeleteFeed,
  onConfirmDeleteFeed,
  deleteFeed,
  pendingBulkDeleteFeeds,
  setPendingBulkDeleteFeeds,
  onConfirmBulkDeleteFeeds,
  bulkDeleteFeeds,
}: FeedManagementDialogsProps) {
  return (
    <>
      <ConfirmDialog
        open={Boolean(pendingImportReview)}
        title={pendingImportReview?.overwriteCount ? 'Overwrite existing feeds from import?' : 'Run feed import?'}
        description="Review the import preflight summary before applying the file to this workspace."
        confirmLabel={pendingImportReview?.overwriteCount ? 'Run overwrite import' : 'Run import'}
        confirmTone="primary"
        onCancel={() => setPendingImportReview(null)}
        onConfirm={onConfirmImportReview}
        confirmDisabled={importFeeds.isPending || !pendingImportReview}
        isConfirming={importFeeds.isPending}
      >
        {pendingImportReview && (
          <div className="space-y-3">
            <div className="grid gap-2 text-sm sm:grid-cols-2">
              <p>
                <span className="font-semibold text-ink dark:text-white">{pendingImportReview.totalEntries}</span> file entr
                {pendingImportReview.totalEntries === 1 ? 'y' : 'ies'}
              </p>
              <p>
                <span className="font-semibold text-ink dark:text-white">{pendingImportReview.uniqueEntries}</span> unique URL
                {pendingImportReview.uniqueEntries === 1 ? '' : 's'}
              </p>
              <p>
                <span className="font-semibold text-emerald-700 dark:text-emerald-300">{pendingImportReview.createCount}</span>{' '}
                new feed{pendingImportReview.createCount === 1 ? '' : 's'}
              </p>
              <p>
                <span className="font-semibold text-amber-700 dark:text-amber-300">{pendingImportReview.overwriteCount}</span>{' '}
                feed{pendingImportReview.overwriteCount === 1 ? '' : 's'} overwritten
              </p>
              <p>
                <span className="font-semibold text-slate-700 dark:text-slate-200">{pendingImportReview.skipCount}</span>{' '}
                feed{pendingImportReview.skipCount === 1 ? '' : 's'} skipped
              </p>
              <p>
                <span className="font-semibold text-slate-700 dark:text-slate-200">{pendingImportReview.duplicateEntries}</span>{' '}
                duplicate entr{pendingImportReview.duplicateEntries === 1 ? 'y' : 'ies'}
              </p>
            </div>
            {pendingImportReview.matchingExistingFeeds.length > 0 && (
              <div className="max-h-48 overflow-auto rounded border border-slate/20 bg-slate/5 p-3 dark:border-cyan-900/40 dark:bg-white/[0.03]">
                <p className="mb-2 text-xs font-semibold uppercase text-slate dark:text-white/60">
                  Existing feeds in scope
                </p>
                <ul className="space-y-1">
                  {pendingImportReview.matchingExistingFeeds.map((feed) => (
                    <li key={feed.id} className="space-y-0.5">
                      <p className="text-sm font-semibold text-ink dark:text-white">{feed.name}</p>
                      <p className="break-all font-mono text-[11px] text-slate dark:text-white/65">
                        {feed.url.trim() || 'URL unavailable until the original encryption key is restored.'}
                      </p>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </ConfirmDialog>

      <ConfirmDialog
        open={Boolean(pendingBulkSetEnabled?.feeds.length)}
        title={pendingBulkSetEnabled?.enabled ? 'Enable filtered feeds?' : 'Disable filtered feeds?'}
        description="Review the feeds in the current filtered view before applying this bulk status change."
        confirmLabel={pendingBulkSetEnabled?.enabled ? 'Enable feeds' : 'Disable feeds'}
        onCancel={() => setPendingBulkSetEnabled(null)}
        onConfirm={onConfirmBulkSetEnabled}
        confirmDisabled={bulkSetEnabled.isPending || !pendingBulkSetEnabled?.feeds.length}
        isConfirming={bulkSetEnabled.isPending}
      >
        {pendingBulkSetEnabled && (
          <div className="space-y-3">
            <p>
              You are about to {pendingBulkSetEnabled.enabled ? 'enable' : 'disable'}{' '}
              <span className="font-semibold text-ink dark:text-white">{pendingBulkSetEnabled.feeds.length}</span> filtered
              feed{pendingBulkSetEnabled.feeds.length === 1 ? '' : 's'}.
            </p>
            <div className="max-h-48 overflow-auto rounded border border-slate/20 bg-slate/5 p-3 dark:border-cyan-900/40 dark:bg-white/[0.03]">
              <ul className="space-y-1">
                {pendingBulkSetEnabled.feeds.map((feed) => (
                  <li key={feed.id} className="break-all font-mono text-xs text-slate-700 dark:text-white/70">
                    {feed.name}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        )}
      </ConfirmDialog>

      <ConfirmDialog
        open={Boolean(pendingDeleteFeed)}
        title="Delete feed?"
        description="This removes the feed, its related items, and its fetch history."
        confirmLabel="Delete feed"
        onCancel={() => setPendingDeleteFeed(null)}
        onConfirm={onConfirmDeleteFeed}
        confirmDisabled={deleteFeed.isPending}
        isConfirming={deleteFeed.isPending}
      >
        {pendingDeleteFeed && (
          <div className="space-y-2">
            <p className="font-semibold text-ink dark:text-white">{pendingDeleteFeed.name}</p>
            <p className="break-all font-mono text-xs text-slate dark:text-white/65">
              {pendingDeleteFeed.url.trim() || 'URL unavailable until the original encryption key is restored.'}
            </p>
          </div>
        )}
      </ConfirmDialog>

      <ConfirmDialog
        open={Boolean(pendingBulkDeleteFeeds?.feeds.length)}
        title={pendingBulkDeleteFeeds?.kind === 'broken' ? 'Delete broken feeds?' : 'Delete filtered disabled feeds?'}
        description={
          pendingBulkDeleteFeeds?.kind === 'broken'
            ? 'This permanently removes feeds whose stored URLs can no longer be decrypted.'
            : 'This permanently removes every disabled feed in the current filtered view.'
        }
        confirmLabel="Delete feeds"
        onCancel={() => setPendingBulkDeleteFeeds(null)}
        onConfirm={onConfirmBulkDeleteFeeds}
        confirmDisabled={bulkDeleteFeeds.isPending}
        isConfirming={bulkDeleteFeeds.isPending}
      >
        {pendingBulkDeleteFeeds && (
          <div className="space-y-3">
            <p>
              You are about to delete{' '}
              <span className="font-semibold text-ink dark:text-white">{pendingBulkDeleteFeeds.feeds.length}</span>{' '}
              {pendingBulkDeleteFeeds.kind === 'broken' ? 'broken' : 'disabled'} feed
              {pendingBulkDeleteFeeds.feeds.length === 1 ? '' : 's'} from this view.
            </p>
            <div className="max-h-48 overflow-auto rounded border border-slate/20 bg-slate/5 p-3 dark:border-cyan-900/40 dark:bg-white/[0.03]">
              <ul className="space-y-1">
                {pendingBulkDeleteFeeds.feeds.map((feed) => (
                  <li key={feed.id} className="break-all font-mono text-xs text-slate-700 dark:text-white/70">
                    {feed.name}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        )}
      </ConfirmDialog>

    </>
  )
}
