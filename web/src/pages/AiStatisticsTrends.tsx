import { useId, useState } from 'react'
import type { AIOpsOverviewResponse, AITimeSeriesPointResponse } from '../types/ai'
import { AiTrendChart, type AiTrendSeries } from './AiTrendChart'
import { formatTrendValue, measuredLatency, recordedTokens, successRate, trendDate } from './aiTrendModel'

export function AiStatisticsTrends({ overview }: { overview: AIOpsOverviewResponse }) {
  const points = overview.time_series
  // Keep the selected date through background refreshes; new windows remount this component.
  const [selectedDate, setSelectedDate] = useState<string | null>(null)
  const id = useId()
  const foundIndex = points.findIndex((point) => point.bucket === selectedDate)
  const selectedIndex = foundIndex < 0 ? Math.max(0, points.length - 1) : foundIndex
  const selected = points[selectedIndex]
  const dates = points.map((point) => point.bucket)
  const hasRequests = points.some((point) => point.requests > 0)
  const onSelect = (index: number) => setSelectedDate(points[index]?.bucket ?? null)
  const series = (key: string, label: string, color: number, read: (point: AITimeSeriesPointResponse) => number | null, dashed = false): AiTrendSeries => ({
    key, label, color: `var(--tl-chart-${color})`, values: points.map(read), dashed,
  })

  return <section aria-label="AI trends" className="min-w-0 space-y-3">
    <header className="border-b border-slate/15 pb-2 dark:border-cyan-900/30">
      <h2 className="font-display text-lg">AI trends</h2>
      <p className="mt-1 text-sm text-slate dark:text-white/70">Daily UTC buckets for the selected request window. The first and last days can be partial; retained, accessible events only.</p>
      {overview.since && overview.until && <p className="mt-1 text-xs text-slate dark:text-slate-300">{utcTimestamp(overview.since)} to {utcTimestamp(overview.until)} (end exclusive).</p>}
    </header>
    {!hasRequests || !selected ? <p role="status" className="rounded-xl border border-dashed border-slate/25 p-6 text-sm dark:border-white/20">No provider requests recorded in this window. Trends will appear as AI requests are recorded.</p> : <>
      <div className="min-w-0 rounded-lg border border-slate/20 p-3 dark:border-cyan-900/40">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <label htmlFor={`${id}-date`} className="text-sm font-semibold">Trend date</label>
          <span className="text-sm font-semibold tabular-nums">{trendDate(selected.bucket, true)} UTC</span>
        </div>
        <input id={`${id}-date`} type="range" min={0} max={Math.max(0, points.length - 1)} step={1} value={selectedIndex}
          aria-valuetext={`${trendDate(selected.bucket, true)} UTC; ${selected.requests} requests; ${selected.failures} failed`}
          aria-describedby={`${id}-help`} disabled={points.length === 1}
          onChange={(event) => onSelect(Number(event.target.value))} className="mt-2 h-8 w-full accent-cyan" />
        <p id={`${id}-help`} className="text-xs text-slate dark:text-slate-300">Hover a graph, drag the slider, or use its arrow keys to inspect the same date in every chart.</p>
        <p className="mt-1 text-xs text-slate dark:text-slate-300">{selected.requests.toLocaleString()} recorded requests · {usageCompleteness(selected)} · {selected.latency_samples == null ? 'Latency sample count unavailable' : `${selected.latency_samples.toLocaleString()} latency measurements`}</p>
      </div>
      <div className="grid min-w-0 gap-3 xl:grid-cols-2">
        <AiTrendChart title="Request outcomes" description="Successful and failed requests share the same count axis." unit="requests"
          dates={dates} selectedIndex={selectedIndex} onSelect={onSelect} emptyLabel="No request outcomes available."
          series={[series('successful', 'Successful', 1, (point) => point.requests - point.failures), series('failed', 'Failed', 6, (point) => point.failures, true)]} />
        <AiTrendChart title="Recorded token usage" description="Reported totals only. Unreported usage is excluded; gaps mean no usage was reported." unit="tokens"
          dates={dates} selectedIndex={selectedIndex} onSelect={onSelect} emptyLabel="No token usage reported in this window."
          series={[series('tokens', 'Recorded tokens', 3, recordedTokens)]} />
        <AiTrendChart title="Request latency" description="Average and P95 of measured requests, including failures. Gaps mean no measurements." unit="ms"
          dates={dates} selectedIndex={selectedIndex} onSelect={onSelect} emptyLabel="No latency measurements available in this window."
          series={[series('average', 'Average', 2, (point) => measuredLatency(point, 'average_latency_ms')), series('p95', 'P95', 4, (point) => measuredLatency(point, 'p95_latency_ms'), true)]} />
        <AiTrendChart title="Success rate" description="Successful requests as a percentage of recorded requests. No requests means no rate." unit="%"
          dates={dates} selectedIndex={selectedIndex} onSelect={onSelect} emptyLabel="No request outcomes available."
          series={[series('success-rate', 'Success rate', 1, successRate)]} />
      </div>
      <details className="min-w-0 rounded-lg border border-slate/20 p-3 dark:border-cyan-900/40">
        <summary className="cursor-pointer py-2 text-sm font-semibold">View exact trend data</summary>
        <div role="region" aria-label="AI trend data" tabIndex={0} className="mt-2 max-h-80 overflow-auto">
          <table className="min-w-[900px] w-full text-left text-sm">
            <caption className="sr-only">Daily AI request trends in UTC. Unavailable values are not zero.</caption>
            <thead className="sticky top-0 bg-white dark:bg-[#041612]"><tr>{['Date (UTC)', 'Successful', 'Failed', 'Success rate', 'Recorded tokens', 'Unreported usage', 'Latency samples', 'Average latency', 'P95 latency'].map((label) => <th key={label} scope="col" className="p-2">{label}</th>)}</tr></thead>
            <tbody>{points.map((point) => <tr key={point.bucket} className="border-t border-slate/15 dark:border-white/10">
              <th scope="row" className="whitespace-nowrap p-2 font-medium">{trendDate(point.bucket, true)}</th>
              <td className="p-2">{(point.requests - point.failures).toLocaleString()}</td><td className="p-2">{point.failures.toLocaleString()}</td>
              <td className="p-2">{formatTrendValue(successRate(point), '%')}</td><td className="p-2">{formatTrendValue(recordedTokens(point), 'tokens')}</td>
              <td className="p-2">{point.known_usage_requests == null ? 'Unavailable' : (point.requests - point.known_usage_requests).toLocaleString()}</td>
              <td className="p-2">{point.latency_samples?.toLocaleString() ?? 'Unavailable'}</td>
              <td className="p-2">{formatTrendValue(measuredLatency(point, 'average_latency_ms'), 'ms')}</td><td className="p-2">{formatTrendValue(measuredLatency(point, 'p95_latency_ms'), 'ms')}</td>
            </tr>)}</tbody>
          </table>
        </div>
      </details>
    </>}
  </section>
}

function usageCompleteness(point: AITimeSeriesPointResponse): string {
  return point.known_usage_requests == null ? 'Token usage completeness unavailable' : `${(point.requests - point.known_usage_requests).toLocaleString()} with unreported token usage`
}

function utcTimestamp(value: string): string {
  return new Date(value).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short', timeZone: 'UTC' }) + ' UTC'
}
