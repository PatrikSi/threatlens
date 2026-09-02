import { useMutation } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'

import { isAmbiguousMutationError } from '../api/mutationResilience'
import { resolveApiErrorMessage } from '../api/errors'
import { ConfirmDialog } from '../components/ConfirmDialog'
import type {
  LifecyclePolicy,
  LifecyclePreview,
  LifecycleRun,
  LifecycleTarget,
} from '../types/lifecycle'
import { formatDateTime } from '../utils/datetime'
import { newLifecycleRequestKey, startLifecycleRun } from './lifecycleApi'
import { lifecyclePreviewLocalExpiresAt } from './lifecyclePreviewClock'
import { lifecyclePreviewCountLabel } from './lifecycleModel'

const MIN_REASON_LENGTH = 10

export function LifecycleRunDialog({
  open,
  target,
  policy,
  preview,
  onClose,
  onStarted,
}: {
  open: boolean
  target: LifecycleTarget
  policy: LifecyclePolicy
  preview: LifecyclePreview
  onClose: () => void
  onStarted: (run: LifecycleRun) => void
}) {
  const [reason, setReason] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [clock, setClock] = useState(() => Date.now())
  const requestKey = useRef(newLifecycleRequestKey('run'))
  const confirmationRef = useRef<HTMLInputElement | null>(null)

  const mutation = useMutation({
    mutationFn: () => startLifecycleRun({
      target_key: target.key,
      expected_revision: policy.revision,
      preview_id: preview.id,
      confirmation: 'PURGE',
      reason: reason.trim(),
    }, requestKey.current),
    onSuccess: onStarted,
    onError: (error) => {
      if (!isAmbiguousMutationError(error)) {
        requestKey.current = newLifecycleRequestKey('run')
      }
    },
  })
  const resetMutation = mutation.reset
  useEffect(() => {
    if (!open) return
    setReason('')
    setConfirmation('')
    setClock(Date.now())
    requestKey.current = newLifecycleRequestKey('run')
    resetMutation()
  }, [open, preview.id, resetMutation])
  useEffect(() => {
    if (!open) return
    const delay = lifecyclePreviewLocalExpiresAt(preview) - Date.now()
    if (delay <= 0) {
      setClock(Date.now())
      return
    }
    if (delay > 2_147_000_000) return
    const timeout = window.setTimeout(() => setClock(Date.now()), delay + 25)
    return () => window.clearTimeout(timeout)
  }, [open, preview])
  const reasonValid = reason.trim().length >= MIN_REASON_LENGTH
  const confirmValid = confirmation === 'PURGE'
  const previewExpired = lifecyclePreviewLocalExpiresAt(preview) <= clock
  const outcomeUnknown = mutation.isError
    && isAmbiguousMutationError(mutation.error)
  const error = mutation.isError
    ? outcomeUnknown
      ? 'The request outcome is unknown. Close this dialog and check run history before taking another action.'
      : resolveApiErrorMessage(mutation.error, 'The lifecycle run could not be queued')
    : ''

  return (
    <ConfirmDialog
      open={open}
      title={`Run ${target.label} cleanup?`}
      description="This queues an irreversible, bounded cleanup using the saved policy and preview shown below. Already-completed batches cannot be restored by cancelling the run."
      confirmLabel="Queue cleanup"
      confirmingLabel="Queueing cleanup..."
      confirmTone="danger"
      isConfirming={mutation.isPending}
      confirmDisabled={
        !reasonValid || !confirmValid || previewExpired || outcomeUnknown
      }
      cancelDisabled={mutation.isPending}
      initialFocusRef={confirmationRef}
      onCancel={onClose}
      onConfirm={() => mutation.mutate()}
    >
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 rounded border border-slate/15 bg-slate/5 p-3 text-xs dark:border-white/10 dark:bg-white/[0.03]">
        <PreviewFact
          label="Eligible records"
          value={lifecyclePreviewCountLabel(preview.eligible_count, preview)}
        />
        <PreviewFact label="Protected records" value={preview.protected_count.toLocaleString()} />
        <PreviewFact
          label="Run cap"
          value={`${policy.max_records_per_run.toLocaleString()} records`}
        />
        <PreviewFact label="Delete before" value={formatDateTime(preview.cutoff_at)} />
        <PreviewFact label="Preview expires" value={formatDateTime(preview.expires_at)} />
      </dl>
      {(preview.count_is_lower_bound || preview.is_partial) && (
        <p className="text-amber-800 dark:text-amber-200">
          This bounded preview found at least the displayed number of eligible
          records. Additional records may qualify; this run will stop at the
          displayed cap.
        </p>
      )}
      {previewExpired && (
        <p role="alert" className="text-red-700 dark:text-red-300">
          This preview has expired. Close this dialog and generate a new preview.
        </p>
      )}
      <label className="block font-semibold">
        Operational reason
        <textarea
          value={reason}
          rows={2}
          maxLength={500}
          disabled={outcomeUnknown || mutation.isPending}
          className="mt-1 block w-full rounded border border-slate/30 bg-white px-3 py-2 font-normal dark:border-cyan-900/40 dark:bg-[#072019]"
          placeholder="Example: Apply the approved quarterly retention policy"
          onChange={(event) => setReason(event.target.value)}
        />
        <span className="mt-1 block text-xs font-normal text-slate dark:text-slate-400">
          At least {MIN_REASON_LENGTH} characters; this is recorded in the audit trail.
        </span>
      </label>
      <label className="block font-semibold">
        Type PURGE to confirm
        <input
          ref={confirmationRef}
          value={confirmation}
          autoComplete="off"
          disabled={outcomeUnknown || mutation.isPending}
          className="mt-1 block w-full rounded border border-slate/30 bg-white px-3 py-2 font-mono font-normal dark:border-cyan-900/40 dark:bg-[#072019]"
          onChange={(event) => setConfirmation(event.target.value)}
        />
      </label>
      {error && <p role="alert" className="text-red-700 dark:text-red-300">{error}</p>}
    </ConfirmDialog>
  )
}

function PreviewFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-slate dark:text-slate-400">{label}</dt>
      <dd className="mt-0.5 break-words font-semibold text-ink dark:text-slate-100">{value}</dd>
    </div>
  )
}
