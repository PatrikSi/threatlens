import { useEffect, useMemo, useState, type Dispatch, type SetStateAction } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  Archive,
  CheckCircle2,
  Plus,
  RotateCcw,
  Save,
  ShieldAlert,
} from 'lucide-react'

import { resolveApiErrorMessage } from '../api/errors'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import type {
  DataPolicyMode,
  DataPolicyOverview,
  DataPolicyPreflight,
  HandlingLabel,
  IAMRole,
} from '../types/api'
import {
  createHandlingLabel,
  replaceHandlingLabelRoles,
  setHandlingLabelStatus,
  updateDataPolicyMode,
  updateHandlingLabel,
} from './accessGovernanceApi'

interface LabelDraft {
  key: string
  name: string
  description: string
  color: string
  roleIds: string[]
}

interface LabelStatusRequest {
  labelId: string
  labelName: string
  active: boolean
  revision: number
  assignedFeedCount: number
}

const EMPTY_LABEL: LabelDraft = {
  key: '',
  name: '',
  description: '',
  color: '#64748B',
  roleIds: [],
}

export function DataPolicyPanel({
  overview,
  roles,
  canWrite,
  isLoading,
  error,
  onDirtyChange,
}: {
  overview: DataPolicyOverview | null
  roles: IAMRole[]
  canWrite: boolean
  isLoading: boolean
  error: unknown
  onDirtyChange?: (dirty: boolean) => void
}) {
  if (isLoading) {
    return <div className="tl-surface rounded-xl p-6 text-sm text-slate dark:text-slate-300">Loading handling policy…</div>
  }
  if (error || !overview) {
    return (
      <div className="tl-surface rounded-xl p-5" role="alert">
        <div className="flex items-start gap-2">
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-600" aria-hidden="true" />
          <div>
            <h2 className="font-display text-lg">Handling policy unavailable</h2>
            <p className="mt-1 text-sm text-slate dark:text-slate-300">
              {resolveApiErrorMessage(error, 'The data-policy overview could not be loaded')}
            </p>
          </div>
        </div>
      </div>
    )
  }
  return <LoadedDataPolicyPanel overview={overview} roles={roles} canWrite={canWrite} onDirtyChange={onDirtyChange} />
}

