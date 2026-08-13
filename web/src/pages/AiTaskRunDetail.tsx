import type { RefObject } from 'react'
import { Link } from 'react-router-dom'

import { resolveApiErrorMessage } from '../api/errors'
import type { AIDailyBriefSourceItemResponse, AITaskRunResponse } from '../types/api'
import { ProviderExchangeModal } from './AiProviderExchangeModal'
import type { ActivityTabProps, TaskRunListQuery } from './AiActivityTypes'
import type { AiActivityRunState } from './useAiActivityRunState'
import { EmptyInline, Metric, OverviewSection, Panel, ProgressBar, StatusPill } from './aiSettingsSupport'
import {
  canCancelRun,
  canInspectProviderExchange,
  cancelActionLabel,
  describeRunScope,
  formatDailyBriefChildRunMeta,
  formatDailyBriefChildRunTitle,
  formatDuration,
  formatMetadataValue,
  formatRunTaskLabel,
  formatStatusLabel,
  formatTimestamp,
  formatTriggerLabel,
  humanizeKey,
  isDailyBriefBackfillRun,
  remainingCount,
  statusTone,
} from './aiSettingsUtils'

type SelectedRunSectionProps = {
  selectedRunSectionRef: RefObject<HTMLDivElement | null>
  runDetailQuery: ActivityTabProps['runDetailQuery']
  briefSources: AIDailyBriefSourceItemResponse[]
  briefSourcesLoading: boolean
  briefSourcesErrorMessage: string
  onCancelRun: (run: AITaskRunResponse) => void
  cancelingRunId: string | null
  runState: AiActivityRunState
}

export function SelectedRunSection(props: SelectedRunSectionProps) {
  const { runState } = props
  return (
    <div ref={props.selectedRunSectionRef}>
      <OverviewSection
        title="Selected Run"
        description="Inspect the currently selected run, its event timeline, request metadata, and any related article or daily-brief context."
      >
        <Panel title="Run Detail" subtitle="Selected run timeline, metadata, and related sources.">
          <RunDetailContent {...props} />
        </Panel>

        <ProviderExchangeModal
          run={runState.inspectedRun}
          event={runState.inspectedProviderEvent}
          isLoading={runState.inspectedRunDetailQuery.isLoading}
          errorMessage={runState.inspectedRunErrorMessage}
          onClose={() => runState.setInspectedRunId(null)}
        />
      </OverviewSection>
    </div>
  )
}

function RunDetailContent({
  runDetailQuery,
  briefSources,
  briefSourcesLoading,
  briefSourcesErrorMessage,
  onCancelRun,
  cancelingRunId,
  runState,
}: SelectedRunSectionProps) {
  const selectedRun = runState.selectedRun
  return (
    <>
      {runDetailQuery.isLoading && (
        <p className="text-sm text-slate dark:text-white/70">Loading run detail...</p>
      )}
      {runDetailQuery.isError && (
        <p className="text-sm text-red-600">
          {resolveApiErrorMessage(runDetailQuery.error, 'AI run details could not be loaded')}
        </p>
      )}
      {!selectedRun && <EmptyInline>Select a run to inspect it.</EmptyInline>}
      {selectedRun && (
        <div className="space-y-4">
          <RunSummary
            run={selectedRun}
            onInspectRun={runState.setInspectedRunId}
            onCancelRun={onCancelRun}
            cancelingRunId={cancelingRunId}
          />
          {selectedRun.task_type === 'reprocess' && (
            <RunArticlesSection
              parentRun={selectedRun}
              childRunsQuery={runState.childRunsQuery}
              visibleCount={runState.articlePreviewLimit}
              onInspectRun={runState.setInspectedRunId}
              onShowMore={runState.showMoreChildRuns}
              onShowLess={() => runState.setArticlePreviewLimit(8)}
            />
          )}
          <RunEventTimeline events={runDetailQuery.data?.events ?? []} />
          <RunMetadata run={selectedRun} />
          {selectedRun.daily_brief_id && (
            <DailyBriefSources
              sources={briefSources}
              isLoading={briefSourcesLoading}
              errorMessage={briefSourcesErrorMessage}
            />
          )}
        </div>
      )}
    </>
  )
}

