import { Dispatch, SetStateAction, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
import { useCurrentUser } from '../hooks/useCurrentUser'
import {
  AIAuditEntryResponse,
  AIDailyBrief,
  AIDailyBriefSourceItemResponse,
  AILiveStatusResponse,
  AIOpsOverviewResponse,
  AIReprocessResponse,
  AISettings,
  AISettingsUpdateRequest,
  AITestConnectionResponse,
  AITaskRunDetailResponse,
  AITaskRunListResponse,
  AITaskRunResponse,
} from '../types/api'

type AIWorkspaceTab = 'overview' | 'runs' | 'configuration'

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
  item_enrichment_system_prompt: string
  daily_brief_system_prompt: string
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
  item_enrichment_system_prompt: '',
  daily_brief_system_prompt: '',
  global_instructions: '',
  item_summary_instructions: '',
  relevance_instructions: '',
  daily_brief_instructions: '',
}

const WORKSPACE_TABS: Array<{ key: AIWorkspaceTab; label: string; description: string }> = [
  { key: 'overview', label: 'Overview', description: 'AI health, usage, and operational summaries' },
  { key: 'runs', label: 'Runs & Logs', description: 'Task history, run details, and audit trails' },
  { key: 'configuration', label: 'Configuration', description: 'Provider, prompts, and org context' },
]

const RUN_STATUS_STYLES: Record<string, string> = {
  queued: 'border-amber-500/30 bg-amber-500/10 text-amber-900 dark:text-amber-100',
  running: 'border-cyan-500/30 bg-cyan-500/10 text-cyan-900 dark:text-cyan-100',
  ready: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-900 dark:text-emerald-100',
  error: 'border-red-500/30 bg-red-500/10 text-red-900 dark:text-red-100',
  skipped: 'border-slate/20 bg-slate/10 text-slate-800 dark:border-cyan-900/40 dark:bg-white/10 dark:text-white/80',
}

const LIVE_STATE_STYLES: Record<string, string> = {
  active: 'border-cyan-500/30 bg-cyan-500/10 text-cyan-900 dark:text-cyan-100',
  reserved: 'border-amber-500/30 bg-amber-500/10 text-amber-900 dark:text-amber-100',
  scheduled: 'border-slate/20 bg-slate/10 text-slate-800 dark:border-cyan-900/40 dark:bg-white/10 dark:text-white/80',
}

const RUN_TASK_TYPE_LABELS: Record<AITaskRunResponse['task_type'], string> = {
  item_enrichment: 'Item Enrichment',
  daily_brief: 'Daily Brief',
  connection_test: 'Connection Test',
  reprocess: 'Reprocess',
}

const RUN_TRIGGER_LABELS: Record<AITaskRunResponse['trigger_source'], string> = {
  auto: 'Auto',
  manual: 'Manual',
  scheduled: 'Scheduled',
}

const FEATURE_LABELS: Record<string, string> = {
  item_enrichment: 'Item enrichment',
  daily_brief: 'Daily brief',
  relevance: 'Relevance scoring',
  summary: 'Summaries',
  auto_enrich: 'Auto-enrich',
}

