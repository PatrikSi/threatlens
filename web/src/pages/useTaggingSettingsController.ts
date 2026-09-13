import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import {
  Feed,
  TaggingReapplyResponse,
  TaggingRule,
  TaggingRulePreviewResponse,
  TaggingRuleWriteRequest,
  TaggingSettingsBundleResponse,
} from '../types/api'
import {
  DEFAULT_TAGGING_SETTINGS_DRAFT,
  TaggingNotice,
  TaggingReapplyRequest,
  TaggingSettingsDraft,
  createRuleRequestFromDraft,
  createSettingsDraft,
  getRuleDraftValidationError,
  parseTaggingReapplyRequest,
} from './taggingSettingsModel'
import { useTaggingRuleDraft, type TaggingRuleSubmission } from './useTaggingRuleDraft'
import { hasRequiredPermissions } from '../workspace/workspaceModel'

export function useTaggingSettingsController() {
  const queryClient = useQueryClient()
  const currentUserQuery = useCurrentUser()
  const [settingsDraft, setSettingsDraft] = useState<TaggingSettingsDraft>({
    ...DEFAULT_TAGGING_SETTINGS_DRAFT,
    enabled_categories: [...DEFAULT_TAGGING_SETTINGS_DRAFT.enabled_categories],
  })
  const ruleEditor = useTaggingRuleDraft()
  const { selectedRuleId, selectedRule, ruleDraft, setRuleDraft, baselineRuleDraft } = ruleEditor
  const [preview, setPreview] = useState<{ result: TaggingRulePreviewResponse; submission: TaggingRuleSubmission } | null>(null)
  const previewResult = preview && ruleEditor.isCurrentSubmission(preview.submission) ? preview.result : null
  const [notice, setNotice] = useState<TaggingNotice | null>(null)
  const [reapplyDays, setReapplyDays] = useState('30')
  const [reapplyLimit, setReapplyLimit] = useState('0')
  const [pendingRuleDelete, setPendingRuleDelete] = useState<TaggingRule | null>(null)
  const [pendingReapplyRequest, setPendingReapplyRequest] = useState<TaggingReapplyRequest | null>(null)
  const syncedSettingsDraftRef = useRef<TaggingSettingsDraft | null>(null)
  const canManageTagging = hasRequiredPermissions(
    currentUserQuery.data?.access?.permissions ?? [],
    ['write:tagging'],
  )
  const isReadOnly = !currentUserQuery.isLoading && !canManageTagging
  const accessNotice = isReadOnly
    ? 'You can review content tagging, but changes require permission to manage tagging.'
    : null

  const bundleQuery = useQuery({
    queryKey: ['tagging', 'settings'],
    queryFn: () => apiFetch<TaggingSettingsBundleResponse>('/tagging/settings'),
  })
  const feedsQuery = useQuery({
    queryKey: ['feeds'],
    queryFn: () => apiFetch<Feed[]>('/feeds'),
  })

  useEffect(() => {
    if (!bundleQuery.data?.settings) {
      return
    }
    const nextServerDraft = createSettingsDraft(bundleQuery.data.settings)
    const previousServerDraft = syncedSettingsDraftRef.current
    setSettingsDraft((current) =>
      shouldSyncSettingsDraft(current, previousServerDraft, nextServerDraft) ? nextServerDraft : current,
    )
    syncedSettingsDraftRef.current = nextServerDraft
  }, [bundleQuery.data?.settings])

  const baselineSettingsDraft = bundleQuery.data
    ? createSettingsDraft(bundleQuery.data.settings)
    : DEFAULT_TAGGING_SETTINGS_DRAFT
  const hasUnsavedTaggingChanges =
    !draftsEqual(settingsDraft, baselineSettingsDraft) || !draftsEqual(ruleDraft, baselineRuleDraft)
  const hasUnsavedRuleDraftChanges = !draftsEqual(ruleDraft, baselineRuleDraft)
  const confirmDiscardUnsavedTaggingChanges = useUnsavedChangesWarning(
    hasUnsavedTaggingChanges,
    'Discard unsaved tagging changes?',
  )

  const saveSettings = useMutation({
    mutationKey: ['tagging', 'settings', 'save'],
    mutationFn: () =>
      apiFetch('/tagging/settings', {
        method: 'PUT',
        body: JSON.stringify({
          enabled_categories: settingsDraft.enabled_categories,
          min_auto_tag_confidence: Number(settingsDraft.min_auto_tag_confidence) || 0.45,
          secondary_tag_limit: Number(settingsDraft.secondary_tag_limit) || 0,
        }),
      }),
    onSuccess: () => {
      setNotice({ tone: 'success', message: 'Tagging settings updated.' })
      void queryClient.invalidateQueries({ queryKey: ['tagging', 'settings'] })
    },
  })
  const saveRule = useMutation({
    mutationKey: ['tagging', 'rules', 'save'],
    mutationFn: (submission: TaggingRuleSubmission) =>
      saveRuleRequest(submission.ruleId, createRuleRequestFromDraft(submission.draft)),
    onSuccess: (saved, submission) => {
      // Cancel an older inventory request before inserting the accepted server rule.
      void queryClient.cancelQueries({ queryKey: ['tagging', 'settings'] })
      queryClient.setQueryData<TaggingSettingsBundleResponse>(['tagging', 'settings'], (current) => current ? {
        ...current,
        rules: [...current.rules.filter((rule) => rule.id !== saved.id), saved],
      } : current)
      ruleEditor.acceptSavedRule(saved, submission)
      if (ruleEditor.isSelectedSubmission(submission)) {
        setNotice({ tone: 'success', message: submission.ruleId ? 'Tagging rule updated.' : 'Tagging rule created.' })
      }
      void queryClient.invalidateQueries({ queryKey: ['tagging', 'settings'] })
    },
  })
  const deleteRule = useMutation({
    mutationKey: ['tagging', 'rules', 'delete'],
    mutationFn: (submission: TaggingRuleSubmission) => apiFetch<void>(`/tagging/rules/${submission.ruleId}`, { method: 'DELETE' }),
    onSuccess: (_result, submission) => {
      void queryClient.cancelQueries({ queryKey: ['tagging', 'settings'] })
      queryClient.setQueryData<TaggingSettingsBundleResponse>(['tagging', 'settings'], (current) => current ? {
        ...current, rules: current.rules.filter((rule) => rule.id !== submission.ruleId),
      } : current)
      if (ruleEditor.isSelectedSubmission(submission)) {
        ruleEditor.replaceRuleDraft(null)
        setPreview(null)
        setNotice({ tone: 'success', message: 'Tagging rule deleted.' })
      }
      void queryClient.invalidateQueries({ queryKey: ['tagging', 'settings'] })
    },
  })
  const previewRule = useMutation({
    mutationKey: ['tagging', 'rules', 'preview'],
    mutationFn: (submission: TaggingRuleSubmission) =>
      apiFetch<TaggingRulePreviewResponse>('/tagging/rules/preview', {
        method: 'POST',
        body: JSON.stringify({ ...createRuleRequestFromDraft(submission.draft), limit: 5 }),
      }),
    onSuccess: (result, submission) => {
      if (!ruleEditor.isCurrentSubmission(submission)) return
      setPreview({ result, submission })
      setNotice({ tone: result.warnings?.length ? 'error' : 'success', message: result.complete === false
        ? 'Partial preview loaded. Review the result scope and any evaluation warnings.'
        : result.total > 0 ? 'Preview loaded.' : 'No current matches for this rule.' })
    },
  })
  const reapplyTagging = useMutation({
    mutationKey: ['tagging', 'reapply'],
    mutationFn: (payload: TaggingReapplyRequest) =>
      apiFetch<TaggingReapplyResponse>('/tagging/reapply', { method: 'POST', body: JSON.stringify(payload) }),
    onSuccess: (result) => {
      setNotice({ tone: 'success', message: `Retagging queued. Task ID: ${result.task_id}` })
      setPendingReapplyRequest(null)
    },
  })

  const ruleValidationError = getRuleDraftValidationError(ruleDraft)
  const reapplyRequestDraft = parseTaggingReapplyRequest(reapplyDays, reapplyLimit)
  const replaceRuleDraft = (rule: TaggingRule | null) => {
    ruleEditor.replaceRuleDraft(rule)
    setPreview(null)
    setNotice(null)
  }
  const selectRule = (rule: TaggingRule | null) => {
    if (rule?.id === selectedRuleId) {
      return
    }
    if (hasUnsavedRuleDraftChanges) {
      confirmDiscardUnsavedTaggingChanges(() => replaceRuleDraft(rule))
      return
    }
    replaceRuleDraft(rule)
  }
  const submitRuleMutation = (mutation: typeof saveRule | typeof previewRule) => {
    if (ruleValidationError) {
      setNotice({ tone: 'error', message: ruleValidationError })
      return
    }
    setNotice(null)
    mutation.mutate(ruleEditor.captureSubmission())
  }

  return {
    accessNotice,
    bundleQuery,
    canManageTagging,
    confirmDiscardUnsavedTaggingChanges,
    currentUserQuery,
    deleteRule,
    feeds: feedsQuery.data ?? [],
    feedsQuery,
    notice,
    onConfirmDeleteRule: () => {
      if (pendingRuleDelete && canManageTagging) {
        const ruleId = pendingRuleDelete.id
        setPendingRuleDelete(null)
        deleteRule.mutate({ ...ruleEditor.captureSubmission(), ruleId })
      }
    },
    onConfirmReapplyTagging: () => {
      if (pendingReapplyRequest && canManageTagging) {
        const request = pendingReapplyRequest
        setPendingReapplyRequest(null)
        setNotice(null)
        reapplyTagging.mutate(request)
      }
    },
    onCreateNewRule: () => {
      if (canManageTagging) selectRule(null)
    },
    onPreviewRule: () => {
      if (canManageTagging) submitRuleMutation(previewRule)
    },
    onRequestDeleteRule: (rule: TaggingRule | null) => {
      if (rule && canManageTagging && !saveRule.isPending) {
        confirmDiscardUnsavedTaggingChanges(() => setPendingRuleDelete(rule))
      }
    },
    onRequestReapplyTagging: () => {
      if (reapplyRequestDraft.request && canManageTagging) {
        setNotice(null)
        setPendingReapplyRequest(reapplyRequestDraft.request)
      }
    },
    onSaveRule: () => {
      if (canManageTagging && !deleteRule.isPending) submitRuleMutation(saveRule)
    },
    onSaveSettings: () => {
      if (!canManageTagging) return
      setNotice(null)
      saveSettings.mutate()
    },
    onSelectRule: selectRule,
    pendingReapplyRequest,
    pendingRuleDelete,
    previewResult,
    previewRule,
    reapplyDays,
    reapplyLimit,
    reapplyRequestDraft,
    reapplyTagging,
    ruleDraft,
    ruleValidationError,
    saveRule,
    saveSettings,
    selectedRule,
    selectedRuleId,
    setPendingReapplyRequest,
    setPendingRuleDelete,
    setReapplyDays,
    setReapplyLimit,
    setRuleDraft,
    setSettingsDraft,
    settingsDraft,
  }
}

function shouldSyncSettingsDraft(
  current: TaggingSettingsDraft,
  previous: TaggingSettingsDraft | null,
  next: TaggingSettingsDraft,
): boolean {
  return previous === null || draftsEqual(current, previous) || draftsEqual(current, next)
}

function draftsEqual(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right)
}

function saveRuleRequest(ruleId: string | null, payload: TaggingRuleWriteRequest): Promise<TaggingRule> {
  return apiFetch<TaggingRule>(ruleId ? `/tagging/rules/${ruleId}` : '/tagging/rules', {
    method: ruleId ? 'PATCH' : 'POST',
    body: JSON.stringify(payload),
  })
}

export type TaggingSettingsController = ReturnType<typeof useTaggingSettingsController>
