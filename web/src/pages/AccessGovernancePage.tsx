import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import {
  AlertTriangle,
  CheckCircle2,
  CircleGauge,
  RefreshCw,
  ShieldCheck,
  Tags,
  UsersRound,
} from 'lucide-react'

import { resolveApiErrorMessage } from '../api/errors'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { SettingsPageHeader } from '../components/SettingsPageHeader'
import { useCurrentUser } from '../hooks/useCurrentUser'
import type { EffectiveAccess } from '../types/access'
import type { CurrentAuthentication } from '../types/api'
import { formatDateTime } from '../utils/datetime'
import { hasRequiredPermissions } from '../workspace/workspaceModel'
import {
  loadAccessReviews,
  loadActionApprovals,
  loadDataPolicyOverview,
  loadIAMCatalog,
  loadServiceAccounts,
  loadTemporaryElevations,
} from './accessGovernanceApi'
import { AccessGroupsPanel } from './AccessGroupsPanel'
import { AccessRolesPanel } from './AccessRolesPanel'
import { DataPolicyPanel } from './DataPolicyPanel'

type GovernanceTab = 'overview' | 'roles' | 'groups' | 'data-policy'
type OptionalDataState = 'ready' | 'loading' | 'unavailable' | 'hidden'

const tabs: Array<{
  id: GovernanceTab
  label: string
  icon: typeof ShieldCheck
  permission?: string
}> = [
  { id: 'overview', label: 'Overview', icon: CircleGauge },
  { id: 'roles', label: 'Access roles', icon: ShieldCheck },
  { id: 'groups', label: 'Groups', icon: UsersRound },
  {
    id: 'data-policy',
    label: 'Data handling',
    icon: Tags,
    permission: 'read:data_policies',
  },
]

function accessGovernanceCapabilities(
  access: EffectiveAccess | undefined,
  authentication: CurrentAuthentication | undefined,
) {
  const permissions = access?.permissions ?? []
  const durablePermissions =
    access?.durable_permissions ??
    ((access?.elevation_ids?.length ?? 0) === 0 ? permissions : [])
  const allows = (permission: string) =>
    hasRequiredPermissions(permissions, [permission])
  const allowsDurably = (permission: string) =>
    hasRequiredPermissions(durablePermissions, [permission])
  const dataPolicySessionReady = sensitiveSessionReady(authentication)
  return {
    permissions,
    durablePermissions,
    hasWriteIAM: allows('write:iam'),
    hasWriteDataPolicy: allows('write:data_policies'),
    canWriteIAM: allowsDurably('write:iam'),
    canWriteDataPolicy:
      allowsDurably('write:data_policies') && dataPolicySessionReady,
    dataPolicySessionReady,
    canReadUsers: allows('read:users'),
    canReadDataPolicy: allows('read:data_policies'),
    canReadServiceAccounts: allows('read:service_accounts'),
    canReadAccessReviews: allows('read:access_reviews'),
    canReadElevations: allows('read:elevations'),
    canReadApprovals: allows('read:approvals'),
  }
}

