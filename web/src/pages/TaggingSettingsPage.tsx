import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, apiFetch } from '../api/client'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import { formatDateTime } from '../utils/datetime'
import {
  Feed,
  TaggingReapplyResponse,
  TaggingRule,
  TaggingRulePreviewResponse,
  TaggingRuleWriteRequest,
  TaggingSettingsBundleResponse,
} from '../types/api'

const BUILTIN_CATEGORIES = [
  'vulnerability',
  'apt_campaign',
  'malware_ransomware',
  'phishing_social_engineering',
  'supply_chain',
  'incident_breach',
  'threat_intelligence_research',
  'defensive_guidance',
  'technology_ai',
  'multi',
] as const

type TaggingRuleField = TaggingRuleWriteRequest['applies_to'][number]

const RULE_FIELDS = [
  { value: 'title', label: 'Title' },
  { value: 'summary', label: 'Summary' },
  { value: 'article_text', label: 'Article Text' },
  { value: 'feed_name', label: 'Feed Name' },
] as const satisfies ReadonlyArray<{ value: TaggingRuleField; label: string }>

type TaggingSettingsDraft = {
  enabled_categories: string[]
  min_auto_tag_confidence: string
  secondary_tag_limit: string
}

type TaggingRuleDraft = Omit<TaggingRuleWriteRequest, 'min_classification_confidence'> & {
  min_classification_confidence: string
}

type TaggingReapplyRequest = {
  days: number
  limit: number
}

type TaggingNotice = {
  tone: 'success' | 'error'
  message: string
}

