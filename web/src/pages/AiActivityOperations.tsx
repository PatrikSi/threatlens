import type { Dispatch, SetStateAction } from 'react'

import type { AILiveStatusResponse, AITaskRunResponse, Feed, ItemListEntry } from '../types/api'
import type { AIReprocessScopeValidation } from './aiReprocessQueueState'
import { EmptyInline, Field, MiniStat, Panel, ProgressBar, StatusPill } from './aiSettingsSupport'
import {
  canCancelRun,
  cancelActionLabel,
  describeRunScope,
  formatAgeSeconds,
  formatRunTaskLabel,
  formatStatusLabel,
  formatTimestamp,
  formatTriggerLabel,
  remainingCount,
  shouldUseLookbackWindow,
  statusTone,
  truncate,
} from './aiSettingsUtils'

export function ActiveTasksPanel({
  runs,
  live,
  isLoading,
  isRefreshing,
  errorMessage,
  onOpenRun,
  onCancelRun,
  cancelingRunId,
}: {
  runs: AITaskRunResponse[]
  live: AILiveStatusResponse | undefined
  isLoading: boolean
  isRefreshing: boolean
  errorMessage: string
  onOpenRun: (runId: string) => void
  onCancelRun: (run: AITaskRunResponse) => void
  cancelingRunId: string | null
}) {
  return (
    <Panel title="Active tasks" subtitle="Queued and running top-level AI work plus the current worker queue snapshot.">
      <div aria-busy={isLoading || isRefreshing}>
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
          <MiniStat label="Workers" value={live?.worker_count ?? 0} />
          <MiniStat label="Active" value={live?.active_count ?? 0} />
          <MiniStat label="Reserved" value={live?.reserved_count ?? 0} />
          <MiniStat label="Scheduled" value={live?.scheduled_count ?? 0} />
          <MiniStat
            label="Oldest queued"
            value={live?.oldest_queued_age_seconds != null ? formatAgeSeconds(live.oldest_queued_age_seconds) : 'n/a'}
          />
        </div>

        <div className="mt-4 space-y-3">
          {errorMessage && (
            <p className="rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-sm text-red-700 dark:text-red-300">
              {errorMessage}
            </p>
          )}
          {isLoading && !runs.length && (
            <div className="rounded-xl border border-slate/20 bg-white/70 p-4 text-sm text-slate dark:border-cyan-900/40 dark:bg-[#072019]/80 dark:text-white/70">
              Checking queued and running AI tasks...
            </div>
          )}
          {runs.map((run) => (
            <div
              key={run.id}
              className="rounded-xl border border-slate/20 bg-white/70 p-3 dark:border-cyan-900/40 dark:bg-[#072019]/80"
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="text-sm font-semibold">{formatRunTaskLabel(run)}</p>
                    <StatusPill tone={statusTone(run.status)} label={formatStatusLabel(run.status, run.reason)} />
                  </div>
                  <p className="mt-1 text-xs text-slate dark:text-white/65">
                    {formatTriggerLabel(run.trigger_source)} · queued {formatTimestamp(run.queued_at)}
                    {run.worker_name ? ` · ${run.worker_name}` : ''}
                    {run.model ? ` · ${run.model}` : ''}
                  </p>
                  <p className="mt-2 text-sm text-slate dark:text-white/70">{describeRunScope(run)}</p>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    className="rounded border border-slate/30 px-3 py-2 text-xs font-semibold dark:border-cyan-900/40"
                    onClick={() => onOpenRun(run.id)}
                  >
                    Open Run
                  </button>
                  {canCancelRun(run) && (
                    <button
                      type="button"
                      className="rounded border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs font-semibold text-red-700 disabled:opacity-50 dark:text-red-300"
                      onClick={() => onCancelRun(run)}
                      disabled={cancelingRunId === run.id}
                    >
                      {cancelingRunId === run.id ? 'Working...' : cancelActionLabel(run)}
                    </button>
                  )}
                </div>
              </div>
              {run.task_type === 'reprocess' && (
                <div className="mt-3">
                  <ProgressBar value={run.processed_count} max={run.target_count || Math.max(run.processed_count, 1)} />
                  <p className="mt-2 text-xs text-slate dark:text-white/60">
                    Processed {run.processed_count}/{run.target_count ?? '?'} · Success {run.success_count} · Errors{' '}
                    {run.error_count} · Skipped {run.skipped_count} · Remaining {remainingCount(run)}
                  </p>
                </div>
              )}
            </div>
          ))}
          {!isLoading && !errorMessage && !runs.length && (
            <EmptyInline>No queued or running top-level AI tasks right now.</EmptyInline>
          )}
        </div>
      </div>
    </Panel>
  )
}