function LoadedDataPolicyPanel({
  overview,
  roles,
  canWrite,
  onDirtyChange,
}: {
  overview: DataPolicyOverview
  roles: IAMRole[]
  canWrite: boolean
  onDirtyChange?: (dirty: boolean) => void
}) {
  const queryClient = useQueryClient()
  const requiredRoleIds = useMemo(
    () => roles.filter((role) => role.key === 'admin').map((role) => role.id),
    [roles],
  )
  const [selectedLabelId, setSelectedLabelId] = useState<string | 'new' | null>(
    overview.labels[0]?.id ?? null,
  )
  const selectedLabel =
    selectedLabelId === 'new'
      ? null
      : overview.labels.find((label) => label.id === selectedLabelId) ?? null
  const [draft, setDraft] = useState<LabelDraft>(() =>
    selectedLabel ? labelDraft(selectedLabel) : EMPTY_LABEL,
  )
  const [baselineDraft, setBaselineDraft] = useState<LabelDraft>(() =>
    selectedLabel
      ? labelDraft(selectedLabel)
      : { ...EMPTY_LABEL, roleIds: requiredRoleIds },
  )
  const [draftRevision, setDraftRevision] = useState<number | null>(
    selectedLabel?.revision ?? null,
  )
  const [draftPolicyRevision, setDraftPolicyRevision] = useState(
    overview.state.revision,
  )
  const [statusRequest, setStatusRequest] = useState<LabelStatusRequest | null>(null)
  const [targetMode, setTargetMode] = useState<DataPolicyMode>(overview.state.mode)
  const [baselineMode, setBaselineMode] = useState<DataPolicyMode>(
    overview.state.mode,
  )
  const [modeDraftRevision, setModeDraftRevision] = useState(
    overview.state.revision,
  )
  const [modeReason, setModeReason] = useState('')
  const [modeRequested, setModeRequested] = useState(false)

  const creating = selectedLabelId === 'new'
  const normalizedDraft = normalizeLabelDraft({
    ...draft,
    roleIds: [...draft.roleIds, ...requiredRoleIds],
  })
  const normalizedBaseline = normalizeLabelDraft(baselineDraft)
  const metadataDirty = !sameLabelMetadata(normalizedDraft, normalizedBaseline)
  const roleGrantsDirty = !sameIds(
    normalizedDraft.roleIds,
    normalizedBaseline.roleIds,
  )
  const dirty = metadataDirty || roleGrantsDirty
  const modeDraftDirty =
    targetMode !== baselineMode || modeReason.trim().length > 0

  useEffect(() => {
    if (selectedLabelId === 'new') {
      if (!dirty) {
        const nextDraft = { ...EMPTY_LABEL, roleIds: requiredRoleIds }
        setDraft(nextDraft)
        setBaselineDraft(nextDraft)
        setDraftRevision(null)
        setDraftPolicyRevision(overview.state.revision)
      }
      return
    }
    const current = overview.labels.find((label) => label.id === selectedLabelId)
    if (current) {
      if (!dirty) {
        const nextDraft = labelDraft(current)
        setDraft(nextDraft)
        setBaselineDraft(nextDraft)
        setDraftRevision(current.revision)
        setDraftPolicyRevision(overview.state.revision)
      }
      return
    }
    if (dirty) return
    const fallback = overview.labels[0] ?? null
    setSelectedLabelId(fallback?.id ?? null)
    const nextDraft = fallback
      ? labelDraft(fallback)
      : { ...EMPTY_LABEL, roleIds: requiredRoleIds }
    setDraft(nextDraft)
    setBaselineDraft(nextDraft)
    setDraftRevision(fallback?.revision ?? null)
    setDraftPolicyRevision(overview.state.revision)
  }, [
    dirty,
    overview.labels,
    overview.state.revision,
    requiredRoleIds,
    selectedLabelId,
  ])

  useEffect(() => {
    if (!modeDraftDirty) {
      setTargetMode(overview.state.mode)
      setBaselineMode(overview.state.mode)
      setModeDraftRevision(overview.state.revision)
    }
  }, [modeDraftDirty, overview.state.mode, overview.state.revision])

  const validation = labelValidation(draft, creating)
  const editable =
    canWrite &&
    (creating || Boolean(selectedLabel && !selectedLabel.is_system))
  const roleGrantsEditable =
    editable && (creating || Boolean(selectedLabel?.is_active))
  const confirmDiscard = useUnsavedChangesWarning(
    dirty || modeDraftDirty,
    'Discard the unsaved handling-label or policy-mode changes?',
  )
  useEffect(() => {
    onDirtyChange?.(dirty || modeDraftDirty)
  }, [dirty, modeDraftDirty, onDirtyChange])
  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange])
  const reconcilePolicy = () =>
    queryClient.invalidateQueries({ queryKey: ['governance', 'data-policy'] })

  const createLabel = useMutation({
    mutationFn: () =>
      createHandlingLabel({
        expected_policy_revision: draftPolicyRevision,
        key: normalizedDraft.key,
        name: normalizedDraft.name,
        description: normalizedDraft.description,
        color: normalizedDraft.color,
        role_ids: normalizedDraft.roleIds,
      }),
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: ['governance', 'data-policy'] })
      setSelectedLabelId(result.label.id)
      const nextDraft = labelDraft(result.label)
      setDraft(nextDraft)
      setBaselineDraft(nextDraft)
      setDraftRevision(result.label.revision)
      setDraftPolicyRevision(result.policy_revision)
    },
    onError: reconcilePolicy,
  })
  const saveMetadata = useMutation({
    mutationFn: () =>
      updateHandlingLabel(selectedLabelId as string, {
        expected_revision: draftRevision!,
        name: normalizedDraft.name,
        description: normalizedDraft.description,
        color: normalizedDraft.color,
      }),
    onSuccess: async (result) => {
      await reconcilePolicy()
      const serverDraft = labelDraft(result.label)
      setDraft((current) => ({
        ...current,
        name: serverDraft.name,
        description: serverDraft.description,
        color: serverDraft.color,
      }))
      setBaselineDraft(serverDraft)
      setDraftRevision(result.label.revision)
      setDraftPolicyRevision(result.policy_revision)
    },
    onError: reconcilePolicy,
  })
  const saveRoleGrants = useMutation({
    mutationFn: () =>
      replaceHandlingLabelRoles(
        selectedLabelId as string,
        draftRevision!,
        normalizedDraft.roleIds,
      ),
    onSuccess: async (result) => {
      await reconcilePolicy()
      const serverDraft = labelDraft(result.label)
      setDraft((current) => ({ ...current, roleIds: serverDraft.roleIds }))
      setBaselineDraft(serverDraft)
      setDraftRevision(result.label.revision)
      setDraftPolicyRevision(result.policy_revision)
    },
    onError: reconcilePolicy,
  })
  const changeStatus = useMutation({
    mutationFn: () =>
      setHandlingLabelStatus(
        statusRequest!.labelId,
        statusRequest!.revision,
        statusRequest!.active,
      ),
    onSuccess: async (result) => {
      setStatusRequest(null)
      await queryClient.invalidateQueries({ queryKey: ['governance', 'data-policy'] })
      const nextDraft = labelDraft(result.label)
      setDraft(nextDraft)
      setBaselineDraft(nextDraft)
      setDraftRevision(result.label.revision)
      setDraftPolicyRevision(result.policy_revision)
    },
    onError: reconcilePolicy,
  })
  const changeMode = useMutation({
    mutationFn: () =>
      updateDataPolicyMode({
        expected_revision: modeDraftRevision,
        mode: targetMode,
        reason: modeReason.trim(),
      }),
    onSuccess: async (result) => {
      setModeRequested(false)
      await queryClient.invalidateQueries({ queryKey: ['governance', 'data-policy'] })
      setTargetMode(result.state.mode)
      setBaselineMode(result.state.mode)
      setModeDraftRevision(result.state.revision)
      setModeReason('')
    },
    onError: reconcilePolicy,
  })
  const busy =
    createLabel.isPending ||
    saveMetadata.isPending ||
    saveRoleGrants.isPending ||
    changeStatus.isPending ||
    changeMode.isPending
  const mutationError =
    createLabel.error ??
    saveMetadata.error ??
    saveRoleGrants.error ??
    changeStatus.error ??
    changeMode.error
  const modeBlocked = dataPolicyModeBlocked(overview.preflight, targetMode)

  const chooseLabel = (labelId: string | 'new') => {
    if (busy || labelId === selectedLabelId) return
    confirmDiscard(() => {
      setSelectedLabelId(labelId)
      setTargetMode(overview.state.mode)
      setBaselineMode(overview.state.mode)
      setModeDraftRevision(overview.state.revision)
      setModeReason('')
      const nextLabel =
        labelId === 'new'
          ? null
          : overview.labels.find((label) => label.id === labelId) ?? null
      const nextDraft = nextLabel
        ? labelDraft(nextLabel)
        : { ...EMPTY_LABEL, roleIds: requiredRoleIds }
      setDraft(nextDraft)
      setBaselineDraft(nextDraft)
      setDraftRevision(nextLabel?.revision ?? null)
      setDraftPolicyRevision(overview.state.revision)
      createLabel.reset()
      saveMetadata.reset()
      saveRoleGrants.reset()
    })
  }

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
      <HandlingLabelsPanel
        labels={overview.labels}
        roles={roles}
        selectedLabelId={selectedLabelId}
        selectedLabel={selectedLabel}
        draft={draft}
        setDraft={setDraft}
        normalizedDraft={normalizedDraft}
        creating={creating}
        canWrite={canWrite}
        editable={editable}
        roleGrantsEditable={roleGrantsEditable}
        busy={busy}
        dirty={dirty}
        metadataDirty={metadataDirty}
        roleGrantsDirty={roleGrantsDirty}
        validation={validation}
        mutationError={mutationError}
        creatingPending={createLabel.isPending}
        metadataPending={saveMetadata.isPending}
        roleGrantsPending={saveRoleGrants.isPending}
        targetUnavailable={!creating && !selectedLabel}
        draftRevision={draftRevision}
        onChooseLabel={chooseLabel}
        onCreate={() => createLabel.mutate()}
        onSaveMetadata={() => saveMetadata.mutate()}
        onSaveRoleGrants={() => saveRoleGrants.mutate()}
        onRequestStatus={(active) => {
          changeStatus.reset()
          if (!selectedLabel || draftRevision === null) return
          setStatusRequest({
            labelId: selectedLabel.id,
            labelName: selectedLabel.name,
            active,
            revision: draftRevision,
            assignedFeedCount: selectedLabel.assigned_feed_count,
          })
        }}
      />

      <aside className="space-y-4">
        <PolicyModePanel
          overview={overview}
          canWrite={canWrite}
          busy={busy}
          targetMode={targetMode}
          modeReason={modeReason}
          modeBlocked={modeBlocked}
          labelDirty={dirty}
          onTargetModeChange={setTargetMode}
          onReasonChange={setModeReason}
          onReview={() => {
            changeMode.reset()
            setModeRequested(true)
          }}
        />
        <PreflightPanel preflight={overview.preflight} />
      </aside>

      <DataPolicyConfirmations
        statusRequest={statusRequest}
        statusPending={changeStatus.isPending}
        statusError={changeStatus.error}
        onConfirmStatus={() => changeStatus.mutate()}
        onCancelStatus={() => {
          setStatusRequest(null)
          changeStatus.reset()
        }}
        modeRequested={modeRequested}
        currentMode={overview.state.mode}
        targetMode={targetMode}
        modeReason={modeReason}
        modePending={changeMode.isPending}
        modeError={changeMode.error}
        onConfirmMode={() => changeMode.mutate()}
        onCancelMode={() => {
          setModeRequested(false)
          changeMode.reset()
        }}
      />
      {confirmDiscard.discardDialog}
    </div>
  )
}

