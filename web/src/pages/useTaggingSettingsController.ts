import { useQueryAccessScope } from '../hooks/useQueryAccessScope'
import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { rebaseSavedDraft } from '../hooks/rebaseSavedDraft'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import type { Feed, TaggingReapplyResponse, TaggingRule, TaggingRulePreviewResponse, TaggingRuleWriteRequest, TaggingSettingsBundleResponse } from '../types/api'
import {
  DEFAULT_TAGGING_SETTINGS_DRAFT, type TaggingNotice, type TaggingReapplyRequest,
  type TaggingSettingsDraft, type TaggingSettingsRequest, createRuleRequestFromDraft,
  createSettingsDraft, getRuleDraftValidationError, parseTaggingReapplyRequest, parseTaggingSettingsDraft,
} from './taggingSettingsModel'
import { useTaggingRuleDraft, type TaggingRuleSubmission } from './useTaggingRuleDraft'
import { hasRequiredPermissions } from '../workspace/workspaceModel'

type Admission = { generation: number; value: boolean }
const BUNDLE_KEY = ['tagging', 'settings']

export function useTaggingSettingsController() {
  const queryClient = useQueryClient()
  const currentUserQuery = useCurrentUser()
  const access = useQueryAccessScope(BUNDLE_KEY)
  const previewAccess = useQueryAccessScope(['tagging', 'preview'])
  const [settingsDraft, setSettingsDraft] = useState<TaggingSettingsDraft>(freshDefaults)
  const [settingsBaseline, setSettingsBaseline] = useState<TaggingSettingsDraft>(freshDefaults)
  const [settingsGeneration, setSettingsGeneration] = useState(access.generation)
  const initializedSettings = useRef(false)
  const ruleEditor = useTaggingRuleDraft(access.generation)
  const { selectedRuleId, selectedRule, ruleDraft, setRuleDraft, baselineRuleDraft } = ruleEditor
  const [preview, setPreview] = useState<{
    result: TaggingRulePreviewResponse; submission: TaggingRuleSubmission; parent: Admission; preview: Admission
  } | null>(null)
  const previewResult = preview && ruleEditor.isCurrentSubmission(preview.submission)
    && access.currentData(preview.parent) && previewAccess.currentData(preview.preview) ? preview.result : null
  const [notice, setNotice] = useState<TaggingNotice | null>(null)
  const [reapplyDays, setReapplyDays] = useState('30')
  const [reapplyLimit, setReapplyLimit] = useState('0')
  const [deleteIntent, setDeleteIntent] = useState<{ rule: TaggingRule; submission: TaggingRuleSubmission; admission: Admission } | null>(null)
  const [reapplyIntent, setReapplyIntent] = useState<{ request: TaggingReapplyRequest; admission: Admission } | null>(null)
  const pending = useRef(new Set<string>())
  const hasWritePermission = !currentUserQuery.isError && hasRequiredPermissions(currentUserQuery.data?.access?.permissions ?? [], ['write:tagging'])
  const isReadOnly = !currentUserQuery.isLoading && !hasWritePermission
  const accessNotice = isReadOnly ? 'You can review content tagging, but changes require permission to manage tagging.' : null

  const bundleQuery = useQuery({
    queryKey: BUNDLE_KEY,
    ...access.queryOptions(({ signal }) => apiFetch<TaggingSettingsBundleResponse>('/tagging/settings', { signal })),
  })
  const feedAccess = useQueryAccessScope(['feeds'])
  const feedsQuery = useQuery({
    queryKey: ['feeds'],
    ...feedAccess.queryOptions(({ signal }) => apiFetch<Feed[]>('/feeds', { signal })),
  })
  const readUnavailable = bundleQuery.isError || access.denied || access.generation > 0 && !bundleQuery.data
  const privateReadHidden = access.denied || access.generation > 0 && !bundleQuery.data
  const canManageTagging = hasWritePermission && !readUnavailable
  const settingsDirty = !draftsEqual(settingsDraft, settingsBaseline)
  const ruleDirty = !draftsEqual(ruleDraft, baselineRuleDraft)
  const settingsNeedsReload = settingsGeneration !== access.generation
  const ruleNeedsReload = ruleEditor.readGeneration !== access.generation
  const ruleReadHidden = privateReadHidden || Boolean(ruleNeedsReload && selectedRuleId && !bundleQuery.data?.rules.some((rule) => rule.id === selectedRuleId))
  const confirmDiscardUnsavedTaggingChanges = useUnsavedChangesWarning(settingsDirty || ruleDirty, 'Discard unsaved tagging changes?')

  useEffect(() => {
    if (!bundleQuery.data) return
    const next = createSettingsDraft(bundleQuery.data.settings)
    if (!settingsDirty || !settingsNeedsReload && draftsEqual(settingsDraft, next)) {
      setSettingsDraft((current) => draftsEqual(current, next) ? current : next)
      setSettingsBaseline((current) => draftsEqual(current, next) ? current : next)
      setSettingsGeneration(access.generation)
    } else if (!initializedSettings.current) {
      setSettingsBaseline(next)
      setSettingsGeneration(access.generation)
    }
    initializedSettings.current = true
  }, [access.generation, bundleQuery.data, settingsDirty, settingsDraft, settingsNeedsReload])
  useEffect(() => {
    if (!bundleQuery.data || ruleDirty || !ruleNeedsReload) return
    ruleEditor.replaceRuleDraft(bundleQuery.data.rules.find((rule) => rule.id === selectedRuleId) ?? null)
  }, [bundleQuery.data, ruleDirty, ruleNeedsReload, selectedRuleId, ruleEditor])
  useEffect(() => { if (access.denied) setNotice(null) }, [access.denied])

  function currentBundle() {
    return queryClient.getQueryState(BUNDLE_KEY)?.status === 'error' ? undefined
      : access.currentData<TaggingSettingsBundleResponse>(queryClient.getQueryData(BUNDLE_KEY))
  }
  function assertReadAvailable(write = false) {
    if (write && !hasWritePermission || queryClient.getQueryState(BUNDLE_KEY)?.status === 'error'
      || !access.currentData(access.captureResult()(true))) throw new Error('Refresh tagging settings before continuing. Your draft is preserved.')
  }
  function assertRuleSubmission(submission: TaggingRuleSubmission, write: boolean) {
    assertReadAvailable(write)
    if (submission.readGeneration !== access.generation) throw new Error('Reload the current rule before submitting this preserved draft.')
    if (submission.ruleId) {
      const rule = currentBundle()?.rules.find((entry) => entry.id === submission.ruleId)
      if (!rule || !submission.baseline || !sameRule(rule, submission.baseline)) throw new Error('The saved rule changed. Reload its current values before continuing.')
    }
  }
  function cacheBundle(update: (value: TaggingSettingsBundleResponse) => TaggingSettingsBundleResponse, capture: ReturnType<typeof access.captureResult>) {
    if (queryClient.getQueryState(BUNDLE_KEY)?.status === 'error' || !access.currentData(capture(true))) return
    queryClient.setQueryData(BUNDLE_KEY, (raw: { generation: number; value: TaggingSettingsBundleResponse } | undefined) => {
      const value = access.currentData(raw)
      return value ? capture(update(value)) : raw
    })
  }
  const refreshBundle = () => void queryClient.invalidateQueries({ queryKey: BUNDLE_KEY, exact: true })

  const saveSettings = useMutation({
    mutationKey: ['tagging', 'settings', 'save'],
    onMutate: access.captureResult,
    mutationFn: (payload: TaggingSettingsRequest) => {
      assertReadAvailable(true)
      if (settingsNeedsReload) throw new Error('Reload the current defaults before saving this preserved draft.')
      return apiFetch<TaggingSettingsBundleResponse['settings']>('/tagging/settings', { method: 'PUT', body: JSON.stringify(payload) })
    },
    onSuccess: async (saved, payload, capture) => {
      if (!capture || !access.currentData(capture(true))) return
      await queryClient.cancelQueries({ queryKey: BUNDLE_KEY, exact: true })
      if (!access.currentData(capture(true))) return
      const next = createSettingsDraft(saved)
      setSettingsDraft((current) => rebaseSavedDraft(settingsDraftFromRequest(payload), current, next))
      setSettingsBaseline(next)
      setSettingsGeneration(access.generation)
      cacheBundle((current) => ({ ...current, settings: saved }), capture)
      setNotice({ tone: 'success', message: 'Tagging settings updated.' })
      refreshBundle()
    },
    onSettled: () => { pending.current.delete('settings') },
  })
  const saveRule = useMutation({
    mutationKey: ['tagging', 'rules', 'save'],
    onMutate: access.captureResult,
    mutationFn: (submission: TaggingRuleSubmission) => {
      assertRuleSubmission(submission, true)
      return saveRuleRequest(submission.ruleId, createRuleRequestFromDraft(submission.draft))
    },
    onSuccess: async (saved, submission, capture) => {
      if (!capture || !access.currentData(capture(true))) return
      // Retire any older inventory response before recording the accepted rule.
      await queryClient.cancelQueries({ queryKey: BUNDLE_KEY, exact: true })
      if (!access.currentData(capture(true))) return
      cacheBundle((current) => ({ ...current, rules: [...current.rules.filter((rule) => rule.id !== saved.id), saved] }), capture)
      ruleEditor.acceptSavedRule(saved, submission)
      if (ruleEditor.isSelectedSubmission(submission)) setNotice({ tone: 'success', message: submission.ruleId ? 'Tagging rule updated.' : 'Tagging rule created.' })
      refreshBundle()
    },
    onSettled: () => { pending.current.delete('rule') },
  })
  const deleteRule = useMutation({
    mutationKey: ['tagging', 'rules', 'delete'],
    onMutate: access.captureResult,
    mutationFn: (submission: TaggingRuleSubmission) => {
      assertRuleSubmission(submission, true)
      if (!submission.ruleId) throw new Error('Select the current saved rule before deleting it.')
      return apiFetch<void>(`/tagging/rules/${submission.ruleId}`, { method: 'DELETE' })
    },
    onSuccess: async (_result, submission, capture) => {
      if (!capture || !access.currentData(capture(true))) return
      await queryClient.cancelQueries({ queryKey: BUNDLE_KEY, exact: true })
      if (!access.currentData(capture(true))) return
      cacheBundle((current) => ({ ...current, rules: current.rules.filter((rule) => rule.id !== submission.ruleId) }), capture)
      if (ruleEditor.isSelectedSubmission(submission)) {
        ruleEditor.replaceRuleDraft(null); setPreview(null); setNotice({ tone: 'success', message: 'Tagging rule deleted.' })
      }
      refreshBundle()
    },
    onSettled: () => { pending.current.delete('delete') },
  })
  const previewRule = useMutation({
    mutationKey: ['tagging', 'rules', 'preview'],
    onMutate: () => ({ parent: access.captureResult()(true), preview: previewAccess.captureResult()(true) }),
    mutationFn: async (submission: TaggingRuleSubmission) => {
      assertRuleSubmission(submission, false)
      const result = await previewAccess.read(() => apiFetch<TaggingRulePreviewResponse>('/tagging/rules/preview', {
        method: 'POST', body: JSON.stringify({ ...createRuleRequestFromDraft(submission.draft), limit: 5 }),
      }))
      return result.value
    },
    onSuccess: (result, submission, admission) => {
      if (!admission || !access.currentData(admission.parent) || !previewAccess.currentData(admission.preview) || !ruleEditor.isCurrentSubmission(submission)) return
      setPreview({ result, submission, ...admission })
      setNotice({ tone: result.warnings?.length ? 'error' : 'success', message: result.complete === false
        ? 'Partial preview loaded. Review the result scope and any evaluation warnings.' : result.total > 0 ? 'Preview loaded.' : 'No current matches for this rule.' })
    },
    onSettled: () => { pending.current.delete('preview') },
  })
  const reapplyTagging = useMutation({
    mutationKey: ['tagging', 'reapply'],
    onMutate: access.captureResult,
    mutationFn: (payload: TaggingReapplyRequest) => {
      assertReadAvailable(true)
      return apiFetch<TaggingReapplyResponse>('/tagging/reapply', { method: 'POST', body: JSON.stringify(payload) })
    },
    onSuccess: (result, _payload, capture) => {
      if (!capture || !access.currentData(capture(true))) return
      setNotice({ tone: 'success', message: `Retagging queued. Task ID: ${result.task_id}` })
      setReapplyIntent(null)
    },
    onSettled: () => { pending.current.delete('reapply') },
  })

  const ruleValidationError = getRuleDraftValidationError(ruleDraft)
  const reapplyRequestDraft = parseTaggingReapplyRequest(reapplyDays, reapplyLimit)
  const pendingRuleDelete = deleteIntent && access.currentData(deleteIntent.admission)
    && ruleEditor.isSelectedSubmission(deleteIntent.submission)
    && (bundleQuery.isError || bundleQuery.data?.rules.some((rule) => sameRule(rule, deleteIntent.rule))) ? deleteIntent.rule : null
  const pendingReapplyRequest = reapplyIntent && access.currentData(reapplyIntent.admission) ? reapplyIntent.request : null
  const replaceRuleDraft = (rule: TaggingRule | null) => { ruleEditor.replaceRuleDraft(rule); setPreview(null); setNotice(null) }
  const selectRule = (rule: TaggingRule | null) => {
    if (readUnavailable || rule?.id === selectedRuleId) return
    const apply = () => { setDeleteIntent(null); replaceRuleDraft(rule) }
    if (ruleDirty) confirmDiscardUnsavedTaggingChanges(apply)
    else apply()
  }
  const submitRuleMutation = (kind: 'rule' | 'preview') => {
    if (!canManageTagging || ruleNeedsReload || pending.current.has(kind)) return
    if (ruleValidationError) { setNotice({ tone: 'error', message: ruleValidationError }); return }
    pending.current.add(kind); setNotice(null)
    const submission = ruleEditor.captureSubmission()
    if (kind === 'rule') saveRule.mutate(submission)
    else previewRule.mutate(submission)
  }
  const reloadSettings = () => confirmDiscardUnsavedTaggingChanges(() => {
    const current = currentBundle()
    if (!current) return
    const next = createSettingsDraft(current.settings)
    setSettingsDraft(next); setSettingsBaseline(next); setSettingsGeneration(access.generation); setNotice(null)
  })
  const reloadRule = () => confirmDiscardUnsavedTaggingChanges(() => {
    const current = currentBundle()
    if (current) replaceRuleDraft(current.rules.find((rule) => rule.id === selectedRuleId) ?? null)
  })

  return {
    accessNotice, bundleQuery, canManageTagging, confirmDiscardUnsavedTaggingChanges, currentUserQuery, deleteRule,
    feeds: feedsQuery.data ?? [], feedsQuery, notice, privateReadHidden, ruleReadHidden, readUnavailable, settingsNeedsReload, ruleNeedsReload,
    settingsDirty, ruleDirty, onReloadSettings: reloadSettings, onReloadRule: reloadRule,
    onConfirmDeleteRule: () => {
      if (!pendingRuleDelete || !deleteIntent || !canManageTagging || pending.current.has('delete')) return
      const submission = deleteIntent.submission
      setDeleteIntent(null); pending.current.add('delete'); deleteRule.mutate(submission)
    },
    onConfirmReapplyTagging: () => {
      if (!pendingReapplyRequest || !canManageTagging || pending.current.has('reapply')) return
      const request = pendingReapplyRequest
      setReapplyIntent(null); setNotice(null); pending.current.add('reapply'); reapplyTagging.mutate(request)
    },
    onCreateNewRule: () => { if (canManageTagging) selectRule(null) },
    onPreviewRule: () => submitRuleMutation('preview'),
    onRequestDeleteRule: (rule: TaggingRule | null) => {
      if (!rule || !canManageTagging || ruleNeedsReload || saveRule.isPending) return
      confirmDiscardUnsavedTaggingChanges(() => {
        const submission = ruleEditor.captureSubmission()
        if (submission.ruleId === rule.id) setDeleteIntent({ rule, submission, admission: access.captureResult()(true) })
      })
    },
    onRequestReapplyTagging: () => {
      if (reapplyRequestDraft.request && canManageTagging) {
        setNotice(null); setReapplyIntent({ request: reapplyRequestDraft.request, admission: access.captureResult()(true) })
      }
    },
    onSaveRule: () => { if (!deleteRule.isPending) submitRuleMutation('rule') },
    onSaveSettings: () => {
      if (!canManageTagging || settingsNeedsReload || pending.current.has('settings')) return
      const parsed = parseTaggingSettingsDraft(settingsDraft)
      if (!parsed.request) { setNotice({ tone: 'error', message: parsed.error }); return }
      setNotice(null); pending.current.add('settings'); saveSettings.mutate(parsed.request)
    },
    onSelectRule: selectRule, pendingReapplyRequest, pendingRuleDelete, previewResult, previewRule,
    reapplyDays, reapplyLimit, reapplyRequestDraft, reapplyTagging, ruleDraft, ruleValidationError, saveRule, saveSettings,
    selectedRule, selectedRuleId,
    setPendingReapplyRequest: (request: TaggingReapplyRequest | null) => setReapplyIntent(request ? { request, admission: access.captureResult()(true) } : null),
    setPendingRuleDelete: (rule: TaggingRule | null) => setDeleteIntent(rule ? { rule, submission: ruleEditor.captureSubmission(), admission: access.captureResult()(true) } : null),
    setReapplyDays, setReapplyLimit, setRuleDraft, setSettingsDraft, settingsDraft,
  }
}

