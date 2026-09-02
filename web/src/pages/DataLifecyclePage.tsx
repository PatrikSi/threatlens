import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Archive, Clock3, RefreshCw, ShieldAlert } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { resolveApiErrorMessage } from '../api/errors'
import { SettingsPageHeader, SettingsReadOnlyNotice } from '../components/SettingsPageHeader'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import type { EffectiveAccess } from '../types/access'
import type { CurrentAuthentication } from '../types/identity'
import type {
  LifecycleOverviewResponse,
  LifecyclePolicy,
  LifecyclePreview,
  LifecycleRun,
  LifecycleRunStatus,
  LifecycleRunTrigger,
} from '../types/lifecycle'
import { formatDateTime } from '../utils/datetime'
import { hasRequiredPermissions } from '../workspace/workspaceModel'
import { loadLifecycleOverview, loadLifecycleRuns } from './lifecycleApi'
import { LifecyclePolicyTable } from './LifecyclePolicyTable'
import {
  LifecycleRunHistory,
  type LifecycleRunFilters,
} from './LifecycleRunHistory'

type LifecycleTab = 'policies' | 'history'
const RUN_PAGE_SIZE = 25

export function DataLifecyclePage() {
  const queryClient = useQueryClient()
  const meQuery = useCurrentUser()
  const [searchParams, setSearchParams] = useSearchParams()
  const tab: LifecycleTab = searchParams.get('tab') === 'history' ? 'history' : 'policies'
  const page = positiveInteger(searchParams.get('page'))
  const requestedFilters = readRunFilters(searchParams)
  const [dirtyKeys, setDirtyKeys] = useState<Set<string>>(new Set())
  const access = lifecycleAccessState(
    meQuery.data?.access,
    meQuery.data?.authentication,
    meQuery.isError,
  )
  const unsaved = useUnsavedChangesWarning(
    dirtyKeys.size > 0,
    'Discard unsaved data lifecycle policy changes?',
    { ignoreSearchChanges: true },
  )

  const overviewQuery = useQuery({
    queryKey: ['operations', 'lifecycle'],
    queryFn: ({ signal }) => loadLifecycleOverview(signal),
    staleTime: 15_000,
  })
  const targets = overviewQuery.data?.targets ?? []
  const { filters, targetFilterSettled, validTargetKey } = normalizeRunFilters(
    requestedFilters,
    targets,
    overviewQuery.isSuccess || overviewQuery.isError,
  )
  const runsQuery = useQuery({
    queryKey: ['operations', 'lifecycle', 'runs', page, filters],
    queryFn: ({ signal }) => loadLifecycleRuns({
      page,
      pageSize: RUN_PAGE_SIZE,
      targetKey: filters.targetKey || undefined,
      status: filters.status || undefined,
      trigger: filters.trigger || undefined,
      signal,
    }),
    enabled: tab === 'history' && targetFilterSettled,
    refetchInterval: (query) => query.state.data?.runs.some(
      (run) => run.status === 'queued' || run.status === 'running',
    ) ? 10_000 : false,
  })
  const totalPages = Math.max(1, Math.ceil((runsQuery.data?.total ?? 0) / RUN_PAGE_SIZE))

  useEffect(() => {
    if (runsQuery.data && page > totalPages) setRunPage(totalPages)
    // setRunPage intentionally derives the latest URL state below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, runsQuery.data, totalPages])
  useEffect(() => {
    if (!targetFilterSettled
      || !requestedFilters.targetKey
      || validTargetKey) return
    const next = new URLSearchParams(searchParams)
    next.delete('target')
    next.delete('page')
    setSearchParams(next, { replace: true })
  }, [
    requestedFilters.targetKey,
    searchParams,
    setSearchParams,
    targetFilterSettled,
    validTargetKey,
  ])

  const setTab = (nextTab: LifecycleTab) => {
    const next = new URLSearchParams(searchParams)
    if (nextTab === 'policies') next.delete('tab')
    else next.set('tab', nextTab)
    setSearchParams(next, { replace: true })
  }
  function setRunPage(nextPage: number) {
    const next = new URLSearchParams(searchParams)
    if (nextPage <= 1) next.delete('page')
    else next.set('page', String(nextPage))
    setSearchParams(next, { replace: true })
  }
  const setFilters = (nextFilters: LifecycleRunFilters) => {
    const next = new URLSearchParams(searchParams)
    updateOptionalParam(next, 'target', nextFilters.targetKey)
    updateOptionalParam(next, 'status', nextFilters.status)
    updateOptionalParam(next, 'trigger', nextFilters.trigger)
    next.delete('page')
    setSearchParams(next, { replace: true })
  }
  const handleDirtyChange = useCallback((targetKey: string, dirty: boolean) => {
    setDirtyKeys((current) => {
      const next = new Set(current)
      if (dirty) next.add(targetKey)
      else next.delete(targetKey)
      if (next.size === current.size && [...next].every((key) => current.has(key))) return current
      return next
    })
  }, [])
  const updatePolicy = useCallback((targetKey: string, policy: LifecyclePolicy) => {
    queryClient.setQueryData<LifecycleOverviewResponse>(
      ['operations', 'lifecycle'],
      (current) => current ? {
        ...current,
        targets: current.targets.map((target) => target.key === targetKey
          ? { ...target, policy, latest_preview: null }
          : target),
      } : current,
    )
    void queryClient.invalidateQueries({ queryKey: ['operations', 'lifecycle'] })
  }, [queryClient])
  const updatePreview = useCallback((
    targetKey: string,
    preview: LifecyclePreview | null,
  ) => {
    queryClient.setQueryData<LifecycleOverviewResponse>(
      ['operations', 'lifecycle'],
      (current) => current ? {
        ...current,
        targets: current.targets.map((target) => target.key === targetKey
          ? { ...target, latest_preview: preview }
          : target),
      } : current,
    )
  }, [queryClient])
  const reloadTarget = useCallback(async (targetKey: string) => {
    const result = await overviewQuery.refetch()
    if (!result.isSuccess) return null
    return result.data?.targets.find((target) => target.key === targetKey) ?? null
  }, [overviewQuery])
  const handleRunChanged = useCallback((run: LifecycleRun) => {
    queryClient.setQueriesData<{ runs: LifecycleRun[] }>(
      { queryKey: ['operations', 'lifecycle', 'runs'] },
      (current) => current ? {
        ...current,
        runs: current.runs.map((entry) => entry.id === run.id ? run : entry),
      } : current,
    )
    void queryClient.invalidateQueries({ queryKey: ['operations', 'lifecycle', 'runs'] })
    void queryClient.invalidateQueries({ queryKey: ['operations', 'lifecycle'] })
  }, [queryClient])
  const handleRunStarted = useCallback((run: LifecycleRun) => {
    updatePreview(run.target_key, null)
    void queryClient.invalidateQueries({ queryKey: ['operations', 'lifecycle', 'runs'] })
    handleRunChanged(run)
    const next = new URLSearchParams(searchParams)
    next.set('tab', 'history')
    next.delete('target')
    next.delete('status')
    next.delete('trigger')
    next.delete('page')
    setSearchParams(next, { replace: true })
  }, [handleRunChanged, queryClient, searchParams, setSearchParams, updatePreview])

  const overviewError = overviewQuery.isError
    ? resolveApiErrorMessage(overviewQuery.error, 'Data lifecycle settings could not be loaded')
    : ''
  const runsError = runsQuery.isError
    ? resolveApiErrorMessage(runsQuery.error, 'Lifecycle run history could not be loaded')
    : ''
  const enabledCount = targets.filter((target) => target.policy.enabled).length
  const fetching = tab === 'policies' ? overviewQuery.isFetching : runsQuery.isFetching

  return (
    <div className="space-y-3">
      <SettingsPageHeader
        scope="System"
        title="Data lifecycle"
        description="Control how long operational data is retained, preview cleanup impact, and monitor bounded deletion runs. Policies are disabled only where explicitly shown."
        badges={overviewQuery.data && <span className="tl-chip tl-chip-neutral">{enabledCount} of {targets.length} enabled</span>}
        actions={(
          <button
            type="button"
            disabled={fetching}
            className="inline-flex min-h-11 items-center gap-2 rounded border border-slate/25 px-3 py-2 text-sm font-semibold disabled:opacity-50 dark:border-white/10"
            onClick={() => tab === 'policies'
              ? void overviewQuery.refetch()
              : void runsQuery.refetch()}
          >
            <RefreshCw className={`h-4 w-4 ${fetching ? 'animate-spin' : ''}`} aria-hidden="true" />
            {fetching ? 'Refreshing...' : 'Refresh'}
          </button>
        )}
      >
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2 text-xs text-slate dark:text-slate-400">
          <span>Policy catalog: {overviewQuery.data ? formatDateTime(overviewQuery.data.generated_at) : 'not loaded'}</span>
          <span>Schedules use UTC.</span>
          <span>Cleanup runs are bounded and auditable.</span>
        </div>
      </SettingsPageHeader>

      <LifecycleAccessNotice
        access={access}
        loading={meQuery.isLoading}
        error={lifecycleAccessError(meQuery.isError, meQuery.error)}
        onRetry={() => void meQuery.refetch()}
      />

      <section className="tl-surface overflow-hidden rounded-xl">
        <nav aria-label="Data lifecycle views" className="flex gap-1 border-b border-slate/15 px-3 py-2 dark:border-white/10">
          <TabButton active={tab === 'policies'} icon={Archive} label="Policies" onClick={() => setTab('policies')} />
          <TabButton active={tab === 'history'} icon={Clock3} label="Run history" onClick={() => setTab('history')} />
        </nav>
        <div hidden={tab !== 'policies'} className="p-3 sm:p-4">
          {overviewQuery.isLoading && <p role="status" className="py-10 text-center text-sm text-slate dark:text-slate-300">Loading lifecycle policy catalog...</p>}
          {overviewError && !overviewQuery.data && <LoadFailure message={overviewError} onRetry={() => void overviewQuery.refetch()} />}
          {overviewQuery.data && targets.length === 0 && <p className="py-10 text-center text-sm text-slate dark:text-slate-300">No lifecycle policy targets are available in this deployment.</p>}
          {targets.length > 0 && (
            <>
              {overviewError && <p role="alert" className="mb-3 text-sm text-red-700 dark:text-red-300">{overviewError} The last loaded policy catalog remains visible.</p>}
              <LifecyclePolicyTable targets={targets} canWrite={access.canWrite} onDirtyChange={handleDirtyChange} onPolicyUpdated={updatePolicy} onPreviewUpdated={updatePreview} onReloadTarget={reloadTarget} onRunStarted={handleRunStarted} />
            </>
          )}
        </div>
      </section>

      <div hidden={tab !== 'history'}>
        {overviewError && !overviewQuery.data && (
          <p
            role="alert"
            className="mb-3 rounded border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100"
          >
            {overviewError} Run history remains available, but dataset labels and
            filters may be limited.
          </p>
        )}
        <LifecycleRunHistory
          targets={targets}
          runs={runsQuery.data?.runs ?? []}
          total={runsQuery.data?.total ?? 0}
          page={page}
          pageSize={RUN_PAGE_SIZE}
          filters={filters}
          loading={runsQuery.isLoading || !targetFilterSettled}
          fetching={runsQuery.isFetching}
          error={runsError}
          canWrite={access.canWrite}
          onPageChange={setRunPage}
          onFiltersChange={setFilters}
          onRetry={() => void runsQuery.refetch()}
          onRunChanged={handleRunChanged}
        />
      </div>
      {unsaved.discardDialog}
    </div>
  )
}

function lifecycleAccess(
  access: EffectiveAccess | undefined,
  authentication: CurrentAuthentication | undefined,
) {
  const permissions = access?.permissions ?? []
  const durable = access?.durable_permissions ?? ((access?.elevation_ids?.length ?? 0) === 0 ? permissions : [])
  const hasEffectiveWrite = hasRequiredPermissions(permissions, ['write:operations'])
  const hasDurableWrite = hasRequiredPermissions(durable, ['write:operations'])
  const sessionReady = authentication?.sensitive_actions_ready ?? Boolean(authentication?.credential_kind === 'opaque_session' && authentication.recently_authenticated && (authentication.session_auth_method !== 'oidc' || authentication.identity_provider_mfa_asserted))
  return { hasEffectiveWrite, hasDurableWrite, sessionReady, canWrite: hasDurableWrite && sessionReady }
}

function lifecycleAccessState(
  access: EffectiveAccess | undefined,
  authentication: CurrentAuthentication | undefined,
  failed: boolean,
) {
  const resolved = lifecycleAccess(access, authentication)
  return failed ? { ...resolved, canWrite: false } : resolved
}

function lifecycleAccessError(failed: boolean, error: unknown) {
  return failed
    ? resolveApiErrorMessage(
      error,
      'Your lifecycle permission state could not be loaded',
    )
    : ''
}

function normalizeRunFilters(
  requested: LifecycleRunFilters,
  targets: LifecycleOverviewResponse['targets'],
  catalogSettled: boolean,
) {
  const targetFilterSettled = !requested.targetKey || catalogSettled
  const validTargetKey = requested.targetKey
    && targets.some((target) => target.key === requested.targetKey)
    ? requested.targetKey
    : ''
  return {
    targetFilterSettled,
    validTargetKey,
    filters: {
      ...requested,
      targetKey: targetFilterSettled ? validTargetKey : requested.targetKey,
    },
  }
}

function LifecycleAccessNotice({
  access,
  loading,
  error,
  onRetry,
}: {
  access: ReturnType<typeof lifecycleAccess>
  loading: boolean
  error: string
  onRetry: () => void
}) {
  if (loading) return null
  if (error) {
    return (
      <div
        role="alert"
        className="flex flex-col gap-2 rounded-lg border border-red-300/60 bg-red-50 px-3 py-2 text-sm text-red-900 sm:flex-row sm:items-center sm:justify-between dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-100"
      >
        <span><span className="font-semibold">Changes are locked.</span> {error}. Read-only lifecycle data remains available where possible.</span>
        <button
          type="button"
          className="min-h-11 shrink-0 rounded border border-current px-3 py-2 font-semibold sm:min-h-0"
          onClick={onRetry}
        >
          Retry access check
        </button>
      </div>
    )
  }
  if (access.canWrite) return null
  if (access.hasDurableWrite && !access.sessionReady) {
    return (
      <div role="status" className="flex flex-col gap-2 rounded-lg border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 sm:flex-row sm:items-center sm:justify-between dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100">
        <span><span className="font-semibold">Changes are locked.</span> Lifecycle mutations require a recently authenticated browser session with the applicable MFA assurance.</span>
        <Link to="/settings/account" className="inline-flex min-h-11 shrink-0 items-center justify-center rounded border border-current px-3 py-2 font-semibold sm:min-h-0">Open account security</Link>
      </div>
    )
  }
  if (access.hasEffectiveWrite && !access.hasDurableWrite) {
    return (
      <div role="status" className="rounded-lg border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100">
        <p className="flex items-center gap-2 font-semibold"><ShieldAlert className="h-4 w-4" aria-hidden="true" /> Persistent authority required</p>
        <p className="mt-0.5">Temporary elevation can inspect lifecycle data, but persistent policy changes and cleanup runs require durably assigned operations authority.</p>
      </div>
    )
  }
  return <SettingsReadOnlyNotice permission="permission to manage system operations" />
}

function readRunFilters(params: URLSearchParams): LifecycleRunFilters {
  const status = params.get('status')
  const trigger = params.get('trigger')
  const statuses: LifecycleRunStatus[] = ['queued', 'running', 'succeeded', 'partial', 'failed', 'cancelled']
  return {
    targetKey: params.get('target') ?? '',
    status: statuses.includes(status as LifecycleRunStatus) ? status as LifecycleRunStatus : '',
    trigger: trigger === 'manual' || trigger === 'scheduled' ? trigger as LifecycleRunTrigger : '',
  }
}

function positiveInteger(value: string | null) {
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : 1
}

function updateOptionalParam(params: URLSearchParams, key: string, value: string) {
  if (value) params.set(key, value)
  else params.delete(key)
}

function TabButton({
  active,
  icon: Icon,
  label,
  onClick,
}: {
  active: boolean
  icon: typeof Archive
  label: string
  onClick: () => void
}) {
  return (
    <button
      type="button"
      aria-current={active ? 'page' : undefined}
      className={`inline-flex min-h-11 items-center gap-2 rounded px-3 py-2 text-sm font-semibold md:min-h-0 ${
        active
          ? 'bg-ink text-white dark:bg-cyan dark:text-[#053c2e]'
          : 'border border-slate/20 text-slate-700 dark:border-white/10 dark:text-slate-200'
      }`}
      onClick={onClick}
    >
      <Icon className="h-4 w-4" aria-hidden="true" />
      {label}
    </button>
  )
}

function LoadFailure({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div
      role="alert"
      className="rounded border border-red-300/60 bg-red-50 px-3 py-3 text-sm text-red-900 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-100"
    >
      <p>{message}</p>
      <button
        type="button"
        className="mt-3 min-h-11 rounded border border-current px-3 py-2 font-semibold"
        onClick={onRetry}
      >
        Retry
      </button>
    </div>
  )
}
