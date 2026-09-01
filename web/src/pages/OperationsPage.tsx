import { useMutation, useQuery } from '@tanstack/react-query'
import { Download, RefreshCw } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import { apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { SettingsPageHeader } from '../components/SettingsPageHeader'
import type {
  OperationsDiagnosticsResponse,
  OperationsHealthHistoryResponse,
  OperationsHealthWindow,
  OperationsOverviewResponse,
  OperationsWorkerTopology,
  SystemOperationRunListResponse,
  SystemOperationStatus,
  SystemOperationType,
} from '../types/operations'
import { formatDateTime } from '../utils/datetime'
import { OperationsHealthTrends } from './OperationsHealthTrends'
import { OperationsLiveHealth } from './OperationsLiveHealth'
import {
  buildOperationsSignals,
  defaultSignalKey,
  formatDuration,
  OPERATIONS_VIEWS,
  type OperationsView,
  readOperationsView,
  readOperationsWindow,
} from './operationsHealthPresentation'
import { OperationsRecoveryActivity } from './OperationsRecoveryActivity'
import { OperationsStatusChip } from './OperationsStatus'

const OVERVIEW_REFRESH_MS = 30_000
const HEALTH_HISTORY_REFRESH_MS = 5 * 60_000
const OVERVIEW_STALE_AFTER_MS = 90_000
const RUN_PAGE_SIZE = 20

export function OperationsPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const activeView = readOperationsView(searchParams.get('view'))
  const selectedWindow = readOperationsWindow(searchParams.get('range'))
  const requestedSignalKey = searchParams.get('signal')
  const [runPage, setRunPage] = useState(1)
  const [operationType, setOperationType] = useState<SystemOperationType | ''>('')
  const [operationStatus, setOperationStatus] = useState<SystemOperationStatus | ''>('')
  const [downloadMessage, setDownloadMessage] = useState('')
  const [downloadError, setDownloadError] = useState('')

  const overviewQuery = useQuery({
    queryKey: ['operations', 'overview'],
    queryFn: () => apiFetch<OperationsOverviewResponse>('/operations/overview'),
    refetchInterval: activeView === 'live' ? OVERVIEW_REFRESH_MS : false,
    refetchIntervalInBackground: false,
    staleTime: 15_000,
  })
  const overview = overviewQuery.data
  const signals = overview ? buildOperationsSignals(overview) : []
  const selectedSignalKey = requestedSignalKey && signals.some((signal) => signal.key === requestedSignalKey)
    ? requestedSignalKey
    : overview
      ? defaultSignalKey(overview)
      : requestedSignalKey ?? 'workers'
  const workerQuery = useQuery({
    queryKey: ['operations', 'workers'],
    queryFn: () => apiFetch<OperationsWorkerTopology>('/operations/workers'),
    enabled: Boolean(overview && activeView === 'live' && selectedSignalKey === 'workers'),
    refetchInterval: activeView === 'live' && selectedSignalKey === 'workers'
      ? OVERVIEW_REFRESH_MS
      : false,
    refetchIntervalInBackground: false,
    staleTime: 15_000,
  })
  const historyQuery = useQuery({
    queryKey: ['operations', 'health-history', selectedWindow],
    queryFn: () => apiFetch<OperationsHealthHistoryResponse>(`/operations/health-history?window=${selectedWindow}`),
    enabled: activeView === 'trends',
    refetchInterval: activeView === 'trends' ? HEALTH_HISTORY_REFRESH_MS : false,
    refetchIntervalInBackground: false,
    staleTime: 30_000,
  })
  const runsQuery = useQuery({
    queryKey: ['operations', 'runs', runPage, operationType, operationStatus],
    queryFn: () => {
      const params = new URLSearchParams({ page: String(runPage), page_size: String(RUN_PAGE_SIZE) })
      if (operationType) params.set('operation_type', operationType)
      if (operationStatus) params.set('status', operationStatus)
      return apiFetch<SystemOperationRunListResponse>(`/operations/runs?${params.toString()}`)
    },
    enabled: activeView === 'recovery',
    refetchInterval: activeView === 'recovery' ? OVERVIEW_REFRESH_MS : false,
    refetchIntervalInBackground: false,
  })
  const diagnostics = useMutation({
    mutationFn: () => apiFetch<OperationsDiagnosticsResponse>('/operations/diagnostics'),
    onSuccess: (payload) => {
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
      const objectUrl = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = objectUrl
      anchor.download = `threatlens-diagnostics-${payload.generated_at.replace(/[:.]/g, '-')}.json`
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      URL.revokeObjectURL(objectUrl)
      setDownloadError('')
      setDownloadMessage('Diagnostic snapshot downloaded.')
    },
    onError: (error) => {
      setDownloadMessage('')
      setDownloadError(resolveApiErrorMessage(error, 'Diagnostic snapshot could not be downloaded'))
    },
  })

  const overviewError = overviewQuery.isError
    ? resolveApiErrorMessage(overviewQuery.error, overview ? 'System health could not be refreshed' : 'System health could not be loaded')
    : ''
  const snapshotAgeMs = overview ? Math.max(0, Date.now() - Date.parse(overview.generated_at)) : 0
  const lastKnown = Boolean(overview && (overviewQuery.isError || snapshotAgeMs > OVERVIEW_STALE_AFTER_MS))
  const totalRunPages = Math.max(1, Math.ceil((runsQuery.data?.total ?? 0) / RUN_PAGE_SIZE))

  useEffect(() => {
    if (runsQuery.data && runPage > totalRunPages) setRunPage(totalRunPages)
  }, [runPage, runsQuery.data, totalRunPages])

  const setView = (view: typeof activeView) => {
    const next = new URLSearchParams(searchParams)
    if (view === 'live') next.delete('view')
    else next.set('view', view)
    setSearchParams(next, { replace: true })
  }
  const setSignal = (key: string) => {
    if (key === 'recovery') {
      setView('recovery')
      return
    }
    const next = new URLSearchParams(searchParams)
    next.delete('view')
    next.set('signal', key)
    setSearchParams(next, { replace: true })
  }
  const setWindow = (window: OperationsHealthWindow) => {
    const next = new URLSearchParams(searchParams)
    next.set('view', 'trends')
    next.set('range', window)
    setSearchParams(next, { replace: true })
  }
  const refreshActiveView = () => {
    void overviewQuery.refetch()
    if (activeView === 'trends') void historyQuery.refetch()
    else if (activeView === 'recovery') void runsQuery.refetch()
    else if (selectedSignalKey === 'workers') void workerQuery.refetch()
  }
  const activeDatasetFetching = overviewQuery.isFetching ||
    (activeView === 'trends' && historyQuery.isFetching) ||
    (activeView === 'recovery' && runsQuery.isFetching) ||
    (activeView === 'live' && selectedSignalKey === 'workers' && workerQuery.isFetching)

  return (
    <div className="space-y-3">
      <OperationsPageHeader
        overview={overview}
        loading={overviewQuery.isLoading}
        fetching={activeDatasetFetching}
        unavailable={overviewQuery.isError}
        overviewError={overviewError}
        snapshotAgeMs={snapshotAgeMs}
        lastKnown={lastKnown}
        downloadError={downloadError}
        downloadMessage={downloadMessage}
        diagnosticsPending={diagnostics.isPending}
        onRefresh={refreshActiveView}
        onDownload={() => diagnostics.mutate()}
      />
      <OperationsWorkspace
        overview={overview}
        overviewLoading={overviewQuery.isLoading}
        overviewUnavailable={overviewQuery.isError}
        overviewError={overviewError}
        activeView={activeView}
        selectedWindow={selectedWindow}
        selectedSignalKey={selectedSignalKey}
        workerTopology={workerQuery.data}
        workerLoading={workerQuery.isLoading}
        workerFetching={workerQuery.isFetching}
        workerError={workerQuery.isError ? resolveApiErrorMessage(workerQuery.error, 'Worker diagnostics could not be loaded') : ''}
        history={historyQuery.data}
        historyLoading={historyQuery.isLoading}
        historyFetching={historyQuery.isFetching}
        historyError={historyQuery.isError ? resolveApiErrorMessage(historyQuery.error, 'Observed health history could not be loaded') : ''}
        runs={runsQuery.data?.runs ?? []}
        runsLoading={runsQuery.isLoading}
        runsUpdating={runsQuery.isFetching && Boolean(runsQuery.data)}
        runsError={runsQuery.isError ? resolveApiErrorMessage(runsQuery.error, 'Operation history could not be loaded') : ''}
        runPage={runPage}
        totalRunPages={totalRunPages}
        operationType={operationType}
        operationStatus={operationStatus}
        onViewChange={setView}
        onWindowChange={setWindow}
        onSignalChange={setSignal}
        onOverviewRetry={() => void overviewQuery.refetch()}
        onWorkersRetry={() => void workerQuery.refetch()}
        onHistoryRetry={() => void historyQuery.refetch()}
        onRunsRetry={() => void runsQuery.refetch()}
        onRunPageChange={setRunPage}
        onTypeChange={(value) => { setRunPage(1); setOperationType(value) }}
        onStatusChange={(value) => { setRunPage(1); setOperationStatus(value) }}
      />
      {overview && (
        <p className="sr-only" role="status" aria-live="polite">
          Current system health is {overview.overall_status}. {overview.issues.length} active findings.
        </p>
      )}
    </div>
  )
}

