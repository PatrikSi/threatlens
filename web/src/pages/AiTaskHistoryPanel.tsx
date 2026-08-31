import type { Dispatch, SetStateAction } from 'react'

import { resolveApiErrorMessage } from '../api/errors'
import type { AITaskRunResponse } from '../types/api'
import { AiRunMobileList } from './AiRunMobileCard'
import type { RunFilters, TaskRunListQuery } from './AiActivityTypes'
import type { AiActivityRunState } from './useAiActivityRunState'
import { EmptyInline, OverviewSection, Panel, StatusPill } from './aiSettingsSupport'
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

type TaskHistoryPanelProps = {
  days: number
  selectedModel: string
  filters: RunFilters
  setFilters: Dispatch<SetStateAction<RunFilters>>
  runPage: number
  setRunPage: Dispatch<SetStateAction<number>>
  runsQuery: TaskRunListQuery
  selectedRunId: string | null
  onSelectRun: (runId: string) => void
  onInspectRun: (runId: string) => void
  history: AiActivityRunState['history']
}

export function TaskHistoryPanel(props: TaskHistoryPanelProps) {
  const { runsQuery, selectedRunId, onSelectRun, onInspectRun, history } = props

  return (
    <OverviewSection
      title="Run history"
      description="Review every AI task across enrichment, daily briefs, connection tests, and reprocess jobs."
    >
      <Panel title="Task history" subtitle="Filter by type, status, trigger source, and model to find the runs you need.">
        <RunHistoryFilters {...props} />
        <div
          className="mt-3 flex min-h-5 items-center text-xs font-semibold uppercase text-slate dark:text-white/55"
          aria-live="polite"
        >
          {history.statusMessage ?? <span aria-hidden="true">&nbsp;</span>}
        </div>
        {runsQuery.isError && (
          <p className="mt-3 text-sm text-red-600">
            {resolveApiErrorMessage(runsQuery.error, 'AI runs could not be loaded')}
          </p>
        )}

        <AiRunMobileList
          runList={runsQuery.data}
          selectedRunId={selectedRunId}
          isLoading={history.isLoading}
          isRefreshing={history.isRefreshing}
          isPageLoading={history.isPageLoading}
          onSelect={onSelectRun}
          onInspect={onInspectRun}
        />

        <TaskHistoryTable
          runs={runsQuery.data?.items ?? []}
          selectedRunId={selectedRunId}
          isPageLoading={history.isPageLoading}
          isBusy={history.isLoading || history.isRefreshing || history.isPageLoading}
          onSelectRun={onSelectRun}
          onInspectRun={onInspectRun}
        />

        {!history.isLoading && !history.isPageLoading && !runsQuery.isError && !runsQuery.data?.items.length && (
          <EmptyInline>No AI runs matched the current filters.</EmptyInline>
        )}

        <RunHistoryPagination {...props} />
      </Panel>
    </OverviewSection>
  )
}

function RunHistoryFilters({
  days,
  selectedModel,
  filters,
  setFilters,
  setRunPage,
}: Pick<TaskHistoryPanelProps, 'days' | 'selectedModel' | 'filters' | 'setFilters' | 'setRunPage'>) {
  const updateFilter = <Key extends keyof RunFilters>(key: Key, value: RunFilters[Key]) => {
    setRunPage(0)
    setFilters((current) => ({ ...current, [key]: value }))
  }

  return (
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
      <label className="sr-only" htmlFor="ai-history-task-type-filter">
        Task type filter
      </label>
      <select
        id="ai-history-task-type-filter"
        value={filters.taskType}
        onChange={(event) => updateFilter('taskType', event.target.value)}
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
        onChange={(event) => updateFilter('status', event.target.value)}
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
        onChange={(event) => updateFilter('triggerSource', event.target.value)}
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
          onChange={(event) => updateFilter('onlyFailures', event.target.checked)}
        />
        Failures only
      </label>
      <div className="rounded border border-slate/20 bg-slate/5 px-3 py-2 text-sm text-slate dark:border-cyan-900/40 dark:bg-white/[0.03] dark:text-white/65">
        Window {days}d{selectedModel !== 'all' ? ` · ${selectedModel}` : ' · all models'}
      </div>
    </div>
  )
}

type TaskHistoryTableProps = {
  runs: AITaskRunResponse[]
  selectedRunId: string | null
  isPageLoading: boolean
  isBusy: boolean
  onSelectRun: (runId: string) => void
  onInspectRun: (runId: string) => void
}

function TaskHistoryTable(props: TaskHistoryTableProps) {
  return (
    <div className="mt-4 hidden overflow-x-auto sm:block">
      <table
        className={`min-w-full text-sm transition-opacity ${props.isPageLoading ? 'opacity-70' : ''}`}
        aria-busy={props.isBusy}
      >
        <caption className="sr-only">AI task history. Select a run to inspect its details below.</caption>
        <thead className="text-left text-xs uppercase text-slate dark:text-white/55">
          <tr>
            <th scope="col" className="pb-2"><span className="sr-only">Select</span></th>
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
          {props.runs.map((run) => <TaskHistoryRow key={run.id} run={run} {...props} />)}
        </tbody>
      </table>
    </div>
  )
}

function TaskHistoryRow({
  run,
  selectedRunId,
  onSelectRun,
  onInspectRun,
}: Pick<TaskHistoryTableProps, 'selectedRunId' | 'onSelectRun' | 'onInspectRun'> & { run: AITaskRunResponse }) {
  return (
    <tr
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
      <td className="py-2"><StatusPill tone={statusTone(run.status)} label={formatStatusLabel(run.status, run.reason)} /></td>
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
              onInspectRun(run.id)
            }}
          >
            Request / Response
          </button>
        ) : (
          <span className="text-xs text-slate dark:text-white/55">—</span>
        )}
      </td>
    </tr>
  )
}

function RunHistoryPagination({
  runPage,
  setRunPage,
  history,
}: Pick<TaskHistoryPanelProps, 'runPage' | 'setRunPage' | 'history'>) {
  return (
    <div className="mt-4 flex items-center justify-between gap-3 text-sm">
      <span className="text-slate dark:text-white/60">
        {history.runCount > 0
          ? `Showing ${history.visibleRunOffset + 1}-${history.visibleRunOffset + history.runCount} of ${history.runTotal}`
          : `Showing 0 of ${history.runTotal}`}
      </span>
      <div className="flex items-center gap-2">
        <button
          type="button"
          className="rounded border border-slate/30 px-3 py-2 disabled:opacity-50 dark:border-cyan-900/40"
          onClick={() => setRunPage((current) => Math.max(0, current - 1))}
          disabled={runPage === 0 || history.isLoading}
        >
          Previous
        </button>
        <span>Page {runPage + 1} of {history.totalPages}</span>
        <button
          type="button"
          className="rounded border border-slate/30 px-3 py-2 disabled:opacity-50 dark:border-cyan-900/40"
          onClick={() => setRunPage((current) => Math.min(history.totalPages - 1, current + 1))}
          disabled={history.isLoading || runPage >= history.totalPages - 1}
        >
          Next
        </button>
      </div>
    </div>
  )
}