export function AiSettingsPage() {
  const queryClient = useQueryClient()
  const currentUserQuery = useCurrentUser()
  const [draft, setDraft] = useState<AISettingsDraft>(DEFAULT_DRAFT)
  const [activeTab, setActiveTab] = useState<AIWorkspaceTab>('overview')
  const [notice, setNotice] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<AITestConnectionResponse | null>(null)
  const [latestGeneratedBrief, setLatestGeneratedBrief] = useState<AIDailyBrief | null>(null)
  const [overviewDays, setOverviewDays] = useState(30)
  const [runDays, setRunDays] = useState(30)
  const [runLimit, setRunLimit] = useState(25)
  const [runPage, setRunPage] = useState(1)
  const [runTaskType, setRunTaskType] = useState('')
  const [runStatus, setRunStatus] = useState('')
  const [runTriggerSource, setRunTriggerSource] = useState('')
  const [runModel, setRunModel] = useState('')
  const [runOnlyFailures, setRunOnlyFailures] = useState(false)
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [reprocessDays, setReprocessDays] = useState('7')
  const [reprocessLimit, setReprocessLimit] = useState('100')

  const aiEnabled = currentUserQuery.data?.features.ai_enabled ?? false

  const settingsQuery = useQuery({
    queryKey: ['ai', 'settings'],
    queryFn: () => apiFetch<AISettings>('/ai/settings'),
    enabled: aiEnabled,
  })

  const overviewQuery = useQuery({
    queryKey: ['ai', 'ops', 'overview', overviewDays],
    queryFn: () => apiFetch<AIOpsOverviewResponse>(`/ai/ops/overview?days=${overviewDays}`),
    enabled: aiEnabled,
  })

  const liveQuery = useQuery({
    queryKey: ['ai', 'ops', 'live'],
    queryFn: () => apiFetch<AILiveStatusResponse>('/ai/ops/live'),
    enabled: aiEnabled,
  })

  const runsQuery = useQuery({
    queryKey: ['ai', 'ops', 'runs', runDays, runLimit, runPage, runTaskType, runStatus, runTriggerSource, runModel, runOnlyFailures],
    queryFn: () => {
      const params = new URLSearchParams()
      params.set('days', String(runDays))
      params.set('limit', String(runLimit))
      params.set('offset', String((runPage - 1) * runLimit))
      if (runTaskType) {
        params.set('task_type', runTaskType)
      }
      if (runStatus) {
        params.set('status', runStatus)
      }
      if (runTriggerSource) {
        params.set('trigger_source', runTriggerSource)
      }
      if (runModel.trim()) {
        params.set('model', runModel.trim())
      }
      if (runOnlyFailures) {
        params.set('only_failures', 'true')
      }
      return apiFetch<AITaskRunListResponse>(`/ai/ops/runs?${params.toString()}`)
    },
    enabled: aiEnabled,
  })

  const selectedRunDetailQuery = useQuery({
    queryKey: ['ai', 'ops', 'runs', selectedRunId],
    queryFn: () => apiFetch<AITaskRunDetailResponse>(`/ai/ops/runs/${selectedRunId}`),
    enabled: aiEnabled && Boolean(selectedRunId),
  })

  const manualActionsQuery = useQuery({
    queryKey: ['ai', 'ops', 'manual-actions'],
    queryFn: () => apiFetch<AIAuditEntryResponse[]>('/ai/ops/manual-actions?limit=12'),
    enabled: aiEnabled,
  })

  const promptHistoryQuery = useQuery({
    queryKey: ['ai', 'ops', 'prompt-history'],
    queryFn: () => apiFetch<AIAuditEntryResponse[]>('/ai/ops/prompt-history?limit=12'),
    enabled: aiEnabled,
  })

  const selectedBriefId = selectedRunDetailQuery.data?.run.daily_brief_id ?? null

  const briefSourcesQuery = useQuery({
    queryKey: ['ai', 'daily-briefs', selectedBriefId, 'sources'],
    queryFn: () =>
      apiFetch<AIDailyBriefSourceItemResponse[]>(`/ai/daily-briefs/${selectedBriefId}/sources?limit=50`),
    enabled: aiEnabled && Boolean(selectedBriefId),
  })

  useEffect(() => {
    if (!settingsQuery.data) {
      return
    }
    setDraft(createDraftFromSettings(settingsQuery.data))
  }, [settingsQuery.data])

  useEffect(() => {
    if (selectedRunId) {
      return
    }
    const firstRunId = runsQuery.data?.items[0]?.id ?? null
    if (firstRunId) {
      setSelectedRunId(firstRunId)
    }
  }, [runsQuery.data, selectedRunId])

  const refreshAll = () => {
    void queryClient.invalidateQueries({ queryKey: ['ai'] })
  }

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
      refreshAll()
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
      refreshAll()
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
      setActiveTab('overview')
      refreshAll()
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
      if (result.run_id) {
        setSelectedRunId(result.run_id)
      }
      setActiveTab('runs')
      refreshAll()
    },
  })

  const overview = overviewQuery.data
  const liveStatus = overview?.live ?? liveQuery.data ?? null
  const settings = settingsQuery.data
  const runRows = runsQuery.data?.items ?? []
  const runTotal = runsQuery.data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(runTotal / runLimit))
  const selectedRunDetail = selectedRunDetailQuery.data?.run ?? null
  const selectedRunEvents = selectedRunDetailQuery.data?.events ?? []
  const briefSources = briefSourcesQuery.data ?? []
  const promptPreviews = settings?.prompt_previews

  const readinessMessage = useMemo(() => {
    if (!settings) {
      return 'Loading AI runtime state...'
    }
    if (!settings.ai_configured) {
      return 'Complete the base URL and model fields before AI tasks can run.'
    }
    if (!settings.api_key_configured) {
      return 'No API key is configured in the environment. That is fine for local endpoints that do not require auth.'
    }
    return 'AI endpoint settings are configured and ready to use.'
  }, [settings])

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

  const activeCount = (liveStatus?.active_count ?? 0) + (liveStatus?.reserved_count ?? 0) + (liveStatus?.scheduled_count ?? 0) + (liveStatus?.queued_count ?? 0)
  const activeBanner = activeCount > 0

  return (
    <div className="space-y-4">
      <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="font-display text-2xl">AI Workspace</h2>
              <Badge tone={settings?.ai_enabled ? 'success' : 'muted'}>{settings?.ai_enabled ? 'Enabled' : 'Disabled'}</Badge>
              <Badge tone={settings?.ai_configured ? 'success' : 'warning'}>{settings?.ai_configured ? 'Configured' : 'Not configured'}</Badge>
              <Badge tone="neutral">{settings?.model || 'No model selected'}</Badge>
            </div>
            <p className="text-sm text-slate dark:text-white/75">
              Manage the AI provider, review operational telemetry, and inspect the work ThreatLens is doing behind the scenes.
            </p>
            <div className="flex flex-wrap gap-2 text-xs text-slate dark:text-white/60">
              <span className="rounded border border-slate/20 bg-slate/5 px-2 py-1 dark:border-cyan-900/40 dark:bg-white/[0.04]">
                Base URL: {settings?.base_url || 'not set'}
              </span>
              <span className="rounded border border-slate/20 bg-slate/5 px-2 py-1 dark:border-cyan-900/40 dark:bg-white/[0.04]">
                API key: {settings?.api_key_configured ? 'configured' : 'not set / optional'}
              </span>
              <span className="rounded border border-slate/20 bg-slate/5 px-2 py-1 dark:border-cyan-900/40 dark:bg-white/[0.04]">
                Readiness: {readinessMessage}
              </span>
            </div>
          </div>

          <div className="flex min-w-[320px] flex-col gap-2">
            <div className="flex flex-wrap justify-end gap-2">
              <button
                type="button"
                className="rounded border border-slate/30 px-3 py-2 text-sm font-semibold disabled:opacity-50 dark:border-cyan-900/40"
                onClick={refreshAll}
              >
                Refresh
              </button>
              <button
                type="button"
                className="rounded border border-slate/30 px-3 py-2 text-sm font-semibold disabled:opacity-50 dark:border-cyan-900/40"
                onClick={() => {
                  setNotice(null)
                  testConnectionMutation.mutate()
                }}
                disabled={testConnectionMutation.isPending || !settings?.ai_configured}
              >
                {testConnectionMutation.isPending ? 'Testing...' : 'Test Connection'}
              </button>
              <button
                type="button"
                className="rounded border border-slate/30 px-3 py-2 text-sm font-semibold disabled:opacity-50 dark:border-cyan-900/40"
                onClick={() => {
                  setNotice(null)
                  generateBriefMutation.mutate()
                }}
                disabled={generateBriefMutation.isPending || !settings?.daily_brief_enabled || !settings?.ai_configured}
              >
                {generateBriefMutation.isPending ? 'Generating...' : 'Generate Daily Brief'}
              </button>
            </div>
            <div className="grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
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
                disabled={reprocessMutation.isPending || !settings?.ai_configured}
              >
                {reprocessMutation.isPending ? 'Queueing...' : 'Reprocess'}
              </button>
            </div>
          </div>
        </div>

        {notice && (
          <p className="mt-3 rounded border border-cyan/20 bg-cyan/10 px-3 py-2 text-sm text-cyan-900 dark:border-cyan-900/40 dark:bg-cyan/10 dark:text-cyan-100">
            {notice}
          </p>
        )}
        {(settingsQuery.isError || overviewQuery.isError || liveQuery.isError) && (
          <p className="mt-3 text-sm text-red-600">
            {errorMessage(settingsQuery.error, 'Failed to load AI settings.')}
            {overviewQuery.isError ? ` ${errorMessage(overviewQuery.error, '')}` : ''}
            {liveQuery.isError ? ` ${errorMessage(liveQuery.error, '')}` : ''}
          </p>
        )}

        {activeBanner && liveStatus && (
          <div className="mt-4 rounded border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-950 dark:text-amber-100">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <p className="font-semibold">AI work is currently active.</p>
              <p className="text-xs">
                {liveStatus.active_count} active, {liveStatus.reserved_count} reserved, {liveStatus.scheduled_count} scheduled, {liveStatus.queued_count} queued
                {liveStatus.oldest_queued_age_seconds != null ? `, oldest queued ${formatDurationSeconds(liveStatus.oldest_queued_age_seconds)}` : ''}
              </p>
            </div>
          </div>
        )}
      </section>

      <section className="rounded-xl border border-slate/20 bg-white/80 p-2 dark:border-cyan-900/40 dark:bg-[#041612]/90">
        <div className="flex flex-wrap gap-2">
          {WORKSPACE_TABS.map((tab) => (
            <button
              key={tab.key}
              type="button"
              onClick={() => setActiveTab(tab.key)}
              className={`rounded-lg px-4 py-3 text-left text-sm transition ${
                activeTab === tab.key
                  ? 'bg-cyan/15 text-cyan-900 dark:bg-cyan/20 dark:text-cyan-100'
                  : 'text-slate hover:bg-slate/5 dark:text-white/75 dark:hover:bg-white/[0.04]'
              }`}
            >
              <div className="font-semibold">{tab.label}</div>
              <div className="text-xs opacity-80">{tab.description}</div>
            </button>
          ))}
        </div>
      </section>

      {activeTab === 'overview' && (
        <div className="space-y-4">
          <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h3 className="font-display text-xl">Overview</h3>
                <p className="mt-1 text-sm text-slate dark:text-white/70">
                  AI health, model usage, coverage, and operational snapshots for the last {overviewDays} days.
                </p>
              </div>
              <select
                className="rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                value={overviewDays}
                onChange={(event) => setOverviewDays(Number(event.target.value))}
              >
                <option value={7}>Last 7 days</option>
                <option value={30}>Last 30 days</option>
                <option value={90}>Last 90 days</option>
                <option value={180}>Last 180 days</option>
              </select>
            </div>

            {overviewQuery.isLoading && <p className="mt-3 text-sm text-slate dark:text-white/70">Loading AI overview...</p>}

            {overview && (
              <div className="mt-4 space-y-4">
                <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-6">
                  <MetricCard label="Requests" value={overview.kpis.total_requests.toLocaleString()} />
                  <MetricCard label="Success Rate" value={`${overview.kpis.success_rate_pct.toFixed(1)}%`} />
                  <MetricCard label="Total Tokens" value={overview.kpis.total_tokens.toLocaleString()} />
                  <MetricCard label="Avg Latency" value={formatDurationMs(overview.kpis.average_latency_ms)} />
                  <MetricCard label="P95 Latency" value={formatDurationMs(overview.kpis.p95_latency_ms)} />
                  <MetricCard label="Queued / Active" value={`${overview.kpis.queued_runs}/${overview.kpis.active_runs}`} />
                </div>

                <div className="grid gap-4 xl:grid-cols-2">
                  <SectionCard title="Currently Running" description="Active Celery work, backlog, and queue age.">
                    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                      <MetricCard label="Workers" value={String(liveStatus?.worker_count ?? 0)} />
                      <MetricCard label="Active" value={String(liveStatus?.active_count ?? 0)} />
                      <MetricCard label="Reserved" value={String(liveStatus?.reserved_count ?? 0)} />
                      <MetricCard label="Queued" value={String(liveStatus?.queued_count ?? 0)} />
                    </div>
                    <div className="mt-3 rounded border border-slate/20 bg-white/60 p-3 dark:border-cyan-900/40 dark:bg-[#072019]/70">
                      <p className="text-xs uppercase tracking-wide text-slate dark:text-white/60">Oldest queued age</p>
                      <p className="mt-1 text-sm font-semibold">
                        {liveStatus?.oldest_queued_age_seconds != null ? formatDurationSeconds(liveStatus.oldest_queued_age_seconds) : 'Idle'}
                      </p>
                    </div>
                    <div className="mt-3 space-y-3">
                      <TaskStateBlock title="Active tasks" items={liveStatus?.active_tasks ?? []} />
                      <TaskStateBlock title="Reserved tasks" items={liveStatus?.reserved_tasks ?? []} />
                      <TaskStateBlock title="Scheduled tasks" items={liveStatus?.scheduled_tasks ?? []} />
                    </div>
                  </SectionCard>

                  <SectionCard title="Endpoint Health" description="How the AI provider itself has been behaving.">
                    {overview.endpoint_health ? (
                      <div className="space-y-3">
                        <div className="grid gap-3 sm:grid-cols-2">
                          <MetricCard label="Last Success" value={formatTimestamp(overview.endpoint_health.last_success_at)} />
                          <MetricCard label="Last Error" value={formatTimestamp(overview.endpoint_health.last_error_at)} />
                          <MetricCard label="Median Latency" value={formatDurationMs(overview.endpoint_health.median_latency_ms)} />
                          <MetricCard label="Failure Rate" value={`${overview.endpoint_health.rolling_failure_rate_pct.toFixed(1)}%`} />
                        </div>
                        <div className="grid gap-3 sm:grid-cols-2">
                          <MetricCard label="Timeouts" value={String(overview.endpoint_health.timeout_failures)} />
                          <MetricCard label="Auth Error" value={overview.endpoint_health.last_auth_error || 'None'} />
                        </div>
                        <p className="rounded border border-slate/20 bg-white/60 p-3 text-sm dark:border-cyan-900/40 dark:bg-[#072019]/70">
                          <span className="font-semibold">Provider error:</span> {overview.endpoint_health.last_provider_error || 'None'}
                        </p>
                      </div>
                    ) : (
                      <p className="text-sm text-slate dark:text-white/70">No endpoint data yet.</p>
                    )}
                  </SectionCard>
                </div>

                <div className="grid gap-4 xl:grid-cols-2">
                  <SectionCard title="Per-Model Usage" description="Request, token, and latency breakdown by model.">
                    <div className="max-h-80 space-y-2 overflow-auto">
                      {(overview.per_model.length ? overview.per_model : []).map((row) => (
                        <div key={row.model} className="rounded border border-slate/20 bg-white/60 px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]/70">
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <span className="font-semibold">{row.model}</span>
                            <span className="text-xs text-slate dark:text-white/60">{row.success_rate_pct.toFixed(1)}% success</span>
                          </div>
                          <div className="mt-1 grid gap-2 text-xs sm:grid-cols-4">
                            <span>{row.total_requests} requests</span>
                            <span>{row.total_tokens.toLocaleString()} tokens</span>
                            <span>{formatDurationMs(row.average_latency_ms)} avg</span>
                            <span>{formatTimestamp(row.last_request_at)}</span>
                          </div>
                        </div>
                      ))}
                      {!overview.per_model.length && <p className="text-sm text-slate dark:text-white/70">No model activity yet.</p>}
                    </div>
                  </SectionCard>

                  <SectionCard title="Failure Log" description="Grouped by feature, model, and error signature.">
                    <div className="max-h-80 space-y-2 overflow-auto">
                      {(overview.failures.length ? overview.failures : []).map((row) => (
                        <div key={`${row.feature_type ?? row.task_type ?? 'unknown'}-${row.error}`} className="rounded border border-slate/20 bg-white/60 px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]/70">
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <span className="font-semibold">{row.error}</span>
                            <span className="text-xs text-slate dark:text-white/60">{row.count}x</span>
                          </div>
                          <div className="mt-1 flex flex-wrap gap-2 text-xs text-slate dark:text-white/60">
                            <span>{row.task_type || row.feature_type || 'unknown feature'}</span>
                            <span>{row.model || 'any model'}</span>
                            <span>{formatTimestamp(row.last_seen_at)}</span>
                          </div>
                        </div>
                      ))}
                      {!overview.failures.length && <p className="text-sm text-slate dark:text-white/70">No failures recorded.</p>}
                    </div>
                  </SectionCard>
                </div>

                <SectionCard title="Time Series" description="Simple inline views for requests, tokens, latency, and daily brief outcomes.">
                  <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                    <SparklineCard label="Requests" value={sumSeries(overview.time_series, 'requests').toLocaleString()} points={overview.time_series.map((point) => point.requests)} />
                    <SparklineCard label="Tokens" value={sumSeries(overview.time_series, 'total_tokens').toLocaleString()} points={overview.time_series.map((point) => point.total_tokens)} />
                    <SparklineCard label="P95 Latency" value={formatDurationMs(lastSeriesValue(overview.time_series, 'p95_latency_ms'))} points={overview.time_series.map((point) => point.p95_latency_ms)} />
                    <SparklineCard label="Daily Brief Successes" value={sumSeries(overview.time_series, 'daily_brief_successes').toLocaleString()} points={overview.time_series.map((point) => point.daily_brief_successes)} />
                  </div>
                </SectionCard>

                <div className="grid gap-4 xl:grid-cols-2">
                  <SectionCard title="Token Efficiency" description="How expensive the AI workload is on average.">
                    <div className="grid gap-3 sm:grid-cols-2">
                      <MetricCard label="Prompt Tokens" value={overview.token_efficiency.average_prompt_tokens.toFixed(1)} />
                      <MetricCard label="Completion Tokens" value={overview.token_efficiency.average_completion_tokens.toFixed(1)} />
                      <MetricCard label="Total Tokens" value={overview.token_efficiency.average_total_tokens.toFixed(1)} />
                      <MetricCard label="Prompt / Completion" value={overview.token_efficiency.prompt_to_completion_ratio.toFixed(2)} />
                    </div>
                    <p className="mt-3 text-sm text-slate dark:text-white/70">
                      Top expensive feature: {overview.token_efficiency.top_expensive_feature || 'None'} ({overview.token_efficiency.top_expensive_feature_avg_tokens.toFixed(1)} avg tokens)
                    </p>
                  </SectionCard>

                  <SectionCard title="Cache / No-op" description="How often enrichment work is avoided because the source has not changed.">
                    <div className="grid gap-3 sm:grid-cols-2">
                      <MetricCard label="Reused" value={String(overview.cache.reused_count)} />
                      <MetricCard label="Recomputed" value={String(overview.cache.recomputed_count)} />
                    </div>
                    <div className="mt-3">
                      <ProgressBar value={overview.cache.no_op_rate_pct} />
                      <p className="mt-1 text-xs text-slate dark:text-white/60">{overview.cache.no_op_rate_pct.toFixed(1)}% no-op rate</p>
                    </div>
                  </SectionCard>
                </div>

                <div className="grid gap-4 xl:grid-cols-2">
                  <SectionCard title="Relevance Distribution" description="How many items land in each relevance bucket, plus per-feed breakdown.">
                    <div className="grid gap-3 sm:grid-cols-3">
                      <MetricCard label="High" value={String(overview.relevance_distribution.high_count)} />
                      <MetricCard label="Medium" value={String(overview.relevance_distribution.medium_count)} />
                      <MetricCard label="Low" value={String(overview.relevance_distribution.low_count)} />
                    </div>
                    <div className="mt-3">
                      <ProgressBar
                        value={Math.max(
                          0,
                          (overview.relevance_distribution.high_count + overview.relevance_distribution.medium_count + overview.relevance_distribution.low_count) > 0
                            ? (overview.relevance_distribution.high_count /
                                (overview.relevance_distribution.high_count + overview.relevance_distribution.medium_count + overview.relevance_distribution.low_count)) *
                                100
                            : 0,
                        )}
                        accentClassName="bg-emerald-500"
                      />
                      <p className="mt-1 text-xs text-slate dark:text-white/60">
                        Average score: {overview.relevance_distribution.average_score.toFixed(2)}
                      </p>
                    </div>
                    <div className="mt-4 space-y-2">
                      {(overview.relevance_distribution.by_feed.length ? overview.relevance_distribution.by_feed : []).map((feed) => (
                        <div key={feed.feed_name} className="rounded border border-slate/20 bg-white/60 px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]/70">
                          <div className="flex items-center justify-between gap-3">
                            <span className="font-semibold">{feed.feed_name}</span>
                            <span className="text-xs text-slate dark:text-white/60">{feed.average_score.toFixed(2)} avg</span>
                          </div>
                          <div className="mt-2">
                            <StackedFeedBar feed={feed} />
                          </div>
                        </div>
                      ))}
                    </div>
                  </SectionCard>

                  <SectionCard title="Coverage & Freshness" description="What is covered, what is pending, and how stale the pipeline is.">
                    <div className="grid gap-3 sm:grid-cols-2">
                      <MetricCard label="Eligible" value={String(overview.coverage.eligible_items)} />
                      <MetricCard label="Enriched" value={String(overview.coverage.enriched_items)} />
                      <MetricCard label="Pending" value={String(overview.coverage.pending_items)} />
                      <MetricCard label="Failed" value={String(overview.coverage.failed_items)} />
                    </div>
                    <div className="mt-3 space-y-2 text-sm">
                      <p>Oldest pending: {formatTimestamp(overview.coverage.oldest_pending_at)}</p>
                      <p>Last enrichment: {formatTimestamp(overview.coverage.last_successful_enrichment_at)}</p>
                      <p>Last daily brief: {formatTimestamp(overview.coverage.last_successful_daily_brief_at)}</p>
                      <p>Last AI run: {formatTimestamp(overview.coverage.last_ai_run_at)}</p>
                    </div>
                    <div className="mt-3 space-y-1 text-xs text-slate dark:text-white/60">
                      <p>Skipped no article: {overview.coverage.skipped_no_article_count}</p>
                      <p>Skipped AI disabled: {overview.coverage.skipped_ai_disabled_count}</p>
                      <p>Skipped not configured: {overview.coverage.skipped_not_configured_count}</p>
                      <p>Skipped auto-enrich off: {overview.coverage.skipped_auto_enrich_disabled_count}</p>
                      <p>Skipped unchanged: {overview.coverage.skipped_unchanged_count}</p>
                    </div>
                  </SectionCard>
                </div>

                <div className="grid gap-4 xl:grid-cols-2">
                  <SectionCard title="Storage / Retention" description="How much history the AI subsystems are keeping around.">
                    <div className="grid gap-3 sm:grid-cols-2">
                      <MetricCard label="Daily Briefs Retained" value={String(overview.storage.retained_daily_briefs)} />
                      <MetricCard label="History Limit" value={String(overview.storage.daily_brief_history_limit)} />
                      <MetricCard label="Enrichment Rows" value={String(overview.storage.enrichment_rows)} />
                      <MetricCard label="Usage Events" value={String(overview.storage.usage_event_rows)} />
                      <MetricCard label="Task History Rows" value={String(overview.storage.task_history_rows)} />
                      <MetricCard label="Growth 7d / 30d" value={`${overview.storage.growth_last_7d}/${overview.storage.growth_last_30d}`} />
                    </div>
                  </SectionCard>

                  <SectionCard title="Feature Health" description="Whether each AI feature is enabled and when it last did work.">
                    <div className="overflow-auto">
                      <table className="min-w-full text-sm">
                        <thead>
                          <tr className="border-b border-slate/20 text-left text-xs uppercase tracking-wide text-slate dark:border-cyan-900/40 dark:text-white/60">
                            <th className="py-2 pr-3">Feature</th>
                            <th className="py-2 pr-3">Enabled</th>
                            <th className="py-2 pr-3">Last Run</th>
                            <th className="py-2 pr-3">Last Success</th>
                            <th className="py-2 pr-3">Status</th>
                          </tr>
                        </thead>
                        <tbody>
                          {overview.feature_health.map((row) => (
                            <tr key={row.feature_key} className="border-b border-slate/10 dark:border-cyan-900/20">
                              <td className="py-2 pr-3 font-semibold">{FEATURE_LABELS[row.feature_key] || row.feature_key}</td>
                              <td className="py-2 pr-3">{row.enabled ? 'Yes' : 'No'}</td>
                              <td className="py-2 pr-3">{formatTimestamp(row.last_run_at)}</td>
                              <td className="py-2 pr-3">{formatTimestamp(row.last_success_at)}</td>
                              <td className="py-2 pr-3">{row.last_status || 'unknown'}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </SectionCard>
                </div>

                <div className="grid gap-4 xl:grid-cols-2">
                  <SectionCard title="Prompt Snapshots" description="Compact previews of the actual prompts ThreatLens is sending.">
                    {promptPreviews ? (
                      <div className="space-y-3">
                        {[
                          promptPreviews.item_enrichment,
                          promptPreviews.daily_brief,
                        ].map((preview) => (
                          <div key={preview.label} className="rounded border border-slate/20 bg-white/60 p-3 text-sm dark:border-cyan-900/40 dark:bg-[#072019]/70">
                            <div className="flex items-center justify-between gap-2">
                              <span className="font-semibold">{preview.label}</span>
                            </div>
                            <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-black/5 p-2 text-xs dark:bg-white/5">
                              {preview.system_prompt}
                            </pre>
                            {preview.notes.length > 0 && (
                              <ul className="mt-2 space-y-1 text-xs text-slate dark:text-white/60">
                                {preview.notes.map((note) => (
                                  <li key={note}>• {note}</li>
                                ))}
                              </ul>
                            )}
                          </div>
                        ))}
                      </div>
                    ) : (
                      <p className="text-sm text-slate dark:text-white/70">Prompt previews are not available yet.</p>
                    )}
                  </SectionCard>

                  <SectionCard title="Manual Action Log" description="Recent operator actions against the AI system.">
                    <div className="max-h-80 space-y-2 overflow-auto">
                      {(manualActionsQuery.data ?? []).map((entry) => (
                        <div key={entry.id} className="rounded border border-slate/20 bg-white/60 px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]/70">
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <span className="font-semibold">{entry.action}</span>
                            <span className="text-xs text-slate dark:text-white/60">{formatTimestamp(entry.created_at)}</span>
                          </div>
                          <p className="mt-1 text-xs text-slate dark:text-white/60">
                            {entry.actor_email || 'system'} • {entry.success ? 'success' : 'failure'} • {entry.resource_type}
                            {entry.resource_id ? `:${entry.resource_id}` : ''}
                          </p>
                          <p className="mt-1 text-xs text-slate dark:text-white/60">{summarizeMetadata(entry.metadata)}</p>
                        </div>
                      ))}
                    </div>
                  </SectionCard>
                </div>

                {latestGeneratedBrief && (
                  <SectionCard title="Latest Generated Brief" description="A compact summary of the most recent brief generated from the header action.">
                    <div className="grid gap-3 md:grid-cols-3">
                      <MetricCard label="Generated" value={formatTimestamp(latestGeneratedBrief.generated_at)} />
                      <MetricCard label="Items" value={String(latestGeneratedBrief.item_count)} />
                      <MetricCard label="Model" value={latestGeneratedBrief.model || 'unknown'} />
                    </div>
                    {latestGeneratedBrief.key_points.length > 0 && (
                      <div className="mt-3 space-y-2 text-sm">
                        {latestGeneratedBrief.key_points.slice(0, 3).map((point) => (
                          <p key={point}>• {point}</p>
                        ))}
                      </div>
                    )}
                  </SectionCard>
                )}
              </div>
            )}
          </section>
        </div>
      )}

      {activeTab === 'runs' && (
        <div className="space-y-4">
          <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h3 className="font-display text-xl">Runs & Logs</h3>
                <p className="mt-1 text-sm text-slate dark:text-white/70">
                  Filter the AI task history, inspect a selected run, and review manual actions and prompt changes.
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <select
                  className="rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                  value={runDays}
                  onChange={(event) => {
                    setRunDays(Number(event.target.value))
                    setRunPage(1)
                  }}
                >
                  <option value={7}>Last 7 days</option>
                  <option value={30}>Last 30 days</option>
                  <option value={90}>Last 90 days</option>
                  <option value={180}>Last 180 days</option>
                </select>
                <select
                  className="rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                  value={runLimit}
                  onChange={(event) => {
                    setRunLimit(Number(event.target.value))
                    setRunPage(1)
                  }}
                >
                  <option value={10}>10 / page</option>
                  <option value={25}>25 / page</option>
                  <option value={50}>50 / page</option>
                </select>
              </div>
            </div>

            <div className="mt-4 grid gap-3 lg:grid-cols-2 xl:grid-cols-6">
              <select
                className="rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                value={runTaskType}
                onChange={(event) => {
                  setRunTaskType(event.target.value)
                  setRunPage(1)
                }}
              >
                <option value="">All task types</option>
                <option value="item_enrichment">Item enrichment</option>
                <option value="daily_brief">Daily brief</option>
                <option value="connection_test">Connection test</option>
                <option value="reprocess">Reprocess</option>
              </select>
              <select
                className="rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                value={runStatus}
                onChange={(event) => {
                  setRunStatus(event.target.value)
                  setRunPage(1)
                }}
              >
                <option value="">All statuses</option>
                <option value="queued">Queued</option>
                <option value="running">Running</option>
                <option value="ready">Ready</option>
                <option value="error">Error</option>
                <option value="skipped">Skipped</option>
              </select>
              <select
                className="rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                value={runTriggerSource}
                onChange={(event) => {
                  setRunTriggerSource(event.target.value)
                  setRunPage(1)
                }}
              >
                <option value="">All triggers</option>
                <option value="auto">Auto</option>
                <option value="manual">Manual</option>
                <option value="scheduled">Scheduled</option>
              </select>
              <input
                list="ai-model-options"
                className="rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                value={runModel}
                onChange={(event) => {
                  setRunModel(event.target.value)
                  setRunPage(1)
                }}
                placeholder="Model filter"
              />
              <label className="flex items-center gap-2 rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]">
                <input
                  type="checkbox"
                  checked={runOnlyFailures}
                  onChange={(event) => {
                    setRunOnlyFailures(event.target.checked)
                    setRunPage(1)
                  }}
                />
                Only failures
              </label>
              <button
                type="button"
                className="rounded border border-slate/30 px-3 py-2 text-sm font-semibold dark:border-cyan-900/40"
                onClick={() => {
                  setRunTaskType('')
                  setRunStatus('')
                  setRunTriggerSource('')
                  setRunModel('')
                  setRunOnlyFailures(false)
                  setRunDays(30)
                  setRunLimit(25)
                  setRunPage(1)
                }}
              >
                Clear Filters
              </button>
              <datalist id="ai-model-options">
                {Array.from(new Set(runRows.map((row) => row.model).filter((value): value is string => Boolean(value)))).map((model) => (
                  <option key={model} value={model} />
                ))}
              </datalist>
            </div>
          </section>

          <div className="grid gap-4 xl:grid-cols-[1.35fr_0.95fr]">
            <SectionCard title="Task History" description="Queued, running, ready, error, and skipped AI jobs.">
              {runsQuery.isLoading && <p className="text-sm text-slate dark:text-white/70">Loading AI task runs...</p>}
              {runsQuery.isError && <p className="text-sm text-red-600">{errorMessage(runsQuery.error, 'Failed to load AI task runs.')}</p>}
              {runsQuery.data && (
                <>
                  <div className="max-h-[540px] overflow-auto">
                    <table className="min-w-full text-sm">
                      <thead>
                        <tr className="sticky top-0 border-b border-slate/20 bg-white/95 text-left text-xs uppercase tracking-wide text-slate dark:border-cyan-900/40 dark:bg-[#041612]/95 dark:text-white/60">
                          <th className="py-2 pr-3">Type</th>
                          <th className="py-2 pr-3">Status</th>
                          <th className="py-2 pr-3">Trigger</th>
                          <th className="py-2 pr-3">Queued</th>
                          <th className="py-2 pr-3">Started</th>
                          <th className="py-2 pr-3">Finished</th>
                          <th className="py-2 pr-3">Duration</th>
                          <th className="py-2 pr-3">Worker</th>
                          <th className="py-2 pr-3">Model</th>
                          <th className="py-2 pr-3">Counts</th>
                          <th className="py-2 pr-3">Error</th>
                        </tr>
                      </thead>
                      <tbody>
                        {runsQuery.data.items.map((run) => (
                          <tr
                            key={run.id}
                            onClick={() => setSelectedRunId(run.id)}
                            className={`cursor-pointer border-b border-slate/10 transition hover:bg-cyan/5 dark:border-cyan-900/20 ${
                              selectedRunId === run.id ? 'bg-cyan/10 dark:bg-cyan/10' : ''
                            }`}
                          >
                            <td className="py-2 pr-3">
                              <div className="font-semibold">{RUN_TASK_TYPE_LABELS[run.task_type]}</div>
                              <div className="text-xs text-slate dark:text-white/60">{run.reason || '—'}</div>
                            </td>
                            <td className="py-2 pr-3">
                              <Badge tone={run.status}>{run.status}</Badge>
                            </td>
                            <td className="py-2 pr-3 text-xs text-slate dark:text-white/70">{RUN_TRIGGER_LABELS[run.trigger_source]}</td>
                            <td className="py-2 pr-3 text-xs">{formatTimestamp(run.queued_at)}</td>
                            <td className="py-2 pr-3 text-xs">{formatTimestamp(run.started_at)}</td>
                            <td className="py-2 pr-3 text-xs">{formatTimestamp(run.finished_at)}</td>
                            <td className="py-2 pr-3 text-xs">{formatDurationMs(run.duration_ms)}</td>
                            <td className="py-2 pr-3 text-xs">{run.worker_name || '—'}</td>
                            <td className="py-2 pr-3 text-xs">{run.model || '—'}</td>
                            <td className="py-2 pr-3 text-xs">
                              {run.task_type === 'reprocess' || run.target_count != null
                                ? `${run.processed_count}/${run.target_count ?? '—'}`
                                : `${run.success_count}/${run.error_count}/${run.skipped_count}`}
                            </td>
                            <td className="py-2 pr-3 text-xs text-red-600">{truncate(run.error, 40)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>

                  <div className="mt-3 flex flex-wrap items-center justify-between gap-3 text-sm">
                    <p className="text-slate dark:text-white/70">
                      Showing {runRows.length} of {runTotal.toLocaleString()} runs
                    </p>
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        className="rounded border border-slate/30 px-3 py-2 font-semibold disabled:opacity-40 dark:border-cyan-900/40"
                        disabled={runPage <= 1}
                        onClick={() => setRunPage((value) => Math.max(1, value - 1))}
                      >
                        Previous
                      </button>
                      <span className="text-xs text-slate dark:text-white/60">
                        Page {runPage} of {totalPages}
                      </span>
                      <button
                        type="button"
                        className="rounded border border-slate/30 px-3 py-2 font-semibold disabled:opacity-40 dark:border-cyan-900/40"
                        disabled={runPage >= totalPages}
                        onClick={() => setRunPage((value) => Math.min(totalPages, value + 1))}
                      >
                        Next
                      </button>
                    </div>
                  </div>
                </>
              )}
            </SectionCard>

            <SectionCard title="Selected Run Detail" description="Run-level counts, metadata, and task events.">
              {selectedRunDetailQuery.isLoading && <p className="text-sm text-slate dark:text-white/70">Loading selected run...</p>}
              {selectedRunDetailQuery.isError && (
                <p className="text-sm text-red-600">{errorMessage(selectedRunDetailQuery.error, 'Failed to load run detail.')}</p>
              )}
              {selectedRunDetail && (
                <div className="space-y-3">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div>
                      <p className="font-semibold">{RUN_TASK_TYPE_LABELS[selectedRunDetail.task_type]}</p>
                      <p className="text-xs text-slate dark:text-white/60">{selectedRunDetail.id}</p>
                    </div>
                    <Badge tone={selectedRunDetail.status}>{selectedRunDetail.status}</Badge>
                  </div>

                  <div className="grid gap-3 sm:grid-cols-2">
                    <MetricCard label="Trigger" value={RUN_TRIGGER_LABELS[selectedRunDetail.trigger_source]} />
                    <MetricCard label="Worker" value={selectedRunDetail.worker_name || '—'} />
                    <MetricCard label="Model" value={selectedRunDetail.model || '—'} />
                    <MetricCard label="Duration" value={formatDurationMs(selectedRunDetail.duration_ms)} />
                  </div>

                  <div className="grid gap-3 sm:grid-cols-3">
                    <MetricCard label="Target" value={formatOptionalCount(selectedRunDetail.target_count)} />
                    <MetricCard label="Processed" value={String(selectedRunDetail.processed_count)} />
                    <MetricCard label="Ready" value={String(selectedRunDetail.success_count)} />
                    <MetricCard label="Error" value={String(selectedRunDetail.error_count)} />
                    <MetricCard label="Skipped" value={String(selectedRunDetail.skipped_count)} />
                    <MetricCard label="Skipped Changed" value={String(selectedRunDetail.skipped_unchanged_count)} />
                  </div>

                  {selectedRunDetail.task_type === 'reprocess' && selectedRunDetail.target_count != null && (
                    <div>
                      <ProgressBar
                        value={selectedRunDetail.target_count > 0 ? (selectedRunDetail.processed_count / selectedRunDetail.target_count) * 100 : 0}
                      />
                      <p className="mt-1 text-xs text-slate dark:text-white/60">
                        Remaining {Math.max(selectedRunDetail.target_count - selectedRunDetail.processed_count, 0)} of {selectedRunDetail.target_count}
                      </p>
                    </div>
                  )}

                  <div className="space-y-2">
                    <p className="text-xs uppercase tracking-wide text-slate dark:text-white/60">Task events</p>
                    {(selectedRunEvents.length ? selectedRunEvents : []).map((event) => (
                      <div key={event.id} className="rounded border border-slate/20 bg-white/60 px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]/70">
                        <div className="flex items-center justify-between gap-2">
                          <span className="font-semibold">{event.event_type}</span>
                          <span className="text-xs text-slate dark:text-white/60">{formatTimestamp(event.created_at)}</span>
                        </div>
                        {event.message && <p className="mt-1 text-xs text-slate dark:text-white/70">{event.message}</p>}
                        <p className="mt-1 text-xs text-slate dark:text-white/60">{summarizeMetadata(event.payload)}</p>
                      </div>
                    ))}
                  </div>

                  <div className="space-y-2">
                    <p className="text-xs uppercase tracking-wide text-slate dark:text-white/60">Metadata</p>
                    <pre className="max-h-48 overflow-auto rounded border border-slate/20 bg-white/60 p-3 text-xs dark:border-cyan-900/40 dark:bg-[#072019]/70">
                      {JSON.stringify(selectedRunDetail.metadata, null, 2)}
                    </pre>
                  </div>

                  {selectedBriefId && (
                    <div className="space-y-2">
                      <p className="text-xs uppercase tracking-wide text-slate dark:text-white/60">Daily brief source items</p>
                      {briefSourcesQuery.isLoading && <p className="text-sm text-slate dark:text-white/70">Loading brief source items...</p>}
                      {briefSourcesQuery.isError && (
                        <p className="text-sm text-red-600">{errorMessage(briefSourcesQuery.error, 'Failed to load brief source items.')}</p>
                      )}
                      <div className="max-h-72 space-y-2 overflow-auto">
                        {briefSources.map((source) => (
                          <div key={source.id} className="rounded border border-slate/20 bg-white/60 px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]/70">
                            <div className="flex items-start justify-between gap-2">
                              <div>
                                <p className="font-semibold">{source.title_snapshot}</p>
                                <p className="text-xs text-slate dark:text-white/60">
                                  {source.feed_name_snapshot || 'Unknown feed'} • {source.classification_snapshot || 'uncategorized'}
                                </p>
                              </div>
                              <Badge tone={source.included ? 'success' : 'muted'}>{source.included ? 'Included' : 'Excluded'}</Badge>
                            </div>
                            <p className="mt-1 text-xs text-slate dark:text-white/60">
                              Rank {source.rank}
                              {source.exclusion_reason ? ` • ${source.exclusion_reason}` : ''}
                            </p>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
              {!selectedRunDetail && <p className="text-sm text-slate dark:text-white/70">Select a row in the task history to inspect it.</p>}
            </SectionCard>
          </div>

          <div className="grid gap-4 xl:grid-cols-2">
            <SectionCard title="Manual Action Log" description="Latest operator actions.">
              <div className="max-h-80 space-y-2 overflow-auto">
                {(manualActionsQuery.data ?? []).map((entry) => (
                  <AuditEntryCard key={entry.id} entry={entry} />
                ))}
              </div>
            </SectionCard>

            <SectionCard title="Prompt History" description="Prompt template and threshold updates.">
              <div className="max-h-80 space-y-2 overflow-auto">
                {(promptHistoryQuery.data ?? []).map((entry) => (
                  <AuditEntryCard key={entry.id} entry={entry} />
                ))}
              </div>
            </SectionCard>
          </div>
        </div>
      )}

      {activeTab === 'configuration' && (
        <div className="space-y-4">
          <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h3 className="font-display text-xl">Configuration</h3>
                <p className="mt-1 text-sm text-slate dark:text-white/70">
                  Configure the provider, feature flags, company context, and prompt templates.
                </p>
              </div>
              <div className="flex flex-wrap gap-2 text-xs text-slate dark:text-white/60">
                <span className="rounded border border-slate/20 bg-slate/5 px-2 py-1 dark:border-cyan-900/40 dark:bg-white/[0.04]">
                  {settings?.ai_configured ? 'Configured' : 'Not configured'}
                </span>
                <span className="rounded border border-slate/20 bg-slate/5 px-2 py-1 dark:border-cyan-900/40 dark:bg-white/[0.04]">
                  {settings?.api_key_configured ? 'API key present' : 'No API key in env'}
                </span>
                <span className="rounded border border-slate/20 bg-slate/5 px-2 py-1 dark:border-cyan-900/40 dark:bg-white/[0.04]">
                  {settings?.provider_type || 'openai_compatible'}
                </span>
              </div>
            </div>

            {settingsQuery.isError && <p className="mt-3 text-sm text-red-600">{errorMessage(settingsQuery.error, 'Failed to load AI settings.')}</p>}

            <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded border border-slate/20 bg-white/60 p-3 dark:border-cyan-900/40 dark:bg-[#072019]/70">
              <div>
                <p className="text-sm font-semibold">{readinessMessage}</p>
                <p className="text-xs text-slate dark:text-white/60">Save changes here, then use the header actions to test or generate.</p>
              </div>
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

          <div className="grid gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(320px,1fr)]">
            <div className="space-y-4">
              <section className="rounded-xl border border-slate/20 bg-slate/5 p-4 dark:border-cyan-900/40 dark:bg-white/[0.03]">
                <h4 className="font-display text-lg">Provider</h4>
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
                <h4 className="font-display text-lg">Feature Controls</h4>
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
                <h4 className="font-display text-lg">Company Context</h4>
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
                <h4 className="font-display text-lg">Prompt Tuning</h4>
                <p className="mt-1 text-sm text-slate dark:text-white/70">
                  The base system prompts below start with the built-in ThreatLens defaults and can be edited directly. Additional helper instructions are appended after them.
                </p>
                <div className="mt-3 grid gap-3">
                  <PromptArea
                    label="Item Enrichment System Prompt"
                    value={draft.item_enrichment_system_prompt}
                    onChange={(value) => updateDraft(setDraft, 'item_enrichment_system_prompt', value)}
                    placeholder="Base system prompt for article summaries and relevance scoring."
                  />
                  <PromptArea
                    label="Daily Brief System Prompt"
                    value={draft.daily_brief_system_prompt}
                    onChange={(value) => updateDraft(setDraft, 'daily_brief_system_prompt', value)}
                    placeholder="Base system prompt for daily brief generation."
                  />
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

            <div className="space-y-4">
              <SectionCard title="Readiness" description="Current runtime configuration and AI gate status.">
                <p className="text-sm text-slate dark:text-white/75">{readinessMessage}</p>
                <div className="mt-3 space-y-2 text-sm">
                  <Row label="Configured" value={settings?.ai_configured ? 'Yes' : 'No'} />
                  <Row label="API key in env" value={settings?.api_key_configured ? 'Yes' : 'No / optional'} />
                  <Row label="Provider" value={settings?.provider_type || 'openai_compatible'} />
                  <Row label="Model" value={settings?.model || '—'} />
                </div>
              </SectionCard>

              <SectionCard title="Prompt Previews" description="The current prompt snapshots ThreatLens uses at runtime.">
                {promptPreviews ? (
                  <div className="space-y-3">
                    {[promptPreviews.item_enrichment, promptPreviews.daily_brief].map((preview) => (
                      <div key={preview.label} className="rounded border border-slate/20 bg-white/60 p-3 text-sm dark:border-cyan-900/40 dark:bg-[#072019]/70">
                        <p className="font-semibold">{preview.label}</p>
                        <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-black/5 p-2 text-xs dark:bg-white/5">{preview.system_prompt}</pre>
                        {preview.notes.length > 0 && <p className="mt-2 text-xs text-slate dark:text-white/60">{preview.notes.join(' • ')}</p>}
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-sm text-slate dark:text-white/70">Prompt previews are unavailable.</p>
                )}
              </SectionCard>

              <SectionCard title="Usage Snapshot" description="A compact reminder of the current AI workload.">
                <div className="grid gap-3 sm:grid-cols-2">
                  <MetricCard label="Requests" value={String(overview?.kpis.total_requests ?? 0)} />
                  <MetricCard label="Tokens" value={String(overview?.kpis.total_tokens ?? 0)} />
                  <MetricCard label="Average Latency" value={formatDurationMs(overview?.kpis.average_latency_ms ?? 0)} />
                  <MetricCard label="Success Rate" value={`${(overview?.kpis.success_rate_pct ?? 0).toFixed(1)}%`} />
                </div>
              </SectionCard>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function SectionCard({
  title,
  description,
  children,
}: {
  title: string
  description?: string
  children: ReactNode
}) {
  return (
    <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="font-display text-lg">{title}</h3>
          {description && <p className="mt-1 text-sm text-slate dark:text-white/70">{description}</p>}
        </div>
      </div>
      <div className="mt-3">{children}</div>
    </section>
  )
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-slate/20 bg-white/70 px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]/80">
      <p className="text-xs uppercase tracking-wide text-slate dark:text-white/55">{label}</p>
      <p className="mt-1 text-lg font-semibold">{value}</p>
    </div>
  )
}

function Badge({
  tone,
  children,
}: {
  tone: 'success' | 'warning' | 'muted' | 'neutral' | AITaskRunResponse['status'] | AILiveStatusResponse['active_tasks'][number]['state']
  children: ReactNode
}) {
  const className =
    tone === 'success'
      ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-900 dark:text-emerald-100'
      : tone === 'warning'
        ? 'border-amber-500/30 bg-amber-500/10 text-amber-900 dark:text-amber-100'
        : tone === 'muted'
          ? 'border-slate/20 bg-slate/10 text-slate-800 dark:border-cyan-900/40 dark:bg-white/10 dark:text-white/80'
          : tone === 'neutral'
            ? 'border-cyan-500/30 bg-cyan-500/10 text-cyan-900 dark:text-cyan-100'
            : RUN_STATUS_STYLES[tone] || LIVE_STATE_STYLES[tone] || 'border-slate/20 bg-slate/10 text-slate-800 dark:border-cyan-900/40 dark:bg-white/10 dark:text-white/80'
  return <span className={`inline-flex rounded border px-2 py-1 text-xs font-semibold ${className}`}>{children}</span>
}

function ProgressBar({
  value,
  accentClassName = 'bg-cyan-500',
}: {
  value: number
  accentClassName?: string
}) {
  return (
    <div className="h-2 overflow-hidden rounded bg-slate-200 dark:bg-[#072019]">
      <div className={`h-full rounded ${accentClassName}`} style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
    </div>
  )
}

function SparklineCard({ label, value, points }: { label: string; value: string; points: number[] }) {
  return (
    <div className="rounded border border-slate/20 bg-white/70 p-3 dark:border-cyan-900/40 dark:bg-[#072019]/80">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-xs uppercase tracking-wide text-slate dark:text-white/55">{label}</p>
          <p className="mt-1 text-lg font-semibold">{value}</p>
        </div>
        <Sparkline points={points} />
      </div>
    </div>
  )
}

function Sparkline({ points }: { points: number[] }) {
  if (!points.length) {
    return <div className="h-10 w-28 rounded bg-slate/10 dark:bg-white/5" />
  }

  const width = 112
  const height = 40
  const max = Math.max(...points, 1)
  const min = Math.min(...points, 0)
  const range = max - min || 1
  const coords = points.map((point, index) => {
    const x = (index / Math.max(points.length - 1, 1)) * width
    const y = height - ((point - min) / range) * (height - 4) - 2
    return `${x},${y}`
  })
  const path = `M ${coords.join(' L ')}`

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="h-10 w-28">
      <path d={path} fill="none" stroke="currentColor" strokeWidth="2" className="text-cyan-600 dark:text-cyan-300" />
    </svg>
  )
}

function TaskStateBlock({
  title,
  items,
}: {
  title: string
  items: AILiveStatusResponse['active_tasks']
}) {
  return (
    <div>
      <p className="text-xs uppercase tracking-wide text-slate dark:text-white/60">{title}</p>
      <div className="mt-2 space-y-2">
        {items.length ? (
          items.map((item) => (
            <div key={`${item.state}-${item.celery_task_id ?? item.task_name}-${item.worker_name}`} className="rounded border border-slate/20 bg-white/60 px-3 py-2 text-xs dark:border-cyan-900/40 dark:bg-[#072019]/70">
              <div className="flex items-center justify-between gap-2">
                <span className="font-semibold">{item.task_name}</span>
                <Badge tone={item.state}>{item.state}</Badge>
              </div>
              <p className="mt-1 text-slate dark:text-white/60">
                {item.worker_name} {item.run_id ? `• ${item.run_id}` : ''}
              </p>
              <p className="mt-1 text-slate dark:text-white/60">{item.eta || item.received_at || 'No timing metadata'}</p>
            </div>
          ))
        ) : (
          <p className="text-sm text-slate dark:text-white/70">None</p>
        )}
      </div>
    </div>
  )
}

function StackedFeedBar({ feed }: { feed: { high_count: number; medium_count: number; low_count: number; total_items: number } }) {
  const total = Math.max(feed.total_items, feed.high_count + feed.medium_count + feed.low_count, 1)
  return (
    <div className="h-2 overflow-hidden rounded bg-slate-200 dark:bg-[#072019]">
      <div className="flex h-full w-full">
        <div className="bg-emerald-500" style={{ width: `${(feed.high_count / total) * 100}%` }} />
        <div className="bg-amber-500" style={{ width: `${(feed.medium_count / total) * 100}%` }} />
        <div className="bg-slate-400" style={{ width: `${(feed.low_count / total) * 100}%` }} />
      </div>
    </div>
  )
}

function AuditEntryCard({ entry }: { entry: AIAuditEntryResponse }) {
  return (
    <div className="rounded border border-slate/20 bg-white/60 px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]/70">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-semibold">{entry.action}</span>
        <span className="text-xs text-slate dark:text-white/60">{formatTimestamp(entry.created_at)}</span>
      </div>
      <p className="mt-1 text-xs text-slate dark:text-white/60">
        {entry.actor_email || 'system'} • {entry.success ? 'success' : 'failure'} • {entry.resource_type}
        {entry.resource_id ? `:${entry.resource_id}` : ''}
      </p>
      <p className="mt-1 text-xs text-slate dark:text-white/60">{summarizeMetadata(entry.metadata)}</p>
    </div>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-slate dark:text-white/65">{label}</span>
      <span className="font-semibold">{value}</span>
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
    item_enrichment_system_prompt: settings.item_enrichment_system_prompt ?? '',
    daily_brief_system_prompt: settings.daily_brief_system_prompt ?? '',
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
    item_enrichment_system_prompt: normalizeOptionalText(draft.item_enrichment_system_prompt),
    daily_brief_system_prompt: normalizeOptionalText(draft.daily_brief_system_prompt),
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

function formatDurationMs(value: number | null | undefined) {
  if (value == null) {
    return 'unknown'
  }
  if (value < 1000) {
    return `${Math.round(value)} ms`
  }
  if (value < 60000) {
    return `${(value / 1000).toFixed(1)} s`
  }
  const minutes = Math.floor(value / 60000)
  const seconds = Math.round((value % 60000) / 1000)
  return `${minutes}m ${seconds}s`
}

function formatDurationSeconds(value: number | null | undefined) {
  if (value == null) {
    return 'unknown'
  }
  if (value < 60) {
    return `${value}s`
  }
  if (value < 3600) {
    const minutes = Math.floor(value / 60)
    const seconds = value % 60
    return `${minutes}m ${seconds}s`
  }
  const hours = Math.floor(value / 3600)
  const minutes = Math.floor((value % 3600) / 60)
  return `${hours}h ${minutes}m`
}

function summarizeMetadata(metadata: Record<string, unknown>): string {
  const entries = Object.entries(metadata)
  if (!entries.length) {
    return 'No metadata'
  }
  return entries
    .slice(0, 4)
    .map(([key, value]) => `${key}: ${stringifyValue(value)}`)
    .join(' • ')
}

function stringifyValue(value: unknown): string {
  if (value == null) {
    return 'null'
  }
  if (Array.isArray(value)) {
    return `[${value.slice(0, 3).map((entry) => stringifyValue(entry)).join(', ')}${value.length > 3 ? ', …' : ''}]`
  }
  if (typeof value === 'object') {
    return '{…}'
  }
  return String(value)
}

function formatOptionalCount(value: number | null) {
  return value == null ? '—' : String(value)
}

function errorMessage(error: unknown, fallback: string) {
  if (error instanceof Error && error.message) {
    return error.message
  }
  return fallback
}

type AITrendKey =
  | 'requests'
  | 'failures'
  | 'total_tokens'
  | 'average_latency_ms'
  | 'p95_latency_ms'
  | 'daily_brief_successes'
  | 'daily_brief_failures'
  | 'daily_brief_skips'

function sumSeries(points: AIOpsOverviewResponse['time_series'], key: AITrendKey) {
  return points.reduce((acc, point) => acc + Number(point[key] ?? 0), 0)
}

function lastSeriesValue(points: AIOpsOverviewResponse['time_series'], key: AITrendKey) {
  if (!points.length) {
    return null
  }
  return Number(points[points.length - 1][key] ?? 0)
}

function truncate(value: string | null, maxLength: number) {
  if (!value) {
    return '—'
  }
  if (value.length <= maxLength) {
    return value
  }
  return `${value.slice(0, maxLength - 1)}…`
}

function formatFeatureLabel(value: 'item_enrichment' | 'daily_brief' | 'connection_test') {
  if (value === 'item_enrichment') return 'Item Enrichment'
  if (value === 'daily_brief') return 'Daily Brief'
  return 'Connection Tests'
}