export function AccessGovernancePage() {
  const queryClient = useQueryClient()
  const meQuery = useCurrentUser()
  const {
    permissions,
    durablePermissions,
    hasWriteIAM,
    hasWriteDataPolicy,
    canWriteIAM,
    canWriteDataPolicy,
    dataPolicySessionReady,
    canReadUsers,
    canReadDataPolicy,
    canReadServiceAccounts,
    canReadAccessReviews,
    canReadElevations,
    canReadApprovals,
  } = accessGovernanceCapabilities(
    meQuery.data?.access,
    meQuery.data?.authentication,
  )
  const allows = (permission: string) =>
    hasRequiredPermissions(permissions, [permission])
  const visibleTabs = tabs.filter(
    (tab) => !tab.permission || allows(tab.permission),
  )
  const [activeTab, setActiveTab] = useState<GovernanceTab>('overview')
  const [activePanelDirty, setActivePanelDirty] = useState(false)
  const [pendingTab, setPendingTab] = useState<GovernanceTab | null>(null)

  useEffect(() => {
    if (!visibleTabs.some((tab) => tab.id === activeTab)) {
      setActiveTab('overview')
      setActivePanelDirty(false)
    }
  }, [activeTab, visibleTabs])

  const requestTab = (tab: GovernanceTab) => {
    if (tab === activeTab) return
    if (activePanelDirty) {
      setPendingTab(tab)
      return
    }
    setActiveTab(tab)
  }

  const iamQuery = useQuery({
    queryKey: ['governance', 'iam-catalog'],
    queryFn: loadIAMCatalog,
  })
  const dataPolicyQuery = useQuery({
    queryKey: ['governance', 'data-policy'],
    queryFn: loadDataPolicyOverview,
    enabled: canReadDataPolicy,
  })
  const serviceAccountsQuery = useQuery({
    queryKey: ['governance', 'service-accounts'],
    queryFn: loadServiceAccounts,
    enabled: canReadServiceAccounts,
  })
  const accessReviewsQuery = useQuery({
    queryKey: ['governance', 'access-reviews'],
    queryFn: loadAccessReviews,
    enabled: canReadAccessReviews,
  })
  const elevationsQuery = useQuery({
    queryKey: ['governance', 'elevations'],
    queryFn: loadTemporaryElevations,
    enabled: canReadElevations,
  })
  const approvalsQuery = useQuery({
    queryKey: ['governance', 'action-approvals'],
    queryFn: loadActionApprovals,
    enabled: canReadApprovals,
  })

  useEffect(() => {
    const protectedQueries: Array<[boolean, readonly unknown[]]> = [
      [canReadDataPolicy, ['governance', 'data-policy']],
      [canReadServiceAccounts, ['governance', 'service-accounts']],
      [canReadAccessReviews, ['governance', 'access-reviews']],
      [canReadElevations, ['governance', 'elevations']],
      [canReadApprovals, ['governance', 'action-approvals']],
    ]
    for (const [allowed, queryKey] of protectedQueries) {
      if (!allowed) queryClient.removeQueries({ queryKey, exact: true })
    }
  }, [
    canReadAccessReviews,
    canReadApprovals,
    canReadDataPolicy,
    canReadElevations,
    canReadServiceAccounts,
    queryClient,
  ])

  const refresh = async () => {
    const requests: Array<Promise<unknown>> = [iamQuery.refetch()]
    if (canReadDataPolicy) requests.push(dataPolicyQuery.refetch())
    if (canReadServiceAccounts) requests.push(serviceAccountsQuery.refetch())
    if (canReadAccessReviews) requests.push(accessReviewsQuery.refetch())
    if (canReadElevations) requests.push(elevationsQuery.refetch())
    if (canReadApprovals) requests.push(approvalsQuery.refetch())
    await Promise.all(requests)
  }
  const isRefreshing = [
    iamQuery,
    dataPolicyQuery,
    serviceAccountsQuery,
    accessReviewsQuery,
    elevationsQuery,
    approvalsQuery,
  ].some((query) => query.isFetching)
  const writeGate = governanceWriteGate({
    activeTab,
    hasWriteIAM,
    canWriteIAM,
    hasWriteDataPolicy,
    hasDurableDataPolicy: hasRequiredPermissions(durablePermissions, [
      'write:data_policies',
    ]),
    dataPolicySessionReady,
  })

  return (
    <div className="space-y-3">
      <SettingsPageHeader
        scope="Organization"
        title="Access control"
        description="Manage access roles, group membership, and data-handling policy. Stale revisions are rejected before changes are applied."
        actions={
          <button
            type="button"
            className="inline-flex min-h-11 shrink-0 items-center justify-center gap-2 rounded border border-slate/25 px-3 py-2 text-sm font-semibold disabled:opacity-60 sm:min-h-0 dark:border-cyan-900/40"
            onClick={() => void refresh()}
            disabled={isRefreshing || activePanelDirty}
            title={
              activePanelDirty
                ? 'Save or discard the current edits before refreshing.'
                : undefined
            }
          >
            <RefreshCw
              className={`h-4 w-4 ${isRefreshing ? 'animate-spin' : ''}`}
              aria-hidden="true"
            />
            {isRefreshing ? 'Refreshing…' : 'Refresh'}
          </button>
        }
      >
        <nav
          className="flex max-w-full gap-1 overflow-x-auto"
          aria-label="Access control sections"
        >
          {visibleTabs.map((tab) => {
            const Icon = tab.icon
            const selected = tab.id === activeTab
            return (
              <button
                key={tab.id}
                type="button"
                className={`inline-flex min-h-11 shrink-0 items-center gap-2 border-b-2 px-3 py-2 text-sm font-semibold sm:min-h-0 ${
                  selected
                    ? 'border-cyan text-cyan-800 dark:text-cyan-100'
                    : 'border-transparent text-slate hover:text-ink dark:text-slate-300 dark:hover:text-white'
                }`}
                aria-current={selected ? 'page' : undefined}
                onClick={() => requestTab(tab.id)}
              >
                <Icon className="h-4 w-4" aria-hidden="true" />
                {tab.label}
              </button>
            )
          })}
        </nav>
      </SettingsPageHeader>

      {writeGate && <GovernanceWriteGate {...writeGate} />}

      {iamQuery.isLoading ? (
        <LoadingState label="Loading access catalog…" />
      ) : iamQuery.isError || !iamQuery.data ? (
        <ErrorState
          error={iamQuery.error}
          fallback="The IAM catalog could not be loaded"
          onRetry={() => void iamQuery.refetch()}
        />
      ) : activeTab === 'roles' ? (
        <AccessRolesPanel
          catalog={iamQuery.data}
          canWrite={canWriteIAM}
          durablePermissions={durablePermissions}
          onDirtyChange={setActivePanelDirty}
        />
      ) : activeTab === 'groups' ? (
        <AccessGroupsPanel
          catalog={iamQuery.data}
          canWrite={canWriteIAM}
          canReadUsers={canReadUsers}
          durablePermissions={durablePermissions}
          onDirtyChange={setActivePanelDirty}
        />
      ) : activeTab === 'data-policy' ? (
        <DataPolicyPanel
          overview={canReadDataPolicy ? dataPolicyQuery.data ?? null : null}
          roles={iamQuery.data.roles}
          canWrite={canWriteDataPolicy}
          isLoading={dataPolicyQuery.isLoading}
          error={dataPolicyQuery.error}
          onDirtyChange={setActivePanelDirty}
        />
      ) : (
        <GovernanceOverview
          roleCount={iamQuery.data.roles.length}
          groupCount={iamQuery.data.groups.length}
          dataPolicy={canReadDataPolicy ? dataPolicyQuery.data ?? null : null}
          dataPolicyState={optionalDataState(canReadDataPolicy, dataPolicyQuery)}
          serviceAccounts={canReadServiceAccounts ? serviceAccountsQuery.data ?? null : null}
          serviceAccountsState={optionalDataState(canReadServiceAccounts, serviceAccountsQuery)}
          accessReviews={canReadAccessReviews ? accessReviewsQuery.data ?? null : null}
          accessReviewsState={optionalDataState(canReadAccessReviews, accessReviewsQuery)}
          elevations={canReadElevations ? elevationsQuery.data ?? null : null}
          elevationsState={optionalDataState(canReadElevations, elevationsQuery)}
          approvals={canReadApprovals ? approvalsQuery.data ?? null : null}
          approvalsState={optionalDataState(canReadApprovals, approvalsQuery)}
          optionalErrors={[
            canReadDataPolicy ? dataPolicyQuery.error : null,
            canReadServiceAccounts ? serviceAccountsQuery.error : null,
            canReadAccessReviews ? accessReviewsQuery.error : null,
            canReadElevations ? elevationsQuery.error : null,
            canReadApprovals ? approvalsQuery.error : null,
          ]}
        />
      )}
      <ConfirmDialog
        open={pendingTab !== null}
        title="Discard unsaved governance changes?"
        description="Switching sections will discard the current revision draft."
        confirmLabel="Discard and switch"
        onConfirm={() => {
          if (pendingTab) setActiveTab(pendingTab)
          setPendingTab(null)
          setActivePanelDirty(false)
        }}
        onCancel={() => setPendingTab(null)}
      />
    </div>
  )
}