export type QueueWorkPanelProps = {
  dailyBriefEnabled: boolean
  dailyBriefDays: string
  setDailyBriefDays: Dispatch<SetStateAction<string>>
  dailyBriefPending: boolean
  dailyBriefValidation: string | null
  retainedDailyBriefLimit: number | null
  onQueueDailyBrief: () => void
  reprocessDays: string
  setReprocessDays: Dispatch<SetStateAction<string>>
  reprocessLimit: string
  setReprocessLimit: Dispatch<SetStateAction<string>>
  reprocessStartTime: string
  setReprocessStartTime: Dispatch<SetStateAction<string>>
  reprocessEndTime: string
  setReprocessEndTime: Dispatch<SetStateAction<string>>
  feeds: Feed[]
  selectedFeedIds: string[]
  setSelectedFeedIds: Dispatch<SetStateAction<string[]>>
  itemSearch: string
  setItemSearch: Dispatch<SetStateAction<string>>
  candidateItems: ItemListEntry[]
  selectedItems: ItemListEntry[]
  onAddItem: (item: ItemListEntry) => void
  onRemoveItem: (itemId: string) => void
  onClearScope: () => void
  reprocessPending: boolean
  reprocessValidation: AIReprocessScopeValidation
  reprocessQueueDisabled: boolean
  queueWorkBlockedReason: string | null
  onQueueReprocess: () => void
  itemSearchLoading: boolean
  itemSearchError: string
  itemSearchReady: boolean
}

