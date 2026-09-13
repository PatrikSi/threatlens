import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { accessibleQueryData } from '../api/queryData'
import { useCurrentUser } from '../hooks/useCurrentUser'
import type { AISettings, AIOpsOverviewResponse } from '../types/api'
import type { AIStatisticsResponse } from '../types/aiStatistics'
import { hasRequiredPermissions } from '../workspace/workspaceModel'
import { formatDateTime } from '../utils/datetime'
import { OverviewTab } from './AiSettingsOverviewTab'
import { Metric, Panel } from './aiSettingsSupport'

export function AiStatisticsWorkspace() {
  const user = useCurrentUser()
  const allowed = user.data?.role === 'admin' && hasRequiredPermissions(user.data?.access?.permissions ?? [], ['read:ai'])
  if (user.isLoading) return <p role="status">Checking AI statistics access...</p>
  if (!allowed) return <p role="status" className="tl-surface rounded-xl p-4">AI statistics require the administrator role and read access to AI. Ingestion statistics remain available through the other tab.</p>
  if (!user.data?.features.ai_enabled) return <p role="status" className="tl-surface rounded-xl p-4">AI is disabled on this installation. Enable it before loading AI statistics.</p>
  return <AllowedAiStatistics />
}

function AllowedAiStatistics() {
  const [days, setDays] = useState(30)
  const client = useQueryClient()
  const settings = useQuery({ queryKey: ['ai', 'settings'], queryFn: ({ signal }) => apiFetch<AISettings>('/ai/settings', { signal }), staleTime: 60_000 })
  const overview = useQuery({ queryKey: ['ai', 'ops', 'overview', days], queryFn: ({ signal }) => apiFetch<AIOpsOverviewResponse>(`/ai/ops/overview?days=${days}`, { signal }), staleTime: 30_000, refetchInterval: 60_000 })
  const configuration = accessibleQueryData(settings)
  return <div className="space-y-4">
    {settings.isError && <div role="alert" className="rounded border border-amber-500 p-3 text-sm">
      <p>{resolveApiErrorMessage(settings.error, 'Saved AI configuration could not be loaded')}{' '}
        {configuration ? 'Showing previously loaded configuration.' : 'Configuration status is unknown.'}</p>
      <button type="button" className="mt-2 underline" disabled={settings.isFetching}
        onClick={() => { void settings.refetch() }}>Retry AI configuration</button>
    </div>}
    <OverviewTab settings={configuration} overview={accessibleQueryData(overview)} isLoading={overview.isLoading}
      isError={overview.isError} errorMessage={overview.isError ? resolveApiErrorMessage(overview.error, 'AI statistics could not be loaded') : ''}
      days={days} setDays={setDays} onRefresh={() => { void client.invalidateQueries({ queryKey: ['ai', 'ops'] }) }} />
    <AiReliabilityStatistics days={days} />
  </div>
}

export function AiReliabilityStatistics({ days }: { days: number }) {
  const query = useQuery({ queryKey: ['ai', 'ops', 'statistics', days], queryFn: ({ signal }) => apiFetch<AIStatisticsResponse>(`/ai/ops/statistics?days=${days}`, { signal }), staleTime: 30_000, refetchInterval: 60_000 })
  const data = accessibleQueryData(query)
  return <Panel title="AI reliability and workload" subtitle="Provider outcomes and successful-call latency; current queue age is independent of the selected request window.">
    {query.isLoading && <p role="status">Loading AI reliability...</p>}
    {query.isError && <p role="alert">{resolveApiErrorMessage(query.error, 'AI reliability could not be loaded')} {data && 'Previously loaded metrics remain visible.'}</p>}
    <button type="button" disabled={query.isFetching} className="mb-3 rounded border px-3 py-2 text-sm disabled:opacity-50" onClick={() => { void query.refetch() }}>Refresh AI reliability</button>
    {data && <>
      <p className="mb-3 text-xs">Requests recorded from {formatDateTime(data.since)} through {formatDateTime(data.until)}. Successful latency excludes failed and unmeasured calls. Deadlines are a subset of timeouts.</p>
      <div className="overflow-auto" role="region" aria-label="AI feature statistics" tabIndex={0}>
        <table className="min-w-[1000px] w-full text-left text-sm"><caption className="sr-only">AI feature outcomes and usage</caption>
          <thead><tr>{['Feature', 'Outcomes', 'Recorded tokens', 'Missing usage', 'Successful latency', 'Failures'].map((label) => <th key={label} scope="col" className="p-2">{label}</th>)}</tr></thead>
          <tbody>{data.features.map((row) => <tr key={row.feature} className="border-t align-top">
            <th scope="row" className="p-2 font-semibold">{row.feature.replaceAll('_', ' ')}</th>
            <td className="p-2">{row.successful} successful / {row.requests} recorded<p>{row.failed} failed · {row.not_sent} not sent · {row.ambiguous} uncertain</p></td>
            <td className="p-2">{row.total_tokens.toLocaleString()} total<p>{row.prompt_tokens.toLocaleString()} input / {row.completion_tokens.toLocaleString()} output</p></td>
            <td className="p-2">{row.unknown_usage_requests} calls</td>
            <td className="p-2">P50 {latency(row.p50_latency_ms)}<p>P95 {latency(row.p95_latency_ms)} / P99 {latency(row.p99_latency_ms)}</p><p>{row.latency_samples} measurements</p></td>
            <td className="p-2">{row.deadline_failures} deadlines / {row.timeout_failures} timeouts<p>{row.truncated_outputs} truncated outputs / {row.budget_rejections} budget rejections</p></td>
          </tr>)}</tbody>
        </table>
      </div>
      {!data.features.length && <p className="py-3">No provider requests recorded in this window.</p>}
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <div><h3 className="font-semibold">Successful latency distribution</h3><dl className="mt-2 space-y-2 text-sm">{Object.entries(data.latency_histogram).map(([key, value]) => <Metric key={key} label={key.replaceAll('_', ' ')} value={value} />)}</dl></div>
        <div>
          <h3 className="font-semibold">Retry accounting</h3>
          <dl className="mt-2 space-y-2 text-sm">
            <Metric label="Reserved provider retry attempts" value={data.provider_retry_attempts} />
            <Metric label="Recorded pre-send failures" value={data.recovered_pre_io_failures} />
          </dl>
          <p className="mt-2 text-xs">
            Receipts linked to retained, accessible runs and created in this window. Reservations do not
            prove a request was sent; counts are not additional billable calls.
          </p>
        </div>
      </div>
      <h3 className="mt-4 font-semibold">Current queue by feature</h3>
      {!data.queues.length && <p className="mt-2 text-sm">No queued or running accessible AI jobs.</p>}
      <ul className="mt-2 space-y-2 text-sm">
        {data.queues.map((row) => (
          <li key={row.feature} className="rounded border p-3">
            <strong>{row.feature.replaceAll('_', ' ')}</strong>: {row.queued} queued / {row.running} running
            <p>Oldest queued: {age(row.oldest_queued_at, data.until)} · oldest running: {age(row.oldest_running_at, data.until)}</p>
          </li>
        ))}
      </ul>
    </>}
  </Panel>
}

function latency(value: number | null) { return value == null ? 'unmeasured' : `${Math.round(value).toLocaleString()} ms` }
function age(value: string | null, measuredAt: string) {
  if (!value) return 'none'
  const minutes = Math.max(0, Math.floor((Date.parse(measuredAt) - Date.parse(value)) / 60_000))
  return `${minutes.toLocaleString()} min (${formatDateTime(value)})`
}