interface HandlingLabelsPanelProps {
  labels: HandlingLabel[]
  roles: IAMRole[]
  selectedLabelId: string | 'new' | null
  selectedLabel: HandlingLabel | null
  draft: LabelDraft
  setDraft: Dispatch<SetStateAction<LabelDraft>>
  normalizedDraft: LabelDraft
  creating: boolean
  canWrite: boolean
  editable: boolean
  roleGrantsEditable: boolean
  busy: boolean
  dirty: boolean
  metadataDirty: boolean
  roleGrantsDirty: boolean
  validation: string | null
  mutationError: unknown
  creatingPending: boolean
  metadataPending: boolean
  roleGrantsPending: boolean
  targetUnavailable: boolean
  draftRevision: number | null
  onChooseLabel: (labelId: string | 'new') => void
  onCreate: () => void
  onSaveMetadata: () => void
  onSaveRoleGrants: () => void
  onRequestStatus: (active: boolean) => void
}

function HandlingLabelsPanel(props: HandlingLabelsPanelProps) {
  return (
    <section className="tl-surface overflow-hidden rounded-xl" aria-labelledby="labels-heading">
      <header className="border-b border-slate/15 px-4 py-4 dark:border-white/10 sm:px-5">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h2 id="labels-heading" className="font-display text-xl">Handling labels</h2>
            <p className="mt-1 text-sm text-slate dark:text-slate-300">Feed labels propagate monotonically into derived intelligence and retained history.</p>
          </div>
          {props.canWrite && (
            <button
              type="button"
              className="inline-flex min-h-11 items-center justify-center gap-2 rounded bg-ink px-3 py-2 text-sm font-semibold text-white sm:min-h-0 dark:bg-cyan dark:text-[#053c2e]"
              onClick={() => props.onChooseLabel('new')}
              disabled={props.busy}
            >
              <Plus className="h-4 w-4" aria-hidden="true" />
              New label
            </button>
          )}
        </div>
      </header>
      <div className="grid min-h-[600px] lg:grid-cols-[280px_minmax(0,1fr)]">
        <HandlingLabelSidebar labels={props.labels} selectedLabelId={props.selectedLabelId} busy={props.busy} onChoose={props.onChooseLabel} />
        <HandlingLabelEditor {...props} />
      </div>
    </section>
  )
}