function OperationsPageHeader({
  overview,
  loading,
  fetching,
  unavailable,
  overviewError,
  snapshotAgeMs,
  lastKnown,
  downloadError,
  downloadMessage,
  diagnosticsPending,
  onRefresh,
  onDownload,
}: {
  overview?: OperationsOverviewResponse
  loading: boolean
  fetching: boolean
  unavailable: boolean
  overviewError: string
  snapshotAgeMs: number
  lastKnown: boolean
  downloadError: string
  downloadMessage: string
  diagnosticsPending: boolean
  onRefresh: () => void
  onDownload: () => void
}) {
  const description = overview
    ? `ThreatLens ${overview.application.version} · schema ${overview.application.schema_revision ?? 'unavailable'}`
    : unavailable
      ? 'Deployment health is unavailable.'
      : 'Loading deployment health...'
  const staleLabel = lastKnown && overview
    ? `Last known · ${overview.overall_status.charAt(0).toUpperCase()}${overview.overall_status.slice(1)}`
    : undefined
  return (
    <SettingsPageHeader
      scope="System"
      title="System health"
      description={description}
      badges={overview ? <OperationsStatusChip status={overview.overall_status} label={staleLabel} /> : undefined}
      actions={(
        <div className="grid w-full grid-cols-2 gap-2 sm:flex sm:w-auto">
          <button
            type="button"
            className="inline-flex min-h-11 items-center justify-center gap-2 rounded border border-slate/30 px-3 py-2 text-sm font-semibold disabled:opacity-60 dark:border-cyan-900/40"
            disabled={loading || fetching}
            onClick={onRefresh}
          >
            <RefreshCw className={`h-4 w-4 ${fetching ? 'animate-spin' : ''}`} aria-hidden="true" />
            {loading ? 'Loading...' : fetching ? 'Refreshing...' : overview ? 'Refresh' : 'Retry health'}
          </button>
          <button
            type="button"
            className="inline-flex min-h-11 items-center justify-center gap-2 rounded bg-ink px-3 py-2 text-sm font-semibold text-white disabled:opacity-60 dark:bg-cyan dark:text-[#053c2e]"
            disabled={diagnosticsPending}
            onClick={onDownload}
          >
            <Download className="h-4 w-4" aria-hidden="true" />
            {diagnosticsPending ? 'Preparing...' : 'Download diagnostics'}
          </button>
        </div>
      )}
    >
      <div className="py-2.5">
        <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-slate dark:text-slate-400">
          <span>Observed: {overview ? formatDateTime(overview.generated_at) : 'not available'}</span>
          <span>Age: {overview ? formatDuration(snapshotAgeMs / 1_000) : 'not available'}</span>
          <span>Auto-refresh: every {formatDuration(OVERVIEW_REFRESH_MS / 1_000)}</span>
        </div>
        {lastKnown && overview && (
          <div role="alert" className="mt-2 rounded border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100">
            This is the last successful snapshot from {formatDateTime(overview.generated_at)}. Current state has not been confirmed.
          </div>
        )}
        {overviewError && !lastKnown && <InlineMessage tone="error">{overviewError}</InlineMessage>}
        {downloadError && <InlineMessage tone="error">{downloadError}</InlineMessage>}
        {downloadMessage && <InlineMessage tone="status">{downloadMessage}</InlineMessage>}
      </div>
    </SettingsPageHeader>
  )
}

