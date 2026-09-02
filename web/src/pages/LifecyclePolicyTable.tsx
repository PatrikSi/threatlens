import { useMutation } from '@tanstack/react-query'
import { ChevronDown, Eye, Play, Save, ShieldCheck } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { ApiError } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { isAmbiguousMutationError } from '../api/mutationResilience'
import type {
  LifecyclePolicy,
  LifecyclePolicyDraft,
  LifecyclePreview,
  LifecycleRun,
  LifecycleTarget,
} from '../types/lifecycle'
import { formatDateTime } from '../utils/datetime'
import {
  lifecycleDraftError,
  lifecycleDraftFromPolicy,
  lifecycleDraftIsEqual,
  lifecycleChangeIsDestructive,
  lifecyclePreviewCountLabel,
  lifecyclePreviewIsFreshForDraft,
  lifecyclePreviewMatchesDraft,
  lifecycleScheduleLabel,
  LIFECYCLE_WEEKDAYS,
  groupLifecycleTargets,
} from './lifecycleModel'
import {
  newLifecycleRequestKey,
  previewLifecyclePolicy,
  updateLifecyclePolicy,
} from './lifecycleApi'
import { lifecyclePreviewLocalExpiresAt } from './lifecyclePreviewClock'
import { LifecycleRunDialog } from './LifecycleRunDialog'
import {
  LifecyclePolicyChangeDialog,
  type LifecyclePolicyChangeConfirmation,
} from './LifecyclePolicyChangeDialog'
import { LifecyclePolicyState, LifecycleRunStatusChip } from './LifecycleStatus'

export function LifecyclePolicyTable({
  targets,
  canWrite,
  onDirtyChange,
  onPolicyUpdated,
  onPreviewUpdated,
  onReloadTarget,
  onRunStarted,
}: {
  targets: LifecycleTarget[]
  canWrite: boolean
  onDirtyChange: (targetKey: string, dirty: boolean) => void
  onPolicyUpdated: (targetKey: string, policy: LifecyclePolicy) => void
  onPreviewUpdated: (targetKey: string, preview: LifecyclePreview | null) => void
  onReloadTarget: (targetKey: string) => Promise<LifecycleTarget | null>
  onRunStarted: (run: LifecycleRun) => void
}) {
  const [expandedKeys, setExpandedKeys] = useState<Set<string>>(new Set())
  const groups = groupLifecycleTargets(targets)

  return (
    <div className="overflow-hidden rounded-xl border border-slate/15 bg-white dark:border-white/10 dark:bg-[#041612]">
      <div className="overflow-x-auto">
        <table className="w-full table-fixed text-left text-sm md:min-w-[900px]">
          <thead>
            <tr className="border-b border-slate/20 bg-slate/5 text-xs uppercase tracking-wide text-slate dark:border-white/10 dark:bg-white/[0.03] dark:text-slate-400">
              <th scope="col" className="w-[58%] px-3 py-2 md:w-[32%]">Dataset</th>
              <th scope="col" className="w-[26%] px-3 py-2 md:w-[13%]">Policy</th>
              <th scope="col" className="hidden w-[14%] px-3 py-2 md:table-cell">Retention</th>
              <th scope="col" className="hidden w-[14%] px-3 py-2 md:table-cell">Eligible / protected</th>
              <th scope="col" className="hidden w-[19%] px-3 py-2 md:table-cell">Last cleanup</th>
              <th scope="col" className="w-[16%] px-3 py-2 text-right md:w-[8%]">Edit</th>
            </tr>
          </thead>
          {groups.map((group) => (
            <tbody key={group.category} className="border-b border-slate/15 last:border-0 dark:border-white/10">
              <tr>
                <th colSpan={6} scope="colgroup" className="bg-slate/5 px-3 py-1.5 text-[0.6875rem] font-semibold uppercase tracking-[0.12em] text-slate dark:bg-white/[0.025] dark:text-slate-400">
                  {group.label}
                </th>
              </tr>
              {group.targets.map((target) => {
                const expanded = expandedKeys.has(target.key)
                return (
                  <PolicyRows
                    key={target.key}
                    target={target}
                    expanded={expanded}
                    canWrite={canWrite}
                    onToggle={() => setExpandedKeys((current) => {
                      const next = new Set(current)
                      if (next.has(target.key)) next.delete(target.key)
                      else next.add(target.key)
                      return next
                    })}
                    onDirtyChange={onDirtyChange}
                    onPolicyUpdated={onPolicyUpdated}
                    onPreviewUpdated={onPreviewUpdated}
                    onReloadTarget={onReloadTarget}
                    onRunStarted={onRunStarted}
                  />
                )
              })}
            </tbody>
          ))}
        </table>
      </div>
    </div>
  )
}