export function TaggingSettingsPage() {
  const queryClient = useQueryClient()
  const [settingsDraft, setSettingsDraft] = useState<TaggingSettingsDraft>({
    enabled_categories: [...BUILTIN_CATEGORIES],
    min_auto_tag_confidence: '0.45',
    secondary_tag_limit: '2',
  })
  const [selectedRuleId, setSelectedRuleId] = useState<string | null>(null)
  const [ruleDraft, setRuleDraft] = useState<TaggingRuleDraft>(() => createDefaultRuleDraft())
  const [previewResult, setPreviewResult] = useState<TaggingRulePreviewResponse | null>(null)
  const [notice, setNotice] = useState<TaggingNotice | null>(null)
  const [reapplyDays, setReapplyDays] = useState('30')
  const [reapplyLimit, setReapplyLimit] = useState('0')
  const [pendingRuleDelete, setPendingRuleDelete] = useState<TaggingRule | null>(null)
  const [pendingReapplyRequest, setPendingReapplyRequest] = useState<TaggingReapplyRequest | null>(null)

  const bundleQuery = useQuery({
    queryKey: ['tagging', 'settings'],
    queryFn: () => apiFetch<TaggingSettingsBundleResponse>('/tagging/settings'),
  })

  const feedsQuery = useQuery({
    queryKey: ['feeds'],
    queryFn: () => apiFetch<Feed[]>('/feeds'),
  })

  useEffect(() => {
    if (!bundleQuery.data) {
      return
    }

    setSettingsDraft({
      enabled_categories: [...bundleQuery.data.settings.enabled_categories],
      min_auto_tag_confidence: String(bundleQuery.data.settings.min_auto_tag_confidence),
      secondary_tag_limit: String(bundleQuery.data.settings.secondary_tag_limit),
    })

    const availableRuleIds = new Set(bundleQuery.data.rules.map((rule) => rule.id))
    if (selectedRuleId && !availableRuleIds.has(selectedRuleId)) {
      setSelectedRuleId(null)
      setRuleDraft(createDefaultRuleDraft())
      setPreviewResult(null)
    }
  }, [bundleQuery.data, selectedRuleId])

  useEffect(() => {
    setPreviewResult(null)
  }, [ruleDraft])

  const selectedRule = useMemo(
    () => bundleQuery.data?.rules.find((rule) => rule.id === selectedRuleId) ?? null,
    [bundleQuery.data, selectedRuleId],
  )
  const baselineSettingsDraft: TaggingSettingsDraft = bundleQuery.data
    ? {
        enabled_categories: [...bundleQuery.data.settings.enabled_categories],
        min_auto_tag_confidence: String(bundleQuery.data.settings.min_auto_tag_confidence),
        secondary_tag_limit: String(bundleQuery.data.settings.secondary_tag_limit),
      }
    : {
        enabled_categories: [...BUILTIN_CATEGORIES],
        min_auto_tag_confidence: '0.45',
        secondary_tag_limit: '2',
      }
  const baselineRuleDraft = selectedRule ? createDraftFromRule(selectedRule) : createDefaultRuleDraft()
  const hasUnsavedTaggingChanges =
    JSON.stringify(settingsDraft) !== JSON.stringify(baselineSettingsDraft) ||
    JSON.stringify(ruleDraft) !== JSON.stringify(baselineRuleDraft)
  const hasUnsavedRuleDraftChanges = JSON.stringify(ruleDraft) !== JSON.stringify(baselineRuleDraft)
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
    mutationFn: (payload: TaggingRuleWriteRequest) => {
      if (selectedRuleId) {
        return apiFetch<TaggingRule>(`/tagging/rules/${selectedRuleId}`, {
          method: 'PATCH',
          body: JSON.stringify(payload),
        })
      }
      return apiFetch<TaggingRule>('/tagging/rules', {
        method: 'POST',
        body: JSON.stringify(payload),
      })
    },
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

  const onConfirmDeleteRule = () => {
    if (!pendingRuleDelete) {
      return
    }

    const ruleId = pendingRuleDelete.id
    setPendingRuleDelete(null)
    deleteRule.mutate(ruleId)
  }

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
      apiFetch<TaggingReapplyResponse>('/tagging/reapply', {
        method: 'POST',
        body: JSON.stringify(payload),
      }),
    onSuccess: (result) => {
      setNotice({ tone: 'success', message: `Retagging queued. Task ID: ${result.task_id}` })
      setPendingReapplyRequest(null)
    },
  })

  const feeds = feedsQuery.data ?? []
  const ruleValidationError = getRuleDraftValidationError(ruleDraft)
  const reapplyRequestDraft = parseTaggingReapplyRequest(reapplyDays, reapplyLimit)

  const onSelectRule = (rule: TaggingRule) => {
    if (rule.id === selectedRuleId) {
      return
    }
    if (!hasUnsavedRuleDraftChanges) {
      setSelectedRuleId(rule.id)
      setRuleDraft(createDraftFromRule(rule))
      setPreviewResult(null)
      setNotice(null)
      return
    }

    confirmDiscardUnsavedTaggingChanges(() => {
      setSelectedRuleId(rule.id)
      setRuleDraft(createDraftFromRule(rule))
      setPreviewResult(null)
      setNotice(null)
    })
  }

  const onCreateNewRule = () => {
    if (!hasUnsavedRuleDraftChanges) {
      setSelectedRuleId(null)
      setRuleDraft(createDefaultRuleDraft())
      setPreviewResult(null)
      setNotice(null)
      return
    }

    confirmDiscardUnsavedTaggingChanges(() => {
      setSelectedRuleId(null)
      setRuleDraft(createDefaultRuleDraft())
      setPreviewResult(null)
      setNotice(null)
    })
  }

  const onPreviewRule = () => {
    if (ruleValidationError) {
      setNotice({ tone: 'error', message: ruleValidationError })
      return
    }
    setNotice(null)
    previewRule.mutate(createRuleRequestFromDraft(ruleDraft))
  }

  const onSaveRule = () => {
    if (ruleValidationError) {
      setNotice({ tone: 'error', message: ruleValidationError })
      return
    }
    setNotice(null)
    saveRule.mutate(createRuleRequestFromDraft(ruleDraft))
  }

  const onConfirmReapplyTagging = () => {
    if (!pendingReapplyRequest) {
      return
    }

    const request = pendingReapplyRequest
    setPendingReapplyRequest(null)
    setNotice(null)
    reapplyTagging.mutate(request)
  }

  const onRequestDeleteRule = (rule: TaggingRule | null) => {
    if (!rule) {
      return
    }

    confirmDiscardUnsavedTaggingChanges(() => {
      setPendingRuleDelete(rule)
    })
  }

  return (
    <div className="space-y-4">
      <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
        <p className="text-xs font-semibold uppercase text-slate dark:text-white/55">Automation</p>
        <h2 className="mt-1 font-display text-xl">Custom Tagging</h2>
        <p className="mt-1 text-sm text-slate dark:text-white/75">
          Tune the built-in auto-tagging behavior and add custom rules that create new tags from article content.
        </p>
      </section>

      {notice && (
        <p
          role={notice.tone === 'error' ? 'alert' : 'status'}
          aria-live={notice.tone === 'error' ? 'assertive' : 'polite'}
          aria-atomic="true"
          className={`rounded-lg border px-3 py-2 text-sm ${
            notice.tone === 'error'
              ? 'border-red-500/20 bg-red-500/10 text-red-700 dark:border-red-900/40 dark:bg-red-950/20 dark:text-red-200'
              : 'border-emerald-500/20 bg-emerald-500/10 text-emerald-700 dark:border-emerald-900/40 dark:bg-emerald-950/20 dark:text-emerald-300'
          }`}
        >
          {notice.message}
        </p>
      )}

      <div className="grid gap-4 xl:grid-cols-[1fr_340px]">
        <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h3 className="font-display text-lg">Auto-Tag Defaults</h3>
              <p className="mt-1 text-sm text-slate dark:text-white/70">
                Control which built-in category tags can be applied automatically and how conservative the engine should be.
              </p>
            </div>
            <button
              className="rounded bg-ink px-3 py-2 text-sm font-semibold text-white dark:bg-cyan dark:text-[#053c2e]"
              disabled={saveSettings.isPending}
              onClick={() => {
                setNotice(null)
                saveSettings.mutate()
              }}
            >
              Save defaults
            </button>
          </div>

          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <div>
              <label htmlFor="tagging-auto-confidence" className="text-sm font-semibold">
                Minimum Auto-Tag Confidence
              </label>
              <input
                id="tagging-auto-confidence"
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                type="number"
                min={0.05}
                max={0.995}
                step={0.01}
                value={settingsDraft.min_auto_tag_confidence}
                onChange={(event) => setSettingsDraft((current) => ({ ...current, min_auto_tag_confidence: event.target.value }))}
              />
            </div>
            <div>
              <label htmlFor="tagging-secondary-tag-limit" className="text-sm font-semibold">
                Secondary Tag Limit
              </label>
              <select
                id="tagging-secondary-tag-limit"
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={settingsDraft.secondary_tag_limit}
                onChange={(event) => setSettingsDraft((current) => ({ ...current, secondary_tag_limit: event.target.value }))}
              >
                <option value="0">0</option>
                <option value="1">1</option>
                <option value="2">2</option>
              </select>
            </div>
          </div>

          <div className="mt-4">
            <p className="text-sm font-semibold">Enabled Built-In Category Tags</p>
            <div className="mt-2 flex flex-wrap gap-2">
              {BUILTIN_CATEGORIES.map((category) => {
                const active = settingsDraft.enabled_categories.includes(category)
                return (
                  <button
                    type="button"
                    key={category}
                    aria-pressed={active}
                    className={`rounded-full border px-3 py-1.5 text-sm ${
                      active
                        ? 'tl-chip-filter-active'
                        : 'tl-chip-neutral hover:border-slate/40 dark:hover:border-cyan-700/60'
                    }`}
                    onClick={() =>
                      setSettingsDraft((current) => ({
                        ...current,
                        enabled_categories: current.enabled_categories.includes(category)
                          ? current.enabled_categories.length === 1
                            ? current.enabled_categories
                            : current.enabled_categories.filter((entry) => entry !== category)
                          : [...current.enabled_categories, category],
                      }))
                    }
                  >
                    {formatCategoryLabel(category)}
                  </button>
                )
              })}
            </div>
          </div>

          {saveSettings.isError && (
            <p role="alert" aria-live="assertive" aria-atomic="true" className="mt-3 text-sm text-red-600">
              {resolveApiMessage(saveSettings.error, 'Failed to update tagging settings.')}
            </p>
          )}
        </section>

        <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
          <h3 className="font-display text-lg">Reapply Tagging</h3>
          <p className="mt-1 text-sm text-slate dark:text-white/70">
            Queue a background pass to re-tag recent items using the current settings and rules.
          </p>

          <div className="mt-4 space-y-3">
            <div>
              <label htmlFor="tagging-reapply-days" className="text-sm font-semibold">
                Days Back
              </label>
              <input
                id="tagging-reapply-days"
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                type="number"
                min={1}
                max={365}
                value={reapplyDays}
                onChange={(event) => setReapplyDays(event.target.value)}
              />
            </div>
            <div>
              <label htmlFor="tagging-reapply-limit" className="text-sm font-semibold">
                Limit
              </label>
              <input
                id="tagging-reapply-limit"
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                type="number"
                min={0}
                max={5000}
                value={reapplyLimit}
                onChange={(event) => setReapplyLimit(event.target.value)}
              />
              <p className="mt-1 text-xs text-slate dark:text-white/60">Use 0 to retag all items in the selected time window.</p>
            </div>
            <button
              className="rounded bg-ink px-3 py-2 text-sm font-semibold text-white dark:bg-cyan dark:text-[#053c2e]"
              disabled={reapplyTagging.isPending || !reapplyRequestDraft.request}
              onClick={() => {
                if (!reapplyRequestDraft.request) {
                  return
                }
                setNotice(null)
                setPendingReapplyRequest(reapplyRequestDraft.request)
              }}
            >
              Queue retagging
            </button>
            {reapplyRequestDraft.error && <p className="text-sm text-amber-700 dark:text-amber-300">{reapplyRequestDraft.error}</p>}
            {reapplyTagging.isError && (
              <p role="alert" aria-live="assertive" aria-atomic="true" className="text-sm text-red-600">
                {resolveApiMessage(reapplyTagging.error, 'Failed to queue retagging.')}
              </p>
            )}
          </div>
        </section>
      </div>

      <div className="grid gap-4 xl:grid-cols-[320px_1fr]">
        <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
          <div className="flex items-center justify-between gap-3">
            <h3 className="font-display text-lg">Custom Rules</h3>
            <button
              className="rounded border border-slate/30 px-3 py-1.5 text-sm font-semibold dark:border-cyan-900/40"
              onClick={onCreateNewRule}
            >
              New rule
            </button>
          </div>

          <div className="mt-3 space-y-2">
            {(bundleQuery.data?.rules ?? []).map((rule) => {
              const selected = rule.id === selectedRuleId
              return (
                <button
                  key={rule.id}
                  type="button"
                  aria-pressed={selected}
                  className={`w-full rounded-lg border p-3 text-left transition ${
                    selected
                      ? 'tl-row-selected'
                      : 'border-slate/20 hover:border-slate/40 dark:border-cyan-900/40 dark:hover:border-cyan-700/60'
                  }`}
                  onClick={() => onSelectRule(rule)}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <p className="font-semibold">{rule.name}</p>
                      <p className="mt-1 text-xs text-slate dark:text-white/65">
                        {rule.tag_name} • {rule.match_type}
                      </p>
                    </div>
                    <span
                      className={`tl-chip ${
                        rule.enabled
                          ? 'tl-chip-success'
                          : 'tl-chip-neutral'
                      }`}
                    >
                      {rule.enabled ? 'Enabled' : 'Disabled'}
                    </span>
                  </div>
                  <p className="mt-2 truncate text-xs text-slate dark:text-white/65">{rule.pattern}</p>
                </button>
              )
            })}

            {bundleQuery.isLoading && <p className="text-sm text-slate dark:text-white/70">Loading tagging rules...</p>}
            {bundleQuery.isError && <p className="text-sm text-red-600">{resolveApiMessage(bundleQuery.error, 'Failed to load tagging settings.')}</p>}
            {!bundleQuery.isLoading && (bundleQuery.data?.rules.length ?? 0) === 0 && (
              <p className="rounded-lg border border-dashed border-slate/25 p-3 text-sm text-slate dark:border-cyan-900/40 dark:text-white/70">
                No custom rules yet. Create one to add a new auto-tag based on article text or feed context.
              </p>
            )}
          </div>
        </section>

        <div className="space-y-4">
          <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h3 className="font-display text-lg">{selectedRule ? 'Edit Custom Rule' : 'Create Custom Rule'}</h3>
                <p className="mt-1 text-sm text-slate dark:text-white/75">
                  Add a new tag when article text, title, or feed context matches the conditions below.
                </p>
              </div>
              <label className="flex items-center gap-2 rounded-full border border-slate/20 px-3 py-1 text-sm dark:border-cyan-900/40">
                <input
                  type="checkbox"
                  checked={ruleDraft.enabled}
                  onChange={(event) => setRuleDraft((current) => ({ ...current, enabled: event.target.checked }))}
                />
                Enabled
              </label>
            </div>

            <div className="mt-4 grid gap-4 md:grid-cols-2">
              <div>
                <label htmlFor="tagging-rule-name" className="text-sm font-semibold">
                  Rule Name
                </label>
                <input
                  id="tagging-rule-name"
                  className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                  value={ruleDraft.name}
                  onChange={(event) => setRuleDraft((current) => ({ ...current, name: event.target.value }))}
                />
              </div>
              <div>
                <label htmlFor="tagging-rule-tag-name" className="text-sm font-semibold">
                  Tag Name
                </label>
                <input
                  id="tagging-rule-tag-name"
                  className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                  value={ruleDraft.tag_name}
                  onChange={(event) => setRuleDraft((current) => ({ ...current, tag_name: event.target.value }))}
                />
              </div>
              <div>
                <label htmlFor="tagging-rule-match-type" className="text-sm font-semibold">
                  Match Type
                </label>
                <select
                  id="tagging-rule-match-type"
                  className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                  value={ruleDraft.match_type}
                  onChange={(event) =>
                    setRuleDraft((current) => ({
                      ...current,
                      match_type: event.target.value as TaggingRuleDraft['match_type'],
                    }))
                  }
                >
                  <option value="contains">Contains text</option>
                  <option value="regex">Regular expression</option>
                </select>
              </div>
              <div>
                <label htmlFor="tagging-rule-min-confidence" className="text-sm font-semibold">
                  Minimum Classification Confidence
                </label>
                <input
                  id="tagging-rule-min-confidence"
                  className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                  type="number"
                  min={0}
                  max={1}
                  step={0.01}
                  value={ruleDraft.min_classification_confidence}
                  onChange={(event) => setRuleDraft((current) => ({ ...current, min_classification_confidence: event.target.value }))}
                />
              </div>
            </div>

            <div className="mt-4">
              <label htmlFor="tagging-rule-pattern" className="text-sm font-semibold">
                Pattern
              </label>
              <textarea
                id="tagging-rule-pattern"
                className="mt-1 h-28 w-full rounded border border-slate/30 bg-white px-3 py-2 font-mono text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                value={ruleDraft.pattern}
                onChange={(event) => setRuleDraft((current) => ({ ...current, pattern: event.target.value }))}
              />
              <label className="mt-2 flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={ruleDraft.case_sensitive}
                  onChange={(event) => setRuleDraft((current) => ({ ...current, case_sensitive: event.target.checked }))}
                />
                Case sensitive
              </label>
            </div>

            <RuleSelectionGroup
              title="Look In"
              description="Choose which fields the pattern should inspect."
              entries={RULE_FIELDS.map((field) => ({ value: field.value, label: field.label }))}
              selectedValues={ruleDraft.applies_to}
              onToggle={(value) =>
                setRuleDraft((current) => ({
                  ...current,
                  applies_to: current.applies_to.includes(value)
                    ? current.applies_to.filter((entry) => entry !== value)
                    : [...current.applies_to, value],
                }))
              }
            />

            <RuleSelectionGroup
              title="Only Apply For Categories"
              description="Optional. If empty, the rule can match any classified item."
              entries={BUILTIN_CATEGORIES.map((category) => ({ value: category, label: formatCategoryLabel(category) }))}
              selectedValues={ruleDraft.required_categories}
              onToggle={(value) =>
                setRuleDraft((current) => ({
                  ...current,
                  required_categories: current.required_categories.includes(value)
                    ? current.required_categories.filter((entry) => entry !== value)
                    : [...current.required_categories, value],
                }))
              }
            />

            <div className="mt-5 rounded-lg border border-slate/20 p-4 dark:border-cyan-900/40">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h4 className="font-semibold">Feed Scope</h4>
                  <p className="mt-1 text-xs text-slate dark:text-white/65">Target all feeds or limit this rule to selected feeds.</p>
                </div>
                <div
                  role="group"
                  aria-label="Rule feed scope"
                  className="flex rounded-lg border border-slate/20 p-1 dark:border-cyan-900/40"
                >
                  <button
                    type="button"
                    aria-pressed={ruleDraft.feed_scope === 'all'}
                    className={`rounded px-3 py-1 text-sm ${
                      ruleDraft.feed_scope === 'all' ? 'bg-ink text-white dark:bg-cyan dark:text-[#053c2e]' : 'text-slate dark:text-white/75'
                    }`}
                    onClick={() => setRuleDraft((current) => ({ ...current, feed_scope: 'all', feed_ids: [] }))}
                  >
                    Any feed
                  </button>
                  <button
                    type="button"
                    aria-pressed={ruleDraft.feed_scope === 'selected'}
                    className={`rounded px-3 py-1 text-sm ${
                      ruleDraft.feed_scope === 'selected'
                        ? 'bg-ink text-white dark:bg-cyan dark:text-[#053c2e]'
                        : 'text-slate dark:text-white/75'
                    }`}
                    onClick={() => setRuleDraft((current) => ({ ...current, feed_scope: 'selected' }))}
                  >
                    Selected feeds
                  </button>
                </div>
              </div>

              {ruleDraft.feed_scope === 'selected' && (
                <div className="mt-4 grid gap-2 md:grid-cols-2">
                  {feeds.map((feed) => {
                    const checked = ruleDraft.feed_ids.includes(feed.id)
                    return (
                      <label key={feed.id} className="flex items-start gap-3 rounded border border-slate/20 p-3 text-sm dark:border-cyan-900/40">
                        <input
                          className="mt-1"
                          type="checkbox"
                          checked={checked}
                          onChange={() =>
                            setRuleDraft((current) => ({
                              ...current,
                              feed_scope: 'selected',
                              feed_ids: checked
                                ? current.feed_ids.filter((candidate) => candidate !== feed.id)
                                : [...current.feed_ids, feed.id],
                            }))
                          }
                        />
                        <span>
                          <span className="block font-semibold">{feed.name}</span>
                          <span className="text-xs text-slate dark:text-white/60">{feed.url}</span>
                        </span>
                      </label>
                    )
                  })}
                </div>
              )}
            </div>

            <div className="mt-5 flex flex-wrap items-center gap-2">
              <button
                className="rounded bg-ink px-3 py-2 text-sm font-semibold text-white dark:bg-cyan dark:text-[#053c2e]"
                disabled={saveRule.isPending || Boolean(ruleValidationError)}
                onClick={onSaveRule}
              >
                {selectedRule ? 'Save rule' : 'Create rule'}
              </button>
              <button
                className="rounded border border-slate/30 px-3 py-2 text-sm font-semibold dark:border-cyan-900/40"
                disabled={previewRule.isPending || Boolean(ruleValidationError)}
                onClick={onPreviewRule}
              >
                Preview rule
              </button>
              {selectedRule && (
                <button
                  className="rounded border border-red-300 px-3 py-2 text-sm font-semibold text-red-700 dark:border-red-900/60 dark:text-red-300"
                  disabled={deleteRule.isPending || Boolean(pendingRuleDelete)}
                  onClick={() => onRequestDeleteRule(selectedRule)}
                >
                  Delete rule
                </button>
              )}
            </div>

            {ruleValidationError && <p className="mt-2 text-sm text-amber-700 dark:text-amber-300">{ruleValidationError}</p>}
            {saveRule.isError && (
              <p role="alert" aria-live="assertive" aria-atomic="true" className="mt-2 text-sm text-red-600">
                {resolveApiMessage(saveRule.error, 'Failed to save tagging rule.')}
              </p>
            )}
            {deleteRule.isError && (
              <p role="alert" aria-live="assertive" aria-atomic="true" className="mt-2 text-sm text-red-600">
                {resolveApiMessage(deleteRule.error, 'Failed to delete tagging rule.')}
              </p>
            )}
            {previewRule.isError && (
              <p role="alert" aria-live="assertive" aria-atomic="true" className="mt-2 text-sm text-red-600">
                {resolveApiMessage(previewRule.error, 'Failed to preview tagging rule.')}
              </p>
            )}
          </section>

          <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h3 className="font-display text-lg">Rule Preview</h3>
                <p className="mt-1 text-sm text-slate dark:text-white/75">
                  See how this rule would match the current corpus before you save it.
                </p>
              </div>
              {previewResult && (
                <span className="tl-chip tl-chip-md tl-chip-info">
                  {previewResult.total} current match{previewResult.total === 1 ? '' : 'es'}
                </span>
              )}
            </div>

            {!previewResult && (
              <p className="mt-3 text-sm text-slate dark:text-white/70">Run a preview to inspect recent matches and affected items.</p>
            )}

            {previewResult && (
              <div className="mt-4 space-y-3">
                {previewResult.items.length > 0 ? (
                  previewResult.items.map((item) => (
                    <article key={item.id} className="rounded-lg border border-slate/20 bg-white/70 p-3 dark:border-cyan-900/40 dark:bg-[#072019]/70">
                      <div className="flex flex-wrap items-start justify-between gap-2">
                        <div>
                          <p className="font-semibold">{item.title}</p>
                          <p className="mt-1 text-xs text-slate dark:text-white/60">
                            {item.feed_name} • {formatTimestamp(item.first_seen_at)}
                          </p>
                        </div>
                        {item.classification && (
                          <span className="tl-chip tl-chip-neutral">
                            {formatCategoryLabel(item.classification)}
                          </span>
                        )}
                      </div>
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {item.matched_sections.map((section) => (
                          <span
                            key={`${item.id}-${section}`}
                            className="tl-chip tl-chip-neutral"
                          >
                            matched in {formatFieldLabel(section)}
                          </span>
                        ))}
                        {item.current_tags.map((tagName) => (
                          <span
                            key={`${item.id}-${tagName}`}
                            className="rounded-full border border-slate/25 bg-slate/10 px-2 py-0.5 text-[11px] text-slate-700 dark:border-white/10 dark:bg-white/5 dark:text-white/70"
                          >
                            current: {tagName}
                          </span>
                        ))}
                      </div>
                    </article>
                  ))
                ) : (
                  <p className="text-sm text-slate dark:text-white/70">No current items would match this rule.</p>
                )}

                {previewResult.total > previewResult.items.length && (
                  <p className="text-xs text-slate dark:text-white/60">
                    Showing the {previewResult.items.length} most recent matches out of {previewResult.total}.
                  </p>
                )}
              </div>
            )}
          </section>
        </div>
      </div>

      <ConfirmDialog
        open={Boolean(pendingReapplyRequest)}
        title={pendingReapplyRequest?.limit === 0 ? 'Queue full retagging pass?' : 'Queue retagging pass?'}
        description="Review the scope before scheduling a bulk retagging job."
        confirmLabel={pendingReapplyRequest?.limit === 0 ? 'Queue full retagging' : 'Queue retagging'}
        confirmTone="primary"
        onCancel={() => setPendingReapplyRequest(null)}
        onConfirm={onConfirmReapplyTagging}
        confirmDisabled={!pendingReapplyRequest || reapplyTagging.isPending}
        isConfirming={reapplyTagging.isPending}
      >
        {pendingReapplyRequest && (
          <div className="space-y-2 text-sm">
            <p>
              Time window:{' '}
              <span className="font-semibold text-ink dark:text-white">
                last {pendingReapplyRequest.days} day{pendingReapplyRequest.days === 1 ? '' : 's'}
              </span>
            </p>
            <p>
              Scope:{' '}
              <span className="font-semibold text-ink dark:text-white">
                {pendingReapplyRequest.limit === 0
                  ? 'all items in the selected time window'
                  : `up to ${pendingReapplyRequest.limit} recent item${pendingReapplyRequest.limit === 1 ? '' : 's'}`}
              </span>
            </p>
            {pendingReapplyRequest.limit === 0 && (
              <p className="text-amber-700 dark:text-amber-300">
                Limit 0 reprocesses every eligible item in the selected time window.
              </p>
            )}
          </div>
        )}
      </ConfirmDialog>

      <ConfirmDialog
        open={Boolean(pendingRuleDelete)}
        title="Delete tagging rule?"
        description="This permanently removes the rule from auto-tagging."
        confirmLabel="Delete rule"
        onCancel={() => setPendingRuleDelete(null)}
        onConfirm={onConfirmDeleteRule}
        confirmDisabled={deleteRule.isPending}
        isConfirming={deleteRule.isPending}
      >
        {pendingRuleDelete && (
          <div className="space-y-3">
            <p className="font-semibold text-ink dark:text-white">{pendingRuleDelete.name}</p>
            <p className="text-xs text-slate dark:text-white/70">Tag: {pendingRuleDelete.tag_name}</p>
            <p className="text-xs text-slate dark:text-white/70">
              Match: {pendingRuleDelete.match_type} on {pendingRuleDelete.applies_to.join(', ')}
            </p>
          </div>
        )}
      </ConfirmDialog>
      {confirmDiscardUnsavedTaggingChanges.discardDialog}
    </div>
  )
}

