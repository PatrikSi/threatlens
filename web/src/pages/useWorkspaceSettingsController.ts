import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  useCallback,
  useMemo,
  useState,
  type Dispatch,
  type SetStateAction,
} from 'react'

import { resolveApiErrorMessage } from '../api/errors'
import { apiFetch } from '../api/client'
import type { SavedView } from '../types/api'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { useQueryAccessScope } from '../hooks/useQueryAccessScope'
import type {
  WorkspaceEffectiveResponse,
  WorkspaceRole,
  WorkspaceRolePolicyResponse,
  WorkspaceUserPreferenceResponse,
  WorkspaceUserPreferenceWriteRequest,
} from '../types/workspace'
import { hasRequiredPermissions } from '../workspace/workspaceModel'
import {
  getWorkspaceRolePolicies,
  resetWorkspaceRolePolicy,
  updateWorkspaceRolePolicy,
  workspaceQueryKeys,
} from '../workspace/workspaceApi'
import { useWorkspace } from '../workspace/useWorkspace'
import {
  buildPersonalPreferencePayload,
  buildRolePolicyPayload,
  createPersonalWorkspaceDraft,
  createRolePolicyDraft,
  personalDraftIsDirty,
  rolePolicyDraftIsDirty,
  rolePolicyDraftValidation,
  type PersonalWorkspaceDraft,
  type RolePolicyDraft,
} from './workspaceSettingsModel'

const WORKSPACE_ROLES: readonly WorkspaceRole[] = ['admin', 'analyst', 'viewer']

interface RoleDraftEdit {
  role: WorkspaceRole
  baseline: WorkspaceRolePolicyResponse
  draft: RolePolicyDraft
  generation: number
}

interface PersonalDraftEdit {
  baseline: {
    effective: WorkspaceEffectiveResponse
    preferences: WorkspaceUserPreferenceResponse
  }
  draft: PersonalWorkspaceDraft
  sourcePreferenceRevision: number
}