function PolicyRows({
  target,
  expanded,
  canWrite,
  onToggle,
  onDirtyChange,
  onPolicyUpdated,
  onPreviewUpdated,
  onReloadTarget,
  onRunStarted,
}: {
  target: LifecycleTarget
  expanded: boolean
  canWrite: boolean
  onToggle: () => void
  onDirtyChange: (targetKey: string, dirty: boolean) => void
  onPolicyUpdated: (targetKey: string, policy: LifecyclePolicy) => void
  onPreviewUpdated: (targetKey: string, preview: LifecyclePreview | null) => void
  onReloadTarget: (targetKey: string) => Promise<LifecycleTarget | null>
  onRunStarted: (run: LifecycleRun) => void
}) {
  const policy = target.policy
  const [preview, setPreview] = useState(target.latest_preview)
  useEffect(() => setPreview(target.latest_preview), [target.latest_preview])
  const handlePreviewUpdated = (
    targetKey: string,
    nextPreview: LifecyclePreview | null,
  ) => {
    setPreview(nextPreview)
    onPreviewUpdated(targetKey, nextPreview)
  }
  return (
    <>
      <tr className="border-t border-slate/10 align-middle dark:border-white/5">
        <td className="px-3 py-2">
          <p className="font-semibold text-ink dark:text-slate-100">{target.label}</p>
          <p className="mt-0.5 line-clamp-2 text-xs text-slate dark:text-slate-400 md:truncate" title={target.description}>{target.description}</p>
          <p className="mt-1 text-[0.6875rem] text-slate dark:text-slate-400 md:hidden">
            {policy.retention_days.toLocaleString()} days · {lifecycleScheduleLabel(policy)}
          </p>
          <p className="mt-0.5 text-[0.6875rem] text-slate dark:text-slate-400 md:hidden">
            {preview
              ? `${lifecyclePreviewCountLabel(preview.eligible_count, preview)} eligible · ${preview.protected_count.toLocaleString()} protected`
              : 'Impact preview required'}
          </p>
        </td>
        <td className="px-3 py-2"><LifecyclePolicyState enabled={policy.enabled} /></td>
        <td className="hidden px-3 py-2 md:table-cell">
          <p className="font-semibold">{policy.retention_days.toLocaleString()} days</p>
          <p className="text-xs text-slate dark:text-slate-400">{lifecycleScheduleLabel(policy)}</p>
        </td>
        <td className="hidden px-3 py-2 md:table-cell">
          {preview ? (
            <p title={`Preview generated ${formatDateTime(preview.generated_at)}`}>
              <span className="font-semibold">{lifecyclePreviewCountLabel(preview.eligible_count, preview)}</span>
              <span className="text-slate dark:text-slate-400"> / {preview.protected_count.toLocaleString()}</span>
            </p>
          ) : <span className="text-xs text-slate dark:text-slate-400">Preview required</span>}
        </td>
        <td className="hidden px-3 py-2 md:table-cell">
          {policy.last_run_at ? (
            <div className="flex flex-wrap items-center gap-2">
              {policy.last_run_status && <LifecycleRunStatusChip status={policy.last_run_status} />}
              <span className="text-xs text-slate dark:text-slate-400">{formatDateTime(policy.last_run_at)}</span>
            </div>
          ) : <span className="text-xs text-slate dark:text-slate-400">Never run</span>}
          <p className="mt-0.5 text-[0.6875rem] text-slate dark:text-slate-400">
            {policy.next_run_at ? `Next ${formatDateTime(policy.next_run_at)}` : 'No next run scheduled'}
          </p>
        </td>
        <td className="px-3 py-2 text-right">
          <button
            type="button"
            aria-expanded={expanded}
            aria-label={`${expanded ? 'Close' : 'Configure'} ${target.label} lifecycle policy`}
            className="inline-flex min-h-11 min-w-11 items-center justify-center rounded border border-slate/25 hover:bg-slate/5 md:min-h-0 md:min-w-0 md:px-2 md:py-1.5 dark:border-white/10 dark:hover:bg-white/[0.04]"
            onClick={onToggle}
          >
            <ChevronDown className={`h-4 w-4 transition-transform ${expanded ? 'rotate-180' : ''}`} aria-hidden="true" />
          </button>
        </td>
      </tr>
      <tr hidden={!expanded} className="border-t border-slate/10 dark:border-white/5">
          <td colSpan={6} className="p-0">
            <LifecyclePolicyEditor
              target={target}
              canWrite={canWrite}
              onDirtyChange={onDirtyChange}
              onPolicyUpdated={onPolicyUpdated}
              onPreviewUpdated={handlePreviewUpdated}
              onReloadTarget={onReloadTarget}
              onRunStarted={onRunStarted}
            />
          </td>
      </tr>
    </>
  )
}

