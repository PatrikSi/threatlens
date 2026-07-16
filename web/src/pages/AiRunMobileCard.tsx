import { AITaskRunListResponse, AITaskRunResponse } from '../types/api'
import { StatusPill } from './aiSettingsSupport'
import {
  canInspectProviderExchange,
  formatDuration,
  formatRunSelectionLabel,
  formatRunTaskLabel,
  formatStatusLabel,
  formatTimestamp,
  formatTriggerLabel,
  statusTone,
  truncate,
} from './aiSettingsUtils'

export function AiRunMobileList({
  runList,
  selectedRunId,
  isLoading,
  isRefreshing,
  isPageLoading,
  onSelect,
  onInspect,
}: {
  runList: AITaskRunListResponse | undefined
  selectedRunId: string | null
  isLoading: boolean
  isRefreshing: boolean
  isPageLoading: boolean
  onSelect: (runId: string) => void
  onInspect: (runId: string) => void
}) {
  return (
    <div
      className={`mt-4 space-y-2 transition-opacity sm:hidden ${isPageLoading ? 'opacity-70' : ''}`}
      aria-label="AI task history"
      aria-busy={isLoading || isRefreshing || isPageLoading}
    >
      {runList?.items.map((run) => (
        <AiRunMobileCard
          key={run.id}
          run={run}
          selected={selectedRunId === run.id}
          onSelect={onSelect}
          onInspect={onInspect}
        />
      ))}
    </div>
  )
}

export function AiRunMobileCard({
  run,
  selected,
  onSelect,
  onInspect,
}: {
  run: AITaskRunResponse
  selected: boolean
  onSelect: (runId: string) => void
  onInspect: (runId: string) => void
}) {
  return (
    <article
      className={`rounded-lg border p-3 text-sm ${
        selected
          ? 'border-cyan/40 bg-cyan/5 dark:border-cyan-500/35 dark:bg-cyan/10'
          : 'border-slate/15 bg-slate/5 dark:border-cyan-900/30 dark:bg-white/[0.03]'
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <label className="flex min-w-0 cursor-pointer items-start gap-2">
          <input
            type="radio"
            name="ai-selected-run-mobile"
            className="mt-0.5 h-4 w-4 shrink-0"
            aria-label={formatRunSelectionLabel(run)}
            checked={selected}
            onChange={() => onSelect(run.id)}
          />
          <span className="min-w-0">
            <span className="block font-semibold text-ink dark:text-slate-100">{formatRunTaskLabel(run)}</span>
            {run.feed_name && <span className="mt-0.5 block text-xs text-slate dark:text-white/55">{run.feed_name}</span>}
          </span>
        </label>
        <StatusPill tone={statusTone(run.status)} label={formatStatusLabel(run.status, run.reason)} />
      </div>

      {run.item_title && (
        <div className="mt-3 border-t border-slate/10 pt-3 dark:border-cyan-900/30">
          <p className="font-semibold text-ink dark:text-slate-100">{truncate(run.item_title, 96)}</p>
          <p className="mt-0.5 text-xs text-slate dark:text-white/55">
            {run.item_published_at ? `Published ${formatTimestamp(run.item_published_at)}` : 'Article-linked run'}
          </p>
        </div>
      )}

      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
        <MobileRunMetric label="Trigger" value={formatTriggerLabel(run.trigger_source)} />
        <MobileRunMetric label="Duration" value={formatDuration(run.duration_ms)} />
        <MobileRunMetric label="Queued" value={formatTimestamp(run.queued_at)} />
        <MobileRunMetric label="Execution" value={`${run.worker_name || 'api'} / ${run.model || 'n/a'}`} breakWords />
        <MobileRunMetric label="Tokens" value={run.total_tokens?.toLocaleString() || 'n/a'} />
        <MobileRunMetric label="Finished" value={run.finished_at ? formatTimestamp(run.finished_at) : 'In progress'} />
      </dl>

      {(run.error || run.reason) && (
        <p className="mt-3 break-words rounded border border-slate/15 bg-white/70 px-2.5 py-2 text-xs text-slate dark:border-cyan-900/30 dark:bg-[#041612]/70 dark:text-white/65">
          {run.error || run.reason}
        </p>
      )}

      {canInspectProviderExchange(run) && (
        <button
          type="button"
          className="mt-3 w-full rounded border border-slate/30 px-3 py-2 text-xs font-semibold dark:border-cyan-900/40"
          onClick={() => onInspect(run.id)}
        >
          Request / Response
        </button>
      )}
    </article>
  )
}

function MobileRunMetric({ label, value, breakWords = false }: { label: string; value: string; breakWords?: boolean }) {
  return (
    <div>
      <dt className="font-semibold uppercase text-slate dark:text-slate-400">{label}</dt>
      <dd className={`mt-0.5 text-ink dark:text-slate-200 ${breakWords ? 'break-all' : ''}`}>{value}</dd>
    </div>
  )
}