function GovernanceWriteGate({
  message,
  reauthenticate,
}: {
  message: string
  reauthenticate: boolean
}) {
  return (
    <div role="status" className="flex flex-col gap-3 rounded-xl border border-amber-300/60 bg-amber-50 px-4 py-3 text-sm text-amber-900 sm:flex-row sm:items-center sm:justify-between dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100">
      <div>
        <p className="font-semibold">Changes are currently locked</p>
        <p className="mt-1">{message}</p>
      </div>
      {reauthenticate && <Link className="inline-flex min-h-11 shrink-0 items-center justify-center rounded border border-current px-3 py-2 font-semibold sm:min-h-0" to="/settings/account">Open account security</Link>}
    </div>
  )
}

function governanceWriteGate({
  activeTab,
  hasWriteIAM,
  canWriteIAM,
  hasWriteDataPolicy,
  hasDurableDataPolicy,
  dataPolicySessionReady,
}: {
  activeTab: GovernanceTab
  hasWriteIAM: boolean
  canWriteIAM: boolean
  hasWriteDataPolicy: boolean
  hasDurableDataPolicy: boolean
  dataPolicySessionReady: boolean
}): { message: string; reauthenticate: boolean } | null {
  if (
    (activeTab === 'roles' || activeTab === 'groups') &&
    hasWriteIAM &&
    !canWriteIAM
  ) {
    return {
      message: 'Temporary elevation can inspect access policy, but persistent entitlements require durably assigned IAM authority.',
      reauthenticate: false,
    }
  }
  if (activeTab !== 'data-policy' || !hasWriteDataPolicy) return null
  if (!hasDurableDataPolicy) {
    return {
      message: 'Temporary elevation cannot change persistent handling policy. A durable data-policy role is required.',
      reauthenticate: false,
    }
  }
  if (!dataPolicySessionReady) {
    return {
      message: 'Handling-policy changes require a recently authenticated browser session with the applicable MFA assurance.',
      reauthenticate: true,
    }
  }
  return null
}

