import { useState } from 'react'

import { resolveApiErrorMessage } from '../api/errors'
import { CopyableIdentifier } from '../components/CopyableIdentifier'
import type { InvestigationEvidence } from '../types/investigations'
import { formatDateTime } from '../utils/datetime'
import {
  formatEvidenceType,
  isTerminalInvestigationAccessError,
  safeInvestigationExternalUrl,
} from './investigationPageModel'
import {
  InvestigationCollectionPagination,
  InvestigationCollectionQueryState,
} from './InvestigationCollectionPagination'
import { InvestigationConfirmDialog } from './InvestigationShared'
import { InvestigationEvidenceFinder } from './InvestigationEvidenceFinder'
import type { InvestigationDetailController } from './useInvestigationDetail'

export function InvestigationEvidencePanel({
  controller,
}: {
  controller: InvestigationDetailController
}) {
  const detail = controller.detailQuery.data
  const [pendingRemoval, setPendingRemoval] = useState<{
    evidence: InvestigationEvidence
    expectedVersion: number
  } | null>(null)
  if (!detail || !controller.access) return null
  const terminalCollectionError =
    controller.evidenceQuery.isError &&
    isTerminalInvestigationAccessError(controller.evidenceQuery.error)
  const evidencePage = terminalCollectionError ? undefined : controller.evidenceQuery.data
  const hasEvidencePage = Boolean(evidencePage)
  const evidenceTotal = evidencePage?.total ?? detail.evidence_count
  const removalError =
    pendingRemoval &&
    controller.mutation.isError &&
    controller.mutation.variables?.kind === 'remove-evidence' &&
    controller.mutation.variables.evidenceId === pendingRemoval.evidence.id
      ? resolveApiErrorMessage(controller.mutation.error, 'Evidence could not be removed', {
          retryGuidance: 'Review the latest evidence list and try again.',
        })
      : null

  return (
    <section aria-labelledby="investigation-evidence-heading" className="min-w-0">
      <div>
        <h2 id="investigation-evidence-heading" className="text-base font-semibold">
          Evidence ({evidenceTotal})
        </h2>
        <p className="mt-0.5 text-sm text-slate dark:text-slate-300">
          Evidence keeps a point-in-time snapshot so the investigation remains understandable as
          source records change.
        </p>
      </div>

      {controller.access.canWrite && !terminalCollectionError && (
        <InvestigationEvidenceFinder controller={controller} />
      )}

      <InvestigationCollectionQueryState
        label="evidence"
        total={evidenceTotal}
        truncated={detail.evidence_truncated}
        loading={controller.evidenceQuery.isLoading}
        fetching={controller.evidenceQuery.isFetching}
        error={controller.evidenceQuery.isError ? controller.evidenceQuery.error : null}
        hasData={hasEvidencePage}
        onRetry={() => void controller.evidenceQuery.refetch()}
      />

      {evidencePage && evidencePage.evidence.length === 0 && evidencePage.total === 0 ? (
        <p className="py-8 text-center text-sm text-slate dark:text-slate-300">
          No evidence has been added to this investigation.
        </p>
      ) : evidencePage && evidencePage.evidence.length === 0 ? (
        <p role="status" className="py-8 text-center text-sm text-slate dark:text-slate-300">
          This page no longer contains evidence. Returning to the last available page...
        </p>
      ) : evidencePage ? (
        <div className="mt-4 divide-y divide-slate/15 border-y border-slate/15 dark:divide-white/10 dark:border-white/10">
          {evidencePage.evidence.map((evidence) => (
            <EvidenceEntry
              key={evidence.id}
              evidence={evidence}
              canRemove={controller.access?.canWrite ?? false}
              pending={controller.mutation.isPending}
              onRemove={(evidence) =>
                setPendingRemoval({ evidence, expectedVersion: detail.version })
              }
            />
          ))}
        </div>
      ) : null}

      {evidencePage && (
        <InvestigationCollectionPagination
          label="evidence"
          total={evidencePage.total}
          page={evidencePage.page}
          pageSize={evidencePage.page_size}
          itemCount={evidencePage.evidence.length}
          fetching={controller.evidenceQuery.isFetching}
          disabled={controller.mutation.isPending}
          disabledReason="Wait for the current evidence change to finish before changing pages."
          onPageChange={controller.setEvidencePage}
        />
      )}

      <InvestigationConfirmDialog
        open={Boolean(pendingRemoval)}
        title="Remove evidence?"
        description={
          pendingRemoval
            ? `Remove the saved snapshot “${pendingRemoval.evidence.title_snapshot}” from this investigation? The source record will not be deleted.`
            : undefined
        }
        confirmLabel="Remove evidence"
        isConfirming={controller.mutation.isPending}
        error={removalError}
        onCancel={() => setPendingRemoval(null)}
        onConfirm={() => {
          if (!pendingRemoval) return
          controller.mutation.mutate(
            {
              kind: 'remove-evidence',
              evidenceId: pendingRemoval.evidence.id,
              expectedVersion: pendingRemoval.expectedVersion,
            },
            { onSuccess: () => setPendingRemoval(null) },
          )
        }}
      />
    </section>
  )
}

