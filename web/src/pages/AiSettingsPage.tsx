import { Dispatch, SetStateAction, useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
import { useCurrentUser } from '../hooks/useCurrentUser'
import {
  AIAuditEntryResponse,
  AIDailyBrief,
  AIDailyBriefSourceItemResponse,
  AIFailureGroupResponse,
  AILiveTaskResponse,
  AIOpsOverviewResponse,
  AIReprocessResponse,
  AISettings,
  AISettingsUpdateRequest,
  AITaskRunDetailResponse,
  AITaskRunListResponse,
  AITaskRunResponse,
  AITestConnectionResponse,
} from '../types/api'

type AiTab = 'overview' | 'runs' | 'configuration'

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

type RunFilters = {
  taskType: string
  status: string
  triggerSource: string
  onlyFailures: boolean
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

const RUN_PAGE_SIZE = 20

export function AiSettingsPage() {
  const queryClient = useQueryClient()
  const currentUserQuery = useCurrentUser()
  const [activeTab, setActiveTab] = useState<AiTab>('overview')
  const [days, setDays] = useState(30)
  const [draft, setDraft] = useState<AISettingsDraft>(DEFAULT_DRAFT)
  const [notice, setNotice] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<AITestConnectionResponse | null>(null)
  const [latestGeneratedBrief, setLatestGeneratedBrief] = useState<AIDailyBrief | null>(null)
  const [reprocessDays, setReprocessDays] = useState('7')
  const [reprocessLimit, setReprocessLimit] = useState('100')
  const [selectedModel, setSelectedModel] = useState('all')
  const [runPage, setRunPage] = useState(0)
  const [runFilters, setRunFilters] = useState<RunFilters>({
    taskType: '',
    status: '',
    triggerSource: '',
    onlyFailures: false,
  })
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)

  const aiEnabled = currentUserQuery.data?.features.ai_enabled ?? false

  const settingsQuery = useQuery({
    queryKey: ['ai', 'settings'],
    queryFn: () => apiFetch<AISettings>('/ai/settings'),
    enabled: aiEnabled,
  })

  const overviewQuery = useQuery({
    queryKey: ['ai', 'ops', 'overview', days],
    queryFn: () => apiFetch<AIOpsOverviewResponse>(`/ai/ops/overview?days=${days}`),
    enabled: aiEnabled,
    refetchInterval: activeTab === 'configuration' ? false : 10000,
  })

  const reprocessRunsQuery = useQuery({
    queryKey: ['ai', 'ops', 'runs', 'reprocess-banner', days],
    queryFn: () => apiFetch<AITaskRunListResponse>(`/ai/ops/runs?task_type=reprocess&limit=5&days=${days}`),
    enabled: aiEnabled,
    refetchInterval: 5000,
  })

  const promptHistoryQuery = useQuery({
    queryKey: ['ai', 'ops', 'prompt-history'],
    queryFn: () => apiFetch<AIAuditEntryResponse[]>('/ai/ops/prompt-history?limit=12'),
    enabled: aiEnabled,
  })

  const manualActionsQuery = useQuery({
    queryKey: ['ai', 'ops', 'manual-actions'],
    queryFn: () => apiFetch<AIAuditEntryResponse[]>('/ai/ops/manual-actions?limit=12'),
    enabled: aiEnabled,
  })

  const runsPath = useMemo(() => {
    const params = new URLSearchParams()
    params.set('limit', String(RUN_PAGE_SIZE))
    params.set('offset', String(runPage * RUN_PAGE_SIZE))
    params.set('days', String(days))
    if (selectedModel !== 'all') {
      params.set('model', selectedModel)
    }
    if (runFilters.taskType) {
      params.set('task_type', runFilters.taskType)
    }
    if (runFilters.status) {
      params.set('status', runFilters.status)
    }
    if (runFilters.triggerSource) {
      params.set('trigger_source', runFilters.triggerSource)
    }
    if (runFilters.onlyFailures) {
      params.set('only_failures', 'true')
    }
    return `/ai/ops/runs?${params.toString()}`
  }, [days, runFilters.onlyFailures, runFilters.status, runFilters.taskType, runFilters.triggerSource, runPage, selectedModel])

  const runsQuery = useQuery({
    queryKey: ['ai', 'ops', 'runs', days, selectedModel, runPage, runFilters],
    queryFn: () => apiFetch<AITaskRunListResponse>(runsPath),
    enabled: aiEnabled,
    refetchInterval: activeTab === 'runs' ? 10000 : false,
  })

  const runDetailQuery = useQuery({
    queryKey: ['ai', 'ops', 'run', selectedRunId],
    queryFn: () => apiFetch<AITaskRunDetailResponse>(`/ai/ops/runs/${selectedRunId}`),
    enabled: aiEnabled && Boolean(selectedRunId),
    refetchInterval: activeTab === 'runs' ? 10000 : false,
  })

  const briefSourcesQuery = useQuery({
    queryKey: ['ai', 'daily-brief-sources', runDetailQuery.data?.run.daily_brief_id],
    queryFn: () =>
      apiFetch<AIDailyBriefSourceItemResponse[]>(
        `/ai/daily-briefs/${runDetailQuery.data?.run.daily_brief_id}/sources?limit=50`,
      ),
    enabled: aiEnabled && Boolean(runDetailQuery.data?.run.daily_brief_id),
  })

  useEffect(() => {
    if (!settingsQuery.data) {
      return
    }
    setDraft(createDraftFromSettings(settingsQuery.data))
  }, [settingsQuery.data])

  useEffect(() => {
    const firstRunId = runsQuery.data?.items[0]?.id ?? null
    if (!selectedRunId && firstRunId) {
      setSelectedRunId(firstRunId)
      return
    }
    if (selectedRunId && runsQuery.data && !runsQuery.data.items.some((run) => run.id === selectedRunId)) {
      setSelectedRunId(firstRunId)
    }
  }, [runsQuery.data, selectedRunId])

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
      invalidateAiQueries(queryClient)
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
      invalidateAiQueries(queryClient)
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
      invalidateAiQueries(queryClient)
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
      invalidateAiQueries(queryClient)
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

  const modelOptions = useMemo(() => {
    const values = new Set<string>()
    if (settingsQuery.data?.model) {
      values.add(settingsQuery.data.model)
    }
    for (const row of overviewQuery.data?.per_model ?? []) {
      values.add(row.model)
    }
    return ['all', ...Array.from(values)]
  }, [overviewQuery.data?.per_model, settingsQuery.data?.model])

  const filteredFailures = useMemo(() => {
    if (selectedModel === 'all') {
      return overviewQuery.data?.failures ?? []
    }
    return (overviewQuery.data?.failures ?? []).filter((row) => row.model === selectedModel)
  }, [overviewQuery.data?.failures, selectedModel])

  const filteredPerModel = useMemo(() => {
    if (selectedModel === 'all') {
      return overviewQuery.data?.per_model ?? []
    }
    return (overviewQuery.data?.per_model ?? []).filter((row) => row.model === selectedModel)
  }, [overviewQuery.data?.per_model, selectedModel])

  const activeReprocessRun = useMemo(() => {
    return (
      reprocessRunsQuery.data?.items.find(
        (run) => run.status === 'running' || run.status === 'queued' || (run.status === 'error' && !run.finished_at),
      ) ?? null
    )
  }, [reprocessRunsQuery.data?.items])

  if (currentUserQuery.isLoading) {
    return (
      <div className="rounded-xl border border-slate/20 bg-white/80 p-4 text-sm dark:border-cyan-900/40 dark:bg-[#041612]/90">
        Loading AI settings...
      </div>
    )
  }

  if (!aiEnabled) {
    return (
      <div className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
        <h2 className="font-display text-xl">AI</h2>
        <p className="mt-2 text-sm text-slate dark:text-white/75">
          AI features are disabled by the deployment configuration. Enable `AI_ENABLED=true` and restart ThreatLens to use
          this section.
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="font-display text-2xl">AI Workspace</h2>
              <span className="rounded-full border border-cyan/20 bg-cyan/10 px-2 py-1 text-xs font-semibold uppercase tracking-wide text-cyan-900 dark:border-cyan/30 dark:text-cyan-100">
                {settingsQuery.data?.ai_enabled ? 'Enabled' : 'Disabled'}
              </span>
              {settingsQuery.data?.ai_configured ? (
                <span className="rounded-full border border-emerald-500/20 bg-emerald-500/10 px-2 py-1 text-xs font-semibold uppercase tracking-wide text-emerald-700 dark:text-emerald-300">
                  Configured
                </span>
              ) : (
                <span className="rounded-full border border-amber-500/20 bg-amber-500/10 px-2 py-1 text-xs font-semibold uppercase tracking-wide text-amber-700 dark:text-amber-300">
                  Needs setup
                </span>
              )}
            </div>
            <p className="mt-1 text-sm text-slate dark:text-white/75">
              Monitor local AI health, inspect run history, and adjust how ThreatLens uses summaries, relevance scoring, and
              daily briefs.
            </p>
            <p className="mt-2 text-xs text-slate dark:text-white/60">
              Model: {settingsQuery.data?.model || 'not configured'}
              {settingsQuery.data?.base_url ? `, ${settingsQuery.data.base_url}` : ''}
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <select
              value={days}
              onChange={(event) => {
                setDays(Number(event.target.value))
                setRunPage(0)
              }}
              className="rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
            >
              <option value={1}>Last 24h</option>
              <option value={7}>Last 7d</option>
              <option value={30}>Last 30d</option>
              <option value={90}>Last 90d</option>
            </select>
            <select
              value={selectedModel}
              onChange={(event) => {
                setSelectedModel(event.target.value)
                setRunPage(0)
              }}
              className="rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
            >
              {modelOptions.map((model) => (
                <option key={model} value={model}>
                  {model === 'all' ? 'All models' : model}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="rounded border border-slate/30 px-3 py-2 text-sm font-semibold dark:border-cyan-900/40"
              onClick={() => invalidateAiQueries(queryClient)}
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
              disabled={testConnectionMutation.isPending || !settingsQuery.data?.ai_configured}
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
              disabled={generateBriefMutation.isPending || !draft.daily_brief_enabled}
            >
              {generateBriefMutation.isPending ? 'Generating...' : 'Generate Daily Brief'}
            </button>
            <button
              type="button"
              className="rounded bg-ink px-3 py-2 text-sm font-semibold text-white disabled:opacity-50 dark:bg-cyan dark:text-slate-950"
              onClick={() => {
                setNotice(null)
                reprocessMutation.mutate()
              }}
              disabled={reprocessMutation.isPending}
            >
              {reprocessMutation.isPending ? 'Queueing...' : 'Reprocess'}
            </button>
          </div>
        </div>

        {notice && (
          <p className="mt-3 rounded border border-cyan/20 bg-cyan/10 px-3 py-2 text-sm text-cyan-900 dark:border-cyan-900/40 dark:bg-cyan/10 dark:text-cyan-100">
            {notice}
          </p>
        )}

        {activeReprocessRun && (
          <div className="mt-3 rounded-xl border border-cyan/20 bg-cyan/5 p-3 dark:border-cyan-900/40 dark:bg-cyan/10">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <p className="text-sm font-semibold">
                  Reprocess {formatStatusLabel(activeReprocessRun.status)}: {activeReprocessRun.processed_count}/
                  {activeReprocessRun.target_count ?? '?'} processed
                </p>
                <p className="text-xs text-slate dark:text-white/65">
                  Success {activeReprocessRun.success_count}, errors {activeReprocessRun.error_count}, skipped{' '}
                  {activeReprocessRun.skipped_count}, remaining {remainingCount(activeReprocessRun)}
                </p>
              </div>
              <div className="text-xs text-slate dark:text-white/65">
                Queued {formatTimestamp(activeReprocessRun.queued_at)}
                {activeReprocessRun.worker_name ? `, ${activeReprocessRun.worker_name}` : ''}
              </div>
            </div>
            <ProgressBar
              value={activeReprocessRun.processed_count}
              max={activeReprocessRun.target_count || Math.max(activeReprocessRun.processed_count, 1)}
              className="mt-3"
            />
          </div>
        )}

        {testResult && (
          <div className="mt-3 rounded-xl border border-slate/20 bg-white/70 p-3 text-sm dark:border-cyan-900/40 dark:bg-[#072019]/80">
            <p className="font-semibold">{testResult.success ? 'Connection succeeded' : 'Connection failed'}</p>
            <p className="mt-1 text-slate dark:text-white/70">
              Model: {testResult.model || 'unknown'}
              {typeof testResult.latency_ms === 'number' ? `, ${testResult.latency_ms} ms` : ''}
            </p>
            {testResult.error && <p className="mt-1 text-red-600">{testResult.error}</p>}
          </div>
        )}

        <div className="mt-4 flex flex-wrap gap-2">
          <TabButton active={activeTab === 'overview'} onClick={() => setActiveTab('overview')}>
            Overview
          </TabButton>
          <TabButton active={activeTab === 'runs'} onClick={() => setActiveTab('runs')}>
            Runs &amp; Logs
          </TabButton>
          <TabButton active={activeTab === 'configuration'} onClick={() => setActiveTab('configuration')}>
            Configuration
          </TabButton>
        </div>
      </section>

      {activeTab === 'overview' && (
        <OverviewTab
          settings={settingsQuery.data}
          readiness={readiness}
          overview={overviewQuery.data}
          isLoading={overviewQuery.isLoading}
          isError={overviewQuery.isError}
          errorMessage={(overviewQuery.error as Error | undefined)?.message ?? ''}
          failures={filteredFailures}
          perModel={filteredPerModel}
          promptHistory={promptHistoryQuery.data ?? []}
          manualActions={manualActionsQuery.data ?? []}
          latestGeneratedBrief={latestGeneratedBrief}
          onShowFailure={() => setActiveTab('runs')}
        />
      )}

      {activeTab === 'runs' && (
        <RunsTab
          days={days}
          selectedModel={selectedModel}
          filters={runFilters}
          setFilters={setRunFilters}
          runPage={runPage}
          setRunPage={setRunPage}
          runsQuery={runsQuery}
          selectedRunId={selectedRunId}
          setSelectedRunId={setSelectedRunId}
          runDetailQuery={runDetailQuery}
          briefSources={briefSourcesQuery.data ?? []}
          manualActions={manualActionsQuery.data ?? []}
          promptHistory={promptHistoryQuery.data ?? []}
        />
      )}

      {activeTab === 'configuration' && (
        <ConfigurationTab
          draft={draft}
          setDraft={setDraft}
          settings={settingsQuery.data}
          readiness={readiness}
          isLoading={settingsQuery.isLoading}
          isError={settingsQuery.isError}
          errorMessage={(settingsQuery.error as Error | undefined)?.message ?? ''}
          savePending={saveMutation.isPending}
          onSave={() => {
            setNotice(null)
            saveMutation.mutate(createRequestFromDraft(draft))
          }}
          reprocessDays={reprocessDays}
          reprocessLimit={reprocessLimit}
          setReprocessDays={setReprocessDays}
          setReprocessLimit={setReprocessLimit}
        />
      )}
    </div>
  )
}

function OverviewTab({
  settings,
  readiness,
  overview,
  isLoading,
  isError,
  errorMessage,
  failures,
  perModel,
  promptHistory,
  manualActions,
  latestGeneratedBrief,
  onShowFailure,
}: {
  settings: AISettings | undefined
  readiness: string | null
  overview: AIOpsOverviewResponse | undefined
  isLoading: boolean
  isError: boolean
  errorMessage: string
  failures: AIFailureGroupResponse[]
  perModel: AIOpsOverviewResponse['per_model']
  promptHistory: AIAuditEntryResponse[]
  manualActions: AIAuditEntryResponse[]
  latestGeneratedBrief: AIDailyBrief | null
  onShowFailure: () => void
}) {
  if (isLoading && !overview) {
    return <Panel title="Overview">Loading AI analytics...</Panel>
  }

  if (isError && !overview) {
    return <Panel title="Overview">Failed to load AI analytics. {errorMessage}</Panel>
  }

  if (!overview) {
    return null
  }

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(320px,1fr)]">
      <div className="space-y-4">
        <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          <StatCard label="Requests" value={overview.kpis.total_requests.toLocaleString()} />
          <StatCard label="Success Rate" value={`${overview.kpis.success_rate_pct.toFixed(1)}%`} />
          <StatCard label="Total Tokens" value={overview.kpis.total_tokens.toLocaleString()} />
          <StatCard label="Avg Latency" value={`${overview.kpis.average_latency_ms.toFixed(1)} ms`} />
          <StatCard label="P95 Latency" value={`${overview.kpis.p95_latency_ms.toFixed(1)} ms`} />
          <StatCard
            label="Last Success"
            value={overview.kpis.last_successful_run_at ? formatTimestamp(overview.kpis.last_successful_run_at) : 'Never'}
          />
        </section>

        <Panel title="Requests & Failures Over Time" subtitle="Recent request volume and failure pressure across the selected window.">
          <TimeSeriesBars
            points={overview.time_series}
            valueKey="requests"
            accentClass="bg-cyan"
            secondaryKey="failures"
            secondaryClass="bg-red-400/80"
          />
        </Panel>

        <Panel title="Token Usage Over Time" subtitle="Total tokens by day.">
          <TimeSeriesBars points={overview.time_series} valueKey="total_tokens" accentClass="bg-emerald-500" />
        </Panel>

        <Panel title="Per-Model Usage" subtitle="Requests, success rate, latency, and token footprint by model.">
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="text-left text-xs uppercase tracking-wide text-slate dark:text-white/55">
                <tr>
                  <th className="pb-2">Model</th>
                  <th className="pb-2">Requests</th>
                  <th className="pb-2">Success</th>
                  <th className="pb-2">Avg Latency</th>
                  <th className="pb-2">Tokens</th>
                </tr>
              </thead>
              <tbody>
                {perModel.map((row) => (
                  <tr key={row.model} className="border-t border-slate/10 text-slate dark:border-cyan-900/30 dark:text-white/80">
                    <td className="py-2 font-medium">{row.model}</td>
                    <td className="py-2">{row.total_requests}</td>
                    <td className="py-2">{row.success_rate_pct.toFixed(1)}%</td>
                    <td className="py-2">{row.average_latency_ms.toFixed(1)} ms</td>
                    <td className="py-2">{row.total_tokens.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!perModel.length && <EmptyInline>No model usage has been recorded yet.</EmptyInline>}
        </Panel>

        <Panel title="Failure Log" subtitle="Grouped AI failures across provider calls and task runs.">
          <div className="space-y-2">
            {failures.slice(0, 8).map((failure) => (
              <button
                key={`${failure.task_type || 'usage'}:${failure.error}:${failure.model || 'unknown'}`}
                type="button"
                className="w-full rounded-xl border border-slate/20 bg-white/70 px-3 py-3 text-left dark:border-cyan-900/40 dark:bg-[#072019]/80"
                onClick={onShowFailure}
              >
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-sm font-semibold">
                      {formatTaskTypeLabel(failure.task_type || failure.feature_type || 'request')}
                      {failure.model ? ` · ${failure.model}` : ''}
                    </p>
                    <p className="mt-1 text-sm text-slate dark:text-white/70">{failure.error}</p>
                  </div>
                  <div className="text-right text-xs text-slate dark:text-white/60">
                    <p>{failure.count} hits</p>
                    <p>{failure.last_seen_at ? formatTimestamp(failure.last_seen_at) : 'unknown'}</p>
                  </div>
                </div>
              </button>
            ))}
            {!failures.length && <EmptyInline>No recent AI failures.</EmptyInline>}
          </div>
        </Panel>

        <div className="grid gap-4 lg:grid-cols-2">
          <Panel title="Relevance Distribution" subtitle="Current relevance labels and the feeds producing them.">
            <div className="grid gap-3 sm:grid-cols-4">
              <MiniStat label="High" value={overview.relevance_distribution.high_count} />
              <MiniStat label="Medium" value={overview.relevance_distribution.medium_count} />
              <MiniStat label="Low" value={overview.relevance_distribution.low_count} />
              <MiniStat label="Avg Score" value={overview.relevance_distribution.average_score.toFixed(2)} />
            </div>
            <div className="mt-4 space-y-2">
              {overview.relevance_distribution.by_feed.slice(0, 6).map((feed) => (
                <div key={feed.feed_name} className="rounded-lg border border-slate/10 px-3 py-2 text-sm dark:border-cyan-900/30">
                  <div className="flex items-center justify-between gap-3">
                    <span className="font-semibold">{feed.feed_name}</span>
                    <span className="text-xs text-slate dark:text-white/60">{feed.total_items} items</span>
                  </div>
                  <p className="mt-1 text-xs text-slate dark:text-white/60">
                    High {feed.high_count} · Medium {feed.medium_count} · Low {feed.low_count} · Avg {feed.average_score.toFixed(2)}
                  </p>
                </div>
              ))}
            </div>
          </Panel>

          <Panel title="Token Efficiency" subtitle="Average AI cost profile across successful requests.">
            <div className="grid gap-3 sm:grid-cols-2">
              <MiniStat label="Avg Prompt" value={overview.token_efficiency.average_prompt_tokens.toFixed(1)} />
              <MiniStat label="Avg Completion" value={overview.token_efficiency.average_completion_tokens.toFixed(1)} />
              <MiniStat label="Avg Total" value={overview.token_efficiency.average_total_tokens.toFixed(1)} />
              <MiniStat label="Prompt/Completion" value={overview.token_efficiency.prompt_to_completion_ratio.toFixed(2)} />
            </div>
            <p className="mt-4 text-sm text-slate dark:text-white/70">
              Top expensive feature: {formatTaskTypeLabel(overview.token_efficiency.top_expensive_feature || 'n/a')} (
              {overview.token_efficiency.top_expensive_feature_avg_tokens.toFixed(1)} avg tokens)
            </p>
          </Panel>
        </div>
      </div>

      <div className="space-y-4">
        <Panel title="Readiness" subtitle={readiness ?? 'Loading runtime state...'}>
          <dl className="space-y-2 text-sm">
            <Metric label="Configured" value={settings?.ai_configured ? 'Yes' : 'No'} />
            <Metric label="API Key In Env" value={settings?.api_key_configured ? 'Yes' : 'No / Optional'} />
            <Metric label="Model" value={settings?.model || 'Not configured'} />
          </dl>
        </Panel>

        <Panel title="Currently Running" subtitle="Worker snapshot from Celery inspect plus queued AI runs.">
          <dl className="space-y-2 text-sm">
            <Metric label="Workers" value={overview.live.worker_count} />
            <Metric label="Active" value={overview.live.active_count} />
            <Metric label="Reserved" value={overview.live.reserved_count} />
            <Metric label="Scheduled" value={overview.live.scheduled_count} />
            <Metric label="Queued" value={overview.live.queued_count} />
            <Metric
              label="Oldest queued age"
              value={overview.live.oldest_queued_age_seconds != null ? formatAgeSeconds(overview.live.oldest_queued_age_seconds) : 'n/a'}
            />
          </dl>
          <div className="mt-3 space-y-2">
            {overview.live.active_tasks.slice(0, 4).map((task) => (
              <LiveTaskCard key={`${task.worker_name}:${task.celery_task_id}`} task={task} />
            ))}
            {!overview.live.active_tasks.length && <EmptyInline>No active AI tasks right now.</EmptyInline>}
          </div>
        </Panel>

        <Panel title="Endpoint Health">
          <dl className="space-y-2 text-sm">
            <Metric label="Last success" value={overview.endpoint_health.last_success_at ? formatTimestamp(overview.endpoint_health.last_success_at) : 'Never'} />
            <Metric label="Last error" value={overview.endpoint_health.last_error_at ? formatTimestamp(overview.endpoint_health.last_error_at) : 'None'} />
            <Metric label="Rolling failure rate" value={`${overview.endpoint_health.rolling_failure_rate_pct.toFixed(1)}%`} />
            <Metric label="Median latency" value={`${overview.endpoint_health.median_latency_ms.toFixed(1)} ms`} />
            <Metric label="Timeouts" value={overview.endpoint_health.timeout_failures} />
          </dl>
          {overview.endpoint_health.last_auth_error && (
            <p className="mt-3 rounded border border-amber-500/20 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
              Last auth/provider issue: {overview.endpoint_health.last_auth_error}
            </p>
          )}
          {overview.endpoint_health.last_provider_error && !overview.endpoint_health.last_auth_error && (
            <p className="mt-3 rounded border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-700 dark:text-red-300">
              Last provider error: {overview.endpoint_health.last_provider_error}
            </p>
          )}
        </Panel>

        <Panel title="Feature Health Matrix">
          <div className="space-y-2">
            {overview.feature_health.map((row) => (
              <div key={row.feature_key} className="rounded-lg border border-slate/10 px-3 py-2 text-sm dark:border-cyan-900/30">
                <div className="flex items-center justify-between gap-3">
                  <span className="font-semibold">{formatFeatureKey(row.feature_key)}</span>
                  <StatusPill tone={row.enabled ? 'success' : 'neutral'} label={row.enabled ? 'Enabled' : 'Disabled'} />
                </div>
                <p className="mt-1 text-xs text-slate dark:text-white/60">
                  Last run {row.last_run_at ? formatTimestamp(row.last_run_at) : 'never'} · Last success{' '}
                  {row.last_success_at ? formatTimestamp(row.last_success_at) : 'never'} · Last failure{' '}
                  {row.last_failure_at ? formatTimestamp(row.last_failure_at) : 'never'}
                </p>
              </div>
            ))}
          </div>
        </Panel>

        <Panel title="Coverage & Freshness">
          <dl className="space-y-2 text-sm">
            <Metric label="Eligible items" value={overview.coverage.eligible_items} />
            <Metric label="Enriched" value={overview.coverage.enriched_items} />
            <Metric label="Pending" value={overview.coverage.pending_items} />
            <Metric label="Failed" value={overview.coverage.failed_items} />
            <Metric label="No article" value={overview.coverage.skipped_no_article_count} />
            <Metric label="AI disabled skips" value={overview.coverage.skipped_ai_disabled_count} />
            <Metric label="Config skips" value={overview.coverage.skipped_not_configured_count} />
            <Metric label="Auto-enrich off skips" value={overview.coverage.skipped_auto_enrich_disabled_count} />
            <Metric label="Unchanged skips" value={overview.coverage.skipped_unchanged_count} />
            <Metric label="Oldest pending" value={overview.coverage.oldest_pending_at ? formatTimestamp(overview.coverage.oldest_pending_at) : 'n/a'} />
            <Metric label="Last enrichment" value={overview.coverage.last_successful_enrichment_at ? formatTimestamp(overview.coverage.last_successful_enrichment_at) : 'Never'} />
            <Metric label="Last daily brief" value={overview.coverage.last_successful_daily_brief_at ? formatTimestamp(overview.coverage.last_successful_daily_brief_at) : 'Never'} />
          </dl>
        </Panel>

        <Panel title="Cache / No-op">
          <dl className="space-y-2 text-sm">
            <Metric label="Reused" value={overview.cache.reused_count} />
            <Metric label="Recomputed" value={overview.cache.recomputed_count} />
            <Metric label="No-op rate" value={`${overview.cache.no_op_rate_pct.toFixed(1)}%`} />
          </dl>
        </Panel>

        <Panel title="Storage / Retention">
          <dl className="space-y-2 text-sm">
            <Metric label="Retained briefs" value={`${overview.storage.retained_daily_briefs}/${overview.storage.daily_brief_history_limit}`} />
            <Metric label="Enrichment rows" value={overview.storage.enrichment_rows} />
            <Metric label="Usage rows" value={overview.storage.usage_event_rows} />
            <Metric label="Task history rows" value={overview.storage.task_history_rows} />
            <Metric label="Growth 7d" value={overview.storage.growth_last_7d} />
            <Metric label="Growth 30d" value={overview.storage.growth_last_30d} />
          </dl>
        </Panel>

        <Panel title="Prompt History" subtitle="Most recent prompt/config changes.">
          <AuditPreviewList entries={promptHistory.slice(0, 4)} emptyLabel="No AI prompt changes yet." />
        </Panel>

        <Panel title="Manual Actions" subtitle="Recent admin-triggered AI actions.">
          <AuditPreviewList entries={manualActions.slice(0, 4)} emptyLabel="No manual AI actions yet." />
        </Panel>

        {latestGeneratedBrief && (
          <Panel title="Latest Generated Brief">
            <p className="text-sm font-semibold">{latestGeneratedBrief.title || 'Daily Brief'}</p>
            <p className="mt-1 text-xs text-slate dark:text-white/60">
              Generated {formatTimestamp(latestGeneratedBrief.generated_at)} for {latestGeneratedBrief.item_count} items.
            </p>
            {latestGeneratedBrief.brief_text && <p className="mt-2 text-sm text-slate dark:text-white/70">{latestGeneratedBrief.brief_text}</p>}
          </Panel>
        )}
      </div>
    </div>
  )
}

function RunsTab({
  days,
  selectedModel,
  filters,
  setFilters,
  runPage,
  setRunPage,
  runsQuery,
  selectedRunId,
  setSelectedRunId,
  runDetailQuery,
  briefSources,
  manualActions,
  promptHistory,
}: {
  days: number
  selectedModel: string
  filters: RunFilters
  setFilters: Dispatch<SetStateAction<RunFilters>>
  runPage: number
  setRunPage: Dispatch<SetStateAction<number>>
  runsQuery: ReturnType<typeof useQuery<AITaskRunListResponse>>
  selectedRunId: string | null
  setSelectedRunId: Dispatch<SetStateAction<string | null>>
  runDetailQuery: ReturnType<typeof useQuery<AITaskRunDetailResponse>>
  briefSources: AIDailyBriefSourceItemResponse[]
  manualActions: AIAuditEntryResponse[]
  promptHistory: AIAuditEntryResponse[]
}) {
  const selectedRun = runDetailQuery.data?.run
  const totalPages = Math.max(1, Math.ceil((runsQuery.data?.total ?? 0) / RUN_PAGE_SIZE))

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(340px,1fr)]">
      <div className="space-y-4">
        <Panel title="Task History" subtitle="Every AI run across enrichment, daily briefs, connection tests, and reprocess jobs.">
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
            <select
              value={filters.taskType}
              onChange={(event) => {
                setRunPage(0)
                setFilters((current) => ({ ...current, taskType: event.target.value }))
              }}
              className="rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
            >
              <option value="">All task types</option>
              <option value="item_enrichment">Item Enrichment</option>
              <option value="daily_brief">Daily Brief</option>
              <option value="connection_test">Connection Test</option>
              <option value="reprocess">Reprocess</option>
            </select>
            <select
              value={filters.status}
              onChange={(event) => {
                setRunPage(0)
                setFilters((current) => ({ ...current, status: event.target.value }))
              }}
              className="rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
            >
              <option value="">All statuses</option>
              <option value="queued">Queued</option>
              <option value="running">Running</option>
              <option value="ready">Ready</option>
              <option value="error">Error</option>
              <option value="skipped">Skipped</option>
            </select>
            <select
              value={filters.triggerSource}
              onChange={(event) => {
                setRunPage(0)
                setFilters((current) => ({ ...current, triggerSource: event.target.value }))
              }}
              className="rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
            >
              <option value="">All triggers</option>
              <option value="auto">Auto</option>
              <option value="manual">Manual</option>
              <option value="scheduled">Scheduled</option>
            </select>
            <label className="flex items-center gap-2 rounded border border-slate/20 bg-white/70 px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]/80">
              <input
                type="checkbox"
                checked={filters.onlyFailures}
                onChange={(event) => {
                  setRunPage(0)
                  setFilters((current) => ({ ...current, onlyFailures: event.target.checked }))
                }}
              />
              Failures only
            </label>
            <div className="rounded border border-slate/20 bg-slate/5 px-3 py-2 text-sm text-slate dark:border-cyan-900/40 dark:bg-white/[0.03] dark:text-white/65">
              Window {days}d{selectedModel !== 'all' ? ` · ${selectedModel}` : ''}
            </div>
          </div>

          {runsQuery.isLoading && <p className="mt-3 text-sm text-slate dark:text-white/70">Loading AI runs...</p>}
          {runsQuery.isError && (
            <p className="mt-3 text-sm text-red-600">
              Failed to load AI runs. {(runsQuery.error as Error | undefined)?.message ?? ''}
            </p>
          )}

          <div className="mt-4 overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="text-left text-xs uppercase tracking-wide text-slate dark:text-white/55">
                <tr>
                  <th className="pb-2">Type</th>
                  <th className="pb-2">Trigger</th>
                  <th className="pb-2">Queued</th>
                  <th className="pb-2">Finished</th>
                  <th className="pb-2">Duration</th>
                  <th className="pb-2">Status</th>
                  <th className="pb-2">Worker</th>
                  <th className="pb-2">Model</th>
                  <th className="pb-2">Tokens</th>
                  <th className="pb-2">Error</th>
                </tr>
              </thead>
              <tbody>
                {runsQuery.data?.items.map((run) => (
                  <tr
                    key={run.id}
                    className={`cursor-pointer border-t border-slate/10 text-slate dark:border-cyan-900/30 dark:text-white/80 ${
                      selectedRunId === run.id ? 'bg-cyan/5 dark:bg-cyan/10' : ''
                    }`}
                    onClick={() => setSelectedRunId(run.id)}
                  >
                    <td className="py-2 font-medium">{formatTaskTypeLabel(run.task_type)}</td>
                    <td className="py-2">{formatTriggerLabel(run.trigger_source)}</td>
                    <td className="py-2">{formatTimestamp(run.queued_at)}</td>
                    <td className="py-2">{run.finished_at ? formatTimestamp(run.finished_at) : 'In progress'}</td>
                    <td className="py-2">{formatDuration(run.duration_ms)}</td>
                    <td className="py-2">
                      <StatusPill tone={statusTone(run.status)} label={formatStatusLabel(run.status)} />
                    </td>
                    <td className="py-2">{run.worker_name || 'api'}</td>
                    <td className="py-2">{run.model || 'n/a'}</td>
                    <td className="py-2">{run.total_tokens?.toLocaleString() || 'n/a'}</td>
                    <td className="py-2 text-xs text-slate dark:text-white/60">{truncate(run.error || run.reason || '', 36) || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {!runsQuery.data?.items.length && <EmptyInline>No AI runs matched the current filters.</EmptyInline>}

          <div className="mt-4 flex items-center justify-between gap-3 text-sm">
            <span className="text-slate dark:text-white/60">
              Showing {runsQuery.data?.items.length ?? 0} of {runsQuery.data?.total ?? 0}
            </span>
            <div className="flex items-center gap-2">
              <button
                type="button"
                className="rounded border border-slate/30 px-3 py-2 disabled:opacity-50 dark:border-cyan-900/40"
                onClick={() => setRunPage((current) => Math.max(0, current - 1))}
                disabled={runPage === 0}
              >
                Previous
              </button>
              <span>
                Page {runPage + 1} / {totalPages}
              </span>
              <button
                type="button"
                className="rounded border border-slate/30 px-3 py-2 disabled:opacity-50 dark:border-cyan-900/40"
                onClick={() => setRunPage((current) => Math.min(totalPages - 1, current + 1))}
                disabled={runPage >= totalPages - 1}
              >
                Next
              </button>
            </div>
          </div>
        </Panel>

        <div className="grid gap-4 lg:grid-cols-2">
          <Panel title="Manual Actions" subtitle="Recent admin-triggered AI actions.">
            <AuditPreviewList entries={manualActions} emptyLabel="No manual actions yet." />
          </Panel>
          <Panel title="Prompt History" subtitle="Recent AI configuration and prompt changes.">
            <AuditPreviewList entries={promptHistory} emptyLabel="No AI prompt changes yet." />
          </Panel>
        </div>
      </div>

      <div className="space-y-4">
        <Panel title="Run Detail" subtitle="Selected run timeline, metadata, and related sources.">
          {runDetailQuery.isLoading && <p className="text-sm text-slate dark:text-white/70">Loading run detail...</p>}
          {runDetailQuery.isError && (
            <p className="text-sm text-red-600">
              Failed to load run detail. {(runDetailQuery.error as Error | undefined)?.message ?? ''}
            </p>
          )}
          {!selectedRun && <EmptyInline>Select a run to inspect it.</EmptyInline>}
          {selectedRun && (
            <div className="space-y-4">
              <div className="rounded-xl border border-slate/20 bg-white/70 p-3 dark:border-cyan-900/40 dark:bg-[#072019]/80">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <p className="text-sm font-semibold">{formatTaskTypeLabel(selectedRun.task_type)}</p>
                    <p className="text-xs text-slate dark:text-white/60">
                      {formatTriggerLabel(selectedRun.trigger_source)} · {selectedRun.actor_email || selectedRun.worker_name || 'system'}
                    </p>
                  </div>
                  <StatusPill tone={statusTone(selectedRun.status)} label={formatStatusLabel(selectedRun.status)} />
                </div>
                <dl className="mt-3 space-y-2 text-sm">
                  <Metric label="Queued" value={formatTimestamp(selectedRun.queued_at)} />
                  <Metric label="Started" value={selectedRun.started_at ? formatTimestamp(selectedRun.started_at) : 'n/a'} />
                  <Metric label="Finished" value={selectedRun.finished_at ? formatTimestamp(selectedRun.finished_at) : 'n/a'} />
                  <Metric label="Duration" value={formatDuration(selectedRun.duration_ms)} />
                  <Metric label="Worker" value={selectedRun.worker_name || 'api'} />
                  <Metric label="Model" value={selectedRun.model || 'n/a'} />
                  <Metric label="Prompt size" value={selectedRun.prompt_char_count ?? 'n/a'} />
                  <Metric label="Response size" value={selectedRun.response_char_count ?? 'n/a'} />
                  <Metric label="Input text chars" value={selectedRun.input_text_chars ?? 'n/a'} />
                  <Metric label="Tokens" value={selectedRun.total_tokens ?? 'n/a'} />
                </dl>
                {selectedRun.reason && (
                  <p className="mt-3 rounded border border-slate/20 bg-slate/5 px-3 py-2 text-xs text-slate dark:border-cyan-900/40 dark:bg-white/[0.03] dark:text-white/70">
                    Reason: {selectedRun.reason}
                  </p>
                )}
                {selectedRun.error && (
                  <p className="mt-3 rounded border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-700 dark:text-red-300">
                    {selectedRun.error}
                  </p>
                )}
                {selectedRun.task_type === 'reprocess' && (
                  <div className="mt-3">
                    <p className="text-xs font-semibold uppercase tracking-wide text-slate dark:text-white/55">Progress</p>
                    <ProgressBar
                      className="mt-2"
                      value={selectedRun.processed_count}
                      max={selectedRun.target_count || Math.max(selectedRun.processed_count, 1)}
                    />
                    <p className="mt-2 text-xs text-slate dark:text-white/60">
                      Processed {selectedRun.processed_count}/{selectedRun.target_count ?? '?'} · Success {selectedRun.success_count} ·
                      Errors {selectedRun.error_count} · Skipped {selectedRun.skipped_count}
                    </p>
                  </div>
                )}
              </div>

              <div>
                <p className="text-xs font-semibold uppercase tracking-wide text-slate dark:text-white/55">Event Timeline</p>
                <div className="mt-2 space-y-2">
                  {runDetailQuery.data?.events.map((event) => (
                    <div key={event.id} className="rounded-lg border border-slate/10 px-3 py-2 text-sm dark:border-cyan-900/30">
                      <div className="flex items-center justify-between gap-3">
                        <span className="font-semibold">{event.event_type}</span>
                        <span className="text-xs text-slate dark:text-white/60">{formatTimestamp(event.created_at)}</span>
                      </div>
                      {event.message && <p className="mt-1 text-sm text-slate dark:text-white/70">{event.message}</p>}
                    </div>
                  ))}
                </div>
              </div>

              {Object.keys(selectedRun.metadata || {}).length > 0 && (
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wide text-slate dark:text-white/55">Request / Response Summary</p>
                  <div className="mt-2 rounded-xl border border-slate/20 bg-white/70 p-3 text-xs dark:border-cyan-900/40 dark:bg-[#072019]/80">
                    <dl className="space-y-2">
                      {Object.entries(selectedRun.metadata).map(([key, value]) => (
                        <Metric key={key} label={humanizeKey(key)} value={formatMetadataValue(value)} />
                      ))}
                    </dl>
                  </div>
                </div>
              )}

              {selectedRun.daily_brief_id && (
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wide text-slate dark:text-white/55">Daily Brief Source Items</p>
                  <div className="mt-2 space-y-2">
                    {briefSources.map((source) => (
                      <div key={source.id} className="rounded-lg border border-slate/10 px-3 py-2 text-sm dark:border-cyan-900/30">
                        <div className="flex items-start justify-between gap-3">
                          <div>
                            <p className="font-semibold">{source.title_snapshot}</p>
                            <p className="mt-1 text-xs text-slate dark:text-white/60">
                              {source.feed_name_snapshot || 'Unknown feed'}
                              {source.classification_snapshot ? ` · ${source.classification_snapshot}` : ''}
                            </p>
                          </div>
                          <StatusPill tone={source.included ? 'success' : 'neutral'} label={source.included ? 'Included' : 'Excluded'} />
                        </div>
                        {!source.included && source.exclusion_reason && (
                          <p className="mt-2 text-xs text-slate dark:text-white/60">Reason: {source.exclusion_reason}</p>
                        )}
                      </div>
                    ))}
                    {!briefSources.length && <EmptyInline>No source log recorded for this brief.</EmptyInline>}
                  </div>
                </div>
              )}
            </div>
          )}
        </Panel>
      </div>
    </div>
  )
}

function ConfigurationTab({
  draft,
  setDraft,
  settings,
  readiness,
  isLoading,
  isError,
  errorMessage,
  savePending,
  onSave,
  reprocessDays,
  reprocessLimit,
  setReprocessDays,
  setReprocessLimit,
}: {
  draft: AISettingsDraft
  setDraft: Dispatch<SetStateAction<AISettingsDraft>>
  settings: AISettings | undefined
  readiness: string | null
  isLoading: boolean
  isError: boolean
  errorMessage: string
  savePending: boolean
  onSave: () => void
  reprocessDays: string
  reprocessLimit: string
  setReprocessDays: Dispatch<SetStateAction<string>>
  setReprocessLimit: Dispatch<SetStateAction<string>>
}) {
  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(320px,1fr)]">
      <div className="space-y-4">
        {isLoading && (
          <div className="rounded-xl border border-slate/20 bg-white/80 p-4 text-sm dark:border-cyan-900/40 dark:bg-[#041612]/90">
            Loading AI settings...
          </div>
        )}
        {isError && (
          <div className="rounded-xl border border-red-500/20 bg-red-500/10 p-4 text-sm text-red-700 dark:text-red-300">
            Failed to load AI settings. {errorMessage}
          </div>
        )}

        <Panel title="Provider" subtitle="ThreatLens currently speaks to one OpenAI-compatible chat endpoint. Secrets stay in the environment.">
          <div className="grid gap-3 md:grid-cols-2">
            <Field label="Base URL">
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={draft.base_url}
                onChange={(event) => updateDraft(setDraft, 'base_url', event.target.value)}
                placeholder="http://localhost:11434/v1"
              />
            </Field>
            <Field label="Model">
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={draft.model}
                onChange={(event) => updateDraft(setDraft, 'model', event.target.value)}
                placeholder="local-threat-model"
              />
            </Field>
            <Field label="Temperature">
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={draft.temperature}
                onChange={(event) => updateDraft(setDraft, 'temperature', event.target.value)}
                inputMode="decimal"
              />
            </Field>
            <Field label="Max Completion Tokens">
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={draft.max_completion_tokens}
                onChange={(event) => updateDraft(setDraft, 'max_completion_tokens', event.target.value)}
                inputMode="numeric"
              />
            </Field>
            <Field label="Request Timeout Seconds" className="md:col-span-2">
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={draft.request_timeout_seconds}
                onChange={(event) => updateDraft(setDraft, 'request_timeout_seconds', event.target.value)}
                inputMode="numeric"
              />
            </Field>
          </div>
        </Panel>

        <Panel title="Feature Controls">
          <div className="grid gap-3 md:grid-cols-2">
            <CheckboxRow label="AI article summaries" checked={draft.summary_enabled} onChange={(checked) => updateDraft(setDraft, 'summary_enabled', checked)} />
            <CheckboxRow label="AI relevance scoring" checked={draft.relevance_enabled} onChange={(checked) => updateDraft(setDraft, 'relevance_enabled', checked)} />
            <CheckboxRow label="Daily brief widget" checked={draft.daily_brief_enabled} onChange={(checked) => updateDraft(setDraft, 'daily_brief_enabled', checked)} />
            <CheckboxRow label="Auto-enrich new items" checked={draft.auto_enrich_new_items} onChange={(checked) => updateDraft(setDraft, 'auto_enrich_new_items', checked)} />
            <Field label="Medium Relevance Threshold">
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={draft.relevance_medium_threshold}
                onChange={(event) => updateDraft(setDraft, 'relevance_medium_threshold', event.target.value)}
                inputMode="decimal"
              />
            </Field>
            <Field label="High Relevance Threshold">
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={draft.relevance_high_threshold}
                onChange={(event) => updateDraft(setDraft, 'relevance_high_threshold', event.target.value)}
                inputMode="decimal"
              />
            </Field>
            <Field label="Daily Brief Window Hours">
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={draft.daily_brief_window_hours}
                onChange={(event) => updateDraft(setDraft, 'daily_brief_window_hours', event.target.value)}
                inputMode="numeric"
              />
            </Field>
            <Field label="Daily Brief Max Items">
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={draft.daily_brief_max_items}
                onChange={(event) => updateDraft(setDraft, 'daily_brief_max_items', event.target.value)}
                inputMode="numeric"
              />
            </Field>
            <Field label="Retained Daily Briefings">
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={draft.daily_brief_history_limit}
                onChange={(event) => updateDraft(setDraft, 'daily_brief_history_limit', event.target.value)}
                inputMode="numeric"
              />
              <span className="mt-1 block text-xs text-slate dark:text-white/60">
                Keep only the most recent X daily briefings for dashboard selection.
              </span>
            </Field>
          </div>
        </Panel>

        <Panel title="Company Context" subtitle="This context is global so relevance scoring stays consistent across users.">
          <div className="grid gap-3 md:grid-cols-2">
            <Field label="Company Name">
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={draft.company_name}
                onChange={(event) => updateDraft(setDraft, 'company_name', event.target.value)}
              />
            </Field>
            <Field label="Industry">
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={draft.company_industry}
                onChange={(event) => updateDraft(setDraft, 'company_industry', event.target.value)}
              />
            </Field>
            <TextAreaList label="Regions" value={draft.company_regions} placeholder="US&#10;EU" onChange={(value) => updateDraft(setDraft, 'company_regions', value)} />
            <TextAreaList label="Technology Stack" value={draft.company_stack} placeholder="Fortinet&#10;Microsoft 365&#10;Okta" onChange={(value) => updateDraft(setDraft, 'company_stack', value)} />
            <TextAreaList label="Priority Topics" value={draft.company_priority_topics} placeholder="edge security&#10;identity" onChange={(value) => updateDraft(setDraft, 'company_priority_topics', value)} />
            <TextAreaList label="Keywords" value={draft.company_keywords} placeholder="vpn&#10;sso&#10;exchange" onChange={(value) => updateDraft(setDraft, 'company_keywords', value)} />
            <TextAreaList label="Exclusions" value={draft.company_exclusions} placeholder="consumer scams&#10;gaming malware" onChange={(value) => updateDraft(setDraft, 'company_exclusions', value)} />
            <Field label="Additional Company Context" className="md:col-span-2">
              <textarea
                className="mt-1 h-28 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                value={draft.company_profile_text}
                onChange={(event) => updateDraft(setDraft, 'company_profile_text', event.target.value)}
                placeholder="Describe the defended environment, the systems you care about, and what should be treated as especially relevant."
              />
            </Field>
          </div>
        </Panel>

        <Panel title="Prompt Tuning" subtitle="Built-in defaults stay visible here, but you can edit and save them directly.">
          <div className="grid gap-3">
            <PromptArea label="Item Enrichment System Prompt" value={draft.item_enrichment_system_prompt} onChange={(value) => updateDraft(setDraft, 'item_enrichment_system_prompt', value)} placeholder="Base system prompt for article summaries and relevance scoring." />
            <PromptArea label="Daily Brief System Prompt" value={draft.daily_brief_system_prompt} onChange={(value) => updateDraft(setDraft, 'daily_brief_system_prompt', value)} placeholder="Base system prompt for daily brief generation." />
            <PromptArea label="Global Instructions" value={draft.global_instructions} onChange={(value) => updateDraft(setDraft, 'global_instructions', value)} placeholder="Instructions applied to every AI request." />
            <PromptArea label="Item Summary Instructions" value={draft.item_summary_instructions} onChange={(value) => updateDraft(setDraft, 'item_summary_instructions', value)} placeholder="Guide the style and focus of article summaries." />
            <PromptArea label="Relevance Instructions" value={draft.relevance_instructions} onChange={(value) => updateDraft(setDraft, 'relevance_instructions', value)} placeholder="Explain how relevance should be interpreted for this environment." />
            <PromptArea label="Daily Brief Instructions" value={draft.daily_brief_instructions} onChange={(value) => updateDraft(setDraft, 'daily_brief_instructions', value)} placeholder="Guide the tone and structure of the daily brief." />
          </div>
        </Panel>
      </div>

      <div className="space-y-4">
        <Panel title="Readiness" subtitle={readiness ?? 'Loading runtime state...'}>
          <dl className="space-y-2 text-sm">
            <Metric label="Configured" value={settings?.ai_configured ? 'Yes' : 'No'} />
            <Metric label="API Key In Env" value={settings?.api_key_configured ? 'Yes' : 'No / Optional'} />
            <Metric label="Created" value={settings?.created_at ? formatTimestamp(settings.created_at) : 'n/a'} />
            <Metric label="Updated" value={settings?.updated_at ? formatTimestamp(settings.updated_at) : 'n/a'} />
          </dl>
        </Panel>

        <Panel title="Reprocess Defaults" subtitle="Header actions use these values when you queue a reprocess job.">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Days">
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                value={reprocessDays}
                onChange={(event) => setReprocessDays(event.target.value)}
                inputMode="numeric"
              />
            </Field>
            <Field label="Limit">
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                value={reprocessLimit}
                onChange={(event) => setReprocessLimit(event.target.value)}
                inputMode="numeric"
              />
            </Field>
          </div>
        </Panel>

        <div className="sticky top-4 rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
          <h3 className="font-display text-lg">Save</h3>
          <p className="mt-1 text-sm text-slate dark:text-white/70">
            Configuration changes affect future AI runs and are recorded in prompt history.
          </p>
          <button
            type="button"
            className="mt-4 w-full rounded bg-ink px-3 py-2 text-sm font-semibold text-white disabled:opacity-50 dark:bg-cyan dark:text-slate-950"
            onClick={onSave}
            disabled={savePending}
          >
            {savePending ? 'Saving...' : 'Save Settings'}
          </button>
        </div>
      </div>
    </div>
  )
}

function Panel({
  title,
  subtitle,
  children,
}: {
  title: string
  subtitle?: string
  children: React.ReactNode
}) {
  return (
    <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
      <h3 className="font-display text-lg">{title}</h3>
      {subtitle && <p className="mt-1 text-sm text-slate dark:text-white/70">{subtitle}</p>}
      <div className="mt-3">{children}</div>
    </section>
  )
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-full px-3 py-2 text-sm font-semibold transition ${
        active
          ? 'bg-ink text-white dark:bg-cyan dark:text-slate-950'
          : 'border border-slate/20 bg-white/70 text-slate dark:border-cyan-900/40 dark:bg-[#072019]/80 dark:text-white/75'
      }`}
    >
      {children}
    </button>
  )
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-slate/20 bg-white/80 px-4 py-3 dark:border-cyan-900/40 dark:bg-[#041612]/90">
      <p className="text-xs uppercase tracking-wide text-slate dark:text-white/55">{label}</p>
      <p className="mt-1 text-xl font-semibold">{value}</p>
    </div>
  )
}

function MiniStat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg border border-slate/10 bg-slate/5 px-3 py-2 dark:border-cyan-900/30 dark:bg-white/[0.03]">
      <p className="text-xs uppercase tracking-wide text-slate dark:text-white/55">{label}</p>
      <p className="mt-1 text-base font-semibold">{value}</p>
    </div>
  )
}

function Metric({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <dt className="text-slate dark:text-white/65">{label}</dt>
      <dd className="text-right font-medium">{value}</dd>
    </div>
  )
}

function StatusPill({ label, tone }: { label: string; tone: 'success' | 'warning' | 'danger' | 'neutral' }) {
  const toneClass =
    tone === 'success'
      ? 'border-emerald-500/20 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
      : tone === 'warning'
        ? 'border-amber-500/20 bg-amber-500/10 text-amber-700 dark:text-amber-300'
        : tone === 'danger'
          ? 'border-red-500/20 bg-red-500/10 text-red-700 dark:text-red-300'
          : 'border-slate/20 bg-slate/10 text-slate-700 dark:text-white/70'
  return <span className={`rounded-full border px-2 py-1 text-xs font-semibold uppercase tracking-wide ${toneClass}`}>{label}</span>
}

function ProgressBar({ value, max, className = '' }: { value: number; max: number; className?: string }) {
  const pct = max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0
  return (
    <div className={`h-2 rounded-full bg-slate-200 dark:bg-[#072019] ${className}`}>
      <div className="h-2 rounded-full bg-cyan" style={{ width: `${pct}%` }} />
    </div>
  )
}

function TimeSeriesBars({
  points,
  valueKey,
  accentClass,
  secondaryKey,
  secondaryClass,
}: {
  points: AIOpsOverviewResponse['time_series']
  valueKey: 'requests' | 'total_tokens'
  accentClass: string
  secondaryKey?: 'failures'
  secondaryClass?: string
}) {
  const maxPrimary = Math.max(...points.map((point) => Number(point[valueKey]) || 0), 1)
  const maxSecondary = secondaryKey ? Math.max(...points.map((point) => Number(point[secondaryKey]) || 0), 1) : 1

  return (
    <div className="space-y-2">
      <div className="flex h-36 items-end gap-1">
        {points.map((point) => {
          const primaryHeight = `${Math.max(4, ((Number(point[valueKey]) || 0) / maxPrimary) * 100)}%`
          const secondaryHeight = secondaryKey ? `${Math.max(0, ((Number(point[secondaryKey]) || 0) / maxSecondary) * 38)}%` : '0%'
          return (
            <div key={String(point.bucket)} className="flex min-w-0 flex-1 flex-col justify-end gap-1">
              {secondaryKey && secondaryClass && <div className={`rounded-t ${secondaryClass}`} style={{ height: secondaryHeight }} />}
              <div className={`rounded-t ${accentClass}`} style={{ height: primaryHeight }} />
            </div>
          )
        })}
      </div>
      <div className="flex justify-between gap-2 text-[11px] text-slate dark:text-white/55">
        <span>{String(points[0]?.bucket || '')}</span>
        <span>{String(points[points.length - 1]?.bucket || '')}</span>
      </div>
    </div>
  )
}

function LiveTaskCard({ task }: { task: AILiveTaskResponse }) {
  return (
    <div className="rounded-lg border border-slate/10 px-3 py-2 text-sm dark:border-cyan-900/30">
      <div className="flex items-center justify-between gap-3">
        <span className="font-semibold">{formatTaskTypeLabel(task.task_name)}</span>
        <span className="text-xs text-slate dark:text-white/60">{task.worker_name}</span>
      </div>
      <p className="mt-1 text-xs text-slate dark:text-white/60">
        {task.state}
        {task.eta ? ` · eta ${task.eta}` : ''}
        {task.received_at ? ` · received ${task.received_at}` : ''}
      </p>
    </div>
  )
}

function AuditPreviewList({ entries, emptyLabel }: { entries: AIAuditEntryResponse[]; emptyLabel: string }) {
  if (!entries.length) {
    return <EmptyInline>{emptyLabel}</EmptyInline>
  }
  return (
    <div className="space-y-2">
      {entries.map((entry) => (
        <div key={entry.id} className="rounded-lg border border-slate/10 px-3 py-2 text-sm dark:border-cyan-900/30">
          {(() => {
            const changedFields = Array.isArray(entry.metadata.changed_fields)
              ? (entry.metadata.changed_fields as string[])
              : null
            return (
              <>
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="font-semibold">{entry.action}</p>
              <p className="mt-1 text-xs text-slate dark:text-white/60">{entry.actor_email || 'system'}</p>
            </div>
            <span className="text-xs text-slate dark:text-white/60">{formatTimestamp(entry.created_at)}</span>
          </div>
          {changedFields && (
            <p className="mt-2 text-xs text-slate dark:text-white/60">
              Changed: {changedFields.join(', ')}
            </p>
          )}
              </>
            )
          })()}
        </div>
      ))}
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
    <Field label={label}>
      <textarea
        className="mt-1 h-24 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
      />
    </Field>
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
    <Field label={label}>
      <textarea
        className="mt-1 h-28 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
      />
    </Field>
  )
}

function Field({
  label,
  children,
  className = '',
}: {
  label: string
  children: React.ReactNode
  className?: string
}) {
  return (
    <label className={`text-sm ${className}`}>
      <span className="font-semibold">{label}</span>
      {children}
    </label>
  )
}

function EmptyInline({ children }: { children: React.ReactNode }) {
  return <p className="text-sm text-slate dark:text-white/60">{children}</p>
}

function invalidateAiQueries(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: ['ai'] })
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

function formatTimestamp(value: string | null | undefined) {
  if (!value) {
    return 'unknown'
  }
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) {
    return value
  }
  return parsed.toLocaleString()
}

function formatTaskTypeLabel(value: string) {
  if (value === 'item_enrichment') return 'Item Enrichment'
  if (value === 'daily_brief') return 'Daily Brief'
  if (value === 'connection_test') return 'Connection Test'
  if (value === 'reprocess') return 'Reprocess'
  return value
}

function formatTriggerLabel(value: string) {
  if (value === 'auto') return 'Auto'
  if (value === 'manual') return 'Manual'
  if (value === 'scheduled') return 'Scheduled'
  return value
}

function formatStatusLabel(value: string) {
  if (value === 'ready') return 'Ready'
  if (value === 'error') return 'Error'
  if (value === 'queued') return 'Queued'
  if (value === 'running') return 'Running'
  if (value === 'skipped') return 'Skipped'
  return value
}

function formatFeatureKey(value: string) {
  if (value === 'daily_brief') return 'Daily Brief'
  if (value === 'auto_enrichment') return 'Auto-Enrichment'
  return value.replace(/_/g, ' ').replace(/\b\w/g, (char) => char.toUpperCase())
}

function statusTone(value: string): 'success' | 'warning' | 'danger' | 'neutral' {
  if (value === 'ready') return 'success'
  if (value === 'error') return 'danger'
  if (value === 'running' || value === 'queued') return 'warning'
  return 'neutral'
}

function formatAgeSeconds(value: number) {
  if (value < 60) return `${value}s`
  if (value < 3600) return `${Math.round(value / 60)}m`
  return `${(value / 3600).toFixed(1)}h`
}

function formatDuration(value: number | null) {
  if (value == null) return 'n/a'
  if (value < 1000) return `${value} ms`
  if (value < 60_000) return `${(value / 1000).toFixed(1)} s`
  return `${(value / 60_000).toFixed(1)} min`
}

function remainingCount(run: AITaskRunResponse) {
  if (!run.target_count) {
    return '?'
  }
  return Math.max(0, run.target_count - run.processed_count)
}

function truncate(value: string, max: number) {
  if (!value) return ''
  if (value.length <= max) return value
  return `${value.slice(0, max - 1)}…`
}

function humanizeKey(value: string) {
  return value.replace(/_/g, ' ').replace(/\b\w/g, (char) => char.toUpperCase())
}

function formatMetadataValue(value: unknown) {
  if (typeof value === 'boolean') {
    return value ? 'Yes' : 'No'
  }
  if (Array.isArray(value)) {
    return value.join(', ')
  }
  if (value == null) {
    return 'n/a'
  }
  if (typeof value === 'object') {
    return JSON.stringify(value)
  }
  return String(value)
}