export function QueueWorkPanel(props: QueueWorkPanelProps) {
  const {
    dailyBriefEnabled,
    dailyBriefDays,
    setDailyBriefDays,
    dailyBriefPending,
    dailyBriefValidation,
    retainedDailyBriefLimit,
    onQueueDailyBrief,
    reprocessDays,
    setReprocessDays,
    reprocessLimit,
    setReprocessLimit,
    reprocessStartTime,
    setReprocessStartTime,
    reprocessEndTime,
    setReprocessEndTime,
    feeds,
    selectedFeedIds,
    setSelectedFeedIds,
    itemSearch,
    setItemSearch,
    candidateItems,
    selectedItems,
    onAddItem,
    onRemoveItem,
    onClearScope,
    reprocessPending,
    reprocessValidation,
    reprocessQueueDisabled,
    queueWorkBlockedReason,
    onQueueReprocess,
    itemSearchLoading,
    itemSearchError,
    itemSearchReady,
  } = props
  const usingExplicitScope = !shouldUseLookbackWindow(reprocessStartTime, reprocessEndTime, selectedItems)
  const hasReprocessValidationError = Boolean(
    reprocessValidation.days ||
      reprocessValidation.limit ||
      reprocessValidation.timeRange ||
      reprocessValidation.itemSelection,
  )

  return (
    <Panel title="Queue AI work" subtitle="Launch daily brief and reprocess jobs from one place, with optional feed, time, and item targeting.">
      <div className="space-y-4">
        {queueWorkBlockedReason && (
          <div
            role="status"
            aria-live="polite"
            aria-atomic="true"
            className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-900/50 dark:bg-amber-950/35 dark:text-amber-200"
          >
            {queueWorkBlockedReason}
          </div>
        )}
        <div className="rounded-xl border border-slate/20 bg-white/70 p-4 dark:border-cyan-900/40 dark:bg-[#072019]/80">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="text-sm font-semibold">Daily brief</p>
              <p className="mt-1 text-sm text-slate dark:text-white/70">
                Reprocess daily briefs for the last X days, ending today. The batch runs sequentially so local models are not flooded.
              </p>
            </div>
            <button
              type="button"
              className="rounded border border-slate/30 px-3 py-2 text-sm font-semibold disabled:opacity-50 dark:border-cyan-900/40"
              onClick={onQueueDailyBrief}
              disabled={dailyBriefPending || !dailyBriefEnabled || Boolean(dailyBriefValidation) || Boolean(queueWorkBlockedReason)}
            >
              {dailyBriefPending ? 'Queueing...' : 'Queue daily brief'}
            </button>
          </div>
          <div className="mt-4 grid gap-3 md:grid-cols-[minmax(0,220px)_minmax(0,1fr)]">
            <Field label="Daily brief lookback (days)">
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#041612]/90"
                value={dailyBriefDays}
                onChange={(event) => setDailyBriefDays(event.target.value)}
                inputMode="numeric"
                aria-invalid={Boolean(dailyBriefValidation)}
              />
              {dailyBriefValidation && <p className="mt-1 text-xs text-red-600">{dailyBriefValidation}</p>}
            </Field>
            <div className="rounded-lg border border-slate/15 bg-slate/5 px-3 py-2 text-xs text-slate dark:border-cyan-900/30 dark:bg-white/[0.03] dark:text-white/60">
              {retainedDailyBriefLimit == null
                ? 'Retention limit is still loading. Increase retained daily briefings in Configuration before queueing a larger daily brief range.'
                : `Retention allows ${retainedDailyBriefLimit} brief${retainedDailyBriefLimit === 1 ? '' : 's'}. Increase retained daily briefings in Configuration before queueing a larger daily brief range.`}
            </div>
          </div>
        </div>

        <div className="rounded-xl border border-slate/20 bg-white/70 p-4 dark:border-cyan-900/40 dark:bg-[#072019]/80">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="text-sm font-semibold">Reprocess scope</p>
              <p className="mt-1 text-sm text-slate dark:text-white/70">
                Use a recent lookback, narrow it to feeds or a time range, or select exact articles to re-enrich.
              </p>
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                className="rounded border border-slate/30 px-3 py-2 text-sm font-semibold dark:border-cyan-900/40"
                onClick={onClearScope}
              >
                Reset scope
              </button>
              <button
                type="button"
                className="rounded bg-ink px-3 py-2 text-sm font-semibold text-white disabled:opacity-50 dark:bg-cyan dark:text-slate-950"
                onClick={onQueueReprocess}
                disabled={reprocessPending || reprocessQueueDisabled || Boolean(queueWorkBlockedReason)}
              >
                {reprocessPending ? 'Queueing...' : 'Queue reprocess'}
              </button>
            </div>
          </div>

          <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <Field label="Reprocess lookback (days)">
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#041612]/90"
                value={reprocessDays}
                onChange={(event) => setReprocessDays(event.target.value)}
                inputMode="numeric"
              />
              {reprocessValidation.days && <p className="mt-1 text-xs text-red-600">{reprocessValidation.days}</p>}
            </Field>
            <Field label="Maximum articles">
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#041612]/90"
                value={reprocessLimit}
                onChange={(event) => setReprocessLimit(event.target.value)}
                inputMode="numeric"
              />
              {reprocessValidation.limit && <p className="mt-1 text-xs text-red-600">{reprocessValidation.limit}</p>}
            </Field>
            <Field label="Start time">
              <input
                type="datetime-local"
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#041612]/90"
                value={reprocessStartTime}
                onChange={(event) => setReprocessStartTime(event.target.value)}
              />
            </Field>
            <Field label="End time">
              <input
                type="datetime-local"
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#041612]/90"
                value={reprocessEndTime}
                onChange={(event) => setReprocessEndTime(event.target.value)}
              />
            </Field>
          </div>
          <div className="space-y-1">
            <p className="text-xs text-slate dark:text-white/60">
              Blank or <code>0</code> values are rejected so the queued job cannot widen beyond the scope you review here.
            </p>
            {reprocessValidation.timeRange && <p className="text-xs text-red-600">{reprocessValidation.timeRange}</p>}
            {!hasReprocessValidationError && (
              <p className="text-xs text-slate dark:text-white/60">
                {usingExplicitScope
                  ? 'Explicit time or article scope is active, so lookback days are ignored for this run.'
                  : 'Lookback days use article publication time, falling back to first-seen time only when a feed has no publication date.'}
              </p>
            )}
          </div>

          <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,280px)_minmax(0,1fr)]">
            <FeedScope
              feeds={feeds}
              selectedFeedIds={selectedFeedIds}
              setSelectedFeedIds={setSelectedFeedIds}
            />
            <ArticleScope
              usingExplicitScope={usingExplicitScope}
              itemSearch={itemSearch}
              setItemSearch={setItemSearch}
              candidateItems={candidateItems}
              selectedItems={selectedItems}
              onAddItem={onAddItem}
              onRemoveItem={onRemoveItem}
              validationMessage={reprocessValidation.itemSelection}
              isLoading={itemSearchLoading}
              errorMessage={itemSearchError}
              isReady={itemSearchReady}
            />
          </div>

          <p className="mt-4 text-xs text-slate dark:text-white/60">
            Selected articles override the lookback window. Without selected articles, ThreatLens uses the time range and feed
            filters against the last X articles by publication time, falling back to first-seen time for undated feed items.
          </p>
        </div>
      </div>
    </Panel>
  )
}