function EvidenceEntry({
  evidence,
  canRemove,
  pending,
  onRemove,
}: {
  evidence: InvestigationEvidence
  canRemove: boolean
  pending: boolean
  onRemove: (evidence: InvestigationEvidence) => void
}) {
  const metadata = Object.entries(evidence.metadata_snapshot)
  const sourceUrl = safeInvestigationExternalUrl(evidence.url_snapshot)
  return (
    <article className="min-w-0 py-3">
      <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="tl-chip tl-chip-neutral">
              {formatEvidenceType(evidence.source_type)}
            </span>
          </div>
          <h3 className="mt-1 break-words font-semibold">{evidence.title_snapshot}</h3>
          {evidence.description_snapshot && (
            <p className="mt-1 whitespace-pre-wrap break-words text-sm text-slate dark:text-slate-300">
              {evidence.description_snapshot}
            </p>
          )}
          {sourceUrl && (
            <a
              href={sourceUrl}
              target="_blank"
              rel="noreferrer"
              className="mt-2 inline-flex min-h-11 items-center break-all text-sm font-semibold text-cyan hover:underline md:min-h-0"
            >
              Open captured source URL
            </a>
          )}
          {evidence.url_snapshot && !sourceUrl && (
            <p className="mt-2 text-xs text-amber-800 dark:text-amber-200">
              The captured source URL uses an unsupported or unsafe scheme and cannot be opened
              here.
            </p>
          )}
        </div>
        {canRemove && (
          <button
            type="button"
            className="min-h-11 shrink-0 rounded border border-slate/20 px-3 py-2 text-sm font-semibold text-red-700 disabled:opacity-50 md:min-h-0 md:py-1.5 dark:border-white/10 dark:text-red-300"
            disabled={pending}
            aria-label={`Remove evidence ${evidence.title_snapshot}`}
            onClick={() => onRemove(evidence)}
          >
            Remove
          </button>
        )}
      </div>
      {evidence.note && (
        <div className="mt-2 border-l-2 border-cyan/40 pl-3">
          <p className="text-xs font-semibold text-slate dark:text-slate-400">Analyst context</p>
          <p className="mt-0.5 whitespace-pre-wrap break-words text-sm">{evidence.note}</p>
        </div>
      )}
      <details className="mt-2 text-xs">
        <summary className="min-h-11 cursor-pointer py-2 font-semibold text-slate md:min-h-0 md:py-1 dark:text-slate-300">
          Technical details
        </summary>
        <dl className="mt-1 grid min-w-0 gap-x-4 gap-y-2 sm:grid-cols-2">
          <div className="min-w-0">
            <dt className="text-slate dark:text-slate-400">Source ID</dt>
            <dd className="mt-0.5">
              <CopyableIdentifier label="Source ID" value={evidence.source_id} />
            </dd>
          </div>
          {metadata.map(([key, value]) => (
            <div key={key} className="min-w-0">
              <dt className="break-words text-slate dark:text-slate-400">{humanizeKey(key)}</dt>
              <dd className="mt-0.5 break-all font-mono">{formatMetadataValue(value)}</dd>
            </div>
          ))}
        </dl>
      </details>
      <p className="mt-2 text-xs text-slate dark:text-slate-400">
        Snapshot added{' '}
        <time dateTime={evidence.created_at}>{formatDateTime(evidence.created_at)}</time>
      </p>
    </article>
  )
}

function humanizeKey(key: string): string {
  const value = key.replaceAll('_', ' ')
  return `${value.charAt(0).toUpperCase()}${value.slice(1)}`
}

function formatMetadataValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return 'Not recorded'
  if (Array.isArray(value)) return value.map((entry) => String(entry)).join(', ') || 'None'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}
