import { Dispatch, SetStateAction, useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
import { useCurrentUser } from '../hooks/useCurrentUser'
import {
  AIDailyBrief,
  AIReprocessResponse,
  AISettings,
  AISettingsUpdateRequest,
  AITestConnectionResponse,
  AIUsageSummary,
} from '../types/api'

type AISettingsDraft = {
  base_url: string
  model: string
  temperature: string
  max_completion_tokens: string
  request_timeout_seconds: string
  summary_enabled: boolean
  relevance_enabled: boolean
  daily_brief_enabled: boolean
  auto_enrich_new_items: boolean
  daily_brief_window_hours: string
  daily_brief_max_items: string
  daily_brief_history_limit: string
  relevance_medium_threshold: string
  relevance_high_threshold: string
  company_name: string
  company_industry: string
  company_regions: string
  company_stack: string
  company_priority_topics: string
  company_keywords: string
  company_exclusions: string
  company_profile_text: string
  global_instructions: string
  item_summary_instructions: string
  relevance_instructions: string
  daily_brief_instructions: string
}

const DEFAULT_DRAFT: AISettingsDraft = {
  base_url: '',
  model: '',
  temperature: '0.2',
  max_completion_tokens: '700',
  request_timeout_seconds: '60',
  summary_enabled: true,
  relevance_enabled: true,
  daily_brief_enabled: true,
  auto_enrich_new_items: true,
  daily_brief_window_hours: '24',
  daily_brief_max_items: '20',
  daily_brief_history_limit: '7',
  relevance_medium_threshold: '0.55',
  relevance_high_threshold: '0.80',
  company_name: '',
  company_industry: '',
  company_regions: '',
  company_stack: '',
  company_priority_topics: '',
  company_keywords: '',
  company_exclusions: '',
  company_profile_text: '',
  global_instructions: '',
  item_summary_instructions: '',
  relevance_instructions: '',
  daily_brief_instructions: '',
}

export function AiSettingsPage() {
  const queryClient = useQueryClient()
  const currentUserQuery = useCurrentUser()
  const [draft, setDraft] = useState<AISettingsDraft>(DEFAULT_DRAFT)
  const [notice, setNotice] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<AITestConnectionResponse | null>(null)
  const [latestGeneratedBrief, setLatestGeneratedBrief] = useState<AIDailyBrief | null>(null)
  const [reprocessDays, setReprocessDays] = useState('7')
  const [reprocessLimit, setReprocessLimit] = useState('100')

  const aiEnabled = currentUserQuery.data?.features.ai_enabled ?? false

  const settingsQuery = useQuery({
    queryKey: ['ai', 'settings'],
    queryFn: () => apiFetch<AISettings>('/ai/settings'),
    enabled: aiEnabled,
  })

  const usageQuery = useQuery({
    queryKey: ['ai', 'usage'],
    queryFn: () => apiFetch<AIUsageSummary>('/ai/usage'),
    enabled: aiEnabled,
  })

  useEffect(() => {
    if (!settingsQuery.data) {
      return
    }
    setDraft(createDraftFromSettings(settingsQuery.data))
  }, [settingsQuery.data])

  const saveMutation = useMutation({
    mutationFn: (payload: AISettingsUpdateRequest) =>
      apiFetch<AISettings>('/ai/settings', {
        method: 'PUT',
        body: JSON.stringify(payload),
      }),
    onSuccess: (saved) => {
      setDraft(createDraftFromSettings(saved))
      setNotice('AI settings saved.')
      setTestResult(null)
      void queryClient.invalidateQueries({ queryKey: ['ai', 'settings'] })
      void queryClient.invalidateQueries({ queryKey: ['auth', 'me'] })
    },
  })

  const testConnectionMutation = useMutation({
    mutationFn: () =>
      apiFetch<AITestConnectionResponse>('/ai/test-connection', {
        method: 'POST',
      }),
    onSuccess: (result) => {
      setTestResult(result)
      setNotice(result.success ? 'AI connection test succeeded.' : 'AI connection test failed.')
      void queryClient.invalidateQueries({ queryKey: ['ai', 'usage'] })
    },
  })

  const generateBriefMutation = useMutation({
    mutationFn: () =>
      apiFetch<AIDailyBrief>('/ai/daily-brief/generate', {
        method: 'POST',
      }),
    onSuccess: (brief) => {
      setLatestGeneratedBrief(brief)
      setNotice('Daily brief generated.')
      void queryClient.invalidateQueries({ queryKey: ['ai', 'usage'] })
      void queryClient.invalidateQueries({ queryKey: ['ai', 'daily-brief', 'latest'] })
      void queryClient.invalidateQueries({ queryKey: ['ai', 'daily-briefs'] })
      void queryClient.invalidateQueries({ queryKey: ['auth', 'me'] })
    },
  })

  const reprocessMutation = useMutation({
    mutationFn: () =>
      apiFetch<AIReprocessResponse>('/ai/reprocess', {
        method: 'POST',
        body: JSON.stringify({
          days: Number(reprocessDays) || 7,
          limit: Number(reprocessLimit) || 100,
        }),
      }),
    onSuccess: (result) => {
      setNotice(`Queued AI reprocessing task ${result.task_id}.`)
    },
  })

  const readiness = useMemo(() => {
    if (!settingsQuery.data) {
      return null
    }
    if (!settingsQuery.data.ai_configured) {
      return 'Complete the base URL and model to enable AI-generated output.'
    }
    if (!settingsQuery.data.api_key_configured) {
      return 'No API key is configured in the environment. That is fine for local endpoints that do not require auth.'
    }
    return 'AI endpoint settings are configured and ready to use.'
  }, [settingsQuery.data])

  if (currentUserQuery.isLoading) {
    return <div className="rounded-xl border border-slate/20 bg-white/80 p-4 text-sm dark:border-cyan-900/40 dark:bg-[#041612]/90">Loading AI settings...</div>
  }

  if (!aiEnabled) {
    return (
      <div className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
        <h2 className="font-display text-xl">AI</h2>
        <p className="mt-2 text-sm text-slate dark:text-white/75">
          AI features are disabled by the deployment configuration. Enable `AI_ENABLED=true` and restart ThreatLens to use this section.
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="font-display text-xl">AI Integration</h2>
            <p className="mt-1 text-sm text-slate dark:text-white/75">
              Configure a local OpenAI-compatible endpoint, define organization context, and control how AI augments RSS triage and daily briefs.
            </p>
          </div>
          <div className="rounded border border-slate/20 bg-slate/5 px-3 py-2 text-xs text-slate dark:border-cyan-900/40 dark:bg-white/[0.04] dark:text-white/70">
            Runtime gate: <span className="font-semibold">{settingsQuery.data?.ai_enabled ? 'Enabled' : 'Disabled'}</span>
          </div>
        </div>

        {notice && (
          <p className="mt-3 rounded border border-cyan/20 bg-cyan/10 px-3 py-2 text-sm text-cyan-900 dark:border-cyan-900/40 dark:bg-cyan/10 dark:text-cyan-100">
            {notice}
          </p>
        )}
        {settingsQuery.isError && (
          <p className="mt-3 text-sm text-red-600">
            Failed to load AI settings. {(settingsQuery.error as Error | undefined)?.message ?? ''}
          </p>
        )}

        <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(320px,1fr)]">
          <div className="space-y-4">
            <section className="rounded-xl border border-slate/20 bg-slate/5 p-4 dark:border-cyan-900/40 dark:bg-white/[0.03]">
              <h3 className="font-display text-lg">Provider</h3>
              <p className="mt-1 text-sm text-slate dark:text-white/70">
                ThreatLens currently speaks to one OpenAI-compatible chat endpoint. Secrets stay in the environment; this page manages the non-secret runtime shape.
              </p>
              <div className="mt-3 grid gap-3 md:grid-cols-2">
                <label className="text-sm">
                  <span className="font-semibold">Base URL</span>
                  <input
                    className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                    value={draft.base_url}
                    onChange={(event) => updateDraft(setDraft, 'base_url', event.target.value)}
                    placeholder="http://localhost:11434/v1"
                  />
                </label>
                <label className="text-sm">
                  <span className="font-semibold">Model</span>
                  <input
                    className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                    value={draft.model}
                    onChange={(event) => updateDraft(setDraft, 'model', event.target.value)}
                    placeholder="local-threat-model"
                  />
                </label>
                <label className="text-sm">
                  <span className="font-semibold">Temperature</span>
                  <input
                    className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                    value={draft.temperature}
                    onChange={(event) => updateDraft(setDraft, 'temperature', event.target.value)}
                    inputMode="decimal"
                  />
                </label>
                <label className="text-sm">
                  <span className="font-semibold">Max Completion Tokens</span>
                  <input
                    className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                    value={draft.max_completion_tokens}
                    onChange={(event) => updateDraft(setDraft, 'max_completion_tokens', event.target.value)}
                    inputMode="numeric"
                  />
                </label>
                <label className="text-sm md:col-span-2">
                  <span className="font-semibold">Request Timeout Seconds</span>
                  <input
                    className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                    value={draft.request_timeout_seconds}
                    onChange={(event) => updateDraft(setDraft, 'request_timeout_seconds', event.target.value)}
                    inputMode="numeric"
                  />
                </label>
              </div>
            </section>

            <section className="rounded-xl border border-slate/20 bg-slate/5 p-4 dark:border-cyan-900/40 dark:bg-white/[0.03]">
              <h3 className="font-display text-lg">Feature Controls</h3>
              <div className="mt-3 grid gap-3 md:grid-cols-2">
                <CheckboxRow label="AI article summaries" checked={draft.summary_enabled} onChange={(checked) => updateDraft(setDraft, 'summary_enabled', checked)} />
                <CheckboxRow label="AI relevance scoring" checked={draft.relevance_enabled} onChange={(checked) => updateDraft(setDraft, 'relevance_enabled', checked)} />
                <CheckboxRow label="Daily brief widget" checked={draft.daily_brief_enabled} onChange={(checked) => updateDraft(setDraft, 'daily_brief_enabled', checked)} />
                <CheckboxRow label="Auto-enrich new items" checked={draft.auto_enrich_new_items} onChange={(checked) => updateDraft(setDraft, 'auto_enrich_new_items', checked)} />
                <label className="text-sm">
                  <span className="font-semibold">Medium Relevance Threshold</span>
                  <input
                    className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                    value={draft.relevance_medium_threshold}
                    onChange={(event) => updateDraft(setDraft, 'relevance_medium_threshold', event.target.value)}
                    inputMode="decimal"
                  />
                </label>
                <label className="text-sm">
                  <span className="font-semibold">High Relevance Threshold</span>
                  <input
                    className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                    value={draft.relevance_high_threshold}
                    onChange={(event) => updateDraft(setDraft, 'relevance_high_threshold', event.target.value)}
                    inputMode="decimal"
                  />
                </label>
                <label className="text-sm">
                  <span className="font-semibold">Daily Brief Window Hours</span>
                  <input
                    className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                    value={draft.daily_brief_window_hours}
                    onChange={(event) => updateDraft(setDraft, 'daily_brief_window_hours', event.target.value)}
                    inputMode="numeric"
                  />
                </label>
                <label className="text-sm">
                  <span className="font-semibold">Daily Brief Max Items</span>
                  <input
                    className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                    value={draft.daily_brief_max_items}
                    onChange={(event) => updateDraft(setDraft, 'daily_brief_max_items', event.target.value)}
                    inputMode="numeric"
                  />
                </label>
                <label className="text-sm">
                  <span className="font-semibold">Retained Daily Briefings</span>
                  <input
                    className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                    value={draft.daily_brief_history_limit}
                    onChange={(event) => updateDraft(setDraft, 'daily_brief_history_limit', event.target.value)}
                    inputMode="numeric"
                  />
                  <span className="mt-1 block text-xs text-slate dark:text-white/60">
                    Keep only the most recent X daily briefings for dashboard selection. Older briefings are discarded automatically.
                  </span>
                </label>
              </div>
            </section>

            <section className="rounded-xl border border-slate/20 bg-slate/5 p-4 dark:border-cyan-900/40 dark:bg-white/[0.03]">
              <h3 className="font-display text-lg">Company Context</h3>
              <p className="mt-1 text-sm text-slate dark:text-white/70">
                This context is global for the whole deployment so relevance scoring stays consistent across users.
              </p>
              <div className="mt-3 grid gap-3 md:grid-cols-2">
                <label className="text-sm">
                  <span className="font-semibold">Company Name</span>
                  <input
                    className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                    value={draft.company_name}
                    onChange={(event) => updateDraft(setDraft, 'company_name', event.target.value)}
                  />
                </label>
                <label className="text-sm">
                  <span className="font-semibold">Industry</span>
                  <input
                    className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                    value={draft.company_industry}
                    onChange={(event) => updateDraft(setDraft, 'company_industry', event.target.value)}
                  />
                </label>
                <TextAreaList
                  label="Regions"
                  value={draft.company_regions}
                  placeholder="US&#10;EU"
                  onChange={(value) => updateDraft(setDraft, 'company_regions', value)}
                />
                <TextAreaList
                  label="Technology Stack"
                  value={draft.company_stack}
                  placeholder="Fortinet&#10;Microsoft 365&#10;Okta"
                  onChange={(value) => updateDraft(setDraft, 'company_stack', value)}
                />
                <TextAreaList
                  label="Priority Topics"
                  value={draft.company_priority_topics}
                  placeholder="edge security&#10;identity"
                  onChange={(value) => updateDraft(setDraft, 'company_priority_topics', value)}
                />
                <TextAreaList
                  label="Keywords"
                  value={draft.company_keywords}
                  placeholder="vpn&#10;sso&#10;exchange"
                  onChange={(value) => updateDraft(setDraft, 'company_keywords', value)}
                />
                <TextAreaList
                  label="Exclusions"
                  value={draft.company_exclusions}
                  placeholder="consumer scams&#10;gaming malware"
                  onChange={(value) => updateDraft(setDraft, 'company_exclusions', value)}
                />
                <label className="text-sm md:col-span-2">
                  <span className="font-semibold">Additional Company Context</span>
                  <textarea
                    className="mt-1 h-28 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                    value={draft.company_profile_text}
                    onChange={(event) => updateDraft(setDraft, 'company_profile_text', event.target.value)}
                    placeholder="Describe the defended environment, the kinds of systems you care about, and what should be treated as especially relevant."
                  />
                </label>
              </div>
            </section>

            <section className="rounded-xl border border-slate/20 bg-slate/5 p-4 dark:border-cyan-900/40 dark:bg-white/[0.03]">
              <h3 className="font-display text-lg">Prompt Tuning</h3>
              <div className="mt-3 grid gap-3">
                <PromptArea
                  label="Global Instructions"
                  value={draft.global_instructions}
                  onChange={(value) => updateDraft(setDraft, 'global_instructions', value)}
                  placeholder="Instructions applied to every AI request."
                />
                <PromptArea
                  label="Item Summary Instructions"
                  value={draft.item_summary_instructions}
                  onChange={(value) => updateDraft(setDraft, 'item_summary_instructions', value)}
                  placeholder="Guide the style and focus of article summaries."
                />
                <PromptArea
                  label="Relevance Instructions"
                  value={draft.relevance_instructions}
                  onChange={(value) => updateDraft(setDraft, 'relevance_instructions', value)}
                  placeholder="Explain how relevance should be interpreted for this environment."
                />
                <PromptArea
                  label="Daily Brief Instructions"
                  value={draft.daily_brief_instructions}
                  onChange={(value) => updateDraft(setDraft, 'daily_brief_instructions', value)}
                  placeholder="Guide the tone and structure of the daily brief."
                />
              </div>
            </section>
          </div>

          <aside className="space-y-4">
            <section className="rounded-xl border border-slate/20 bg-slate/5 p-4 dark:border-cyan-900/40 dark:bg-white/[0.03]">
              <h3 className="font-display text-lg">Readiness</h3>
              <p className="mt-2 text-sm text-slate dark:text-white/75">{readiness ?? 'Loading runtime state...'}</p>
              {settingsQuery.data && (
                <dl className="mt-3 space-y-2 text-sm">
                  <div className="flex items-center justify-between gap-3">
                    <dt className="text-slate dark:text-white/65">Configured</dt>
                    <dd className="font-semibold">{settingsQuery.data.ai_configured ? 'Yes' : 'No'}</dd>
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <dt className="text-slate dark:text-white/65">API Key In Env</dt>
                    <dd className="font-semibold">{settingsQuery.data.api_key_configured ? 'Yes' : 'No / Optional'}</dd>
                  </div>
                </dl>
              )}
              <div className="mt-4 flex flex-wrap gap-2">
                <button
                  type="button"
                  className="rounded bg-ink px-3 py-2 text-sm font-semibold text-white disabled:opacity-50 dark:bg-cyan dark:text-slate-950"
                  onClick={() => {
                    setNotice(null)
                    saveMutation.mutate(createRequestFromDraft(draft))
                  }}
                  disabled={saveMutation.isPending}
                >
                  {saveMutation.isPending ? 'Saving...' : 'Save Settings'}
                </button>
                <button
                  type="button"
                  className="rounded border border-slate/30 px-3 py-2 text-sm font-semibold disabled:opacity-50 dark:border-cyan-900/40"
                  onClick={() => {
                    setNotice(null)
                    testConnectionMutation.mutate()
                  }}
                  disabled={testConnectionMutation.isPending || !settingsQuery.data?.ai_configured}
                >
                  {testConnectionMutation.isPending ? 'Testing...' : 'Test Connection'}
                </button>
              </div>
              {testResult && (
                <div className="mt-3 rounded border border-slate/20 bg-white/70 p-3 text-sm dark:border-cyan-900/40 dark:bg-[#072019]/80">
                  <p className="font-semibold">{testResult.success ? 'Connection succeeded' : 'Connection failed'}</p>
                  <p className="mt-1 text-slate dark:text-white/70">
                    Model: {testResult.model || 'unknown'}
                    {typeof testResult.latency_ms === 'number' ? `, ${testResult.latency_ms} ms` : ''}
                  </p>
                  {testResult.error && <p className="mt-1 text-red-600">{testResult.error}</p>}
                </div>
              )}
            </section>

            <section className="rounded-xl border border-slate/20 bg-slate/5 p-4 dark:border-cyan-900/40 dark:bg-white/[0.03]">
              <h3 className="font-display text-lg">Operations</h3>
              <p className="mt-2 text-sm text-slate dark:text-white/75">
                Generate a fresh daily brief now or reprocess recent items after changing company context or prompts.
              </p>
              <div className="mt-3 flex flex-wrap gap-2">
                <button
                  type="button"
                  className="rounded border border-slate/30 px-3 py-2 text-sm font-semibold disabled:opacity-50 dark:border-cyan-900/40"
                  onClick={() => {
                    setNotice(null)
                    generateBriefMutation.mutate()
                  }}
                  disabled={generateBriefMutation.isPending || !draft.daily_brief_enabled}
                >
                  {generateBriefMutation.isPending ? 'Generating...' : 'Generate Daily Brief'}
                </button>
              </div>
              <div className="mt-4 grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
                <input
                  className="rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                  value={reprocessDays}
                  onChange={(event) => setReprocessDays(event.target.value)}
                  placeholder="Days"
                  inputMode="numeric"
                />
                <input
                  className="rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                  value={reprocessLimit}
                  onChange={(event) => setReprocessLimit(event.target.value)}
                  placeholder="Limit"
                  inputMode="numeric"
                />
                <button
                  type="button"
                  className="rounded border border-slate/30 px-3 py-2 text-sm font-semibold disabled:opacity-50 dark:border-cyan-900/40"
                  onClick={() => {
                    setNotice(null)
                    reprocessMutation.mutate()
                  }}
                  disabled={reprocessMutation.isPending}
                >
                  {reprocessMutation.isPending ? 'Queueing...' : 'Reprocess'}
                </button>
              </div>

              {latestGeneratedBrief && (
                <div className="mt-4 rounded border border-slate/20 bg-white/70 p-3 dark:border-cyan-900/40 dark:bg-[#072019]/80">
                  <p className="text-sm font-semibold">{latestGeneratedBrief.title || 'Latest Daily Brief'}</p>
                  <p className="mt-1 text-xs text-slate dark:text-white/65">
                    Generated {formatTimestamp(latestGeneratedBrief.generated_at)} for {latestGeneratedBrief.item_count} items.
                  </p>
                  {latestGeneratedBrief.brief_text && (
                    <p className="mt-2 text-sm text-slate dark:text-white/75">{latestGeneratedBrief.brief_text}</p>
                  )}
                </div>
              )}
            </section>

            <section className="rounded-xl border border-slate/20 bg-slate/5 p-4 dark:border-cyan-900/40 dark:bg-white/[0.03]">
              <h3 className="font-display text-lg">Usage</h3>
              {usageQuery.isLoading && <p className="mt-2 text-sm text-slate dark:text-white/75">Loading AI usage...</p>}
              {usageQuery.isError && (
                <p className="mt-2 text-sm text-red-600">
                  Failed to load AI usage. {(usageQuery.error as Error | undefined)?.message ?? ''}
                </p>
              )}
              {usageQuery.data && (
                <>
                  <div className="mt-3 grid gap-2 sm:grid-cols-2">
                    <UsageCard label="Requests" value={String(usageQuery.data.total_requests)} />
                    <UsageCard label="Success Rate" value={`${usageQuery.data.success_rate_pct.toFixed(1)}%`} />
                    <UsageCard label="Tokens" value={usageQuery.data.total_tokens.toLocaleString()} />
                    <UsageCard label="Last 24h" value={String(usageQuery.data.requests_last_24h)} />
                  </div>
                  <p className="mt-3 text-xs text-slate dark:text-white/60">
                    Average latency: {usageQuery.data.average_latency_ms.toFixed(1)} ms
                    {usageQuery.data.last_request_at ? `, last request ${formatTimestamp(usageQuery.data.last_request_at)}` : ''}
                  </p>
                  <div className="mt-3 space-y-2">
                    {usageQuery.data.features.map((feature) => (
                      <div
                        key={feature.feature_type}
                        className="rounded border border-slate/20 bg-white/70 px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]/80"
                      >
                        <div className="flex items-center justify-between gap-3">
                          <span className="font-semibold">{formatFeatureLabel(feature.feature_type)}</span>
                          <span className="text-xs text-slate dark:text-white/60">
                            {feature.successful_requests}/{feature.total_requests} succeeded
                          </span>
                        </div>
                        <p className="mt-1 text-xs text-slate dark:text-white/65">
                          {feature.total_tokens.toLocaleString()} tokens, {feature.average_latency_ms.toFixed(1)} ms average latency
                        </p>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </section>
          </aside>
        </div>
      </section>
    </div>
  )
}

function UsageCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-slate/20 bg-white/70 px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]/80">
      <p className="text-xs uppercase tracking-wide text-slate dark:text-white/55">{label}</p>
      <p className="mt-1 text-lg font-semibold">{value}</p>
    </div>
  )
}

function CheckboxRow({
  label,
  checked,
  onChange,
}: {
  label: string
  checked: boolean
  onChange: (checked: boolean) => void
}) {
  return (
    <label className="flex items-center justify-between gap-3 rounded border border-slate/20 bg-white/60 px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]/80">
      <span>{label}</span>
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
    </label>
  )
}

function TextAreaList({
  label,
  value,
  placeholder,
  onChange,
}: {
  label: string
  value: string
  placeholder: string
  onChange: (value: string) => void
}) {
  return (
    <label className="text-sm">
      <span className="font-semibold">{label}</span>
      <textarea
        className="mt-1 h-24 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
      />
    </label>
  )
}

function PromptArea({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  placeholder: string
}) {
  return (
    <label className="text-sm">
      <span className="font-semibold">{label}</span>
      <textarea
        className="mt-1 h-24 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
      />
    </label>
  )
}

function createDraftFromSettings(settings: AISettings): AISettingsDraft {
  return {
    base_url: settings.base_url ?? '',
    model: settings.model ?? '',
    temperature: String(settings.temperature),
    max_completion_tokens: String(settings.max_completion_tokens),
    request_timeout_seconds: String(settings.request_timeout_seconds),
    summary_enabled: settings.summary_enabled,
    relevance_enabled: settings.relevance_enabled,
    daily_brief_enabled: settings.daily_brief_enabled,
    auto_enrich_new_items: settings.auto_enrich_new_items,
    daily_brief_window_hours: String(settings.daily_brief_window_hours),
    daily_brief_max_items: String(settings.daily_brief_max_items),
    daily_brief_history_limit: String(settings.daily_brief_history_limit),
    relevance_medium_threshold: String(settings.relevance_medium_threshold),
    relevance_high_threshold: String(settings.relevance_high_threshold),
    company_name: settings.company_name ?? '',
    company_industry: settings.company_industry ?? '',
    company_regions: settings.company_regions.join('\n'),
    company_stack: settings.company_stack.join('\n'),
    company_priority_topics: settings.company_priority_topics.join('\n'),
    company_keywords: settings.company_keywords.join('\n'),
    company_exclusions: settings.company_exclusions.join('\n'),
    company_profile_text: settings.company_profile_text ?? '',
    global_instructions: settings.global_instructions ?? '',
    item_summary_instructions: settings.item_summary_instructions ?? '',
    relevance_instructions: settings.relevance_instructions ?? '',
    daily_brief_instructions: settings.daily_brief_instructions ?? '',
  }
}

function createRequestFromDraft(draft: AISettingsDraft): AISettingsUpdateRequest {
  return {
    provider_type: 'openai_compatible',
    base_url: normalizeOptionalText(draft.base_url),
    model: normalizeOptionalText(draft.model),
    temperature: Number(draft.temperature) || 0.2,
    max_completion_tokens: Number(draft.max_completion_tokens) || 700,
    request_timeout_seconds: Number(draft.request_timeout_seconds) || 60,
    summary_enabled: draft.summary_enabled,
    relevance_enabled: draft.relevance_enabled,
    daily_brief_enabled: draft.daily_brief_enabled,
    auto_enrich_new_items: draft.auto_enrich_new_items,
    daily_brief_window_hours: Number(draft.daily_brief_window_hours) || 24,
    daily_brief_max_items: Number(draft.daily_brief_max_items) || 20,
    daily_brief_history_limit: Number(draft.daily_brief_history_limit) || 7,
    relevance_medium_threshold: Number(draft.relevance_medium_threshold) || 0.55,
    relevance_high_threshold: Number(draft.relevance_high_threshold) || 0.8,
    company_name: normalizeOptionalText(draft.company_name),
    company_industry: normalizeOptionalText(draft.company_industry),
    company_regions: parseListText(draft.company_regions),
    company_stack: parseListText(draft.company_stack),
    company_priority_topics: parseListText(draft.company_priority_topics),
    company_keywords: parseListText(draft.company_keywords),
    company_exclusions: parseListText(draft.company_exclusions),
    company_profile_text: normalizeOptionalText(draft.company_profile_text),
    global_instructions: normalizeOptionalText(draft.global_instructions),
    item_summary_instructions: normalizeOptionalText(draft.item_summary_instructions),
    relevance_instructions: normalizeOptionalText(draft.relevance_instructions),
    daily_brief_instructions: normalizeOptionalText(draft.daily_brief_instructions),
  }
}

function parseListText(value: string): string[] {
  return value
    .split(/[\n,]/)
    .map((entry) => entry.trim())
    .filter((entry, index, array) => entry.length > 0 && array.indexOf(entry) === index)
}

function normalizeOptionalText(value: string): string | null {
  const normalized = value.trim()
  return normalized ? normalized : null
}

function updateDraft<K extends keyof AISettingsDraft>(
  setter: Dispatch<SetStateAction<AISettingsDraft>>,
  key: K,
  value: AISettingsDraft[K],
) {
  setter((current) => ({ ...current, [key]: value }))
}

function formatTimestamp(value: string | null) {
  if (!value) {
    return 'unknown'
  }
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) {
    return value
  }
  return parsed.toLocaleString()
}

function formatFeatureLabel(value: AIUsageSummary['features'][number]['feature_type']) {
  if (value === 'item_enrichment') return 'Item Enrichment'
  if (value === 'daily_brief') return 'Daily Brief'
  return 'Connection Tests'
}
