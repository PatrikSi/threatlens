import { type Dispatch, type SetStateAction } from 'react'

import { AISettings, AIOpsOverviewResponse } from '../types/api'
import {
  EmptyInline,
  LiveTaskCard,
  Metric,
  MiniStat,
  OverviewSection,
  Panel,
  StatCard,
  StatusPill,
  TimeSeriesBars,
} from './aiSettingsSupport'
import {
  formatAgeSeconds,
  formatFeatureKey,
  formatTaskTypeLabel,
  formatTimestamp,
} from './aiSettingsUtils'

export function OverviewTab({
  settings,
  readiness,
  overview,
  isLoading,
  isError,
  errorMessage,
  days,
  setDays,
  onRefresh,
}: {
  settings: AISettings | undefined
  readiness: string | null
  overview: AIOpsOverviewResponse | undefined
  isLoading: boolean
  isError: boolean
  errorMessage: string
  days: number
  setDays: Dispatch<SetStateAction<number>>
  onRefresh: () => void
}) {
  if (isLoading && !overview) {
    return <Panel title="Overview">Loading AI analytics...</Panel>
  }

  if (isError && !overview) {
    return <Panel title="Overview">{errorMessage}</Panel>
  }

  if (!overview) {
    return null
  }

  return (
    <div className="space-y-4">
      <Panel title="Overview" subtitle="Start here to see whether AI is healthy, how much it is being used, and where it needs attention.">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <MiniStat label="Model" value={settings?.model || 'Not configured'} />
            <MiniStat label="Requests" value={overview.kpis.total_requests.toLocaleString()} />
            <MiniStat label="Success rate" value={`${overview.kpis.success_rate_pct.toFixed(1)}%`} />
            <MiniStat label="Queued" value={overview.live.queued_count} />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <label className="sr-only" htmlFor="ai-overview-window-days">
              Overview time window
            </label>
            <select
              id="ai-overview-window-days"
              value={days}
              onChange={(event) => setDays(Number(event.target.value))}
              aria-label="Overview time window"
              className="rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
            >
              <option value={1}>Last 24h</option>
              <option value={7}>Last 7d</option>
              <option value={30}>Last 30d</option>
              <option value={90}>Last 90d</option>
            </select>
            <button
              type="button"
              className="rounded border border-slate/30 px-3 py-2 text-sm font-semibold dark:border-cyan-900/40"
              onClick={onRefresh}
            >
              Refresh
            </button>
          </div>
        </div>
      </Panel>

      <OverviewSection
        title="Health"
        description="Use this section to confirm the endpoint is configured, the queue is moving, and problems are visible quickly."
      >
        <div className="grid gap-4 xl:grid-cols-2">
          <Panel title="AI status" subtitle={readiness ?? 'Loading runtime state...'}>
            <dl className="space-y-2 text-sm">
              <Metric label="Configured" value={settings?.ai_configured ? 'Yes' : 'No'} />
              <Metric label="API key in environment" value={settings?.api_key_configured ? 'Yes' : 'No (optional)'} />
              <Metric label="Model" value={settings?.model || 'Not configured'} />
              <Metric label="Retry attempts" value={settings?.request_max_retries ?? 0} />
              <Metric label="Last success" value={overview.endpoint_health.last_success_at ? formatTimestamp(overview.endpoint_health.last_success_at) : 'Never'} />
              <Metric label="Failure rate" value={`${overview.endpoint_health.rolling_failure_rate_pct.toFixed(1)}%`} />
              <Metric label="Median latency" value={`${overview.endpoint_health.median_latency_ms.toFixed(1)} ms`} />
            </dl>
            <div className="mt-3 space-y-2">
              {overview.feature_health.map((row) => (
                <div key={row.feature_key} className="flex items-center justify-between gap-3 rounded-lg border border-slate/10 px-3 py-2 text-sm dark:border-cyan-900/30">
                  <div>
                    <p className="font-semibold">{formatFeatureKey(row.feature_key)}</p>
                    <p className="text-xs text-slate dark:text-white/60">
                      Last success {row.last_success_at ? formatTimestamp(row.last_success_at) : 'never'}
                    </p>
                  </div>
                  <StatusPill tone={row.enabled ? 'success' : 'neutral'} label={row.enabled ? 'Enabled' : 'Disabled'} />
                </div>
              ))}
            </div>
          </Panel>

          <Panel title="Queue snapshot" subtitle="Database-backed snapshot of AI task runs.">
            <dl className="space-y-2 text-sm">
              <Metric label="Known workers" value={overview.live.worker_count} />
              <Metric label="Running" value={overview.live.active_count} />
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
              {!overview.live.active_tasks.length && <EmptyInline>No running AI tasks right now.</EmptyInline>}
            </div>
          </Panel>
        </div>
      </OverviewSection>

      <OverviewSection
        title="Usage"
        description="Volume, token cost, and model performance for the selected time window."
      >
        <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          <StatCard label="Requests" value={overview.kpis.total_requests.toLocaleString()} />
          <StatCard label="Success rate" value={`${overview.kpis.success_rate_pct.toFixed(1)}%`} />
          <StatCard label="Total tokens" value={overview.kpis.total_tokens.toLocaleString()} />
          <StatCard label="Average latency" value={`${overview.kpis.average_latency_ms.toFixed(1)} ms`} />
          <StatCard label="P95 latency" value={`${overview.kpis.p95_latency_ms.toFixed(1)} ms`} />
          <StatCard
            label="Last success"
            value={overview.kpis.last_successful_run_at ? formatTimestamp(overview.kpis.last_successful_run_at) : 'Never'}
          />
        </section>

        <div className="grid gap-4 xl:grid-cols-2">
          <Panel title="Requests and failures over time" subtitle="Recent request volume and failure pressure across the selected window.">
            <TimeSeriesBars
              points={overview.time_series}
              valueKey="requests"
              accentClass="bg-cyan"
              secondaryKey="failures"
              secondaryClass="bg-red-400/80"
            />
          </Panel>

          <Panel title="Per-model usage" subtitle="Requests, success rate, latency, and token footprint by model.">
            <div className="space-y-2 sm:hidden" aria-label="Per-model AI usage records">
              {overview.per_model.map((row) => (
                <article key={row.model} className="rounded-lg border border-slate/15 bg-slate/5 p-3 dark:border-cyan-900/30 dark:bg-white/[0.03]">
                  <div className="flex items-start justify-between gap-3">
                    <h4 className="min-w-0 break-all text-sm font-semibold">{row.model}</h4>
                    <span className="tl-chip tl-chip-neutral shrink-0">{row.success_rate_pct.toFixed(1)}% success</span>
                  </div>
                  <dl className="mt-3 grid grid-cols-3 gap-2 text-xs">
                    <div>
                      <dt className="font-semibold uppercase text-slate dark:text-slate-400">Requests</dt>
                      <dd className="mt-0.5">{row.total_requests}</dd>
                    </div>
                    <div>
                      <dt className="font-semibold uppercase text-slate dark:text-slate-400">Latency</dt>
                      <dd className="mt-0.5">{row.average_latency_ms.toFixed(1)} ms</dd>
                    </div>
                    <div>
                      <dt className="font-semibold uppercase text-slate dark:text-slate-400">Tokens</dt>
                      <dd className="mt-0.5">{row.total_tokens.toLocaleString()}</dd>
                    </div>
                  </dl>
                </article>
              ))}
            </div>
            <div className="hidden overflow-x-auto sm:block">
              <table className="min-w-full text-sm">
                <thead className="text-left text-xs uppercase text-slate dark:text-white/55">
                  <tr>
                    <th scope="col" className="pb-2">Model</th>
                    <th scope="col" className="pb-2">Requests</th>
                    <th scope="col" className="pb-2">Success</th>
                    <th scope="col" className="pb-2">Average latency</th>
                    <th scope="col" className="pb-2">Tokens</th>
                  </tr>
                </thead>
                <tbody>
                  {overview.per_model.map((row) => (
                    <tr key={row.model} className="border-t border-slate/10 text-slate dark:border-cyan-900/30 dark:text-white/80">
                      <td className="py-2 font-semibold">{row.model}</td>
                      <td className="py-2">{row.total_requests}</td>
                      <td className="py-2">{row.success_rate_pct.toFixed(1)}%</td>
                      <td className="py-2">{row.average_latency_ms.toFixed(1)} ms</td>
                      <td className="py-2">{row.total_tokens.toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!overview.per_model.length && <EmptyInline>No model usage has been recorded yet.</EmptyInline>}
          </Panel>
        </div>

        <div className="grid gap-4 xl:grid-cols-2">
          <Panel title="Token usage over time" subtitle="Total tokens by day.">
            <TimeSeriesBars points={overview.time_series} valueKey="total_tokens" accentClass="bg-emerald-500" />
          </Panel>

          <Panel title="Token efficiency" subtitle="Average AI cost profile across successful requests.">
            <div className="grid gap-3 sm:grid-cols-2">
              <MiniStat label="Average prompt tokens" value={overview.token_efficiency.average_prompt_tokens.toFixed(1)} />
              <MiniStat label="Average completion tokens" value={overview.token_efficiency.average_completion_tokens.toFixed(1)} />
              <MiniStat label="Average total tokens" value={overview.token_efficiency.average_total_tokens.toFixed(1)} />
              <MiniStat label="Prompt-to-completion ratio" value={overview.token_efficiency.prompt_to_completion_ratio.toFixed(2)} />
            </div>
            <p className="mt-4 text-sm text-slate dark:text-white/70">
              Top expensive feature: {formatTaskTypeLabel(overview.token_efficiency.top_expensive_feature || 'n/a')} (
              {overview.token_efficiency.top_expensive_feature_avg_tokens.toFixed(1)} avg tokens)
            </p>
          </Panel>
        </div>
      </OverviewSection>

      <OverviewSection
        title="Quality and coverage"
        description="How complete the enrichment pipeline is, what the relevance output looks like, and how much data the AI subsystem retains."
      >
        <div className="grid gap-4 xl:grid-cols-2">
          <Panel title="Coverage and freshness" subtitle="How much content is enriched and whether the pipeline is keeping up.">
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
        </div>

        <div className="grid gap-4 xl:grid-cols-2">
          <Panel title="Relevance distribution" subtitle="Current relevance labels and the feeds producing them.">
            <div className="grid gap-3 sm:grid-cols-4">
              <MiniStat label="High" value={overview.relevance_distribution.high_count} />
              <MiniStat label="Medium" value={overview.relevance_distribution.medium_count} />
              <MiniStat label="Low" value={overview.relevance_distribution.low_count} />
              <MiniStat label="Average score" value={overview.relevance_distribution.average_score.toFixed(2)} />
            </div>
            <div className="mt-4 space-y-2">
              {overview.relevance_distribution.by_feed.slice(0, 6).map((feed) => (
                <div key={feed.feed_name} className="rounded-lg border border-slate/10 px-3 py-2 text-sm dark:border-cyan-900/30">
                  <div className="flex items-center justify-between gap-3">
                    <span className="font-semibold">{feed.feed_name}</span>
                    <span className="text-xs text-slate dark:text-white/60">{feed.total_items} items</span>
                  </div>
                  <p className="mt-1 text-xs text-slate dark:text-white/60">
                    High {feed.high_count} · Medium {feed.medium_count} · Low {feed.low_count} · Average {feed.average_score.toFixed(2)}
                  </p>
                </div>
              ))}
            </div>
          </Panel>
        </div>

        <div className="grid gap-4 xl:grid-cols-2">
          <Panel title="Cache and no-op activity">
            <dl className="space-y-2 text-sm">
              <Metric label="Reused" value={overview.cache.reused_count} />
              <Metric label="Recomputed" value={overview.cache.recomputed_count} />
              <Metric label="No-op rate" value={`${overview.cache.no_op_rate_pct.toFixed(1)}%`} />
            </dl>
          </Panel>

          <Panel title="Storage and retention">
            <dl className="space-y-2 text-sm">
              <Metric label="Retained briefs" value={`${overview.storage.retained_daily_briefs}/${overview.storage.daily_brief_history_limit}`} />
              <Metric label="Enrichment rows" value={overview.storage.enrichment_rows} />
              <Metric label="Usage rows" value={overview.storage.usage_event_rows} />
              <Metric label="Task history rows" value={overview.storage.task_history_rows} />
              <Metric label="Growth (7 days)" value={overview.storage.growth_last_7d} />
              <Metric label="Growth (30 days)" value={overview.storage.growth_last_30d} />
            </dl>
          </Panel>
        </div>
      </OverviewSection>
    </div>
  )
}