function OperationsWorkspace({
  overview,
  overviewLoading,
  overviewUnavailable,
  overviewError,
  activeView,
  selectedWindow,
  selectedSignalKey,
  workerTopology,
  workerLoading,
  workerFetching,
  workerError,
  history,
  historyLoading,
  historyFetching,
  historyError,
  runs,
  runsLoading,
  runsUpdating,
  runsError,
  runPage,
  totalRunPages,
  operationType,
  operationStatus,
  onViewChange,
  onWindowChange,
  onSignalChange,
  onOverviewRetry,
  onWorkersRetry,
  onHistoryRetry,
  onRunsRetry,
  onRunPageChange,
  onTypeChange,
  onStatusChange,
}: {
  overview?: OperationsOverviewResponse
  overviewLoading: boolean
  overviewUnavailable: boolean
  overviewError: string
  activeView: OperationsView
  selectedWindow: OperationsHealthWindow
  selectedSignalKey: string
  workerTopology?: OperationsWorkerTopology
  workerLoading: boolean
  workerFetching: boolean
  workerError: string
  history?: OperationsHealthHistoryResponse
  historyLoading: boolean
  historyFetching: boolean
  historyError: string
  runs: SystemOperationRunListResponse['runs']
  runsLoading: boolean
  runsUpdating: boolean
  runsError: string
  runPage: number
  totalRunPages: number
  operationType: SystemOperationType | ''
  operationStatus: SystemOperationStatus | ''
  onViewChange: (view: OperationsView) => void
  onWindowChange: (window: OperationsHealthWindow) => void
  onSignalChange: (key: string) => void
  onOverviewRetry: () => void
  onWorkersRetry: () => void
  onHistoryRetry: () => void
  onRunsRetry: () => void
  onRunPageChange: (page: number) => void
  onTypeChange: (value: SystemOperationType | '') => void
  onStatusChange: (value: SystemOperationStatus | '') => void
}) {
  return (
    <section className="tl-surface min-w-0 overflow-hidden rounded-xl">
      <nav aria-label="System health views" className="flex gap-1 overflow-x-auto border-b border-slate/15 px-3 py-2 dark:border-white/10">
        {OPERATIONS_VIEWS.map((view) => (
          <button
            key={view.value}
            type="button"
            aria-current={activeView === view.value ? 'page' : undefined}
            className={`min-h-11 shrink-0 rounded px-3 py-2 text-sm font-semibold md:min-h-0 md:py-1.5 ${activeView === view.value ? 'bg-ink text-white dark:bg-cyan dark:text-[#053c2e]' : 'border border-slate/20 text-slate-700 dark:border-white/10 dark:text-slate-200'}`}
            onClick={() => onViewChange(view.value)}
          >
            {view.label}
          </button>
        ))}
      </nav>

      {!overview && overviewLoading && activeView === 'live' && <p role="status" className="px-4 py-12 text-center text-sm text-slate dark:text-slate-300">Loading deployment health...</p>}
      {!overview && overviewUnavailable && activeView === 'live' && (
        <div role="alert" className="m-4 rounded border border-red-300/60 bg-red-50 px-3 py-3 text-sm text-red-900 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-100">
          <p>{overviewError}</p>
          <button type="button" className="mt-3 min-h-11 rounded border border-current px-3 py-2 font-semibold" onClick={onOverviewRetry}>Retry system health</button>
        </div>
      )}

      {overview && activeView === 'live' && (
        <OperationsLiveHealth
          overview={overview}
          selectedSignalKey={selectedSignalKey}
          onSelectSignal={onSignalChange}
          workerTopology={workerTopology}
          workerLoading={workerLoading}
          workerFetching={workerFetching}
          workerError={workerError}
          onRetryWorkers={onWorkersRetry}
        />
      )}
      {activeView === 'trends' && (
        <OperationsHealthTrends
          history={history}
          window={selectedWindow}
          loading={historyLoading}
          fetching={historyFetching}
          error={historyError}
          onWindowChange={onWindowChange}
          onRetry={onHistoryRetry}
        />
      )}
      {activeView === 'recovery' && (
        <OperationsRecoveryActivity
          recovery={overview?.recovery}
          recoveryLoading={overviewLoading}
          recoveryError={overviewError}
          runs={runs}
          loading={runsLoading}
          updating={runsUpdating}
          error={runsError}
          page={runPage}
          totalPages={totalRunPages}
          operationType={operationType}
          operationStatus={operationStatus}
          onPageChange={onRunPageChange}
          onRetryRecovery={onOverviewRetry}
          onRetry={onRunsRetry}
          onTypeChange={onTypeChange}
          onStatusChange={onStatusChange}
        />
      )}
    </section>
  )
}

function InlineMessage({ tone, children }: { tone: 'error' | 'status'; children: React.ReactNode }) {
  return (
    <p role={tone === 'error' ? 'alert' : 'status'} aria-live={tone === 'error' ? 'assertive' : 'polite'} className={`mt-2 text-sm ${tone === 'error' ? 'text-red-700 dark:text-red-300' : 'text-green-800 dark:text-green-300'}`}>
      {children}
    </p>
  )
}