function LifecyclePolicyEditor({
  target,
  canWrite,
  onDirtyChange,
  onPolicyUpdated,
  onPreviewUpdated,
  onReloadTarget,
  onRunStarted,
}: {
  target: LifecycleTarget
  canWrite: boolean
  onDirtyChange: (targetKey: string, dirty: boolean) => void
  onPolicyUpdated: (targetKey: string, policy: LifecyclePolicy) => void
  onPreviewUpdated: (targetKey: string, preview: LifecyclePreview | null) => void
  onReloadTarget: (targetKey: string) => Promise<LifecycleTarget | null>
  onRunStarted: (run: LifecycleRun) => void
}) {
  const [baseline, setBaseline] = useState(() => lifecycleDraftFromPolicy(target.policy))
  const [baseRevision, setBaseRevision] = useState(target.policy.revision)
  const [draft, setDraft] = useState<LifecyclePolicyDraft>(baseline)
  const [preview, setPreview] = useState<LifecyclePreview | null>(target.latest_preview)
  const [previewedDraft, setPreviewedDraft] = useState<LifecyclePolicyDraft | null>(
    target.latest_preview ? baseline : null,
  )
  const [message, setMessage] = useState('')
  const [, setPreviewClock] = useState(0)
  const [conflict, setConflict] = useState(false)
  const [runDialogOpen, setRunDialogOpen] = useState(false)
  const [policyChangeOpen, setPolicyChangeOpen] = useState(false)
  const updateKey = useRef(newLifecycleRequestKey('policy'))
  const dirty = lifecycleDraftChanged(draft, baseline)
  const validationError = lifecycleDraftError(target, draft)

  useEffect(() => onDirtyChange(target.key, dirty), [dirty, onDirtyChange, target.key])
  useEffect(() => () => onDirtyChange(target.key, false), [onDirtyChange, target.key])
  useEffect(() => {
    if (target.policy.revision === baseRevision) return
    if (dirty) {
      setConflict(true)
      return
    }
    const next = lifecycleDraftFromPolicy(target.policy)
    setBaseline(next)
    setDraft(next)
    setBaseRevision(target.policy.revision)
    setPreview(target.latest_preview)
    setPreviewedDraft(target.latest_preview ? next : null)
  }, [baseRevision, dirty, target])
  useEffect(() => {
    if (!preview) return
    const delay = lifecyclePreviewLocalExpiresAt(preview) - Date.now()
    if (delay <= 0 || delay > 2_147_000_000) return
    const timeout = window.setTimeout(
      () => setPreviewClock((current) => current + 1),
      delay + 25,
    )
    return () => window.clearTimeout(timeout)
  }, [preview])

  const previewMutation = useMutation({
    mutationFn: () => previewLifecyclePolicy({
      target_key: target.key,
      expected_revision: baseRevision,
      draft,
    }),
    onSuccess: (result) => {
      setPreview(result)
      setPreviewedDraft({ ...draft, options: { ...draft.options } })
      onPreviewUpdated(target.key, result)
      setMessage('Preview generated. No records were changed.')
      setConflict(false)
    },
    onError: (error) => {
      setMessage('')
      if (isPolicyRevisionConflict(error, 'preview')) setConflict(true)
    },
  })
  const saveMutation = useMutation({
    mutationFn: (confirmation?: LifecyclePolicyChangeConfirmation) => updateLifecyclePolicy(target.key, {
      expected_revision: baseRevision,
      ...draft,
      schedule_weekday: draft.schedule_cadence === 'weekly' ? draft.schedule_weekday : null,
      ...confirmation,
    }, updateKey.current),
    onSuccess: (result) => {
      const next = lifecycleDraftFromPolicy(result)
      setBaseline(next)
      setDraft(next)
      setBaseRevision(result.revision)
      setPreview(null)
      setPreviewedDraft(null)
      onPreviewUpdated(target.key, null)
      setConflict(false)
      setPolicyChangeOpen(false)
      setMessage('Policy saved. Generate a current preview before running cleanup.')
      updateKey.current = newLifecycleRequestKey('policy')
      onPolicyUpdated(target.key, result)
    },
    onError: (error) => {
      setMessage('')
      if (isAmbiguousMutationError(error)) {
        setPolicyChangeOpen(false)
      } else {
        updateKey.current = newLifecycleRequestKey('policy')
      }
      if (isPolicyRevisionConflict(error, 'save')) setConflict(true)
    },
  })
  const busy = mutationIsBusy(previewMutation.isPending, saveMutation.isPending)
  const previewMatchesDraft = lifecyclePreviewMatchesDraft(
    preview,
    previewedDraft,
    draft,
  )
  const previewFreshForDraft = lifecyclePreviewIsFreshForDraft({
    preview,
    previewMatchesDraft,
    policyRevision: baseRevision,
  })
  const previewCurrent = previewFreshForDraft && !dirty
  useEffect(() => {
    if (message === 'Preview generated. No records were changed.'
      && !previewFreshForDraft) setMessage('')
  }, [message, previewFreshForDraft])
  const error = lifecycleEditorError(
    saveMutation.isError ? saveMutation.error : null,
    previewMutation.isError ? previewMutation.error : null,
  )
  const saveOutcomeUnknown = saveMutation.isError
    && isAmbiguousMutationError(saveMutation.error)
  const editorLocked = conflict || saveOutcomeUnknown

  const resetTo = (latest: LifecycleTarget) => {
    const next = lifecycleDraftFromPolicy(latest.policy)
    setBaseline(next)
    setDraft(next)
    setBaseRevision(latest.policy.revision)
    setPreview(latest.latest_preview)
    setPreviewedDraft(latest.latest_preview ? next : null)
    setConflict(false)
    setPolicyChangeOpen(false)
    previewMutation.reset()
    saveMutation.reset()
    setMessage('Current server policy loaded; the local draft was discarded.')
    updateKey.current = newLifecycleRequestKey('policy')
  }
  const destructiveChange = lifecycleChangeIsDestructive(baseline, draft)
  const disabledSafeguardLabels = target.safeguards
    .filter((safeguard) => (
      baseline.options[safeguard.key] === true
      && draft.options[safeguard.key] === false
    ))
    .map((safeguard) => safeguard.label)
  const controlsDisabled = lifecycleControlsDisabled(
    canWrite,
    busy,
    editorLocked,
  )
  const previewDisabled = lifecyclePreviewDisabled({
    busy,
    validationError,
    blocked: editorLocked,
  })
  const saveDisabled = lifecycleSaveDisabled({
    canWrite,
    busy,
    dirty,
    validationError,
    blocked: editorLocked,
    destructiveChange,
    previewFreshForDraft,
  })
  const runDisabled = lifecycleRunDisabled({
    canWrite,
    policyEnabled: target.policy.enabled,
    previewCurrent,
    busy,
    blocked: editorLocked,
  })
  const actionGuidance = lifecycleActionGuidance({
    destructiveChange,
    previewFreshForDraft,
    policyEnabled: target.policy.enabled,
    previewCurrent,
  })
  const actionGuidanceId = `${target.key}-lifecycle-action-guidance`

  return (
    <div className="bg-slate/5 px-3 py-3 dark:bg-white/[0.025] sm:px-4">
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.25fr)_minmax(18rem,0.75fr)]">
        <div className="min-w-0">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 className="font-semibold">Policy configuration</h3>
              <p className="mt-0.5 text-xs text-slate dark:text-slate-400">{target.cutoff_description}</p>
              <p className="mt-0.5 text-[0.6875rem] text-slate dark:text-slate-400">
                Last changed {formatDateTime(target.policy.configuration_updated_at)} by {target.policy.updated_by ?? 'system bootstrap'} · {target.policy.next_run_at ? `next run ${formatDateTime(target.policy.next_run_at)}` : 'no next run scheduled'}
              </p>
            </div>
            <label className="inline-flex min-h-11 cursor-pointer items-center gap-2 text-sm font-semibold md:min-h-0">
              <input
                type="checkbox"
                checked={draft.enabled}
                disabled={controlsDisabled}
                onChange={(event) => setDraft((current) => ({ ...current, enabled: event.target.checked }))}
              />
              Automated cleanup enabled
            </label>
          </div>
          <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <NumberField label="Retain for (days)" value={draft.retention_days} min={target.min_retention_days} max={target.max_retention_days} disabled={controlsDisabled} onChange={(value) => setDraft((current) => ({ ...current, retention_days: value }))} />
            <SelectField
              label="Schedule"
              value={draft.schedule_cadence}
              disabled={controlsDisabled}
              onChange={(value) => setDraft((current) => ({
                ...current,
                schedule_cadence: value as LifecyclePolicyDraft['schedule_cadence'],
                schedule_weekday: value === 'weekly'
                  ? current.schedule_weekday ?? 0
                  : null,
              }))}
              options={[['daily', 'Daily'], ['weekly', 'Weekly']]}
            />
            {draft.schedule_cadence === 'weekly' ? (
              <SelectField
                label="Weekday"
                value={String(draft.schedule_weekday ?? 0)}
                disabled={controlsDisabled}
                onChange={(value) => setDraft((current) => ({
                  ...current,
                  schedule_weekday: Number(value),
                }))}
                options={LIFECYCLE_WEEKDAYS.map(
                  (label, index) => [String(index), label],
                )}
              />
            ) : (
              <NumberField
                label="Hour (UTC)"
                value={draft.schedule_hour_utc}
                min={0}
                max={23}
                disabled={controlsDisabled}
                onChange={(value) => setDraft((current) => ({
                  ...current,
                  schedule_hour_utc: value,
                }))}
              />
            )}
            {draft.schedule_cadence === 'weekly' && (
              <NumberField
                label="Hour (UTC)"
                value={draft.schedule_hour_utc}
                min={0}
                max={23}
                disabled={controlsDisabled}
                onChange={(value) => setDraft((current) => ({
                  ...current,
                  schedule_hour_utc: value,
                }))}
              />
            )}
            <NumberField label="Maximum per run" value={draft.max_records_per_run} min={100} max={100000} disabled={controlsDisabled} onChange={(value) => setDraft((current) => ({ ...current, max_records_per_run: value }))} />
          </div>
          {target.safeguards.length > 0 && (
            <fieldset className="mt-3">
              <legend className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-slate dark:text-slate-400">
                <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" /> Safeguards
              </legend>
              <div className="mt-1.5 grid gap-x-4 gap-y-1 sm:grid-cols-2">
                {target.safeguards.map((safeguard) => (
                  <label key={safeguard.key} className="flex min-h-11 items-start gap-2 rounded px-1 py-1.5 text-sm md:min-h-0">
                    <input
                      type="checkbox"
                      className="mt-0.5"
                      checked={
                        draft.options[safeguard.key] ?? safeguard.default_enabled
                      }
                      disabled={controlsDisabled}
                      onChange={(event) => setDraft((current) => ({
                        ...current,
                        options: {
                          ...current.options,
                          [safeguard.key]: event.target.checked,
                        },
                      }))}
                    />
                    <span><span className="font-semibold">{safeguard.label}</span><span className="block text-xs text-slate dark:text-slate-400">{safeguard.description}</span></span>
                  </label>
                ))}
              </div>
            </fieldset>
          )}
        </div>
        <PreviewPanel
          preview={preview}
          previewMatchesDraft={previewMatchesDraft}
          previewFresh={previewFreshForDraft}
          actionDescription={target.action_description}
        />
      </div>

      {validationError && <p role="alert" className="mt-3 text-sm text-red-700 dark:text-red-300">{validationError}</p>}
      {conflict && (
        <div role="alert" className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100">
          <span>This policy changed on the server. Reload it before saving or generating another preview.</span>
          <button type="button" className="min-h-11 rounded border border-current px-3 py-2 font-semibold md:min-h-0 md:py-1" onClick={() => void onReloadTarget(target.key).then((latest) => latest && resetTo(latest))}>Discard draft and reload</button>
        </div>
      )}
      {saveOutcomeUnknown && (
        <div
          role="alert"
          className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100"
        >
          <span>
            The policy update outcome is unknown. Reload the server policy
            before editing or submitting another request.
          </span>
          <button
            type="button"
            className="min-h-11 rounded border border-current px-3 py-2 font-semibold md:min-h-0 md:py-1"
            onClick={() => void onReloadTarget(target.key).then(
              (latest) => latest && resetTo(latest),
            )}
          >
            Reload server policy
          </button>
        </div>
      )}
      {error && !editorLocked && !policyChangeOpen && (
        <p
          role="alert"
          className="mt-3 text-sm text-red-700 dark:text-red-300"
        >
          {error}
        </p>
      )}
      {message && <p role="status" className="mt-3 text-sm text-green-800 dark:text-green-300">{message}</p>}

      {actionGuidance && (
        <p
          id={actionGuidanceId}
          role="status"
          className="mt-3 text-xs text-slate dark:text-slate-300"
        >
          {actionGuidance}
        </p>
      )}

      <div className="mt-3 flex flex-wrap items-center justify-end gap-2 border-t border-slate/15 pt-3 dark:border-white/10">
        {dirty && (
          <button
            type="button"
            disabled={busy || editorLocked}
            className="min-h-11 rounded border border-slate/25 px-3 py-2 text-sm font-semibold md:min-h-0 dark:border-white/10"
            onClick={() => {
              setDraft(baseline)
              setPreview(target.latest_preview)
              setPreviewedDraft(target.latest_preview ? baseline : null)
              setConflict(false)
            }}
          >
            Discard changes
          </button>
        )}
        <button
          type="button"
          disabled={previewDisabled}
          className="inline-flex min-h-11 items-center gap-2 rounded border border-slate/25 px-3 py-2 text-sm font-semibold disabled:opacity-50 md:min-h-0 dark:border-white/10"
          onClick={() => previewMutation.mutate()}
        >
          <Eye className="h-4 w-4" aria-hidden="true" /> {previewMutation.isPending ? 'Generating...' : 'Preview impact'}
        </button>
        <button
          type="button"
          disabled={saveDisabled}
          aria-describedby={saveDisabled && actionGuidance ? actionGuidanceId : undefined}
          className="inline-flex min-h-11 items-center gap-2 rounded bg-ink px-3 py-2 text-sm font-semibold text-white disabled:opacity-50 md:min-h-0 dark:bg-cyan dark:text-[#053c2e]"
          onClick={() => destructiveChange
            ? setPolicyChangeOpen(true)
            : saveMutation.mutate(undefined)}
        >
          <Save className="h-4 w-4" aria-hidden="true" /> {saveMutation.isPending ? 'Saving...' : 'Save policy'}
        </button>
        <button
          type="button"
          disabled={runDisabled}
          aria-describedby={runDisabled && actionGuidance ? actionGuidanceId : undefined}
          className="inline-flex min-h-11 items-center gap-2 rounded bg-red-700 px-3 py-2 text-sm font-semibold text-white disabled:opacity-50 md:min-h-0 dark:bg-red-500 dark:text-white"
          onClick={() => setRunDialogOpen(true)}
        >
          <Play className="h-4 w-4" aria-hidden="true" /> Run cleanup
        </button>
      </div>
      {preview && (
        <LifecycleRunDialog
          open={runDialogOpen}
          target={target}
          policy={{ ...target.policy, revision: baseRevision }}
          preview={preview}
          onClose={() => setRunDialogOpen(false)}
          onStarted={(run) => {
            setRunDialogOpen(false)
            setPreview(null)
            setPreviewedDraft(null)
            onPreviewUpdated(target.key, null)
            setMessage('Cleanup queued. Follow progress in run history.')
            onRunStarted(run)
          }}
        />
      )}
      {preview && (
        <LifecyclePolicyChangeDialog
          open={policyChangeOpen}
          target={target}
          enabling={!baseline.enabled && draft.enabled}
          previousRetentionDays={baseline.retention_days}
          nextRetentionDays={draft.retention_days}
          previousRunCap={baseline.max_records_per_run}
          nextRunCap={draft.max_records_per_run}
          disabledSafeguardLabels={disabledSafeguardLabels}
          draft={draft}
          preview={preview}
          previewCurrent={previewFreshForDraft}
          error={saveMutation.isError
            ? resolveApiErrorMessage(
              saveMutation.error,
              'The lifecycle policy could not be saved',
            )
            : ''}
          blocked={conflict}
          busy={saveMutation.isPending}
          onCancel={() => setPolicyChangeOpen(false)}
          onConfirm={(confirmation) => saveMutation.mutate(confirmation)}
          onReload={() => {
            setPolicyChangeOpen(false)
            void onReloadTarget(target.key).then(
              (latest) => latest && resetTo(latest),
            )
          }}
        />
      )}
    </div>
  )
}