function RunSummary({
  run,
  onInspectRun,
  onCancelRun,
  cancelingRunId,
}: {
  run: AITaskRunResponse
  onInspectRun: (runId: string) => void
  onCancelRun: (run: AITaskRunResponse) => void
  cancelingRunId: string | null
}) {
  return (
    <div className="rounded-xl border border-slate/20 bg-white/70 p-3 dark:border-cyan-900/40 dark:bg-[#072019]/80">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-sm font-semibold">{formatRunTaskLabel(run)}</p>
          <p className="text-xs text-slate dark:text-white/60">
            {formatTriggerLabel(run.trigger_source)} · {run.actor_email || run.worker_name || 'system'}
          </p>
          {run.task_type === 'reprocess' && (
            <p className="mt-1 text-xs text-slate dark:text-white/60">{describeRunScope(run)}</p>
          )}
        </div>
        <div className="flex items-center gap-2">
          {canInspectProviderExchange(run) && (
            <button
              type="button"
              className="rounded border border-slate/30 px-3 py-2 text-xs font-semibold dark:border-cyan-900/40"
              onClick={() => onInspectRun(run.id)}
            >
              View Request / Response
            </button>
          )}
          {canCancelRun(run) && (
            <button
              type="button"
              className="rounded border border-slate/30 px-3 py-2 text-xs font-semibold disabled:opacity-50 dark:border-cyan-900/40"
              onClick={() => onCancelRun(run)}
              disabled={cancelingRunId === run.id}
            >
              {cancelingRunId === run.id ? 'Working...' : cancelActionLabel(run)}
            </button>
          )}
          <StatusPill tone={statusTone(run.status)} label={formatStatusLabel(run.status, run.reason)} />
        </div>
      </div>
      <RunMetrics run={run} />
      {run.reason && (
        <p className="mt-3 rounded border border-slate/20 bg-slate/5 px-3 py-2 text-xs text-slate dark:border-cyan-900/40 dark:bg-white/[0.03] dark:text-white/70">
          Reason: {run.reason}
        </p>
      )}
      {run.error && (
        <p className="mt-3 rounded border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-700 dark:text-red-300">
          {run.error}
        </p>
      )}
      {run.report_id && (
        <Link
          className="mt-3 inline-flex rounded border border-slate/30 px-3 py-2 text-xs font-semibold text-cyan-900 dark:border-cyan-900/40 dark:text-cyan-100"
          to={`/reporting/${run.report_id}`}
        >
          Open Report
        </Link>
      )}
      {run.task_type === 'reprocess' && <RunProgress run={run} />}
    </div>
  )
}

function RunMetrics({ run }: { run: AITaskRunResponse }) {
  return (
    <dl className="mt-3 space-y-2 text-sm">
      <Metric label="Queued" value={formatTimestamp(run.queued_at)} />
      <Metric label="Started" value={run.started_at ? formatTimestamp(run.started_at) : 'n/a'} />
      <Metric label="Finished" value={run.finished_at ? formatTimestamp(run.finished_at) : 'n/a'} />
      <Metric label="Duration" value={formatDuration(run.duration_ms)} />
      <Metric label="Worker" value={run.worker_name || 'api'} />
      <Metric label="Model" value={run.model || 'n/a'} />
      <Metric label="Prompt size" value={run.prompt_char_count ?? 'n/a'} />
      <Metric label="Response size" value={run.response_char_count ?? 'n/a'} />
      <Metric label="Input text chars" value={run.input_text_chars ?? 'n/a'} />
      <Metric label="Tokens" value={run.total_tokens ?? 'n/a'} />
    </dl>
  )
}