function RuleSelectionGroup<T extends string>({
  title,
  description,
  entries,
  selectedValues,
  onToggle,
}: {
  title: string
  description: string
  entries: Array<{ value: T; label: string }>
  selectedValues: T[]
  onToggle: (value: T) => void
}) {
  return (
    <div className="mt-5">
      <h4 className="font-semibold">{title}</h4>
      <p className="mt-1 text-xs text-slate dark:text-white/65">{description}</p>
      <div role="group" aria-label={title} className="mt-2 flex flex-wrap gap-2">
        {entries.map((entry) => {
          const active = selectedValues.includes(entry.value)
          return (
            <button
              type="button"
              key={entry.value}
              aria-pressed={active}
              className={`rounded-full border px-3 py-1.5 text-sm ${
                active
                  ? 'tl-chip-filter-active'
                  : 'tl-chip-neutral hover:border-slate/40 dark:hover:border-cyan-700/60'
              }`}
              onClick={() => onToggle(entry.value)}
            >
              {entry.label}
            </button>
          )
        })}
      </div>
    </div>
  )
}

function createDefaultRuleDraft(): TaggingRuleDraft {
  return {
    name: '',
    tag_name: '',
    enabled: true,
    match_type: 'contains',
    pattern: '',
    case_sensitive: false,
    applies_to: ['title', 'summary'],
    required_categories: [],
    feed_scope: 'all',
    feed_ids: [],
    min_classification_confidence: '',
  }
}