function PreviewPanel({
  preview,
  previewMatchesDraft,
  previewFresh,
  actionDescription,
}: {
  preview: LifecyclePreview | null
  previewMatchesDraft: boolean
  previewFresh: boolean
  actionDescription: string
}) {
  if (!preview) {
    return (
      <aside className="rounded border border-dashed border-slate/25 p-3 text-sm text-slate dark:border-white/10 dark:text-slate-300">
        <p className="font-semibold text-ink dark:text-slate-100">Impact preview</p>
        <p className="mt-1 text-xs">
          Generate a server-side preview before cleanup. The preview reports
          aggregate counts only and changes no data.
        </p>
      </aside>
    )
  }
  const stale = lifecyclePreviewLocalExpiresAt(preview) <= Date.now()
  const outOfDate = !stale && (!previewMatchesDraft || !previewFresh)
  return (
    <aside className="rounded border border-slate/20 bg-white p-3 text-sm dark:border-white/10 dark:bg-[#041612]">
      <div className="flex items-center justify-between gap-2">
        <h4 className="font-semibold">Impact preview</h4>
        {stale && <span className="tl-chip tl-chip-warning">Expired</span>}
        {outOfDate && <span className="tl-chip tl-chip-warning">Out of date</span>}
      </div>
      <p className="mt-1 text-xs text-slate dark:text-slate-400">{actionDescription}</p>
      <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1.5 text-xs">
        <PreviewValue label="Eligible" value={lifecyclePreviewCountLabel(preview.eligible_count, preview)} />
        <PreviewValue label="Protected" value={preview.protected_count.toLocaleString()} />
        <PreviewValue label="Cutoff" value={formatDateTime(preview.cutoff_at)} />
        <PreviewValue label="Oldest candidate" value={preview.oldest_candidate_at ? formatDateTime(preview.oldest_candidate_at) : 'None'} />
      </dl>
      {Object.keys(preview.protected_counts).length > 0 && <p className="mt-2 text-xs text-slate dark:text-slate-400">Protected: {Object.entries(preview.protected_counts).map(([key, count]) => `${friendlyKey(key)} ${count.toLocaleString()}`).join(' · ')}</p>}
      <p className="mt-2 text-[0.6875rem] text-slate dark:text-slate-400">Generated {formatDateTime(preview.generated_at)} · expires {formatDateTime(preview.expires_at)}</p>
      {(preview.is_partial || preview.count_is_lower_bound) && <p className="mt-1 text-xs text-amber-800 dark:text-amber-200">The preview was bounded; eligible count is a lower bound.</p>}
      {(stale || outOfDate) && (
        <p className="mt-1 text-xs text-amber-800 dark:text-amber-200">
          {stale
            ? 'This preview has expired. Generate a fresh preview before saving or running cleanup.'
            : 'This preview does not match the current draft or policy revision. Generate it again before continuing.'}
        </p>
      )}
    </aside>
  )
}