export function useWorkspaceSettingsController() {
  const queryClient = useQueryClient()
  const meQuery = useCurrentUser()
  const workspace = useWorkspace()
  const policiesAccess = useQueryAccessScope(['workspace', 'role-policies'])
  const [selectedRole, setSelectedRole] = useState<WorkspaceRole>('analyst')
  const [roleEdit, setRoleEdit] = useState<RoleDraftEdit | null>(null)
  const [personalEdit, setPersonalEdit] = useState<PersonalDraftEdit | null>(null)
  const [roleFeedbackState, setRoleFeedbackState] = useState<Awaited<ReturnType<typeof policiesAccess.read<string>>> | null>(null)
  const [roleErrorState, setRoleErrorState] = useState<Awaited<ReturnType<typeof policiesAccess.read<string>>> | null>(null)
  const [roleRevisionConflict, setRoleRevisionConflict] = useState(false)
  const [personalFeedback, setPersonalFeedback] = useState('')
  const [personalError, setPersonalError] = useState('')
  const [personalRevisionConflict, setPersonalRevisionConflict] = useState(false)
  const [resetRoleIntent, setResetRoleIntent] = useState<Awaited<ReturnType<typeof policiesAccess.read<{ role: WorkspaceRole; revision: number }>>> | null>(null)
  const [resetPersonalRequested, setResetPersonalRequested] = useState(false)
  const [discardReloadRequested, setDiscardReloadRequested] = useState(false)
  const [roleReloadPending, setRoleReloadPending] = useState(false)
  const [personalReloadPending, setPersonalReloadPending] = useState(false)
  const [requestedRole, setRequestedRole] = useState<WorkspaceRole | null>(null)

  const effectiveAccess = meQuery.data?.access
  const permissions = effectiveAccess?.permissions ?? []
  const durablePermissions = effectiveAccess?.durable_permissions ?? (
    (effectiveAccess?.elevation_ids?.length ?? 0) === 0
      ? effectiveAccess?.permissions
      : []
  )
  const canReadPolicies = hasRequiredPermissions(permissions, ['read:workspace'])
  const canManagePolicies = canManageWorkspacePolicies(durablePermissions)
  const policiesRead = policiesAccess.queryOptions(getWorkspaceRolePolicies, 30_000)
  const rolePoliciesQuery = useQuery({
    queryKey: workspaceQueryKeys.rolePolicies,
    ...policiesRead,
    enabled: canReadPolicies,
  })
  const roleFeedback = workspaceRoleNotice(policiesAccess, roleFeedbackState)
  const roleError = workspaceRoleNotice(policiesAccess, roleErrorState)
  const resetIntent = policiesAccess.currentData(resetRoleIntent ?? undefined)
  const resetRoleRequested = resetIntent?.role === selectedRole
  const { selectedPolicy, roleReadAvailable, roleDraftNeedsReload, rolePolicyError } = workspaceRoleReadState(
    rolePoliciesQuery, selectedRole, roleEdit, policiesAccess.generation, policiesAccess.denied, meQuery.isError,
  )
  function setRoleFeedback(message: string) { setRoleFeedbackState(policiesAccess.captureResult()(message)) }
  function setRoleError(message: string) { setRoleErrorState(policiesAccess.captureResult()(message)) }
  function roleEditIsCurrent() {
    return !roleEdit || Boolean(policiesAccess.currentData({ generation: roleEdit.generation, value: true }))
  }
  function setResetRoleRequested(requested: boolean) {
    const policy = currentRolePolicy()
    setResetRoleIntent(requested && policy ? policiesAccess.captureResult()({ role: selectedRole, revision: policy.revision }) : null)
  }
  function currentRolePolicy() {
    const state = queryClient.getQueryState<Awaited<ReturnType<typeof policiesRead.queryFn>>>(workspaceQueryKeys.rolePolicies)
    if (state?.status !== 'success' || state.fetchStatus !== 'idle') return undefined
    return policiesAccess.currentData(state.data)?.find((policy) => policy.role === selectedRole)
  }
  const viewsAccess = useQueryAccessScope(['views'])
  const templateViewsQuery = useQuery({
    queryKey: ['views'], ...viewsAccess.queryOptions(({ signal }) => apiFetch<SavedView[]>('/views', { signal }), 60_000),
    enabled: canManagePolicies && hasRequiredPermissions(permissions, ['read:views']),
  })
  const { draft: roleDraft, baseline: roleBaseline } = useMemo(
    () => resolveRoleEditor(canManagePolicies ? roleEdit : null, selectedRole, selectedPolicy),
    [canManagePolicies, roleEdit, selectedPolicy, selectedRole],
  )
  const setRoleDraft = useCallback<Dispatch<SetStateAction<RolePolicyDraft | null>>>((nextValue) => {
    if (!canManagePolicies) return
    setRoleEdit((currentEdit) => {
      const currentDraft = currentEdit?.role === selectedRole
        ? currentEdit.draft
        : selectedPolicy
          ? createRolePolicyDraft(selectedPolicy)
          : null
      const baseline = currentEdit?.role === selectedRole ? currentEdit.baseline : selectedPolicy
      const resolved = typeof nextValue === 'function'
        ? nextValue(currentDraft)
        : nextValue
      if (!resolved || !baseline || !rolePolicyDraftIsDirty(baseline, resolved)) return null
      return { role: selectedRole, baseline, draft: resolved,
        generation: currentEdit?.role === selectedRole ? currentEdit.generation : policiesAccess.generation }
    })
  }, [canManagePolicies, selectedPolicy, selectedRole, policiesAccess.generation])

  const { draft: personalDraft, baseline: personalBaseline } = useMemo(
    () => resolvePersonalEditor(personalEdit, workspace.effective, workspace.preferences),
    [personalEdit, workspace.effective, workspace.preferences],
  )
  const setPersonalDraft = useCallback<Dispatch<SetStateAction<PersonalWorkspaceDraft | null>>>((nextValue) => {
    setPersonalEdit((currentEdit) => {
      const activeEdit = currentEdit && personalEditIsActive(currentEdit, workspace.preferences)
        ? currentEdit
        : null
      const currentDraft = activeEdit?.draft ?? (
        workspace.effective && workspace.preferences
          ? createPersonalWorkspaceDraft(workspace.effective, workspace.preferences)
          : null
      )
      const baseline = activeEdit?.baseline ?? (
        workspace.effective && workspace.preferences
          ? { effective: workspace.effective, preferences: workspace.preferences }
          : null
      )
      const resolved = typeof nextValue === 'function'
        ? nextValue(currentDraft)
        : nextValue
      if (
        !resolved ||
        !baseline ||
        !personalDraftIsDirty(baseline.effective, baseline.preferences, resolved)
      ) return null
      return {
        baseline,
        draft: resolved,
        sourcePreferenceRevision:
          activeEdit?.sourcePreferenceRevision ?? baseline.preferences.revision,
      }
    })
  }, [workspace.effective, workspace.preferences])

  const updateRolePolicy = useMutation({
    mutationFn: async () => {
      if (!canManagePolicies) {
        throw new Error('Durable workspace-management permission is required to change organization navigation defaults.')
      }
      if (!roleBaseline || !roleDraft) {
        throw new Error('The selected role policy is not loaded.')
      }
      if (meQuery.isError || !currentRolePolicy() || !roleEditIsCurrent()) {
        throw new Error('Reload current role policy access before saving this draft.')
      }
      const capture = policiesAccess.captureResult()
      const updated = await updateWorkspaceRolePolicy(selectedRole, buildRolePolicyPayload(roleBaseline, roleDraft))
      return capture({ updated, submittedDraft: roleDraft })
    },
    onMutate: () => {
      setRoleFeedback('')
      setRoleError('')
      return policiesAccess.captureResult()(selectedRole)
    },
    onSuccess: (result) => {
      const completed = policiesAccess.currentData(result)
      if (!completed || !currentRolePolicy()) { void queryClient.invalidateQueries({ queryKey: workspaceQueryKeys.root }); return }
      const { updated, submittedDraft } = completed
      queryClient.setQueryData(
        workspaceQueryKeys.rolePolicies,
        (current: Awaited<ReturnType<typeof policiesRead.queryFn>> | undefined) => {
          const policies = policiesAccess.currentData(current)
          return policies ? { generation: result.generation, value: policies.map((policy) => policy.role === updated.role ? updated : policy) } : current
        },
      )
      setRoleEdit((current) => current?.draft === submittedDraft ? null : current)
      setRoleRevisionConflict(false)
      setRoleFeedback(`${capitalize(updated.role)} workspace policy saved at revision ${updated.revision}.`)
      void queryClient.invalidateQueries({ queryKey: workspaceQueryKeys.root })
    },
    onError: (error, _variables, admission) => {
      if (policiesAccess.currentData(admission) !== selectedRole) return
      const conflict = isRevisionConflict(error)
      setRoleRevisionConflict(conflict)
      setRoleError(withConflictRecovery(
        resolveApiErrorMessage(error, 'Workspace role policy could not be saved'),
        conflict,
      ))
    },
  })
  const resetRolePolicy = useMutation({
    mutationFn: async () => {
      if (!canManagePolicies) {
        throw new Error('Durable workspace-management permission is required to reset organization navigation defaults.')
      }
      if (!roleBaseline) {
        throw new Error('The selected role policy is not loaded.')
      }
      const intent = policiesAccess.currentData(resetRoleIntent ?? undefined)
      if (meQuery.isError || !currentRolePolicy() || !roleEditIsCurrent() || intent?.role !== selectedRole || intent.revision !== roleBaseline.revision) {
        throw new Error('Review the current role policy before resetting its defaults.')
      }
      const capture = policiesAccess.captureResult()
      const updated = await resetWorkspaceRolePolicy(selectedRole, { expected_revision: roleBaseline.revision })
      return capture({ updated, submittedDraft: roleDraft })
    },
    onMutate: () => {
      setRoleFeedback('')
      setRoleError('')
      return policiesAccess.captureResult()(selectedRole)
    },
    onSuccess: (result) => {
      const completed = policiesAccess.currentData(result)
      if (!completed || !currentRolePolicy()) { void queryClient.invalidateQueries({ queryKey: workspaceQueryKeys.root }); return }
      const { updated, submittedDraft } = completed
      queryClient.setQueryData(
        workspaceQueryKeys.rolePolicies,
        (current: Awaited<ReturnType<typeof policiesRead.queryFn>> | undefined) => {
          const policies = policiesAccess.currentData(current)
          return policies ? { generation: result.generation, value: policies.map((policy) => policy.role === updated.role ? updated : policy) } : current
        },
      )
      setRoleEdit((current) => current?.draft === submittedDraft ? null : current)
      setResetRoleRequested(false)
      setRoleRevisionConflict(false)
      setRoleFeedback(`${capitalize(updated.role)} workspace policy reset to defaults.`)
      void queryClient.invalidateQueries({ queryKey: workspaceQueryKeys.root })
    },
    onError: (error, _variables, admission) => {
      if (policiesAccess.currentData(admission) !== selectedRole) return
      const conflict = isRevisionConflict(error)
      setResetRoleRequested(false)
      setRoleRevisionConflict(conflict)
      setRoleError(withConflictRecovery(
        resolveApiErrorMessage(error, 'Workspace role policy could not be reset'),
        conflict,
      ))
    },
  })

  const roleDirty = Boolean(roleEdit && rolePolicyDraftIsDirty(roleEdit.baseline, roleEdit.draft))
  const roleValidation = roleDraft
    ? rolePolicyDraftValidation(
        roleDraft,
        roleBaseline?.landing_module_id,
        selectedRole,
      )
    : ''
  const personalDirty = Boolean(
    personalBaseline &&
    personalDraft &&
    personalDraftIsDirty(personalBaseline.effective, personalBaseline.preferences, personalDraft),
  )
  const confirmDiscardChanges = useUnsavedChangesWarning(
    roleDirty || personalDirty,
    'You have unsaved workspace changes. Leave without saving?',
  )

  const workspaceError = workspace.error
    ? resolveApiErrorMessage(workspace.error, 'Workspace configuration could not be loaded')
    : ''
  const selectedPolicyWarnings = useMemo(
    () => [
      ...(selectedPolicy?.warnings ?? []),
      ...(selectedPolicy?.unknown_module_ids ?? []).map((id) => `unknown_policy_module:${id}`),
      ...(selectedPolicy?.unknown_dashboard_panel_ids ?? []).map((id) => `unknown_dashboard_panel:${id}`),
    ],
    [selectedPolicy],
  )

  const savePersonal = async () => {
    if (!personalBaseline || !personalDraft) return
    setPersonalFeedback('')
    setPersonalError('')
    if (!personalDraft.inheritDashboardPanels && personalDraft.dashboardPanelIds.length === 0) {
      setPersonalError('Choose at least one first-use dashboard panel or use the organization defaults.')
      return
    }
    try {
      const updated = await workspace.savePreferences(
        buildSparsePersonalPreferencePayload(
          personalBaseline.effective,
          personalBaseline.preferences,
          personalDraft,
        ),
      )
      setPersonalEdit({
        baseline: { effective: personalBaseline.effective, preferences: updated },
        draft: createPersonalWorkspaceDraft(personalBaseline.effective, updated),
        sourcePreferenceRevision: updated.revision,
      })
      setPersonalRevisionConflict(false)
      setPersonalFeedback(`Personal workspace saved at revision ${updated.revision}.`)
    } catch (error) {
      const conflict = isRevisionConflict(error)
      setPersonalRevisionConflict(conflict)
      setPersonalError(withConflictRecovery(
        resolveApiErrorMessage(error, 'Personal workspace preferences could not be saved'),
        conflict,
      ))
    }
  }

  const resetPersonal = async () => {
    if (!personalBaseline) return
    setPersonalFeedback('')
    setPersonalError('')
    try {
      const updated = await workspace.resetPreferences(personalBaseline.preferences.revision)
      setPersonalEdit({
        baseline: { effective: personalBaseline.effective, preferences: updated },
        draft: createPersonalWorkspaceDraft(personalBaseline.effective, updated),
        sourcePreferenceRevision: updated.revision,
      })
      setResetPersonalRequested(false)
      setPersonalRevisionConflict(false)
      setPersonalFeedback('Personal workspace preferences reset to the organization defaults.')
    } catch (error) {
      const conflict = isRevisionConflict(error)
      setResetPersonalRequested(false)
      setPersonalRevisionConflict(conflict)
      setPersonalError(withConflictRecovery(
        resolveApiErrorMessage(error, 'Personal workspace preferences could not be reset'),
        conflict,
      ))
    }
  }

  const applyRoleSelection = (role: WorkspaceRole) => {
    setRoleEdit(null)
    setSelectedRole(role)
    setRoleFeedback('')
    setRoleError('')
    setRequestedRole(null)
  }

  const selectRole = (role: WorkspaceRole) => {
    if (
      role === selectedRole ||
      updateRolePolicy.isPending ||
      resetRolePolicy.isPending ||
      roleReloadPending
    ) return
    if (roleDirty) {
      setRequestedRole(role)
      return
    }
    applyRoleSelection(role)
  }

  const confirmRoleSelection = () => {
    if (requestedRole) applyRoleSelection(requestedRole)
  }

  const refresh = async () => {
    setPersonalError('')
    setRoleError('')
    const refreshes: Promise<unknown>[] = [workspace.refresh()]
    if (canReadPolicies) refreshes.push(rolePoliciesQuery.refetch({ throwOnError: true }))
    try {
      await Promise.all(refreshes)
    } catch (error) {
      const message = resolveApiErrorMessage(error, 'Workspace configuration could not be refreshed')
      setPersonalError(message)
      if (canReadPolicies) setRoleError(message)
    }
  }

  const discardAndReloadRole = async () => {
    if (!canReadPolicies || roleReloadPending || updateRolePolicy.isPending || resetRolePolicy.isPending) return
    setRoleReloadPending(true)
    setRoleFeedback('')
    setRoleError('')
    try {
      const result = await rolePoliciesQuery.refetch({ throwOnError: true })
      const latest = result.data?.find((policy) => policy.role === selectedRole)
      if (!latest) throw new Error('The selected role policy was not returned by the server.')
      setRoleEdit(null)
      setRoleRevisionConflict(false)
      setResetRoleRequested(false)
      setRoleFeedback(`${capitalize(selectedRole)} workspace policy reloaded at revision ${latest.revision}.`)
    } catch (error) {
      setRoleError(resolveApiErrorMessage(error, 'The latest workspace role policy could not be loaded'))
    } finally {
      setRoleReloadPending(false)
    }
  }

  const discardAndReloadPersonal = async () => {
    if (personalReloadPending || workspace.isSavingPreferences || workspace.isResettingPreferences) return
    setPersonalReloadPending(true)
    setPersonalFeedback('')
    setPersonalError('')
    try {
      const { effective, preferences } = await workspace.refresh()
      setPersonalEdit({
        baseline: { effective, preferences },
        draft: createPersonalWorkspaceDraft(effective, preferences),
        sourcePreferenceRevision: preferences.revision,
      })
      setPersonalRevisionConflict(false)
      setResetPersonalRequested(false)
      setPersonalFeedback(`Personal workspace reloaded at revision ${preferences.revision}.`)
    } catch (error) {
      setPersonalError(resolveApiErrorMessage(error, 'The latest personal workspace preferences could not be loaded'))
    } finally {
      setPersonalReloadPending(false)
    }
  }

  const discardAndReload = async () => {
    await Promise.all([
      discardAndReloadPersonal(),
      canReadPolicies ? discardAndReloadRole() : Promise.resolve(),
    ])
    setDiscardReloadRequested(false)
  }

  const personalMutationPending = workspace.isSavingPreferences ||
    workspace.isResettingPreferences ||
    personalReloadPending
  const roleMutationPending = updateRolePolicy.isPending ||
    resetRolePolicy.isPending ||
    roleReloadPending

  return {
    discardDialog: confirmDiscardChanges.discardDialog,
    canReadPolicies,
    canManagePolicies: canManagePolicies && roleReadAvailable && !roleDraftNeedsReload,
    templateViewsQuery,
    meQuery,
    personalDraft,
    personalDirty,
    personalError,
    personalFeedback,
    personalMutationPending,
    personalRevisionConflict,
    resetPersonalRequested,
    resetRoleRequested,
    requestedRole,
    roleDirty,
    roleDraft,
    roleError: roleError || rolePolicyError,
    roleFeedback,
    roleMutationPending,
    roleRevisionConflict,
    roleValidation,
    rolePoliciesLoading: rolePoliciesQuery.isLoading,
    roles: WORKSPACE_ROLES,
    selectedPolicy,
    selectedPolicyWarnings,
    selectedRole,
    updateRolePolicy,
    resetRolePolicy,
    workspace,
    workspaceError,
    savePersonal,
    resetPersonal,
    refresh,
    discardAndReload,
    discardAndReloadPersonal,
    discardAndReloadRole,
    setPersonalDraft,
    setResetPersonalRequested,
    setResetRoleRequested,
    discardReloadRequested,
    setDiscardReloadRequested,
    setRoleDraft,
    selectRole,
    cancelRoleSelection: () => setRequestedRole(null),
    confirmRoleSelection,
    hasUnsavedChanges: roleDirty || personalDirty,
    isRefreshing: workspace.isRefreshing || rolePoliciesQuery.isFetching || roleReloadPending || personalReloadPending,
  }
}