function HandlingLabelSidebar({
  labels,
  selectedLabelId,
  busy,
  onChoose,
}: {
  labels: HandlingLabel[]
  selectedLabelId: string | 'new' | null
  busy: boolean
  onChoose: (labelId: string) => void
}) {
  return (
    <aside className="border-b border-slate/15 p-3 dark:border-white/10 lg:border-b-0 lg:border-r">
      <ul className="space-y-1" aria-label="Handling labels">
        {labels.map((label) => {
          const selected = label.id === selectedLabelId
          return (
            <li key={label.id}>
              <button
                type="button"
                aria-current={selected ? 'true' : undefined}
                className={`w-full rounded-lg border px-3 py-2.5 text-left ${selected ? 'border-cyan/50 bg-cyan/10' : 'border-transparent hover:border-slate/20 hover:bg-slate/5 dark:hover:border-white/10 dark:hover:bg-white/[0.04]'}`}
                onClick={() => onChoose(label.id)}
                disabled={busy}
              >
                <div className="flex items-center gap-2">
                  <span className="h-3 w-3 shrink-0 rounded-full border border-black/10" style={{ backgroundColor: label.color }} aria-hidden="true" />
                  <span className="min-w-0 truncate font-semibold">{label.name}</span>
                </div>
                <p className="mt-1 font-mono text-xs text-slate dark:text-slate-400">{label.key}</p>
                <p className="mt-1 text-xs text-slate dark:text-slate-400">
                  {label.is_active ? 'Active' : 'Archived'} · {label.assigned_feed_count} feeds
                </p>
              </button>
            </li>
          )
        })}
      </ul>
    </aside>
  )
}

function HandlingLabelEditor(props: HandlingLabelsPanelProps) {
  if (props.selectedLabelId === null) {
    return <div className="min-w-0 p-4 text-sm text-slate sm:p-5 dark:text-slate-300">Select a label to inspect its role boundary.</div>
  }
  return (
    <form
      className="min-w-0 p-4 sm:p-5"
      onSubmit={(event) => {
        event.preventDefault()
        if (props.creating && props.editable && props.dirty && !props.validation) {
          props.onCreate()
        }
      }}
    >
      <HandlingLabelFields {...props} />
      <HandlingLabelNotices label={props.selectedLabel} />
      {props.selectedLabel && <LabelRevisionSummary label={props.selectedLabel} draftRevision={props.draftRevision} />}
      {props.targetUnavailable && (
        <p role="alert" className="mt-4 rounded border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100">
          This label changed or was removed while you were editing. Copy any draft details you need, then discard this draft and reload policy state.
        </p>
      )}
      {props.selectedLabel && props.draftRevision !== props.selectedLabel.revision && (
        <p role="status" className="mt-4 rounded border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100">
          A newer server revision is available. This draft keeps revision {props.draftRevision}; saving it will be rejected so concurrent policy changes cannot be overwritten.
        </p>
      )}
      {props.validation && props.editable && <p role="alert" className="mt-4 rounded border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100">{props.validation}</p>}
      {props.mutationError != null && <p role="alert" className="mt-4 rounded border border-red-300/60 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-100">{resolveApiErrorMessage(props.mutationError, 'Data-policy mutation failed')}</p>}
      <LabelEditorActions {...props} />
    </form>
  )
}