function PreviewValue({ label, value }: { label: string; value: string }) {
  return <div><dt className="text-slate dark:text-slate-400">{label}</dt><dd className="font-semibold">{value}</dd></div>
}

function NumberField({
  label,
  value,
  min,
  max,
  disabled,
  onChange,
}: {
  label: string
  value: number
  min: number
  max: number
  disabled: boolean
  onChange: (value: number) => void
}) {
  return (
    <label className="text-xs font-semibold text-slate dark:text-slate-300">
      {label}
      <input
        type="number"
        value={value}
        min={min}
        max={max}
        step={1}
        disabled={disabled}
        className="mt-1 block min-h-11 w-full rounded border border-slate/30 bg-white px-2 py-2 text-sm font-normal md:min-h-0 dark:border-cyan-900/40 dark:bg-[#072019]"
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </label>
  )
}

function SelectField({
  label,
  value,
  options,
  disabled,
  onChange,
}: {
  label: string
  value: string
  options: ReadonlyArray<readonly [string, string]>
  disabled: boolean
  onChange: (value: string) => void
}) {
  return (
    <label className="text-xs font-semibold text-slate dark:text-slate-300">
      {label}
      <select
        value={value}
        disabled={disabled}
        className="mt-1 block min-h-11 w-full rounded border border-slate/30 bg-white px-2 py-2 text-sm font-normal md:min-h-0 dark:border-cyan-900/40 dark:bg-[#072019]"
        onChange={(event) => onChange(event.target.value)}
      >
        {options.map(([optionValue, optionLabel]) => (
          <option key={optionValue} value={optionValue}>{optionLabel}</option>
        ))}
      </select>
    </label>
  )
}

