import { useEffect, useRef, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'

import { resolveApiErrorMessage } from '../api/errors'
import type { InvestigationDetailTab, InvestigationStatus } from '../types/investigations'
import { formatDateTime } from '../utils/datetime'
import { InvestigationActivityPanel } from './InvestigationActivityPanel'
import { InvestigationEvidencePanel } from './InvestigationEvidencePanel'
import { InvestigationMembersPanel } from './InvestigationMembersPanel'
import { InvestigationNotesPanel } from './InvestigationNotesPanel'
import { InvestigationOverviewPanel } from './InvestigationOverviewPanel'
import {
  INVESTIGATION_TABS,
  isInvestigationVersionConflict,
  isTerminalInvestigationAccessError,
} from './investigationPageModel'
import {
  InvestigationInlineMessage,
  InvestigationConfirmDialog,
  InvestigationLoading,
  InvestigationPageError,
  InvestigationRefreshWarning,
  InvestigationSeverityChip,
  InvestigationStatusChip,
} from './InvestigationShared'
import type {
  InvestigationDetailController,
  InvestigationMutationOperation,
} from './useInvestigationDetail'

type LifecycleConfirmation = 'close' | 'reopen' | 'archive' | null

export function InvestigationDetailWorkspace({
  controller,
}: {
  controller: InvestigationDetailController
}) {
  const detail = controller.detailQuery.data
  const location = useLocation()
  const [lifecycleConfirmation, setLifecycleConfirmation] = useState<LifecycleConfirmation>(null)
  const [closeDisposition, setCloseDisposition] = useState('')
  const listSearch = (location.state as { investigationListSearch?: unknown } | null)
    ?.investigationListSearch
  const backPath =
    typeof listSearch === 'string' && listSearch.startsWith('?')
      ? `/investigations${listSearch}`
      : '/investigations'
  const terminalAccessError =
    controller.detailQuery.isError &&
    isTerminalInvestigationAccessError(controller.detailQuery.error)

  if (!detail && controller.detailQuery.isLoading) {
    return <InvestigationLoading message="Loading investigation workspace..." />
  }
  if (terminalAccessError || (!detail && controller.detailQuery.isError)) {
    return (
      <InvestigationPageError
        error={controller.detailQuery.error}
        fallback="Investigation could not be loaded"
        onRetry={() => void controller.detailQuery.refetch()}
      />
    )
  }
  if (!detail || !controller.access) return null

  const runLifecycleMutation = (operation: InvestigationMutationOperation) => {
    controller.mutation.mutate(operation, {
      onSuccess: () => {
        setLifecycleConfirmation(null)
        setCloseDisposition('')
      },
    })
  }

  return (
    <div className="tl-surface min-w-0 overflow-hidden rounded-xl">
      <header className="border-b border-slate/20 px-3 py-3 dark:border-white/10 sm:px-4 sm:py-4">
        <Link
          to={backPath}
          className="inline-flex min-h-11 items-center text-sm font-semibold text-cyan hover:underline md:min-h-0"
        >
          Back to investigations
        </Link>
        <div className="mt-2 flex min-w-0 flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0">
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <h1 className="min-w-0 break-words font-display text-xl leading-tight sm:text-2xl">
                {detail.title}
              </h1>
              <InvestigationStatusChip status={detail.status} />
              <InvestigationSeverityChip severity={detail.severity} />
            </div>
            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate dark:text-slate-400">
              <span>Assigned: {detail.assignee_email ?? 'Unassigned'}</span>
              <span className="capitalize">
                Access: {detail.current_user_role ?? 'team read-only'}
              </span>
              <span>Version {detail.version}</span>
              <span>
                Record updated{' '}
                <time dateTime={detail.updated_at}>{formatDateTime(detail.updated_at)}</time>
              </span>
              {controller.detailQuery.dataUpdatedAt > 0 && (
                <span>
                  Checked {formatDateTime(new Date(controller.detailQuery.dataUpdatedAt))}
                </span>
              )}
            </div>
          </div>
          <LifecycleActions
            status={detail.status}
            canWrite={controller.access.canWrite}
            canArchive={controller.access.canArchive}
            canReopen={controller.access.canReopen}
            pending={controller.mutation.isPending}
            onUpdateStatus={(status) =>
              runLifecycleMutation({ kind: 'update', changes: { status } })
            }
            onConfirm={setLifecycleConfirmation}
          />
          <button
            type="button"
            className="min-h-11 rounded border border-slate/30 px-3 py-2 text-sm font-semibold disabled:opacity-60 md:min-h-0 dark:border-white/15"
            disabled={controller.detailQuery.isFetching}
            onClick={() => void controller.refreshLatest()}
          >
            {controller.detailQuery.isFetching ? 'Refreshing...' : 'Refresh'}
          </button>
        </div>

        <DetailTabs controller={controller} />
      </header>

      <div className="space-y-2 px-3 pt-3 sm:px-4">
        {controller.detailQuery.isError && (
          <InvestigationRefreshWarning onRetry={() => void controller.detailQuery.refetch()}>
            {resolveApiErrorMessage(
              controller.detailQuery.error,
              'Investigation could not be refreshed',
            )}{' '}
            The last loaded workspace remains visible.
          </InvestigationRefreshWarning>
        )}
        {controller.conflictNotice && (
          <div
            role="alert"
            className="flex flex-wrap items-center justify-between gap-2 rounded border border-amber-300/70 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-700/50 dark:bg-amber-950/30 dark:text-amber-100"
          >
            <span>{controller.conflictNotice}</span>
            <button
              type="button"
              className="min-h-11 rounded border border-amber-400 px-3 py-2 font-semibold md:min-h-0 md:py-1 dark:border-amber-700"
              onClick={() => void controller.refreshLatest()}
            >
              Refresh latest
            </button>
          </div>
        )}
        {controller.mutation.isError &&
          !isInvestigationVersionConflict(controller.mutation.error) && (
            <InvestigationInlineMessage tone="error">
              {resolveApiErrorMessage(
                controller.mutation.error,
                mutationFallback(controller.mutation.variables),
                {
                  retryGuidance:
                    'Review the submitted values and try again. Unsaved input has been preserved.',
                },
              )}
            </InvestigationInlineMessage>
          )}
        {controller.successNotice && (
          <InvestigationInlineMessage tone="success">
            {controller.successNotice}
          </InvestigationInlineMessage>
        )}
        {controller.access.readOnlyReason && (
          <InvestigationInlineMessage tone="info">
            {controller.access.readOnlyReason}
          </InvestigationInlineMessage>
        )}
      </div>

      <main className="min-w-0 px-3 py-3 sm:px-4 sm:py-4">
        {controller.activeTab === 'overview' && (
          <InvestigationOverviewPanel controller={controller} />
        )}
        {controller.activeTab === 'members' && (
          <InvestigationMembersPanel controller={controller} />
        )}
        {controller.activeTab === 'evidence' && (
          <InvestigationEvidencePanel controller={controller} />
        )}
        {controller.activeTab === 'notes' && <InvestigationNotesPanel controller={controller} />}
        {controller.activeTab === 'activity' && (
          <InvestigationActivityPanel controller={controller} />
        )}
      </main>

      <LifecycleDialog
        kind={lifecycleConfirmation}
        title={detail.title}
        disposition={closeDisposition}
        pending={controller.mutation.isPending}
        error={
          lifecycleConfirmation &&
          controller.mutation.isError &&
          controller.mutation.variables?.kind === 'update'
            ? resolveApiErrorMessage(
                controller.mutation.error,
                mutationFallback(controller.mutation.variables),
                {
                  retryGuidance: 'Review the latest investigation state and try again.',
                },
              )
            : null
        }
        onDispositionChange={setCloseDisposition}
        onCancel={() => setLifecycleConfirmation(null)}
        onConfirm={() => {
          if (lifecycleConfirmation === 'close') {
            runLifecycleMutation({
              kind: 'update',
              changes: {
                status: 'closed',
                disposition: closeDisposition.trim() || null,
              },
            })
          } else if (lifecycleConfirmation === 'reopen') {
            runLifecycleMutation({
              kind: 'update',
              changes: { status: 'open', disposition: null },
            })
          } else if (lifecycleConfirmation === 'archive') {
            runLifecycleMutation({
              kind: 'update',
              changes: { status: 'archived' },
            })
          }
        }}
      />
      {controller.confirmDiscardChanges.discardDialog}
    </div>
  )
}

function DetailTabs({ controller }: { controller: InvestigationDetailController }) {
  const detail = controller.detailQuery.data
  const tabsRef = useRef<HTMLElement>(null)
  useEffect(() => {
    const activeTab = tabsRef.current?.querySelector<HTMLElement>(
      '[aria-current="page"]',
    )
    activeTab?.scrollIntoView?.({ block: 'nearest', inline: 'center' })
  }, [controller.activeTab])
  if (!detail) return null
  const counts: Partial<Record<InvestigationDetailTab, number>> = {
    members: detail.member_count,
    evidence: detail.evidence_count,
    notes: detail.note_count,
    activity: controller.activityQuery.data?.total,
  }
  return (
    <nav
      ref={tabsRef}
      aria-label="Investigation workspace"
      className="mt-3 flex gap-1 overflow-x-auto border-t border-slate/15 pt-3 dark:border-white/10"
    >
      {INVESTIGATION_TABS.map((tab) => (
        <button
          key={tab.value}
          type="button"
          className={`min-h-11 shrink-0 rounded px-3 py-2 text-sm font-semibold md:min-h-0 md:py-1.5 ${
            controller.activeTab === tab.value
              ? 'bg-ink text-white dark:bg-cyan dark:text-[#053c2e]'
              : 'border border-slate/20 text-slate-700 dark:border-white/10 dark:text-slate-200'
          }`}
          aria-current={controller.activeTab === tab.value ? 'page' : undefined}
          onClick={() => controller.setActiveTab(tab.value)}
        >
          {tab.label}
          {typeof counts[tab.value] === 'number' ? ` (${counts[tab.value]})` : ''}
        </button>
      ))}
    </nav>
  )
}

function LifecycleActions({
  status,
  canWrite,
  canArchive,
  canReopen,
  pending,
  onUpdateStatus,
  onConfirm,
}: {
  status: InvestigationStatus
  canWrite: boolean
  canArchive: boolean
  canReopen: boolean
  pending: boolean
  onUpdateStatus: (status: InvestigationStatus) => void
  onConfirm: (kind: Exclude<LifecycleConfirmation, null>) => void
}) {
  if (status === 'archived') {
    return canReopen ? (
      <button
        type="button"
        className="min-h-11 rounded border border-slate/30 px-3 py-2 text-sm font-semibold dark:border-white/15"
        disabled={pending}
        onClick={() => onConfirm('reopen')}
      >
        Reopen investigation
      </button>
    ) : null
  }
  if (!canWrite) return null
  return (
    <div className="grid w-full grid-cols-2 gap-2 sm:flex sm:w-auto sm:flex-wrap">
      {status === 'open' && (
        <button
          type="button"
          className="min-h-11 rounded border border-slate/30 px-3 py-2 text-sm font-semibold dark:border-white/15"
          disabled={pending}
          onClick={() => onUpdateStatus('monitoring')}
        >
          Start monitoring
        </button>
      )}
      {status === 'monitoring' && (
        <button
          type="button"
          className="min-h-11 rounded border border-slate/30 px-3 py-2 text-sm font-semibold dark:border-white/15"
          disabled={pending}
          onClick={() => onUpdateStatus('open')}
        >
          Return to open
        </button>
      )}
      {status !== 'closed' && (
        <button
          type="button"
          className="min-h-11 rounded bg-ink px-3 py-2 text-sm font-semibold text-white dark:bg-cyan dark:text-[#053c2e]"
          disabled={pending}
          onClick={() => onConfirm('close')}
        >
          Close investigation
        </button>
      )}
      {status === 'closed' && (
        <button
          type="button"
          className="min-h-11 rounded border border-slate/30 px-3 py-2 text-sm font-semibold dark:border-white/15"
          disabled={pending}
          onClick={() => onConfirm('reopen')}
        >
          Reopen investigation
        </button>
      )}
      {status === 'closed' && canArchive && (
        <button
          type="button"
          className="tl-button-danger col-span-2 min-h-11 rounded px-3 py-2 text-sm font-semibold sm:col-auto"
          disabled={pending}
          onClick={() => onConfirm('archive')}
        >
          Archive
        </button>
      )}
    </div>
  )
}

function LifecycleDialog({
  kind,
  title,
  disposition,
  pending,
  error,
  onDispositionChange,
  onCancel,
  onConfirm,
}: {
  kind: LifecycleConfirmation
  title: string
  disposition: string
  pending: boolean
  error: string | null
  onDispositionChange: (value: string) => void
  onCancel: () => void
  onConfirm: () => void
}) {
  const content = {
    close: {
      title: 'Close investigation?',
      description: `Close “${title}” and move it out of active work?`,
      label: 'Close investigation',
      tone: 'primary' as const,
    },
    reopen: {
      title: 'Reopen investigation?',
      description: `Return “${title}” to active investigation work? Its previous closure disposition will be cleared.`,
      label: 'Reopen investigation',
      tone: 'primary' as const,
    },
    archive: {
      title: 'Archive investigation?',
      description: `Archive “${title}”? It will become read-only until an owner or editor explicitly reopens it.`,
      label: 'Archive investigation',
      tone: 'danger' as const,
    },
  }
  if (!kind) return null
  const selected = content[kind]
  return (
    <InvestigationConfirmDialog
      open
      title={selected.title}
      description={selected.description}
      error={error}
      confirmLabel={selected.label}
      confirmTone={selected.tone}
      isConfirming={pending}
      onCancel={onCancel}
      onConfirm={onConfirm}
    >
      {kind === 'close' ? (
        <div>
          <label htmlFor="investigation-close-disposition" className="text-sm font-semibold">
            Closure disposition (optional)
          </label>
          <input
            id="investigation-close-disposition"
            maxLength={64}
            className="mt-1 min-h-11 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
            value={disposition}
            onChange={(event) => onDispositionChange(event.target.value)}
            placeholder="Resolved, benign, duplicate..."
          />
        </div>
      ) : undefined}
    </InvestigationConfirmDialog>
  )
}

function mutationFallback(operation: InvestigationMutationOperation | undefined): string {
  const labels: Record<InvestigationMutationOperation['kind'], string> = {
    update: 'Investigation could not be updated',
    'add-member': 'Member could not be added',
    'update-member': 'Member role could not be updated',
    'remove-member': 'Member could not be removed',
    'add-evidence': 'Evidence could not be added',
    'remove-evidence': 'Evidence could not be removed',
    'add-note': 'Note could not be added',
    'update-note': 'Note could not be updated',
    'remove-note': 'Note could not be removed',
  }
  return operation ? labels[operation.kind] : 'Investigation change could not be saved'
}