function HandlingLabelFields({
  draft,
  setDraft,
  normalizedDraft,
  creating,
  editable,
  roleGrantsEditable,
  busy,
  roles,
}: HandlingLabelsPanelProps) {
  return (
    <fieldset disabled={!editable || busy}>
      <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_130px]">
        <label className="text-sm font-semibold">
          Label name
          <input
            className="mt-1 min-h-11 w-full rounded border border-slate/25 bg-white px-3 py-2 font-normal dark:border-cyan-900/40 dark:bg-[#072019]"
            value={draft.name}
            onChange={(event) => setDraft((current) => ({ ...current, name: event.target.value }))}
          />
        </label>
        <label className="text-sm font-semibold">
          Color
          <input
            type="color"
            className="mt-1 h-11 w-full rounded border border-slate/25 bg-white p-1 dark:border-cyan-900/40 dark:bg-[#072019]"
            value={draft.color}
            onChange={(event) => setDraft((current) => ({ ...current, color: event.target.value.toUpperCase() }))}
          />
        </label>
        <label className="text-sm font-semibold sm:col-span-2">
          Stable key
          <input
            className="mt-1 min-h-11 w-full rounded border border-slate/25 bg-white px-3 py-2 font-mono font-normal dark:border-cyan-900/40 dark:bg-[#072019]"
            value={draft.key}
            readOnly={!creating}
            onChange={(event) => setDraft((current) => ({ ...current, key: event.target.value }))}
          />
        </label>
        <label className="text-sm font-semibold sm:col-span-2">
          Description
          <textarea
            className="mt-1 min-h-24 w-full rounded border border-slate/25 bg-white px-3 py-2 font-normal dark:border-cyan-900/40 dark:bg-[#072019]"
            value={draft.description}
            onChange={(event) => setDraft((current) => ({ ...current, description: event.target.value }))}
          />
        </label>
      </div>
      <fieldset className="mt-5 rounded-lg border border-slate/20 p-3 dark:border-white/10">
        <legend className="px-1 text-sm font-semibold">Allowed roles</legend>
        <p className="mb-2 text-xs text-slate dark:text-slate-400">The built-in administrator role is required as a recovery path.</p>
        <div className="grid gap-1 sm:grid-cols-2">
          {roles.map((role) => (
            <label key={role.id} className="flex min-h-11 items-center gap-2 rounded px-2 py-1.5 hover:bg-slate/5 dark:hover:bg-white/[0.04]">
              <input
                type="checkbox"
                className="h-5 w-5"
                checked={normalizedDraft.roleIds.includes(role.id)}
                disabled={!roleGrantsEditable || role.key === 'admin'}
                onChange={(event) => setDraft((current) => ({
                  ...current,
                  roleIds: event.target.checked
                    ? [...new Set([...current.roleIds, role.id])]
                    : current.roleIds.filter((value) => value !== role.id),
                }))}
              />
              <span>
                <span className="block text-sm font-semibold">{role.name}</span>
                <span className="block font-mono text-[11px] text-slate dark:text-slate-400">{role.key}</span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>
    </fieldset>
  )
}

function HandlingLabelNotices({ label }: { label: HandlingLabel | null }) {
  if (label?.is_system) {
    return (
      <p className="mt-4 flex items-start gap-2 rounded border border-slate/20 bg-slate/5 px-3 py-2 text-sm dark:border-white/10 dark:bg-white/[0.04]">
        <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
        Built-in handling labels and their role grants are sealed.
      </p>
    )
  }
  if (label && !label.is_active) {
    return <p className="mt-4 text-xs text-slate dark:text-slate-300">Restore this label before changing its role grants.</p>
  }
  if (label && label.assigned_feed_count > 0) {
    return (
      <p className="mt-4 rounded border border-amber-300/60 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100">
        Reassign the {label.assigned_feed_count} feed(s) using this label before archiving it.
      </p>
    )
  }
  return null
}

function LabelRevisionSummary({ label, draftRevision }: { label: HandlingLabel; draftRevision: number | null }) {
  return (
    <dl className="mt-4 grid gap-2 rounded border border-slate/15 p-3 text-xs sm:grid-cols-3 dark:border-white/10">
      <div>
        <dt className="text-slate dark:text-slate-400">Draft revision</dt>
        <dd className="mt-1 font-mono font-semibold">{draftRevision ?? 'New'}</dd>
      </div>
      <div>
        <dt className="text-slate dark:text-slate-400">Provenance</dt>
        <dd className="mt-1 font-semibold">{label.is_system ? 'System' : 'Custom'}</dd>
      </div>
      <div>
        <dt className="text-slate dark:text-slate-400">State</dt>
        <dd className="mt-1 font-semibold">{label.is_active ? 'Active' : 'Archived'}</dd>
      </div>
    </dl>
  )
}

function LabelEditorActions({
  selectedLabel,
  canWrite,
  busy,
  dirty,
  metadataDirty,
  roleGrantsDirty,
  editable,
  roleGrantsEditable,
  validation,
  creatingPending,
  metadataPending,
  roleGrantsPending,
  creating,
  onSaveMetadata,
  onSaveRoleGrants,
  onRequestStatus,
}: HandlingLabelsPanelProps) {
  const canChangeStatus = Boolean(selectedLabel && canWrite && !selectedLabel.is_system)
  const archiveBlocked = Boolean(
    selectedLabel?.is_active && selectedLabel.assigned_feed_count > 0,
  )
  return (
    <div className="mt-5 flex flex-col-reverse gap-2 border-t border-slate/15 pt-4 sm:flex-row sm:justify-between dark:border-white/10">
      <div>
        {canChangeStatus && selectedLabel && (
          <button
            type="button"
            className="inline-flex min-h-11 items-center justify-center gap-2 rounded border border-slate/25 px-3 py-2 text-sm font-semibold disabled:opacity-60 sm:min-h-0 dark:border-cyan-900/40"
            onClick={() => onRequestStatus(!selectedLabel.is_active)}
            disabled={busy || dirty || archiveBlocked}
            title={archiveBlocked ? 'Reassign every feed using this label before archiving.' : undefined}
          >
            {selectedLabel.is_active
              ? <Archive className="h-4 w-4" aria-hidden="true" />
              : <RotateCcw className="h-4 w-4" aria-hidden="true" />}
            {selectedLabel.is_active ? 'Archive label' : 'Restore label'}
          </button>
        )}
      </div>
      {creating ? (
        <button
          type="submit"
          className="inline-flex min-h-11 items-center justify-center gap-2 rounded bg-ink px-3 py-2 text-sm font-semibold text-white disabled:opacity-60 sm:min-h-0 dark:bg-cyan dark:text-[#053c2e]"
          disabled={!editable || !dirty || Boolean(validation) || busy}
        >
          <Save className="h-4 w-4" aria-hidden="true" />
          {creatingPending ? 'Creating…' : 'Create label'}
        </button>
      ) : (
        <div className="flex flex-col gap-2 sm:flex-row">
          <button
            type="button"
            className="inline-flex min-h-11 items-center justify-center gap-2 rounded border border-slate/25 px-3 py-2 text-sm font-semibold disabled:opacity-60 sm:min-h-0 dark:border-cyan-900/40"
            disabled={!editable || !roleGrantsEditable || !roleGrantsDirty || busy}
            onClick={onSaveRoleGrants}
          >
            <Save className="h-4 w-4" aria-hidden="true" />
            {roleGrantsPending ? 'Saving boundary…' : 'Save role boundary'}
          </button>
          <button
            type="button"
            className="inline-flex min-h-11 items-center justify-center gap-2 rounded bg-ink px-3 py-2 text-sm font-semibold text-white disabled:opacity-60 sm:min-h-0 dark:bg-cyan dark:text-[#053c2e]"
            disabled={!editable || !metadataDirty || Boolean(validation) || busy}
            onClick={onSaveMetadata}
          >
            <Save className="h-4 w-4" aria-hidden="true" />
            {metadataPending ? 'Saving metadata…' : 'Save metadata'}
          </button>
        </div>
      )}
    </div>
  )
}

function DataPolicyConfirmations({
  statusRequest,
  statusPending,
  statusError,
  onConfirmStatus,
  onCancelStatus,
  modeRequested,
  currentMode,
  targetMode,
  modeReason,
  modePending,
  modeError,
  onConfirmMode,
  onCancelMode,
}: {
  statusRequest: LabelStatusRequest | null
  statusPending: boolean
  statusError: unknown
  onConfirmStatus: () => void
  onCancelStatus: () => void
  modeRequested: boolean
  currentMode: DataPolicyMode
  targetMode: DataPolicyMode
  modeReason: string
  modePending: boolean
  modeError: unknown
  onConfirmMode: () => void
  onCancelMode: () => void
}) {
  return (
    <>
      <ConfirmDialog
        open={statusRequest !== null}
        title={`${statusRequest?.active ? 'Restore' : 'Archive'} ${statusRequest?.labelName ?? 'label'}?`}
        description={statusRequest?.active ? 'Restoring the label makes it available for new feed assignments.' : `This label is currently assigned to ${statusRequest?.assignedFeedCount ?? 0} feed(s). Archiving is rejected while feeds or retained derived intelligence still reference it.`}
        confirmLabel={statusRequest?.active ? 'Restore label' : 'Archive label'}
        confirmTone={statusRequest?.active ? 'primary' : 'danger'}
        isConfirming={statusPending}
        confirmDisabled={statusError != null || Boolean(statusRequest && !statusRequest.active && statusRequest.assignedFeedCount > 0)}
        onConfirm={onConfirmStatus}
        onCancel={onCancelStatus}
      >
        {statusError != null && (
          <p role="alert" className="rounded border border-red-300/60 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-100">
            {resolveApiErrorMessage(statusError, 'Label status change failed')}
          </p>
        )}
      </ConfirmDialog>
      <ConfirmDialog
        open={modeRequested}
        title={`Change data policy to ${targetMode}?`}
        description={targetMode === 'enforced' ? 'Enforcement immediately hides inaccessible data and blocks restricted egress. Confirm only after reviewing every preflight result.' : `The data-policy mode will change from ${currentMode} to ${targetMode}.`}
        confirmLabel={`Change to ${targetMode}`}
        confirmTone={targetMode === 'disabled' ? 'danger' : 'primary'}
        isConfirming={modePending}
        confirmDisabled={modeError != null}
        onConfirm={onConfirmMode}
        onCancel={onCancelMode}
      >
        <p><span className="font-semibold">Recorded reason:</span> {modeReason.trim()}</p>
        {modeError != null && (
          <p role="alert" className="mt-3 rounded border border-red-300/60 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-100">
            {resolveApiErrorMessage(modeError, 'Policy mode change failed')}
          </p>
        )}
      </ConfirmDialog>
    </>
  )
}

function PolicyModePanel({
  overview,
  canWrite,
  busy,
  targetMode,
  modeReason,
  modeBlocked,
  labelDirty,
  onTargetModeChange,
  onReasonChange,
  onReview,
}: {
  overview: DataPolicyOverview
  canWrite: boolean
  busy: boolean
  targetMode: DataPolicyMode
  modeReason: string
  modeBlocked: boolean
  labelDirty: boolean
  onTargetModeChange: (mode: DataPolicyMode) => void
  onReasonChange: (reason: string) => void
  onReview: () => void
}) {
  const cannotReview =
    !canWrite ||
    targetMode === overview.state.mode ||
    modeReason.trim().length < 3 ||
    modeBlocked ||
    labelDirty ||
    busy

  return (
    <section className="tl-surface rounded-xl p-4" aria-labelledby="policy-mode-heading">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 id="policy-mode-heading" className="font-display text-xl">Policy mode</h2>
          <p className="mt-1 text-xs text-slate dark:text-slate-400">Revision {overview.state.revision}</p>
        </div>
        <ModeBadge mode={overview.state.mode} />
      </div>
      <label className="mt-4 block text-sm font-semibold">
        Target mode
        <select
          className="mt-1 min-h-11 w-full rounded border border-slate/25 bg-white px-3 py-2 font-normal dark:border-cyan-900/40 dark:bg-[#072019]"
          value={targetMode}
          onChange={(event) => onTargetModeChange(event.target.value as DataPolicyMode)}
          disabled={!canWrite || busy}
        >
          <option value="disabled">Disabled</option>
          <option value="audit">Audit</option>
          <option value="enforced">Enforced</option>
        </select>
      </label>
      <label className="mt-3 block text-sm font-semibold">
        Change reason
        <textarea
          className="mt-1 min-h-24 w-full rounded border border-slate/25 bg-white px-3 py-2 font-normal dark:border-cyan-900/40 dark:bg-[#072019]"
          value={modeReason}
          onChange={(event) => onReasonChange(event.target.value)}
          disabled={!canWrite || busy}
          placeholder="Why is this mode appropriate now?"
        />
      </label>
      {modeBlocked && (
        <p className="mt-3 flex items-start gap-2 rounded border border-amber-300/60 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100">
          <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          Resolve the preflight blockers before selecting this mode.
        </p>
      )}
      <button type="button" className="mt-4 inline-flex min-h-11 w-full items-center justify-center gap-2 rounded bg-ink px-3 py-2 text-sm font-semibold text-white disabled:opacity-60 dark:bg-cyan dark:text-[#053c2e]" disabled={cannotReview} onClick={onReview}>
        <ShieldAlert className="h-4 w-4" aria-hidden="true" />
        Review mode change
      </button>
    </section>
  )
}

function PreflightPanel({ preflight }: { preflight: DataPolicyPreflight }) {
  const routeClasses = Object.entries(preflight.route_manifest.governance_class_counts)
    .sort(([left], [right]) => left.localeCompare(right))
  return (
    <section className="tl-surface rounded-xl p-4" aria-labelledby="preflight-heading">
      <div className="flex items-start gap-2">
        {preflight.ready_for_enforcement
          ? <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-emerald-600" aria-hidden="true" />
          : <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-600" aria-hidden="true" />}
        <div>
          <h2 id="preflight-heading" className="font-display text-lg">Activation preflight</h2>
          <p className="mt-1 text-xs text-slate dark:text-slate-400">
            Coverage {preflight.current_coverage_version} / {preflight.required_coverage_version}
          </p>
        </div>
      </div>
      <dl className="mt-3 grid gap-2 rounded border border-slate/20 bg-slate/[0.03] p-3 text-xs dark:border-white/10 dark:bg-white/[0.03] sm:grid-cols-2">
        <div>
          <dt className="font-semibold text-slate dark:text-slate-300">Evaluation</dt>
          <dd className="mt-0.5">
            {preflight.full ? 'Full scan' : 'Runtime invariants'} · policy revision {preflight.evaluated_policy_revision}
          </dd>
        </div>
        <div>
          <dt className="font-semibold text-slate dark:text-slate-300">Checked</dt>
          <dd className="mt-0.5 font-mono">{new Date(preflight.checked_at).toISOString()}</dd>
        </div>
        <div>
          <dt className="font-semibold text-slate dark:text-slate-300">Route manifest</dt>
          <dd className="mt-0.5">
            {preflight.route_manifest.installed
              ? preflight.route_manifest.valid ? 'Valid' : 'Invalid'
              : 'Not installed'} · v{preflight.route_manifest.version} · {preflight.route_manifest.validated_operation_count} / {preflight.route_manifest.declared_operation_count} operations · {preflight.route_manifest.request_context_operation_count} request-context
          </dd>
        </div>
        <div>
          <dt className="font-semibold text-slate dark:text-slate-300">Governance classes</dt>
          <dd className="mt-0.5">
            {routeClasses.map(([name, count]) => `${name.replaceAll('_', ' ')} ${count}`).join(' · ')}
          </dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="font-semibold text-slate dark:text-slate-300">Manifest digest</dt>
          <dd className="mt-0.5 break-all font-mono">{preflight.route_manifest.digest}</dd>
        </div>
      </dl>
      {preflight.blockers.length === 0 ? (
        <p className="mt-3 rounded border border-emerald-300/60 bg-emerald-50 px-3 py-2 text-sm text-emerald-800 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-100">
          All enforcement invariants are satisfied.
        </p>
      ) : (
        <ul className="mt-3 space-y-2">
          {preflight.blockers.map((blocker) => (
            <li key={blocker.code} className="rounded border border-slate/20 px-3 py-2 text-sm dark:border-white/10">
              <div className="flex items-start justify-between gap-2">
                <span className="font-semibold">{blocker.code.replaceAll('_', ' ')}</span>
                {blocker.count !== null && (
                  <span className="rounded bg-slate/10 px-1.5 py-0.5 text-xs font-semibold dark:bg-white/10 dark:text-slate-100">{blocker.count}</span>
                )}
              </div>
              <p className="mt-1 text-xs text-slate dark:text-slate-300">{blocker.detail}</p>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function ModeBadge({ mode }: { mode: DataPolicyMode }) {
  const className = mode === 'enforced'
    ? 'border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-100'
    : mode === 'audit'
      ? 'border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100'
      : 'border-slate/25 bg-slate/5 text-slate dark:border-white/10 dark:bg-white/[0.04] dark:text-slate-300'
  return <span className={`rounded-full border px-2.5 py-1 text-xs font-semibold capitalize ${className}`}>{mode}</span>
}

function labelDraft(label: HandlingLabel): LabelDraft {
  return {
    key: label.key,
    name: label.name,
    description: label.description,
    color: label.color.toUpperCase(),
    roleIds: [...label.role_ids].sort(),
  }
}

function normalizeLabelDraft(draft: LabelDraft): LabelDraft {
  return {
    key: draft.key.trim().toLowerCase(),
    name: draft.name.trim(),
    description: draft.description.trim(),
    color: draft.color.toUpperCase(),
    roleIds: [...new Set(draft.roleIds)].sort(),
  }
}

function labelValidation(draft: LabelDraft, creating: boolean): string | null {
  if (!draft.name.trim()) return 'Label name is required.'
  if (creating && !/^[a-z][a-z0-9]*([._-][a-z0-9]+)*$/.test(draft.key.trim().toLowerCase())) return 'Label key must begin with a lowercase letter and contain lowercase segments separated by dots, underscores, or hyphens.'
  if (!/^#[0-9A-Fa-f]{6}$/.test(draft.color)) return 'Choose a six-digit hexadecimal color.'
  return null
}

function sameIds(left: string[], right: string[]): boolean {
  return JSON.stringify([...new Set(left)].sort()) === JSON.stringify([...new Set(right)].sort())
}

function sameLabelMetadata(left: LabelDraft, right: LabelDraft): boolean {
  return (
    left.key === right.key &&
    left.name === right.name &&
    left.description === right.description &&
    left.color === right.color
  )
}

function dataPolicyModeBlocked(
  preflight: DataPolicyPreflight,
  targetMode: DataPolicyMode,
): boolean {
  if (targetMode === 'audit') return !preflight.ready_for_audit
  if (targetMode === 'enforced') return !preflight.ready_for_enforcement
  return false
}