function workspaceRoleNotice(
  access: ReturnType<typeof useQueryAccessScope>,
  notice: Awaited<ReturnType<typeof access.read<string>>> | null,
) {
  return access.currentData(notice ?? undefined) ?? ''
}

function workspaceRoleReadState(
  query: { data?: WorkspaceRolePolicyResponse[]; isError: boolean; isSuccess: boolean; isFetching: boolean; error: unknown },
  role: WorkspaceRole,
  edit: RoleDraftEdit | null,
  generation: number,
  denied: boolean,
  userReadError: boolean,
) {
  const roleDraftNeedsReload = Boolean(edit && edit.generation !== generation)
  return {
    selectedPolicy: query.data?.find((policy) => policy.role === role),
    roleReadAvailable: !userReadError && query.isSuccess && !query.isFetching && query.data !== undefined,
    roleDraftNeedsReload,
    rolePolicyError: query.isError
      ? resolveApiErrorMessage(query.error, 'Organization workspace policies could not be loaded')
      : denied || roleDraftNeedsReload ? 'Reload current role policy access before saving this draft.' : '',
  }
}

function buildSparsePersonalPreferencePayload(
  effective: WorkspaceEffectiveResponse,
  preferences: WorkspaceUserPreferenceResponse,
  draft: PersonalWorkspaceDraft,
): WorkspaceUserPreferenceWriteRequest {
  const complete = buildPersonalPreferencePayload(preferences, draft)
  const initial = createPersonalWorkspaceDraft(effective, preferences)
  const originalById = new Map(preferences.modules.map((module) => [module.module_id, module]))
  const normalizedById = new Map(complete.modules.map((module) => [module.module_id, module]))
  const trustedIds = new Set<string>(draft.modules.keys())
  const modules = complete.modules.filter((module) => !trustedIds.has(module.module_id))

  for (const [moduleId, current] of draft.modules) {
    const original = originalById.get(moduleId)
    const initialValue = initial.modules.get(moduleId)
    const normalized = normalizedById.get(moduleId)
    if (!initialValue || !normalized) continue

    const visible = (
      original?.visible !== null && original?.visible !== undefined
    ) || current.visible !== initialValue.visible
      ? current.visible
      : null
    const order = (
      original?.order !== null && original?.order !== undefined
    ) || current.order !== initialValue.order
      ? normalized.order
      : null
    if (visible !== null || order !== null) modules.push({ module_id: moduleId, visible, order })
  }

  return { ...complete, modules }
}