function parseTaggingReapplyRequest(
  daysInput: string,
  limitInput: string,
): { request: TaggingReapplyRequest | null; error: string | null } {
  const trimmedDays = daysInput.trim()
  const trimmedLimit = limitInput.trim()
  const days = Number(trimmedDays)
  const limit = Number(trimmedLimit)

  if (!trimmedDays || !Number.isInteger(days) || days < 1 || days > 365) {
    return {
      request: null,
      error: 'Days Back must be a whole number between 1 and 365.',
    }
  }

  if (!trimmedLimit || !Number.isInteger(limit) || limit < 0 || limit > 5000) {
    return {
      request: null,
      error: 'Limit must be a whole number between 0 and 5000.',
    }
  }

  return {
    request: { days, limit },
    error: null,
  }
}

function createDraftFromRule(rule: TaggingRule): TaggingRuleDraft {
  return {
    name: rule.name,
    tag_name: rule.tag_name,
    enabled: rule.enabled,
    match_type: rule.match_type,
    pattern: rule.pattern,
    case_sensitive: rule.case_sensitive,
    applies_to: [...rule.applies_to],
    required_categories: [...rule.required_categories],
    feed_scope: rule.feed_scope,
    feed_ids: [...rule.feed_ids],
    min_classification_confidence: rule.min_classification_confidence != null ? String(rule.min_classification_confidence) : '',
  }
}