function friendlyKey(value: string) {
  return value.replaceAll('_', ' ').replace(/^./, (character) => character.toUpperCase())
}

function lifecycleDraftChanged(
  draft: LifecyclePolicyDraft,
  baseline: LifecyclePolicyDraft,
) {
  return !lifecycleDraftIsEqual(draft, baseline)
}

function mutationIsBusy(previewPending: boolean, savePending: boolean) {
  return previewPending || savePending
}

function lifecycleEditorError(
  saveError: unknown,
  previewError: unknown,
) {
  if (saveError) {
    return resolveApiErrorMessage(
      saveError,
      'The lifecycle policy could not be saved',
    )
  }
  if (previewError) {
    return resolveApiErrorMessage(
      previewError,
      'The lifecycle preview could not be generated',
    )
  }
  return ''
}

function isPolicyRevisionConflict(
  error: unknown,
  operation: 'preview' | 'save',
) {
  if (!(error instanceof ApiError) || error.status !== 409) return false
  const expectedMessage = operation === 'preview'
    ? 'The lifecycle policy changed; refresh the preview.'
    : 'The lifecycle policy changed; reload before saving.'
  return error.message.trim() === expectedMessage
}

function lifecycleControlsDisabled(
  canWrite: boolean,
  busy: boolean,
  blocked: boolean,
) {
  return !canWrite || busy || blocked
}

