import {
  type Dispatch,
  type RefObject,
  type SetStateAction,
  useEffect,
  useMemo,
  useState,
} from 'react'
import { useQuery } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
import { DialogSurface } from '../components/ConfirmDialog'
import {
  AIDailyBriefSourceItemResponse,
  AILiveStatusResponse,
  AITaskEventResponse,
  AITaskRunDetailResponse,
  AITaskRunListResponse,
  AITaskRunResponse,
  Feed,
  ItemListEntry,
} from '../types/api'
import { AIReprocessScopeValidation } from './aiReprocessQueueState'
import { AiRunMobileList } from './AiRunMobileCard'
import {
  EmptyInline,
  Field,
  Metric,
  MiniStat,
  OverviewSection,
  Panel,
  ProgressBar,
  StatusPill,
} from './aiSettingsSupport'
import {
  AI_RUN_PAGE_SIZE,
  canCancelRun,
  canInspectProviderExchange,
  cancelActionLabel,
  describeRunScope,
  findLatestProviderExchangeEvent,
  formatAgeSeconds,
  formatDailyBriefChildRunMeta,
  formatDailyBriefChildRunTitle,
  formatDebugPayload,
  formatDuration,
  formatMetadataValue,
  formatRunSelectionLabel,
  formatRunTaskLabel,
  formatStatusLabel,
  formatTimestamp,
  formatTriggerLabel,
  humanizeKey,
  isDailyBriefBackfillRun,
  remainingCount,
  shouldUseLookbackWindow,
  statusTone,
  truncate,
} from './aiSettingsUtils'

export type RunFilters = {
  taskType: string
  status: string
  triggerSource: string
  onlyFailures: boolean
}

function ActiveTasksPanel({
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
    <Panel title="Active Tasks" subtitle="Queued and running top-level AI work plus the current Celery queue snapshot.">
      <div aria-busy={isLoading || isRefreshing}>
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
          <MiniStat label="Workers" value={live?.worker_count ?? 0} />
          <MiniStat label="Active" value={live?.active_count ?? 0} />
          <MiniStat label="Reserved" value={live?.reserved_count ?? 0} />
          <MiniStat label="Scheduled" value={live?.scheduled_count ?? 0} />
          <MiniStat
            label="Oldest Queued"
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
                  <ProgressBar
                    value={run.processed_count}
                    max={run.target_count || Math.max(run.processed_count, 1)}
                  />
                  <p className="mt-2 text-xs text-slate dark:text-white/60">
                    Processed {run.processed_count}/{run.target_count ?? '?'} · Success {run.success_count} · Errors{' '}
                    {run.error_count} · Skipped {run.skipped_count} · Remaining {remainingCount(run)}
                  </p>
                </div>
              )}
            </div>
          ))}
          {!isLoading && !errorMessage && !runs.length && <EmptyInline>No queued or running top-level AI tasks right now.</EmptyInline>}
        </div>
      </div>
    </Panel>
  )
}