function createRuleRequestFromDraft(draft: TaggingRuleDraft): TaggingRuleWriteRequest {
  return {
    name: draft.name.trim(),
    tag_name: draft.tag_name.trim(),
    enabled: draft.enabled,
    match_type: draft.match_type,
    pattern: draft.pattern.trim(),
    case_sensitive: draft.case_sensitive,
    applies_to: [...draft.applies_to],
    required_categories: [...draft.required_categories],
    feed_scope: draft.feed_scope,
    feed_ids: draft.feed_scope === 'selected' ? [...draft.feed_ids] : [],
    min_classification_confidence:
      draft.min_classification_confidence.trim().length > 0 ? Number(draft.min_classification_confidence) : null,
  }
}

function getRuleDraftValidationError(draft: TaggingRuleDraft): string | null {
  if (!draft.name.trim()) {
    return 'Rule name is required.'
  }
  if (!draft.tag_name.trim()) {
    return 'Tag name is required.'
  }
  if (!draft.pattern.trim()) {
    return 'Pattern is required.'
  }
  if (draft.applies_to.length === 0) {
    return 'Choose at least one field to inspect.'
  }
  if (draft.feed_scope === 'selected' && draft.feed_ids.length === 0) {
    return 'Select at least one feed or switch the rule to Any feed.'
  }
  if (draft.min_classification_confidence.trim()) {
    const parsed = Number(draft.min_classification_confidence)
    if (Number.isNaN(parsed) || parsed < 0 || parsed > 1) {
      return 'Minimum classification confidence must be between 0 and 1.'
    }
  }
  return null
}

function formatCategoryLabel(value: string): string {
  return value
    .split('_')
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

function formatFieldLabel(value: string): string {
  if (value === 'article_text') {
    return 'Article Text'
  }
  if (value === 'feed_name') {
    return 'Feed Name'
  }
  return value.charAt(0).toUpperCase() + value.slice(1)
}

function formatTimestamp(value: string): string {
  return formatDateTime(value)
}

function resolveApiMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError && typeof error.message === 'string' && error.message.trim()) {
    return error.message
  }
  return fallback
}