function freshDefaults(): TaggingSettingsDraft {
  return { ...DEFAULT_TAGGING_SETTINGS_DRAFT, enabled_categories: [...DEFAULT_TAGGING_SETTINGS_DRAFT.enabled_categories] }
}
function draftsEqual(left: unknown, right: unknown) { return JSON.stringify(left) === JSON.stringify(right) }
function sameRule(left: TaggingRule, right: TaggingRule) {
  return left.id === right.id && left.updated_at === right.updated_at && draftsEqual(
    createRuleRequestFromDraft({
      ...left,
      min_classification_confidence: left.min_classification_confidence === null
        ? '' : String(left.min_classification_confidence)
    }),
    createRuleRequestFromDraft({
      ...right,
      min_classification_confidence: right.min_classification_confidence === null
        ? '' : String(right.min_classification_confidence)
    })
  )
}
function settingsDraftFromRequest(payload: TaggingSettingsRequest): TaggingSettingsDraft {
  return { enabled_categories: [...payload.enabled_categories], min_auto_tag_confidence: String(payload.min_auto_tag_confidence), secondary_tag_limit: String(payload.secondary_tag_limit) }
}
function saveRuleRequest(ruleId: string | null, payload: TaggingRuleWriteRequest): Promise<TaggingRule> {
  return apiFetch<TaggingRule>(ruleId ? `/tagging/rules/${ruleId}` : '/tagging/rules', { method: ruleId ? 'PATCH' : 'POST', body: JSON.stringify(payload) })
}
export type TaggingSettingsController = ReturnType<typeof useTaggingSettingsController>