function FeedScope({
  feeds,
  selectedFeedIds,
  setSelectedFeedIds,
}: Pick<QueueWorkPanelProps, 'feeds' | 'selectedFeedIds' | 'setSelectedFeedIds'>) {
  return (
    <div>
      <p className="text-xs font-semibold uppercase text-slate dark:text-white/55">Feeds</p>
      <div className="mt-2 max-h-56 space-y-2 overflow-y-auto rounded-lg border border-slate/15 bg-slate/5 p-2 dark:border-cyan-900/30 dark:bg-white/[0.03]">
        {feeds.map((feed) => (
          <label
            key={feed.id}
            className="flex items-start gap-2 rounded border border-transparent px-2 py-2 text-sm transition hover:border-slate/15 dark:hover:border-cyan-900/30"
          >
            <input
              type="checkbox"
              checked={selectedFeedIds.includes(feed.id)}
              onChange={(event) =>
                setSelectedFeedIds((current) =>
                  event.target.checked ? [...current, feed.id] : current.filter((candidateId) => candidateId !== feed.id),
                )
              }
            />
            <span>
              <span className="block font-semibold">{feed.name}</span>
              <span className="block text-xs text-slate dark:text-white/60">{feed.url}</span>
            </span>
          </label>
        ))}
        {!feeds.length && <EmptyInline>No feeds available to scope.</EmptyInline>}
      </div>
    </div>
  )
}

type ArticleScopeProps = Pick<
  QueueWorkPanelProps,
  'itemSearch' | 'setItemSearch' | 'candidateItems' | 'selectedItems' | 'onAddItem' | 'onRemoveItem'
> & {
  usingExplicitScope: boolean
  validationMessage: string | null
  isLoading: boolean
  errorMessage: string
  isReady: boolean
}

function ArticleScope(props: ArticleScopeProps) {
  const {
    usingExplicitScope,
    itemSearch,
    setItemSearch,
    candidateItems,
    selectedItems,
    onAddItem,
    onRemoveItem,
    validationMessage,
    isLoading,
    errorMessage,
    isReady,
  } = props
  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs font-semibold uppercase text-slate dark:text-white/55">Specific Articles</p>
        <span className="text-xs text-slate dark:text-white/60">
          {selectedItems.length
            ? `${selectedItems.length} selected article${selectedItems.length === 1 ? '' : 's'}`
            : usingExplicitScope
              ? 'Explicit scope active'
              : 'Using lookback window'}
        </span>
      </div>
      <input
        className="mt-2 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#041612]/90"
        value={itemSearch}
        onChange={(event) => setItemSearch(event.target.value)}
        placeholder="Search items by title, summary, or URL"
        aria-invalid={Boolean(validationMessage)}
      />
      {validationMessage && <p className="mt-1 text-xs text-red-600">{validationMessage}</p>}

      {selectedItems.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2">
          {selectedItems.map((item) => (
            <button
              key={item.id}
              type="button"
              className="rounded-full border border-cyan/20 bg-cyan/10 px-3 py-1 text-left text-xs text-cyan-900 dark:border-cyan/30 dark:text-cyan-100"
              onClick={() => onRemoveItem(item.id)}
            >
              {truncate(item.title, 56)} · remove
            </button>
          ))}
        </div>
      )}

      <div className="mt-3 max-h-72 space-y-2 overflow-y-auto">
        {!isReady && <EmptyInline>Search by title, summary, or URL, or narrow by feed/time to preview matching articles.</EmptyInline>}
        {isReady && isLoading && <EmptyInline>Loading matching items...</EmptyInline>}
        {isReady &&
          !isLoading &&
          candidateItems.map((item) => (
            <button
              key={item.id}
              type="button"
              className="w-full rounded-lg border border-slate/15 bg-white/80 px-3 py-3 text-left transition hover:border-cyan/30 dark:border-cyan-900/30 dark:bg-[#041612]/90"
              onClick={() => onAddItem(item)}
            >
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="text-sm font-semibold">{item.title}</p>
                  <p className="mt-1 text-xs text-slate dark:text-white/60">
                    {item.feed_name}
                    {item.published_at ? ` · published ${formatTimestamp(item.published_at)}` : ''}
                    {item.first_seen_at ? ` · first seen ${formatTimestamp(item.first_seen_at)}` : ''}
                  </p>
                </div>
                <span className="rounded-full border border-slate/20 px-2 py-1 text-[11px] font-semibold uppercase text-slate dark:border-cyan-900/40 dark:text-white/65">
                  Add
                </span>
              </div>
            </button>
          ))}
        {isReady && !isLoading && !candidateItems.length && !errorMessage && (
          <EmptyInline>No recent items matched the current scope.</EmptyInline>
        )}
        {isReady && errorMessage && <p className="text-sm text-red-600">{errorMessage}</p>}
      </div>
    </div>
  )
}
