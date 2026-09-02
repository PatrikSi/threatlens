import { useEffect, useRef, useState } from 'react'

import { ConfirmDialog } from '../components/ConfirmDialog'
import type {
  LifecyclePolicyDraft,
  LifecyclePreview,
  LifecycleTarget,
} from '../types/lifecycle'
import { formatDateTime } from '../utils/datetime'
import {
  lifecycleDraftScheduleLabel,
  lifecycleNextScheduleAt,
  lifecyclePreviewCountLabel,
} from './lifecycleModel'

export interface LifecyclePolicyChangeConfirmation {
  confirmation: 'PURGE'
  preview_id: string
  reason: string
}

export function LifecyclePolicyChangeDialog({
  open,
  target,
  enabling,
  previousRetentionDays,
  nextRetentionDays,
  previousRunCap,
  nextRunCap,
  disabledSafeguardLabels,
  draft,
  preview,
  previewCurrent,
  error,
  blocked,
  busy,
  onCancel,
  onConfirm,
  onReload,
}: {
  open: boolean
  target: LifecycleTarget
  enabling: boolean
  previousRetentionDays: number
  nextRetentionDays: number
  previousRunCap: number
  nextRunCap: number
  disabledSafeguardLabels: string[]
  draft: LifecyclePolicyDraft
  preview: LifecyclePreview
  previewCurrent: boolean
  error: string
  blocked: boolean
  busy: boolean
  onCancel: () => void
  onConfirm: (confirmation: LifecyclePolicyChangeConfirmation) => void
  onReload: () => void
}) {
  const [reason, setReason] = useState('')
  const [typedConfirmation, setTypedConfirmation] = useState('')
  const confirmationRef = useRef<HTMLInputElement | null>(null)

  useEffect(() => {
    if (!open) return
    setReason('')
    setTypedConfirmation('')
  }, [open])

  const runCapIncreased = target.policy.enabled
    && draft.enabled
    && nextRunCap > previousRunCap
  const summary = lifecyclePolicyChangeSummary({
    enabling,
    previousRetentionDays,
    nextRetentionDays,
    runCapIncreased,
    previousRunCap,
    nextRunCap,
    disabledSafeguardLabels,
  })
  const nextRun = draft.enabled ? lifecycleNextScheduleAt(draft) : null
  const disabled = busy || blocked

  return (
    <ConfirmDialog
      open={open}
      title={`Confirm ${target.label} policy change`}
      description={`${summary} Future runs can irreversibly remove eligible data.`}
      confirmLabel="Save destructive policy"
      confirmingLabel="Saving policy..."
      confirmTone="danger"
      isConfirming={busy}
      confirmDisabled={
        reason.trim().length < 10
        || typedConfirmation !== 'PURGE'
        || !previewCurrent
        || blocked
      }
      cancelDisabled={busy}
      initialFocusRef={confirmationRef}
      onCancel={onCancel}
      onConfirm={() => onConfirm({
        confirmation: 'PURGE',
        preview_id: preview.id,
        reason: reason.trim(),
      })}
    >
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 rounded border border-slate/15 bg-slate/5 p-3 text-xs dark:border-white/10 dark:bg-white/[0.03]">
        <PreviewFact
          label="Eligible records"
          value={lifecyclePreviewCountLabel(preview.eligible_count, preview)}
        />
        <PreviewFact
          label="Protected records"
          value={preview.protected_count.toLocaleString()}
        />
        <PreviewFact label="Delete before" value={formatDateTime(preview.cutoff_at)} />
        <PreviewFact
          label="Run cap"
          value={`${draft.max_records_per_run.toLocaleString()} records`}
        />
        <PreviewFact label="Schedule" value={lifecycleDraftScheduleLabel(draft)} />
        <PreviewFact
          label="Next scheduled run"
          value={nextRun ? formatDateTime(nextRun) : 'Not scheduled while disabled'}
        />
      </dl>
      {(preview.count_is_lower_bound || preview.is_partial) && (
        <p className="text-amber-800 dark:text-amber-200">
          This bounded preview found at least the displayed number of eligible
          records. Additional records may qualify.
        </p>
      )}
      {!previewCurrent && (
        <p role="alert" className="text-red-700 dark:text-red-300">
          This preview expired or no longer matches the current policy draft.
          Close the dialog and generate a fresh preview.
        </p>
      )}
      <label className="block font-semibold">
        Change reason
        <textarea
          value={reason}
          rows={2}
          maxLength={500}
          disabled={disabled}
          className="mt-1 block w-full rounded border border-slate/30 bg-white px-3 py-2 font-normal dark:border-cyan-900/40 dark:bg-[#072019]"
          placeholder="Example: Apply the approved data-retention standard"
          onChange={(event) => setReason(event.target.value)}
        />
        <span className="mt-1 block text-xs font-normal text-slate dark:text-slate-400">At least 10 characters; this is recorded in the audit trail.</span>
      </label>
      <label className="block font-semibold">
        Type PURGE to confirm
        <input
          ref={confirmationRef}
          value={typedConfirmation}
          autoComplete="off"
          disabled={disabled}
          className="mt-1 block w-full rounded border border-slate/30 bg-white px-3 py-2 font-mono font-normal dark:border-cyan-900/40 dark:bg-[#072019]"
          onChange={(event) => setTypedConfirmation(event.target.value)}
        />
      </label>
      {error && (
        <div
          role="alert"
          className="rounded border border-red-300/60 bg-red-50 px-3 py-2 text-red-900 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-100"
        >
          <p>{error}</p>
          {blocked && (
            <button
              type="button"
              className="mt-2 min-h-11 rounded border border-current px-3 py-2 font-semibold sm:min-h-0"
              onClick={onReload}
            >
              Discard draft and reload server policy
            </button>
          )}
        </div>
      )}
    </ConfirmDialog>
  )
}

function lifecyclePolicyChangeSummary({
  enabling,
  previousRetentionDays,
  nextRetentionDays,
  runCapIncreased,
  previousRunCap,
  nextRunCap,
  disabledSafeguardLabels,
}: {
  enabling: boolean
  previousRetentionDays: number
  nextRetentionDays: number
  runCapIncreased: boolean
  previousRunCap: number
  nextRunCap: number
  disabledSafeguardLabels: string[]
}) {
  const changes: string[] = []
  if (enabling) changes.push('This enables automated cleanup on the configured schedule.')
  if (nextRetentionDays < previousRetentionDays) {
    changes.push(`Retention decreases from ${previousRetentionDays.toLocaleString()} to ${nextRetentionDays.toLocaleString()} days.`)
  }
  if (runCapIncreased) {
    changes.push(`The per-run limit increases from ${previousRunCap.toLocaleString()} to ${nextRunCap.toLocaleString()} records.`)
  }
  if (disabledSafeguardLabels.length > 0) {
    changes.push(`Disabled safeguards: ${disabledSafeguardLabels.join(', ')}.`)
  }
  return changes.join(' ')
}

function PreviewFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-slate dark:text-slate-400">{label}</dt>
      <dd className="mt-0.5 break-words font-semibold text-ink dark:text-slate-100">
        {value}
      </dd>
    </div>
  )
}