function lifecycleActionGuidance({
  destructiveChange,
  previewFreshForDraft,
  policyEnabled,
  previewCurrent,
}: {
  destructiveChange: boolean
  previewFreshForDraft: boolean
  policyEnabled: boolean
  previewCurrent: boolean
}) {
  if (destructiveChange && !previewFreshForDraft) {
    return 'Generate a fresh impact preview for this draft before saving the destructive change.'
  }
  if (!policyEnabled) {
    return 'Enable and save this policy before running cleanup.'
  }
  if (!previewCurrent) {
    return 'Save the policy, then generate a current impact preview before running cleanup.'
  }
  return ''
}

function lifecyclePreviewDisabled({
  busy,
  validationError,
  blocked,
}: {
  busy: boolean
  validationError: string | null
  blocked: boolean
}) {
  return busy || Boolean(validationError) || blocked
}

function lifecycleSaveDisabled({
  canWrite,
  busy,
  dirty,
  validationError,
  blocked,
  destructiveChange,
  previewFreshForDraft,
}: {
  canWrite: boolean
  busy: boolean
  dirty: boolean
  validationError: string | null
  blocked: boolean
  destructiveChange: boolean
  previewFreshForDraft: boolean
}) {
  return !canWrite
    || busy
    || !dirty
    || Boolean(validationError)
    || blocked
    || (destructiveChange && !previewFreshForDraft)
}

function lifecycleRunDisabled({
  canWrite,
  policyEnabled,
  previewCurrent,
  busy,
  blocked,
}: {
  canWrite: boolean
  policyEnabled: boolean
  previewCurrent: boolean
  busy: boolean
  blocked: boolean
}) {
  return !canWrite || !policyEnabled || !previewCurrent || busy || blocked
}