function RunProgress({ run }: { run: AITaskRunResponse }) {
  return (
    <div className="mt-3">
      <p className="text-xs font-semibold uppercase text-slate dark:text-white/55">Progress</p>
      <ProgressBar
        className="mt-2"
        value={run.processed_count}
        max={run.target_count || Math.max(run.processed_count, 1)}
      />
      <p className="mt-2 text-xs text-slate dark:text-white/60">
        Processed {run.processed_count}/{run.target_count ?? '?'} · Success {run.success_count} · Errors {run.error_count} ·
        Skipped {run.skipped_count}
      </p>
    </div>
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
  childRunsQuery: TaskRunListQuery
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
  const childRunNounPlural = isBackfill ? 'daily brief runs' : 'article runs'

  return (
    <div>
      <RunArticlesHeader parentRun={parentRun} childRunCount={childRuns.length} totalChildRuns={totalChildRuns} />
      {childRunsQuery.isLoading && !childRuns.length && (
        <p className="mt-3 text-sm text-slate dark:text-white/70">Loading {childRunNounPlural}...</p>
      )}
      {childRunsQuery.isError && (
        <p className="mt-3 text-sm text-red-600">
          {resolveApiErrorMessage(childRunsQuery.error, `${childRunNounPlural} could not be loaded`)}
        </p>
      )}
      {!childRunsQuery.isLoading && !childRuns.length && !childRunsQuery.isError && (
        <EmptyInline>Child {childRunNounPlural} have not been queued yet.</EmptyInline>
      )}
      {!!childRuns.length && (
        <div className={`mt-3 space-y-2 ${visibleCount > 8 ? 'max-h-96 overflow-y-auto pr-1' : ''}`}>
          {childRuns.map((run) => (
            <ChildRun key={run.id} run={run} isBackfill={isBackfill} onInspectRun={onInspectRun} />
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

function RunArticlesHeader({
  parentRun,
  childRunCount,
  totalChildRuns,
}: {
  parentRun: AITaskRunResponse
  childRunCount: number
  totalChildRuns: number
}) {
  const isBackfill = isDailyBriefBackfillRun(parentRun)
  const sectionTitle = isBackfill ? 'Daily Brief Runs' : 'Article Runs'
  const childRunNounPlural = isBackfill ? 'daily brief runs' : 'article runs'
  const targetNoun = isBackfill ? 'day' : 'article'
  const targetNounPlural = isBackfill ? 'days' : 'articles'
  const description = totalChildRuns
    ? `Showing ${childRunCount} of ${totalChildRuns} queued ${childRunNounPlural}${parentRun.target_count ? ` out of ${parentRun.target_count} target ${targetNounPlural}` : ''}.`
    : parentRun.target_count
      ? `No child ${childRunNounPlural} are visible yet. Target size: ${parentRun.target_count} ${targetNoun}${parentRun.target_count === 1 ? '' : 's'}.`
      : `No child ${childRunNounPlural} are visible yet.`

  return (
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <p className="text-xs font-semibold uppercase text-slate dark:text-white/55">{sectionTitle}</p>
        <p className="mt-1 text-xs text-slate dark:text-white/60">{description}</p>
      </div>
      <div className="flex flex-wrap gap-2 text-xs">
        <StatusPill tone="success" label={`Ready ${parentRun.success_count}`} />
        <StatusPill tone="danger" label={`Errors ${parentRun.error_count}`} />
        <StatusPill tone="neutral" label={`Skipped ${parentRun.skipped_count}`} />
        <StatusPill tone="info" label={`Remaining ${remainingCount(parentRun)}`} />
      </div>
    </div>
  )
}

function ChildRun({
  run,
  isBackfill,
  onInspectRun,
}: {
  run: AITaskRunResponse
  isBackfill: boolean
  onInspectRun: (runId: string) => void
}) {
  return (
    <div className="rounded-lg border border-slate/10 px-3 py-3 text-sm dark:border-cyan-900/30">
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
      {(run.error || run.reason) && <p className="mt-2 text-xs text-slate dark:text-white/70">{run.error || run.reason}</p>}
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
  )
}

function RunEventTimeline({ events }: { events: NonNullable<ActivityTabProps['runDetailQuery']['data']>['events'] }) {
  return (
    <div>
      <p className="text-xs font-semibold uppercase text-slate dark:text-white/55">Event Timeline</p>
      <div className="mt-2 space-y-2">
        {events.map((event) => (
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
  )
}

function RunMetadata({ run }: { run: AITaskRunResponse }) {
  if (!Object.keys(run.metadata || {}).length) {
    return null
  }
  return (
    <div>
      <p className="text-xs font-semibold uppercase text-slate dark:text-white/55">Request / Response Summary</p>
      <div className="mt-2 rounded-xl border border-slate/20 bg-white/70 p-3 text-xs dark:border-cyan-900/40 dark:bg-[#072019]/80">
        <dl className="space-y-2">
          {Object.entries(run.metadata).map(([key, value]) => (
            <Metric key={key} label={humanizeKey(key)} value={formatMetadataValue(value)} />
          ))}
        </dl>
      </div>
    </div>
  )
}

function DailyBriefSources({
  sources,
  isLoading,
  errorMessage,
}: {
  sources: AIDailyBriefSourceItemResponse[]
  isLoading: boolean
  errorMessage: string
}) {
  return (
    <div>
      <p className="text-xs font-semibold uppercase text-slate dark:text-white/55">Daily Brief Source Items</p>
      <div className="mt-2 space-y-2">
        {isLoading && <EmptyInline>Loading source log for this brief...</EmptyInline>}
        {!isLoading && errorMessage && <p className="text-sm text-red-600">{errorMessage}</p>}
        {sources.map((source) => (
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
        {!isLoading && !errorMessage && !sources.length && (
          <EmptyInline>No source log recorded for this brief.</EmptyInline>
        )}
      </div>
    </div>
  )
}