function sensitiveSessionReady(
  authentication: CurrentAuthentication | undefined,
): boolean {
  if (authentication?.sensitive_actions_ready !== undefined) {
    return authentication.sensitive_actions_ready
  }
  if (
    authentication?.credential_kind !== 'opaque_session' ||
    !authentication.recently_authenticated
  ) {
    return false
  }
  if (authentication.session_auth_method !== 'oidc') return true
  return authentication.identity_provider_mfa_asserted === true
}

function optionalDataState(
  allowed: boolean,
  query: { data?: unknown; isLoading: boolean; isError: boolean },
): OptionalDataState {
  if (!allowed) return 'hidden'
  if (query.isLoading) return 'loading'
  if (query.isError || query.data === undefined) return 'unavailable'
  return 'ready'
}

function optionalStateDetail(state: OptionalDataState): string {
  if (state === 'loading') return 'Loading inventory'
  if (state === 'unavailable') return 'Inventory unavailable'
  if (state === 'hidden') return 'Not available to this role'
  return 'No inventory returned'
}

function GovernanceOverview({
  roleCount,
  groupCount,
  dataPolicy,
  dataPolicyState,
  serviceAccounts,
  serviceAccountsState,
  accessReviews,
  accessReviewsState,
  elevations,
  elevationsState,
  approvals,
  approvalsState,
  optionalErrors,
}: {
  roleCount: number
  groupCount: number
  dataPolicy: Awaited<ReturnType<typeof loadDataPolicyOverview>> | null
  dataPolicyState: OptionalDataState
  serviceAccounts: Awaited<ReturnType<typeof loadServiceAccounts>> | null
  serviceAccountsState: OptionalDataState
  accessReviews: Awaited<ReturnType<typeof loadAccessReviews>> | null
  accessReviewsState: OptionalDataState
  elevations: Awaited<ReturnType<typeof loadTemporaryElevations>> | null
  elevationsState: OptionalDataState
  approvals: Awaited<ReturnType<typeof loadActionApprovals>> | null
  approvalsState: OptionalDataState
  optionalErrors: unknown[]
}) {
  const activeServiceAccounts = (serviceAccounts?.items ?? []).filter((item) => item.is_active)
  const openReviews = (accessReviews?.campaigns ?? []).filter((item) =>
    ['open', 'closed', 'applying', 'quarantined'].includes(item.status),
  )
  const liveElevations = (elevations?.elevations ?? []).filter((item) =>
    ['pending', 'approved', 'active'].includes(item.status),
  )
  const pendingApprovals = (approvals?.approvals ?? []).filter((item) => item.status === 'pending')
  const errors = optionalErrors.filter(Boolean)
  const attentionStates = [
    accessReviewsState,
    elevationsState,
    approvalsState,
  ]
  const attentionUnavailable = attentionStates.some(
    (state) => state === 'hidden' || state === 'unavailable',
  )
  const attentionLoading = attentionStates.some((state) => state === 'loading')

  return (
    <div className="grid gap-3 xl:grid-cols-[minmax(0,1.35fr)_minmax(320px,0.65fr)]">
      <section className="tl-surface rounded-xl p-3 sm:p-4" aria-labelledby="governance-posture-heading">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 id="governance-posture-heading" className="font-display text-xl">
              Current posture
            </h2>
            <p className="mt-1 text-sm text-slate dark:text-slate-300">
              Durable access objects and live review queues.
            </p>
          </div>
          {dataPolicy && <ModeBadge mode={dataPolicy.state.mode} />}
        </div>

        <dl className="mt-3 grid gap-x-5 sm:grid-cols-2">
          <Metric label="Access roles" value={roleCount} detail="System and custom" />
          <Metric label="Groups" value={groupCount} detail="Local and federated" />
          <Metric
            label="Machine identities"
            value={serviceAccounts?.total ?? '—'}
            detail={serviceAccounts ? `${activeServiceAccounts.length} active in loaded page` : optionalStateDetail(serviceAccountsState)}
          />
          <Metric label="Review campaigns" value={accessReviews?.total ?? '—'} detail={accessReviews ? `${openReviews.length} attention states in loaded page` : optionalStateDetail(accessReviewsState)} />
          <Metric label="Elevation requests" value={elevations?.total ?? '—'} detail={elevations ? `${liveElevations.length} live in loaded page` : optionalStateDetail(elevationsState)} />
          <Metric label="Approval requests" value={approvals?.total ?? '—'} detail={approvals ? `${pendingApprovals.length} pending in loaded page` : optionalStateDetail(approvalsState)} />
        </dl>

        {errors.length > 0 && (
          <div role="status" className="mt-3 flex items-start gap-2 rounded border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
            One or more permitted governance inventories could not be loaded. Counts and queue rows from those inventories are omitted; core IAM data remains available.
          </div>
        )}
      </section>

      <section className="tl-surface rounded-xl p-3 sm:p-4" aria-labelledby="data-boundary-heading">
        <h2 id="data-boundary-heading" className="font-display text-xl">
          Data handling
        </h2>
        {!dataPolicy ? (
          <p className="mt-3 text-sm text-slate dark:text-slate-300">
            {dataPolicyState === 'loading'
              ? 'Loading data-policy state…'
              : dataPolicyState === 'unavailable'
                ? 'Data-policy state could not be loaded.'
                : 'Data-policy state is not available to this role.'}
          </p>
        ) : (
          <div className="mt-2 space-y-2 text-sm">
            <div className="flex items-center justify-between gap-3">
              <span className="text-slate dark:text-slate-300">Policy revision</span>
              <span className="font-mono font-semibold">{dataPolicy.state.revision}</span>
            </div>
            <div className="flex items-center justify-between gap-3">
              <span className="text-slate dark:text-slate-300">Coverage</span>
              <span className="font-semibold">
                {dataPolicy.preflight.current_coverage_version} /{' '}
                {dataPolicy.preflight.required_coverage_version}
              </span>
            </div>
            <div className="flex items-center justify-between gap-3">
              <span className="text-slate dark:text-slate-300">Data-handling labels</span>
              <span className="font-semibold">{dataPolicy.labels.length}</span>
            </div>
            <div className="flex items-start gap-2 border-t border-slate/15 pt-2 dark:border-white/10">
              {dataPolicy.preflight.ready_for_enforcement ? (
                <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" aria-hidden="true" />
              ) : (
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" aria-hidden="true" />
              )}
              <div>
                <p className="font-semibold">
                  {dataPolicy.preflight.ready_for_enforcement
                    ? 'Ready for enforcement'
                    : `${dataPolicy.preflight.blockers.length} preflight blocker${dataPolicy.preflight.blockers.length === 1 ? '' : 's'}`}
                </p>
                <p className="mt-1 text-xs text-slate dark:text-slate-400">
                  Updated {formatDateTime(dataPolicy.state.updated_at)}
                </p>
              </div>
            </div>
          </div>
        )}
      </section>

      <section className="tl-surface rounded-xl p-3 sm:p-4 xl:col-span-2" aria-labelledby="governance-attention-heading">
        <h2 id="governance-attention-heading" className="font-display text-xl">
          Items needing attention
        </h2>
        <div className="mt-2 overflow-x-auto rounded border border-slate/20 dark:border-white/10">
          <table className="min-w-[760px] w-full text-left text-sm">
            <thead className="bg-slate/5 text-xs text-slate dark:bg-white/[0.04] dark:text-slate-300">
              <tr>
                <th scope="col" className="px-3 py-2 font-semibold">Queue</th>
                <th scope="col" className="px-3 py-2 font-semibold">Subject</th>
                <th scope="col" className="px-3 py-2 font-semibold">State</th>
                <th scope="col" className="px-3 py-2 font-semibold">Deadline</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate/15 dark:divide-white/10">
              {openReviews.slice(0, 3).map((review) => (
                <AttentionRow key={review.id} queue="Access review" subject={review.name} state={review.status} deadline={review.review_due_at} />
              ))}
              {liveElevations.slice(0, 3).map((elevation) => (
                <AttentionRow key={elevation.id} queue="Elevation" subject={`${elevation.target_email} · ${elevation.role_name}`} state={elevation.status} deadline={elevation.grant_expires_at ?? elevation.request_expires_at} />
              ))}
              {pendingApprovals.slice(0, 3).map((approval) => (
                <AttentionRow key={approval.id} queue="Approval" subject={`${approval.action_label} · ${approval.target_type}`} state={approval.status} deadline={approval.expires_at} />
              ))}
              {openReviews.length + liveElevations.length + pendingApprovals.length === 0 && (
                <tr>
                  <td colSpan={4} className="px-3 py-4 text-center text-slate dark:text-slate-400">
                    {attentionLoading
                      ? 'Governance queues are still loading.'
                      : attentionUnavailable
                        ? 'No visible items are loaded; one or more queues are unavailable to this role.'
                        : 'No governance queue items in the loaded page require attention.'}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}

function Metric({ label, value, detail }: { label: string; value: number | string; detail: string }) {
  return (
    <div className="flex items-start justify-between gap-3 border-t border-slate/15 py-2 dark:border-white/10">
      <div className="min-w-0">
        <dt className="text-sm font-semibold text-ink dark:text-slate-100">{label}</dt>
        <dd className="mt-0.5 text-xs text-slate dark:text-slate-400">{detail}</dd>
      </div>
      <dd className="shrink-0 font-display text-xl text-ink dark:text-white">{value}</dd>
    </div>
  )
}

function AttentionRow({ queue, subject, state, deadline }: { queue: string; subject: string; state: string; deadline: string }) {
  return (
    <tr>
      <td className="px-3 py-2.5 font-semibold">{queue}</td>
      <td className="max-w-md truncate px-3 py-2.5">{subject}</td>
      <td className="px-3 py-2.5 capitalize">{state.replaceAll('_', ' ')}</td>
      <td className="whitespace-nowrap px-3 py-2.5 text-slate dark:text-slate-300">{formatDateTime(deadline)}</td>
    </tr>
  )
}

function ModeBadge({ mode }: { mode: 'disabled' | 'audit' | 'enforced' }) {
  const className =
    mode === 'enforced'
      ? 'border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-100'
      : mode === 'audit'
        ? 'border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100'
        : 'border-slate/25 bg-slate/5 text-slate dark:border-white/10 dark:bg-white/[0.04] dark:text-slate-300'
  return (
    <span className={`rounded-full border px-2.5 py-1 text-xs font-semibold capitalize ${className}`}>
      {mode}
    </span>
  )
}

function LoadingState({ label }: { label: string }) {
  return <div className="tl-surface rounded-xl p-4 text-sm text-slate dark:text-slate-300">{label}</div>
}

function ErrorState({ error, fallback, onRetry }: { error: unknown; fallback: string; onRetry: () => void }) {
  return (
    <div className="tl-surface rounded-xl p-3.5" role="alert">
      <div className="flex items-start gap-3">
        <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-600" aria-hidden="true" />
        <div>
          <h2 className="font-display text-lg">Governance data unavailable</h2>
          <p className="mt-1 text-sm text-slate dark:text-slate-300">{resolveApiErrorMessage(error, fallback)}</p>
          <button type="button" className="mt-3 inline-flex min-h-11 items-center gap-2 rounded border border-slate/25 px-3 py-2 text-sm font-semibold sm:min-h-0 dark:border-cyan-900/40" onClick={onRetry}>
            <RefreshCw className="h-4 w-4" aria-hidden="true" />
            Retry
          </button>
        </div>
      </div>
    </div>
  )
}