function QueueWorkPanel({
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
}: {
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
}) {
  const usingExplicitScope = !shouldUseLookbackWindow(reprocessStartTime, reprocessEndTime, selectedItems)
  const hasReprocessValidationError = Boolean(
    reprocessValidation.days ||
      reprocessValidation.limit ||
      reprocessValidation.timeRange ||
      reprocessValidation.itemSelection,
  )

  return (
    <Panel title="Queue AI Work" subtitle="Launch daily brief and reprocess jobs from one place, with optional feed, time, and item targeting.">
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
              <p className="text-sm font-semibold">Daily Brief</p>
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
              {dailyBriefPending ? 'Queueing...' : 'Queue Daily Brief'}
            </button>
          </div>
          <div className="mt-4 grid gap-3 md:grid-cols-[minmax(0,220px)_minmax(0,1fr)]">
            <Field label="Last X Days">
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
              <p className="text-sm font-semibold">Reprocess Scope</p>
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
                Clear Scope
              </button>
              <button
                type="button"
                className="rounded bg-ink px-3 py-2 text-sm font-semibold text-white disabled:opacity-50 dark:bg-cyan dark:text-slate-950"
                onClick={onQueueReprocess}
                disabled={reprocessPending || reprocessQueueDisabled || Boolean(queueWorkBlockedReason)}
              >
                {reprocessPending ? 'Queueing...' : 'Queue Reprocess'}
              </button>
            </div>
          </div>

          <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <Field label="Lookback Days">
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#041612]/90"
                value={reprocessDays}
                onChange={(event) => setReprocessDays(event.target.value)}
                inputMode="numeric"
              />
              {reprocessValidation.days && <p className="mt-1 text-xs text-red-600">{reprocessValidation.days}</p>}
            </Field>
            <Field label="Last X Articles">
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#041612]/90"
                value={reprocessLimit}
                onChange={(event) => setReprocessLimit(event.target.value)}
                inputMode="numeric"
              />
              {reprocessValidation.limit && <p className="mt-1 text-xs text-red-600">{reprocessValidation.limit}</p>}
            </Field>
            <Field label="Start Time">
              <input
                type="datetime-local"
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#041612]/90"
                value={reprocessStartTime}
                onChange={(event) => setReprocessStartTime(event.target.value)}
              />
            </Field>
            <Field label="End Time">
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
                          event.target.checked
                            ? [...current, feed.id]
                            : current.filter((candidateId) => candidateId !== feed.id),
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
                aria-invalid={Boolean(reprocessValidation.itemSelection)}
              />
              {reprocessValidation.itemSelection && (
                <p className="mt-1 text-xs text-red-600">{reprocessValidation.itemSelection}</p>
              )}

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
                {!itemSearchReady && (
                  <EmptyInline>Search by title, summary, or URL, or narrow by feed/time to preview matching articles.</EmptyInline>
                )}
                {itemSearchReady && itemSearchLoading && <EmptyInline>Loading matching items...</EmptyInline>}
                {itemSearchReady &&
                  !itemSearchLoading &&
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
                {itemSearchReady && !itemSearchLoading && !candidateItems.length && !itemSearchError && (
                  <EmptyInline>No recent items matched the current scope.</EmptyInline>
                )}
                {itemSearchReady && itemSearchError && <p className="text-sm text-red-600">Failed to load items. {itemSearchError}</p>}
              </div>
            </div>
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

function RunArticlesSection({
  parentRun,
  childRunsQuery,
  visibleCount,
  onInspectRun,
  onShowMore,
  onShowLess,
}: {
  parentRun: AITaskRunResponse
  childRunsQuery: ReturnType<typeof useQuery<AITaskRunListResponse>>
  visibleCount: number
  onInspectRun: (runId: string) => void
  onShowMore: () => void
  onShowLess: () => void
}) {
  const childRuns = childRunsQuery.data?.items ?? []
  const totalChildRuns = childRunsQuery.data?.total ?? 0
  const canShowMore = totalChildRuns > childRuns.length
  const canShowLess = visibleCount > 8 && childRuns.length > 8
  const isBackfill = isDailyBriefBackfillRun(parentRun)
  const sectionTitle = isBackfill ? 'Daily Brief Runs' : 'Article Runs'
  const childRunNounPlural = isBackfill ? 'daily brief runs' : 'article runs'
  const targetNoun = isBackfill ? 'day' : 'article'
  const targetNounPlural = isBackfill ? 'days' : 'articles'

  return (
    <div>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase text-slate dark:text-white/55">{sectionTitle}</p>
          <p className="mt-1 text-xs text-slate dark:text-white/60">
            {totalChildRuns
              ? `Showing ${childRuns.length} of ${totalChildRuns} queued ${childRunNounPlural}${parentRun.target_count ? ` out of ${parentRun.target_count} target ${targetNounPlural}` : ''}.`
              : parentRun.target_count
                ? `No child ${childRunNounPlural} are visible yet. Target size: ${parentRun.target_count} ${targetNoun}${parentRun.target_count === 1 ? '' : 's'}.`
                : `No child ${childRunNounPlural} are visible yet.`}
          </p>
        </div>
        <div className="flex flex-wrap gap-2 text-xs">
          <StatusPill tone="success" label={`Ready ${parentRun.success_count}`} />
          <StatusPill tone="danger" label={`Errors ${parentRun.error_count}`} />
          <StatusPill tone="neutral" label={`Skipped ${parentRun.skipped_count}`} />
          <StatusPill tone="info" label={`Remaining ${remainingCount(parentRun)}`} />
        </div>
      </div>

      {childRunsQuery.isLoading && !childRuns.length && (
        <p className="mt-3 text-sm text-slate dark:text-white/70">Loading {childRunNounPlural}...</p>
      )}
      {childRunsQuery.isError && (
        <p className="mt-3 text-sm text-red-600">
          Failed to load {childRunNounPlural}. {(childRunsQuery.error as Error | undefined)?.message ?? ''}
        </p>
      )}

      {!childRunsQuery.isLoading && !childRuns.length && !childRunsQuery.isError && (
        <EmptyInline>Child {childRunNounPlural} have not been queued yet.</EmptyInline>
      )}

      {!!childRuns.length && (
        <div className={`mt-3 space-y-2 ${visibleCount > 8 ? 'max-h-96 overflow-y-auto pr-1' : ''}`}>
          {childRuns.map((run) => (
            <div
              key={run.id}
              className="rounded-lg border border-slate/10 px-3 py-3 text-sm dark:border-cyan-900/30"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="font-semibold">
                    {isBackfill ? formatDailyBriefChildRunTitle(run) : run.item_title || run.item_id || 'Unknown article'}
                  </p>
                  <p className="mt-1 text-xs text-slate dark:text-white/60">
                    {isBackfill ? (
                      formatDailyBriefChildRunMeta(run)
                    ) : (
                      <>
                        {run.feed_name || 'Unknown feed'}
                        {run.item_published_at ? ` · published ${formatTimestamp(run.item_published_at)}` : ''}
                        {run.item_first_seen_at ? ` · first seen ${formatTimestamp(run.item_first_seen_at)}` : ''}
                      </>
                    )}
                  </p>
                </div>
                <StatusPill tone={statusTone(run.status)} label={formatStatusLabel(run.status, run.reason)} />
              </div>
              <p className="mt-2 text-xs text-slate dark:text-white/60">
                Queued {formatTimestamp(run.queued_at)}
                {run.started_at ? ` · started ${formatTimestamp(run.started_at)}` : ''}
                {run.finished_at ? ` · finished ${formatTimestamp(run.finished_at)}` : ''}
                {run.duration_ms != null ? ` · ${formatDuration(run.duration_ms)}` : ''}
                {run.total_tokens != null ? ` · ${run.total_tokens.toLocaleString()} tokens` : ''}
              </p>
              {(run.error || run.reason) && (
                <p className="mt-2 text-xs text-slate dark:text-white/70">{run.error || run.reason}</p>
              )}
              {canInspectProviderExchange(run) && (
                <div className="mt-3">
                  <button
                    type="button"
                    className="rounded border border-slate/30 px-3 py-2 text-xs font-semibold dark:border-cyan-900/40"
                    onClick={() => onInspectRun(run.id)}
                  >
                    View Request / Response
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {(canShowMore || canShowLess) && (
        <div className="mt-3 flex flex-wrap gap-2">
          {canShowMore && (
            <button
              type="button"
              className="rounded border border-slate/30 px-3 py-2 text-xs font-semibold dark:border-cyan-900/40"
              onClick={onShowMore}
            >
              Show {Math.min(20, totalChildRuns - childRuns.length)} More
            </button>
          )}
          {canShowLess && (
            <button
              type="button"
              className="rounded border border-slate/30 px-3 py-2 text-xs font-semibold dark:border-cyan-900/40"
              onClick={onShowLess}
            >
              Show Less
            </button>
          )}
        </div>
      )}
    </div>
  )
}

function ProviderExchangeModal({
  run,
  event,
  isLoading,
  errorMessage,
  onClose,
}: {
  run: AITaskRunResponse | null
  event: AITaskEventResponse | null
  isLoading: boolean
  errorMessage: string
  onClose: () => void
}) {
  if (!run && !isLoading && !errorMessage) {
    return null
  }

  const payload = event?.payload ?? {}
  const requestPayload = payload.request_payload
  const requestUrl = typeof event?.payload?.request_url === 'string' ? event.payload.request_url : null
  const responseBody = typeof payload.response_body === 'string' ? payload.response_body : null
  const responseJson = payload.response_json
  const responseJsonSummary = payload.response_json_summary
  const statusCode = typeof payload.status_code === 'number' ? payload.status_code : null
  const requestSummary = buildProviderRequestSummary(payload)
  const responseSummary = buildProviderResponseSummary(payload)

  return (
    <DialogSurface
      open
      title="Provider Exchange"
      description={run ? `${formatRunTaskLabel(run)}${run.item_title ? ` · ${run.item_title}` : ''}` : 'Loading run detail'}
      onClose={onClose}
      panelClassName="max-h-[85vh] max-w-5xl overflow-y-auto"
      bodyClassName="mt-4 space-y-4 text-sm text-slate dark:text-white/75"
    >
      {isLoading && <p>Loading request/response details...</p>}
      {!isLoading && errorMessage && <p className="text-red-600">Failed to load run detail. {errorMessage}</p>}
      {!isLoading && !errorMessage && !event && (
        <p>No provider request/response was captured for this run.</p>
      )}

      {!isLoading && !errorMessage && event && (
        <div className="space-y-4">
          <div className="grid gap-3 md:grid-cols-3">
            <MiniStat label="Event" value={event.event_type} />
            <MiniStat label="Captured" value={formatTimestamp(event.created_at)} />
            <MiniStat label="HTTP Status" value={statusCode ?? 'n/a'} />
          </div>

          {event.message && (
            <div className="rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-sm text-red-700 dark:text-red-300">
              {event.message}
            </div>
          )}

          <div className="grid gap-4 xl:grid-cols-2">
            <Panel title="Request">
              {requestUrl && <p className="mb-3 break-all text-xs text-slate dark:text-white/60">{requestUrl}</p>}
              {requestPayload != null ? (
                <pre className="overflow-x-auto rounded-lg border border-slate/15 bg-slate/5 p-3 text-xs dark:border-cyan-900/30 dark:bg-white/[0.03]">
                  {formatDebugPayload(requestPayload)}
                </pre>
              ) : (
                <>
                  <p className="mb-3 text-xs text-slate dark:text-white/60">
                    Raw prompt payload is redacted; the persisted exchange keeps the operational request summary below.
                  </p>
                  <ExchangeSummaryList entries={requestSummary} emptyMessage="No request summary was recorded." />
                </>
              )}
            </Panel>

            <Panel title="Response">
              {responseBody ? (
                <pre className="overflow-x-auto rounded-lg border border-slate/15 bg-slate/5 p-3 text-xs dark:border-cyan-900/30 dark:bg-white/[0.03]">
                  {responseBody}
                </pre>
              ) : (
                <>
                  <p className="mb-3 text-xs text-slate dark:text-white/60">
                    Raw provider response is redacted; the persisted exchange keeps response size, status, and parsed-shape summary.
                  </p>
                  <ExchangeSummaryList entries={responseSummary} emptyMessage="No response summary was recorded." />
                </>
              )}
              {responseJson != null && (
                <>
                  <p className="mt-3 text-xs font-semibold uppercase text-slate dark:text-white/55">
                    Parsed Response JSON
                  </p>
                  <pre className="mt-2 overflow-x-auto rounded-lg border border-slate/15 bg-slate/5 p-3 text-xs dark:border-cyan-900/30 dark:bg-white/[0.03]">
                    {formatDebugPayload(responseJson)}
                  </pre>
                </>
              )}
              {responseJson == null && responseJsonSummary != null && (
                <>
                  <p className="mt-3 text-xs font-semibold uppercase text-slate dark:text-white/55">
                    Parsed Response Summary
                  </p>
                  <pre className="mt-2 overflow-x-auto rounded-lg border border-slate/15 bg-slate/5 p-3 text-xs dark:border-cyan-900/30 dark:bg-white/[0.03]">
                    {formatDebugPayload(responseJsonSummary)}
                  </pre>
                </>
              )}
            </Panel>
          </div>
        </div>
      )}
    </DialogSurface>
  )
}

type ExchangeSummaryEntry = {
  label: string
  value: string | number
}

function ExchangeSummaryList({
  entries,
  emptyMessage,
}: {
  entries: ExchangeSummaryEntry[]
  emptyMessage: string
}) {
  if (!entries.length) {
    return <EmptyInline>{emptyMessage}</EmptyInline>
  }

  return (
    <dl className="space-y-2 rounded-lg border border-slate/15 bg-slate/5 p-3 text-xs dark:border-cyan-900/30 dark:bg-white/[0.03]">
      {entries.map((entry) => (
        <div key={entry.label} className="grid gap-1 sm:grid-cols-[140px_minmax(0,1fr)]">
          <dt className="font-semibold uppercase text-slate dark:text-white/55">{entry.label}</dt>
          <dd className="min-w-0 break-words text-slate-900 dark:text-white/80">{entry.value}</dd>
        </div>
      ))}
    </dl>
  )
}

function buildProviderRequestSummary(payload: Record<string, unknown>): ExchangeSummaryEntry[] {
  return compactExchangeEntries([
    { label: 'Host', value: stringPayloadValue(payload.request_host) },
    { label: 'Path', value: stringPayloadValue(payload.request_path) },
    { label: 'Model', value: stringPayloadValue(payload.request_model) },
    { label: 'Messages', value: numberPayloadValue(payload.request_message_count) },
    { label: 'Roles', value: arrayPayloadValue(payload.request_message_roles) },
    { label: 'Prompt chars', value: numberPayloadValue(payload.request_prompt_chars) },
    { label: 'Temperature', value: numberPayloadValue(payload.request_temperature) },
    { label: 'Max tokens', value: numberPayloadValue(payload.request_max_tokens) },
    { label: 'Attempt', value: formatAttemptSummary(payload) },
  ])
}

function buildProviderResponseSummary(payload: Record<string, unknown>): ExchangeSummaryEntry[] {
  return compactExchangeEntries([
    { label: 'Body chars', value: numberPayloadValue(payload.response_body_chars) },
    { label: 'Body SHA-256', value: stringPayloadValue(payload.response_body_sha256) },
    { label: 'Finish reason', value: stringPayloadValue(payload.finish_reason) },
    { label: 'Attempt', value: formatAttemptSummary(payload) },
  ])
}

function compactExchangeEntries(entries: Array<{ label: string; value: string | number | null }>): ExchangeSummaryEntry[] {
  return entries.filter((entry): entry is ExchangeSummaryEntry => entry.value !== null)
}

function stringPayloadValue(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null
}

function numberPayloadValue(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function arrayPayloadValue(value: unknown): string | null {
  if (!Array.isArray(value)) {
    return null
  }
  const entries = value.filter((entry): entry is string | number => typeof entry === 'string' || typeof entry === 'number')
  return entries.length ? entries.join(', ') : null
}

function formatAttemptSummary(payload: Record<string, unknown>): string | null {
  const attempt = numberPayloadValue(payload.attempt)
  const maxAttempts = numberPayloadValue(payload.max_attempts)
  if (attempt === null) {
    return null
  }
  return maxAttempts === null ? String(attempt) : `${attempt} / ${maxAttempts}`
}

export function ActivityTab({
  days,
  setDays,
  selectedModel,
  setSelectedModel,
  modelOptions,
  onRefresh,
  runs,
  live,
  activeTasksLoading,
  activeTasksRefreshing,
  activeTasksErrorMessage,
  onOpenRun,
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
  filters,
  setFilters,
  runPage,
  setRunPage,
  runsQuery,
  selectedRunId,
  onSelectRun,
  runDetailQuery,
  briefSources,
  briefSourcesLoading,
  briefSourcesErrorMessage,
  selectedRunSectionRef,
  onCancelRun,
  cancelingRunId,
}: {
  days: number
  setDays: Dispatch<SetStateAction<number>>
  selectedModel: string
  setSelectedModel: Dispatch<SetStateAction<string>>
  modelOptions: string[]
  onRefresh: () => void
  runs: AITaskRunResponse[]
  live: AILiveStatusResponse | undefined
  activeTasksLoading: boolean
  activeTasksRefreshing: boolean
  activeTasksErrorMessage: string
  onOpenRun: (runId: string) => void
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
  filters: RunFilters
  setFilters: Dispatch<SetStateAction<RunFilters>>
  runPage: number
  setRunPage: Dispatch<SetStateAction<number>>
  runsQuery: ReturnType<typeof useQuery<AITaskRunListResponse>>
  selectedRunId: string | null
  onSelectRun: (runId: string) => void
  runDetailQuery: ReturnType<typeof useQuery<AITaskRunDetailResponse>>
  briefSources: AIDailyBriefSourceItemResponse[]
  briefSourcesLoading: boolean
  briefSourcesErrorMessage: string
  selectedRunSectionRef: RefObject<HTMLDivElement | null>
  onCancelRun: (run: AITaskRunResponse) => void
  cancelingRunId: string | null
}) {
  const selectedRun = runDetailQuery.data?.run
  const runTotal = runsQuery.data?.total ?? 0
  const runOffset = runPage * AI_RUN_PAGE_SIZE
  const visibleRunOffset = runsQuery.isPlaceholderData ? (runsQuery.data?.offset ?? runOffset) : runOffset
  const runCount = runsQuery.data?.items.length ?? 0
  const totalPages = Math.max(1, Math.ceil(runTotal / AI_RUN_PAGE_SIZE))
  const runListLoading = runsQuery.isLoading && !runsQuery.data
  const runListPageLoading = runsQuery.isFetching && runsQuery.isPlaceholderData
  const runListRefreshing = runsQuery.isFetching && Boolean(runsQuery.data) && !runsQuery.isPlaceholderData
  const runListStatusMessage = runListLoading
    ? 'Loading AI run history...'
    : runListPageLoading
      ? `Loading page ${runPage + 1}...`
      : runListRefreshing
        ? 'Refreshing run history...'
        : null
  const [articlePreviewLimit, setArticlePreviewLimit] = useState(8)
  const [inspectedRunId, setInspectedRunId] = useState<string | null>(null)

  useEffect(() => {
    setArticlePreviewLimit(8)
  }, [selectedRunId])

  useEffect(() => {
    if (!runsQuery.data || runsQuery.isPlaceholderData || runPage === 0) {
      return
    }
    if (runsQuery.data.total > runOffset) {
      return
    }
    setRunPage(Math.max(0, Math.ceil(runsQuery.data.total / AI_RUN_PAGE_SIZE) - 1))
  }, [runOffset, runPage, runsQuery.data, runsQuery.isPlaceholderData, setRunPage])

  const inspectedRunDetailQuery = useQuery({
    queryKey: ['ai', 'ops', 'inspect-run', inspectedRunId],
    queryFn: ({ signal }) => apiFetch<AITaskRunDetailResponse>(`/ai/ops/runs/${inspectedRunId}`, { signal }),
    enabled: Boolean(inspectedRunId),
    staleTime: 5000,
  })

  const inspectedRun = inspectedRunDetailQuery.data?.run ?? null
  const inspectedProviderEvent = useMemo(
    () => findLatestProviderExchangeEvent(inspectedRunDetailQuery.data?.events ?? []),
    [inspectedRunDetailQuery.data?.events],
  )

  const childRunsQuery = useQuery({
    queryKey: ['ai', 'ops', 'child-runs', selectedRunId, articlePreviewLimit],
    queryFn: ({ signal }) =>
      apiFetch<AITaskRunListResponse>(
        `/ai/ops/runs?parent_run_id=${selectedRunId}&limit=${articlePreviewLimit}`,
        { signal },
      ),
    enabled: Boolean(selectedRunId && selectedRun?.task_type === 'reprocess'),
    refetchInterval:
      selectedRun && (selectedRun.status === 'queued' || selectedRun.status === 'running') ? 10000 : false,
    staleTime: 5000,
  })

  return (
    <div className="space-y-4">
      <Panel title="Operations" subtitle="Queue AI work, monitor current jobs, and inspect the full run history in one place.">
        <div className="flex flex-wrap items-center gap-2">
          <label className="sr-only" htmlFor="ai-activity-window-days">
            Activity window
          </label>
          <select
            id="ai-activity-window-days"
            value={days}
            onChange={(event) => {
              setDays(Number(event.target.value))
              setRunPage(0)
            }}
            aria-label="Activity window"
            className="rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
          >
            <option value={1}>Last 24h</option>
            <option value={7}>Last 7d</option>
            <option value={30}>Last 30d</option>
            <option value={90}>Last 90d</option>
          </select>
          <label className="sr-only" htmlFor="ai-activity-model-filter">
            Model filter
          </label>
          <select
            id="ai-activity-model-filter"
            value={selectedModel}
            onChange={(event) => {
              setSelectedModel(event.target.value)
              setRunPage(0)
            }}
            aria-label="Model filter"
            className="rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
          >
            {modelOptions.map((model) => (
              <option key={model} value={model}>
                {model === 'all' ? 'All models' : model}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="rounded border border-slate/30 px-3 py-2 text-sm font-semibold dark:border-cyan-900/40"
            onClick={onRefresh}
          >
            Refresh
          </button>
        </div>
        <p className="mt-3 text-xs text-slate dark:text-white/60">
          These filters apply to the operations and run-history views below.
        </p>
      </Panel>

      <OverviewSection
        title="Live Operations"
        description="Use this section to see what is running right now and to queue new brief or reprocess work."
      >
        <div className="space-y-4">
          <ActiveTasksPanel
            runs={runs}
            live={live}
            isLoading={activeTasksLoading}
            isRefreshing={activeTasksRefreshing}
            errorMessage={activeTasksErrorMessage}
            onOpenRun={onOpenRun}
            onCancelRun={onCancelRun}
            cancelingRunId={cancelingRunId}
          />
          <QueueWorkPanel
            dailyBriefEnabled={dailyBriefEnabled}
            dailyBriefDays={dailyBriefDays}
            setDailyBriefDays={setDailyBriefDays}
            dailyBriefPending={dailyBriefPending}
            dailyBriefValidation={dailyBriefValidation}
            retainedDailyBriefLimit={retainedDailyBriefLimit}
            onQueueDailyBrief={onQueueDailyBrief}
            reprocessDays={reprocessDays}
            setReprocessDays={setReprocessDays}
            reprocessLimit={reprocessLimit}
            setReprocessLimit={setReprocessLimit}
            reprocessStartTime={reprocessStartTime}
            setReprocessStartTime={setReprocessStartTime}
            reprocessEndTime={reprocessEndTime}
            setReprocessEndTime={setReprocessEndTime}
            feeds={feeds}
            selectedFeedIds={selectedFeedIds}
            setSelectedFeedIds={setSelectedFeedIds}
            itemSearch={itemSearch}
            setItemSearch={setItemSearch}
            candidateItems={candidateItems}
            selectedItems={selectedItems}
            onAddItem={onAddItem}
            onRemoveItem={onRemoveItem}
            onClearScope={onClearScope}
            reprocessPending={reprocessPending}
            reprocessValidation={reprocessValidation}
            reprocessQueueDisabled={reprocessQueueDisabled}
            queueWorkBlockedReason={queueWorkBlockedReason}
            onQueueReprocess={onQueueReprocess}
            itemSearchLoading={itemSearchLoading}
            itemSearchError={itemSearchError}
            itemSearchReady={itemSearchReady}
          />
        </div>
      </OverviewSection>

      <OverviewSection
        title="Run History"
        description="Review every AI task across enrichment, daily briefs, connection tests, and reprocess jobs."
      >
        <Panel title="Task History" subtitle="Filter by type, status, trigger source, and model to find the runs you need.">
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
            <label className="sr-only" htmlFor="ai-history-task-type-filter">
              Task type filter
            </label>
            <select
              id="ai-history-task-type-filter"
              value={filters.taskType}
              onChange={(event) => {
                setRunPage(0)
                setFilters((current) => ({ ...current, taskType: event.target.value }))
              }}
              aria-label="Task type filter"
              className="rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
            >
              <option value="">All task types</option>
              <option value="item_enrichment">Item Enrichment</option>
              <option value="daily_brief">Daily Brief</option>
              <option value="connection_test">Connection Test</option>
              <option value="reprocess">Reprocess</option>
            </select>
            <label className="sr-only" htmlFor="ai-history-status-filter">
              Status filter
            </label>
            <select
              id="ai-history-status-filter"
              value={filters.status}
              onChange={(event) => {
                setRunPage(0)
                setFilters((current) => ({ ...current, status: event.target.value }))
              }}
              aria-label="Status filter"
              className="rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
            >
              <option value="">All statuses</option>
              <option value="queued">Queued</option>
              <option value="running">Running</option>
              <option value="ready">Ready</option>
              <option value="error">Error</option>
              <option value="skipped">Skipped</option>
            </select>
            <label className="sr-only" htmlFor="ai-history-trigger-filter">
              Trigger source filter
            </label>
            <select
              id="ai-history-trigger-filter"
              value={filters.triggerSource}
              onChange={(event) => {
                setRunPage(0)
                setFilters((current) => ({ ...current, triggerSource: event.target.value }))
              }}
              aria-label="Trigger source filter"
              className="rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
            >
              <option value="">All triggers</option>
              <option value="auto">Auto</option>
              <option value="manual">Manual</option>
              <option value="scheduled">Scheduled</option>
            </select>
            <label className="flex items-center gap-2 rounded border border-slate/20 bg-white/70 px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]/80">
              <input
                type="checkbox"
                checked={filters.onlyFailures}
                onChange={(event) => {
                  setRunPage(0)
                  setFilters((current) => ({ ...current, onlyFailures: event.target.checked }))
                }}
              />
              Failures only
            </label>
            <div className="rounded border border-slate/20 bg-slate/5 px-3 py-2 text-sm text-slate dark:border-cyan-900/40 dark:bg-white/[0.03] dark:text-white/65">
              Window {days}d{selectedModel !== 'all' ? ` · ${selectedModel}` : ' · all models'}
            </div>
          </div>

          <div
            className="mt-3 flex min-h-5 items-center text-xs font-semibold uppercase text-slate dark:text-white/55"
            aria-live="polite"
          >
            {runListStatusMessage ?? <span aria-hidden="true">&nbsp;</span>}
          </div>
          {runsQuery.isError && (
            <p className="mt-3 text-sm text-red-600">
              Failed to load AI runs. {(runsQuery.error as Error | undefined)?.message ?? ''}
            </p>
          )}

          <AiRunMobileList
            runList={runsQuery.data}
            selectedRunId={selectedRunId}
            isLoading={runListLoading}
            isRefreshing={runListRefreshing}
            isPageLoading={runListPageLoading}
            onSelect={onSelectRun}
            onInspect={setInspectedRunId}
          />

          <div className="mt-4 hidden overflow-x-auto sm:block">
            <table
              className={`min-w-full text-sm transition-opacity ${runListPageLoading ? 'opacity-70' : ''}`}
              aria-busy={runListLoading || runListRefreshing || runListPageLoading}
            >
              <caption className="sr-only">AI task history. Select a run to inspect its details below.</caption>
              <thead className="text-left text-xs uppercase text-slate dark:text-white/55">
                <tr>
                  <th scope="col" className="pb-2">
                    <span className="sr-only">Select</span>
                  </th>
                  <th scope="col" className="pb-2">Type</th>
                  <th scope="col" className="pb-2">Article</th>
                  <th scope="col" className="pb-2">Trigger</th>
                  <th scope="col" className="pb-2">Timing</th>
                  <th scope="col" className="pb-2">Status</th>
                  <th scope="col" className="pb-2">Execution</th>
                  <th scope="col" className="pb-2">Tokens</th>
                  <th scope="col" className="pb-2">Error</th>
                  <th scope="col" className="pb-2">Inspect</th>
                </tr>
              </thead>
              <tbody>
                {runsQuery.data?.items.map((run) => (
                  <tr
                    key={run.id}
                    className={`cursor-pointer border-t border-slate/10 text-slate dark:border-cyan-900/30 dark:text-white/80 ${
                      selectedRunId === run.id ? 'bg-cyan/5 dark:bg-cyan/10' : ''
                    }`}
                    onClick={() => onSelectRun(run.id)}
                  >
                    <td className="py-2 pr-2 align-top">
                      <input
                        type="radio"
                        name="ai-selected-run"
                        className="mt-1 h-4 w-4"
                        aria-label={formatRunSelectionLabel(run)}
                        checked={selectedRunId === run.id}
                        onChange={() => onSelectRun(run.id)}
                      />
                    </td>
                    <td className="py-2">
                      <div className="font-semibold">{formatRunTaskLabel(run)}</div>
                      {run.feed_name && <div className="text-xs text-slate dark:text-white/55">{run.feed_name}</div>}
                    </td>
                    <td className="py-2">
                      {run.item_title ? (
                        <div className="max-w-xs">
                          <div className="font-semibold">{truncate(run.item_title, 72)}</div>
                          <div className="text-xs text-slate dark:text-white/55">
                            {run.item_published_at ? `Published ${formatTimestamp(run.item_published_at)}` : 'Article-linked run'}
                          </div>
                        </div>
                      ) : (
                        <span className="text-xs text-slate dark:text-white/55">—</span>
                      )}
                    </td>
                    <td className="py-2">{formatTriggerLabel(run.trigger_source)}</td>
                    <td className="py-2">
                      <div>{formatTimestamp(run.queued_at)}</div>
                      <div className="text-xs text-slate dark:text-white/55">
                        {run.finished_at ? `Finished ${formatTimestamp(run.finished_at)}` : 'In progress'} · {formatDuration(run.duration_ms)}
                      </div>
                    </td>
                    <td className="py-2">
                      <StatusPill tone={statusTone(run.status)} label={formatStatusLabel(run.status, run.reason)} />
                    </td>
                    <td className="py-2">
                      <div>{run.worker_name || 'api'}</div>
                      <div className="text-xs text-slate dark:text-white/55">{run.model || 'n/a'}</div>
                    </td>
                    <td className="py-2">{run.total_tokens?.toLocaleString() || 'n/a'}</td>
                    <td className="py-2 text-xs text-slate dark:text-white/60">{truncate(run.error || run.reason || '', 36) || '—'}</td>
                    <td className="py-2">
                      {canInspectProviderExchange(run) ? (
                        <button
                          type="button"
                          className="rounded border border-slate/30 px-2 py-1 text-xs font-semibold dark:border-cyan-900/40"
                          onClick={(event) => {
                            event.stopPropagation()
                            setInspectedRunId(run.id)
                          }}
                        >
                          Request / Response
                        </button>
                      ) : (
                        <span className="text-xs text-slate dark:text-white/55">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {!runListLoading && !runListPageLoading && !runsQuery.isError && !runsQuery.data?.items.length && (
            <EmptyInline>No AI runs matched the current filters.</EmptyInline>
          )}

          <div className="mt-4 flex items-center justify-between gap-3 text-sm">
            <span className="text-slate dark:text-white/60">
              {runCount > 0
                ? `Showing ${visibleRunOffset + 1}-${visibleRunOffset + runCount} of ${runTotal}`
                : `Showing 0 of ${runTotal}`}
            </span>
            <div className="flex items-center gap-2">
              <button
                type="button"
                className="rounded border border-slate/30 px-3 py-2 disabled:opacity-50 dark:border-cyan-900/40"
                onClick={() => setRunPage((current) => Math.max(0, current - 1))}
                disabled={runPage === 0 || runListLoading}
              >
                Previous
              </button>
              <span>
                Page {runPage + 1} / {totalPages}
              </span>
              <button
                type="button"
                className="rounded border border-slate/30 px-3 py-2 disabled:opacity-50 dark:border-cyan-900/40"
                onClick={() => setRunPage((current) => Math.min(totalPages - 1, current + 1))}
                disabled={runListLoading || runPage >= totalPages - 1}
              >
                Next
              </button>
            </div>
          </div>
        </Panel>
      </OverviewSection>

      <div ref={selectedRunSectionRef}>
        <OverviewSection
          title="Selected Run"
          description="Inspect the currently selected run, its event timeline, request metadata, and any related article or daily-brief context."
        >
          <Panel title="Run Detail" subtitle="Selected run timeline, metadata, and related sources.">
          {runDetailQuery.isLoading && <p className="text-sm text-slate dark:text-white/70">Loading run detail...</p>}
          {runDetailQuery.isError && (
            <p className="text-sm text-red-600">
              Failed to load run detail. {(runDetailQuery.error as Error | undefined)?.message ?? ''}
            </p>
          )}
          {!selectedRun && <EmptyInline>Select a run to inspect it.</EmptyInline>}
          {selectedRun && (
            <div className="space-y-4">
              <div className="rounded-xl border border-slate/20 bg-white/70 p-3 dark:border-cyan-900/40 dark:bg-[#072019]/80">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <p className="text-sm font-semibold">{formatRunTaskLabel(selectedRun)}</p>
                    <p className="text-xs text-slate dark:text-white/60">
                      {formatTriggerLabel(selectedRun.trigger_source)} · {selectedRun.actor_email || selectedRun.worker_name || 'system'}
                    </p>
                    {selectedRun.task_type === 'reprocess' && (
                      <p className="mt-1 text-xs text-slate dark:text-white/60">{describeRunScope(selectedRun)}</p>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    {canInspectProviderExchange(selectedRun) && (
                      <button
                        type="button"
                        className="rounded border border-slate/30 px-3 py-2 text-xs font-semibold dark:border-cyan-900/40"
                        onClick={() => setInspectedRunId(selectedRun.id)}
                      >
                        View Request / Response
                      </button>
                    )}
                    {canCancelRun(selectedRun) && (
                      <button
                        type="button"
                        className="rounded border border-slate/30 px-3 py-2 text-xs font-semibold disabled:opacity-50 dark:border-cyan-900/40"
                        onClick={() => onCancelRun(selectedRun)}
                        disabled={cancelingRunId === selectedRun.id}
                      >
                        {cancelingRunId === selectedRun.id ? 'Working...' : cancelActionLabel(selectedRun)}
                      </button>
                    )}
                    <StatusPill
                      tone={statusTone(selectedRun.status)}
                      label={formatStatusLabel(selectedRun.status, selectedRun.reason)}
                    />
                  </div>
                </div>
                <dl className="mt-3 space-y-2 text-sm">
                  <Metric label="Queued" value={formatTimestamp(selectedRun.queued_at)} />
                  <Metric label="Started" value={selectedRun.started_at ? formatTimestamp(selectedRun.started_at) : 'n/a'} />
                  <Metric label="Finished" value={selectedRun.finished_at ? formatTimestamp(selectedRun.finished_at) : 'n/a'} />
                  <Metric label="Duration" value={formatDuration(selectedRun.duration_ms)} />
                  <Metric label="Worker" value={selectedRun.worker_name || 'api'} />
                  <Metric label="Model" value={selectedRun.model || 'n/a'} />
                  <Metric label="Prompt size" value={selectedRun.prompt_char_count ?? 'n/a'} />
                  <Metric label="Response size" value={selectedRun.response_char_count ?? 'n/a'} />
                  <Metric label="Input text chars" value={selectedRun.input_text_chars ?? 'n/a'} />
                  <Metric label="Tokens" value={selectedRun.total_tokens ?? 'n/a'} />
                </dl>
                {selectedRun.reason && (
                  <p className="mt-3 rounded border border-slate/20 bg-slate/5 px-3 py-2 text-xs text-slate dark:border-cyan-900/40 dark:bg-white/[0.03] dark:text-white/70">
                    Reason: {selectedRun.reason}
                  </p>
                )}
                {selectedRun.error && (
                  <p className="mt-3 rounded border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-700 dark:text-red-300">
                    {selectedRun.error}
                  </p>
                )}
                {selectedRun.task_type === 'reprocess' && (
                  <div className="mt-3">
                    <p className="text-xs font-semibold uppercase text-slate dark:text-white/55">Progress</p>
                    <ProgressBar
                      className="mt-2"
                      value={selectedRun.processed_count}
                      max={selectedRun.target_count || Math.max(selectedRun.processed_count, 1)}
                    />
                    <p className="mt-2 text-xs text-slate dark:text-white/60">
                      Processed {selectedRun.processed_count}/{selectedRun.target_count ?? '?'} · Success {selectedRun.success_count} ·
                      Errors {selectedRun.error_count} · Skipped {selectedRun.skipped_count}
                    </p>
                  </div>
                )}
              </div>

              {selectedRun.task_type === 'reprocess' && (
                <RunArticlesSection
                  parentRun={selectedRun}
                  childRunsQuery={childRunsQuery}
                  visibleCount={articlePreviewLimit}
                  onInspectRun={(runId) => setInspectedRunId(runId)}
                  onShowMore={() =>
                    setArticlePreviewLimit((current) => {
                      const total = childRunsQuery.data?.total ?? current
                      return Math.min(total, current + 20)
                    })
                  }
                  onShowLess={() => setArticlePreviewLimit(8)}
                />
              )}

              <div>
                <p className="text-xs font-semibold uppercase text-slate dark:text-white/55">Event Timeline</p>
                <div className="mt-2 space-y-2">
                  {runDetailQuery.data?.events.map((event) => (
                    <div key={event.id} className="rounded-lg border border-slate/10 px-3 py-2 text-sm dark:border-cyan-900/30">
                      <div className="flex items-center justify-between gap-3">
                        <span className="font-semibold">{event.event_type}</span>
                        <span className="text-xs text-slate dark:text-white/60">{formatTimestamp(event.created_at)}</span>
                      </div>
                      {event.message && <p className="mt-1 text-sm text-slate dark:text-white/70">{event.message}</p>}
                    </div>
                  ))}
                </div>
              </div>

              {Object.keys(selectedRun.metadata || {}).length > 0 && (
                <div>
                  <p className="text-xs font-semibold uppercase text-slate dark:text-white/55">Request / Response Summary</p>
                  <div className="mt-2 rounded-xl border border-slate/20 bg-white/70 p-3 text-xs dark:border-cyan-900/40 dark:bg-[#072019]/80">
                    <dl className="space-y-2">
                      {Object.entries(selectedRun.metadata).map(([key, value]) => (
                        <Metric key={key} label={humanizeKey(key)} value={formatMetadataValue(value)} />
                      ))}
                    </dl>
                  </div>
                </div>
              )}

              {selectedRun.daily_brief_id && (
                <div>
                  <p className="text-xs font-semibold uppercase text-slate dark:text-white/55">Daily Brief Source Items</p>
                  <div className="mt-2 space-y-2">
                    {briefSourcesLoading && <EmptyInline>Loading source log for this brief...</EmptyInline>}
                    {!briefSourcesLoading && briefSourcesErrorMessage && (
                      <p className="text-sm text-red-600">Failed to load the source log. {briefSourcesErrorMessage}</p>
                    )}
                    {briefSources.map((source) => (
                      <div key={source.id} className="rounded-lg border border-slate/10 px-3 py-2 text-sm dark:border-cyan-900/30">
                        <div className="flex items-start justify-between gap-3">
                          <div>
                            <p className="font-semibold">{source.title_snapshot}</p>
                            <p className="mt-1 text-xs text-slate dark:text-white/60">
                              {source.feed_name_snapshot || 'Unknown feed'}
                              {source.classification_snapshot ? ` · ${source.classification_snapshot}` : ''}
                            </p>
                          </div>
                          <StatusPill tone={source.included ? 'success' : 'neutral'} label={source.included ? 'Included' : 'Excluded'} />
                        </div>
                        {!source.included && source.exclusion_reason && (
                          <p className="mt-2 text-xs text-slate dark:text-white/60">Reason: {source.exclusion_reason}</p>
                        )}
                      </div>
                    ))}
                    {!briefSourcesLoading && !briefSourcesErrorMessage && !briefSources.length && (
                      <EmptyInline>No source log recorded for this brief.</EmptyInline>
                    )}
                  </div>
                </div>
              )}
            </div>
          )}
          </Panel>

          <ProviderExchangeModal
            run={inspectedRun}
            event={inspectedProviderEvent}
            isLoading={inspectedRunDetailQuery.isLoading}
            errorMessage={(inspectedRunDetailQuery.error as Error | undefined)?.message ?? ''}
            onClose={() => setInspectedRunId(null)}
          />
        </OverviewSection>
      </div>
    </div>
  )
}
