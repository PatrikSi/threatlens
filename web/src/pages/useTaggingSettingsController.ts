import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
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
  TaggingRuleDraft,
  TaggingSettingsDraft,
  createDefaultRuleDraft,
  createDraftFromRule,
  createRuleRequestFromDraft,
  createSettingsDraft,
  getRuleDraftValidationError,
  parseTaggingReapplyRequest,
} from './taggingSettingsModel'

export function useTaggingSettingsController() {
  const queryClient = useQueryClient()
  const [settingsDraft, setSettingsDraft] = useState<TaggingSettingsDraft>({
    ...DEFAULT_TAGGING_SETTINGS_DRAFT,
    enabled_categories: [...DEFAULT_TAGGING_SETTINGS_DRAFT.enabled_categories],
  })
  const [selectedRuleId, setSelectedRuleId] = useState<string | null>(null)
  const [ruleDraft, setRuleDraft] = useState<TaggingRuleDraft>(() => createDefaultRuleDraft())
  const [previewResult, setPreviewResult] = useState<TaggingRulePreviewResponse | null>(null)
  const [notice, setNotice] = useState<TaggingNotice | null>(null)
  const [reapplyDays, setReapplyDays] = useState('30')
  const [reapplyLimit, setReapplyLimit] = useState('0')
  const [pendingRuleDelete, setPendingRuleDelete] = useState<TaggingRule | null>(null)
  const [pendingReapplyRequest, setPendingReapplyRequest] = useState<TaggingReapplyRequest | null>(null)
  const syncedSettingsDraftRef = useRef<TaggingSettingsDraft | null>(null)

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

  useEffect(() => {
    if (!bundleQuery.data) {
      return
    }
    const availableRuleIds = new Set(bundleQuery.data.rules.map((rule) => rule.id))
    if (selectedRuleId && !availableRuleIds.has(selectedRuleId)) {
      setSelectedRuleId(null)
      setRuleDraft(createDefaultRuleDraft())
      setPreviewResult(null)
    }
  }, [bundleQuery.data, selectedRuleId])

  useEffect(() => setPreviewResult(null), [ruleDraft])

  const selectedRule = useMemo(
    () => bundleQuery.data?.rules.find((rule) => rule.id === selectedRuleId) ?? null,
    [bundleQuery.data, selectedRuleId],
  )
  const baselineSettingsDraft = bundleQuery.data
    ? createSettingsDraft(bundleQuery.data.settings)
    : DEFAULT_TAGGING_SETTINGS_DRAFT
  const baselineRuleDraft = selectedRule ? createDraftFromRule(selectedRule) : createDefaultRuleDraft()
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
    mutationFn: (payload: TaggingRuleWriteRequest) => saveRuleRequest(selectedRuleId, payload),
    onSuccess: (saved) => {
      setSelectedRuleId(saved.id)
      setRuleDraft(createDraftFromRule(saved))
      setNotice({ tone: 'success', message: selectedRuleId ? 'Tagging rule updated.' : 'Tagging rule created.' })
      void queryClient.invalidateQueries({ queryKey: ['tagging', 'settings'] })
    },
  })
  const deleteRule = useMutation({
    mutationKey: ['tagging', 'rules', 'delete'],
    mutationFn: (ruleId: string) => apiFetch<void>(`/tagging/rules/${ruleId}`, { method: 'DELETE' }),
    onSuccess: () => {
      setSelectedRuleId(null)
      setRuleDraft(createDefaultRuleDraft())
      setPreviewResult(null)
      setNotice({ tone: 'success', message: 'Tagging rule deleted.' })
      void queryClient.invalidateQueries({ queryKey: ['tagging', 'settings'] })
    },
  })
  const previewRule = useMutation({
    mutationKey: ['tagging', 'rules', 'preview'],
    mutationFn: (payload: TaggingRuleWriteRequest) =>
      apiFetch<TaggingRulePreviewResponse>('/tagging/rules/preview', {
        method: 'POST',
        body: JSON.stringify({ ...payload, limit: 5 }),
      }),
    onSuccess: (result) => {
      setPreviewResult(result)
      setNotice({ tone: 'success', message: result.total > 0 ? 'Preview loaded.' : 'No current matches for this rule.' })
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
    setSelectedRuleId(rule?.id ?? null)
    setRuleDraft(rule ? createDraftFromRule(rule) : createDefaultRuleDraft())
    setPreviewResult(null)
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
    mutation.mutate(createRuleRequestFromDraft(ruleDraft))
  }

  return {
    bundleQuery,
    confirmDiscardUnsavedTaggingChanges,
    deleteRule,
    feeds: feedsQuery.data ?? [],
    feedsQuery,
    notice,
    onConfirmDeleteRule: () => {
      if (pendingRuleDelete) {
        const ruleId = pendingRuleDelete.id
        setPendingRuleDelete(null)
        deleteRule.mutate(ruleId)
      }
    },
    onConfirmReapplyTagging: () => {
      if (pendingReapplyRequest) {
        const request = pendingReapplyRequest
        setPendingReapplyRequest(null)
        setNotice(null)
        reapplyTagging.mutate(request)
      }
    },
    onCreateNewRule: () => selectRule(null),
    onPreviewRule: () => submitRuleMutation(previewRule),
    onRequestDeleteRule: (rule: TaggingRule | null) => {
      if (rule) {
        confirmDiscardUnsavedTaggingChanges(() => setPendingRuleDelete(rule))
      }
    },
    onRequestReapplyTagging: () => {
      if (reapplyRequestDraft.request) {
        setNotice(null)
        setPendingReapplyRequest(reapplyRequestDraft.request)
      }
    },
    onSaveRule: () => submitRuleMutation(saveRule),
    onSaveSettings: () => {
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