function personalEditIsActive(
  edit: PersonalDraftEdit,
  serverPreferences: WorkspaceUserPreferenceResponse | undefined,
): boolean {
  if (personalDraftIsDirty(edit.baseline.effective, edit.baseline.preferences, edit.draft)) {
    return true
  }
  return !serverPreferences || serverPreferences.revision <= edit.sourcePreferenceRevision
}

function resolveRoleEditor(
  edit: RoleDraftEdit | null,
  role: WorkspaceRole,
  policy: WorkspaceRolePolicyResponse | undefined,
): { draft: RolePolicyDraft | null; baseline: WorkspaceRolePolicyResponse | null } {
  if (edit?.role === role) return { draft: edit.draft, baseline: edit.baseline }
  return {
    draft: policy ? createRolePolicyDraft(policy) : null,
    baseline: policy ?? null,
  }
}

function resolvePersonalEditor(
  edit: PersonalDraftEdit | null,
  effective: WorkspaceEffectiveResponse | undefined,
  preferences: WorkspaceUserPreferenceResponse | undefined,
): {
  draft: PersonalWorkspaceDraft | null
  baseline: PersonalDraftEdit['baseline'] | null
} {
  if (edit && personalEditIsActive(edit, preferences)) {
    return { draft: edit.draft, baseline: edit.baseline }
  }
  if (!effective || !preferences) return { draft: null, baseline: null }
  return {
    draft: createPersonalWorkspaceDraft(effective, preferences),
    baseline: { effective, preferences },
  }
}

function canManageWorkspacePolicies(
  permissions: readonly string[] | undefined,
): boolean {
  return hasRequiredPermissions(permissions ?? [], ['write:workspace'])
}

function isRevisionConflict(error: unknown): boolean {
  return Boolean(
    error &&
    typeof error === 'object' &&
    'status' in error &&
    (error as { status?: unknown }).status === 409,
  )
}

function withConflictRecovery(message: string, conflict: boolean): string {
  return conflict
    ? `${message} Discard this draft and reload the latest revision before editing again.`
    : message
}

function capitalize(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1)
}

export type WorkspaceSettingsController = ReturnType<typeof useWorkspaceSettingsController>
