import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
import { Feed, StatsFeedTimeSeriesResponse, StatsOverviewResponse } from '../types/api'

const FEED_CHART_COLORS = ['#0891b2', '#06b6d4', '#0ea5e9', '#14b8a6', '#10b981', '#22c55e', '#eab308', '#f97316']

export function StatsPage() {
  const [days, setDays] = useState(30)
  const [selectedFeedIds, setSelectedFeedIds] = useState<string[]>([])

  const feedsQuery = useQuery({
    queryKey: ['feeds'],
    queryFn: () => apiFetch<Feed[]>('/feeds'),
  })

  const feedIdsParam = useMemo(() => selectedFeedIds.slice().sort().join(','), [selectedFeedIds])

  const statsQuery = useQuery({
    queryKey: ['stats', 'overview', days, feedIdsParam],
    queryFn: () => {
      const params = new URLSearchParams()
      params.set('days', String(days))
      if (feedIdsParam) {
        params.set('feed_ids', feedIdsParam)
      }
      return apiFetch<StatsOverviewResponse>(`/stats/overview?${params.toString()}`)
    },
  })

  const feedTimeSeriesQuery = useQuery({
    queryKey: ['stats', 'feed-timeseries', days, feedIdsParam],
    queryFn: () => {
      const params = new URLSearchParams()
      params.set('days', String(days))
      params.set('top_feeds', '8')
      if (feedIdsParam) {
        params.set('feed_ids', feedIdsParam)
      }
      return apiFetch<StatsFeedTimeSeriesResponse>(`/stats/feed-timeseries?${params.toString()}`)
    },
  })

  const statusTotal = useMemo(
    () => (statsQuery.data?.status_breakdown ?? []).reduce((acc, row) => acc + row.count, 0),
    [statsQuery.data?.status_breakdown],
  )

  const maxDaily = useMemo(() => {
    const counts = (statsQuery.data?.daily_volume ?? []).map((point) => point.count)
    return counts.length ? Math.max(...counts, 1) : 1
  }, [statsQuery.data?.daily_volume])

  const maxDomain = useMemo(() => {
    const counts = (statsQuery.data?.top_domains ?? []).map((point) => point.count)
    return counts.length ? Math.max(...counts, 1) : 1
  }, [statsQuery.data?.top_domains])

  const maxFeedWindow = useMemo(() => {
    const counts = (statsQuery.data?.feed_breakdown ?? []).map((point) => point.items_in_window)
    return counts.length ? Math.max(...counts, 1) : 1
  }, [statsQuery.data?.feed_breakdown])

  return (
    <div className="space-y-4">
      <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="font-display text-2xl">Statistics</h2>
            <p className="text-sm text-slate dark:text-slate-300">Feed ingestion and article extraction analytics over time.</p>
          </div>
          <div className="grid gap-2 sm:grid-cols-2">
            <select
              value={days}
              onChange={(event) => setDays(Number(event.target.value))}
              className="rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
            >
              <option value={7}>Last 7 days</option>
              <option value={30}>Last 30 days</option>
              <option value={90}>Last 90 days</option>
              <option value={180}>Last 180 days</option>
            </select>
            <button
              type="button"
              className="rounded border border-slate/30 px-3 py-2 text-sm text-slate-700 dark:border-cyan-900/40 dark:text-slate-100"
              onClick={() => setSelectedFeedIds([])}
            >
              All feeds
            </button>
          </div>
        </div>

        <label className="mt-4 block text-xs font-bold uppercase tracking-wide text-slate dark:text-slate-300">Feeds</label>
        <select
          multiple
          size={Math.min(Math.max(feedsQuery.data?.length ?? 4, 4), 8)}
          className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
          value={selectedFeedIds}
          onChange={(event) => setSelectedFeedIds(Array.from(event.target.selectedOptions).map((option) => option.value))}
        >
          {feedsQuery.data?.map((feed) => (
            <option key={feed.id} value={feed.id}>
              {feed.name}
            </option>
          ))}
        </select>
        <div className="mt-1 flex items-center justify-between text-xs text-slate dark:text-slate-300">
          <span>{selectedFeedIds.length || 'All'} selected</span>
          <button
            type="button"
            className="underline text-slate-700 dark:text-slate-100"
            onClick={() => setSelectedFeedIds(feedsQuery.data?.map((feed) => feed.id) ?? [])}
          >
            Select all
          </button>
        </div>
      </section>

      {statsQuery.isLoading && <p className="text-sm text-slate dark:text-slate-300">Loading stats...</p>}
      {statsQuery.isError && <p className="text-sm text-red-600">Failed to load stats.</p>}

      {statsQuery.data && (
        <>
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <StatCard label="Total Items" value={statsQuery.data.totals.items_total} />
            <StatCard label="Articles Extracted" value={statsQuery.data.totals.articles_total} />
            <StatCard label="Feeds Enabled" value={statsQuery.data.totals.feeds_enabled} />
            <StatCard label="Items / Day (avg)" value={statsQuery.data.derived.avg_items_per_day_window} />
          </div>

          <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
            <h3 className="font-display text-lg">Posts Per Feed Over Time</h3>
            <p className="mt-1 text-xs text-slate dark:text-slate-300">
              Interactive daily time series. Toggle feeds and hover the chart to inspect counts.
            </p>
            {feedTimeSeriesQuery.isLoading && <p className="mt-3 text-sm text-slate dark:text-slate-300">Loading feed time series...</p>}
            {feedTimeSeriesQuery.isError && <p className="mt-3 text-sm text-red-600">Failed to load feed time series.</p>}
            {feedTimeSeriesQuery.data && <FeedTimeSeriesChart data={feedTimeSeriesQuery.data} />}
          </section>

          <div className="grid gap-4 lg:grid-cols-2">
            <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
              <h3 className="font-display text-lg">Derived Health</h3>
              <div className="mt-3 grid gap-2 text-sm md:grid-cols-2">
                <Metric label="Extraction success" value={`${statsQuery.data.derived.extraction_success_rate_pct}%`} />
                <Metric label="Error rate" value={`${statsQuery.data.derived.error_rate_pct}%`} />
                <Metric label="Items last 24h" value={statsQuery.data.activity.items_last_24h} />
                <Metric label="Items last 7d" value={statsQuery.data.activity.items_last_7d} />
              </div>
            </section>

            <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
              <h3 className="font-display text-lg">Status Breakdown</h3>
              <div className="mt-3 space-y-2">
                {statsQuery.data.status_breakdown.map((point) => {
                  const pct = statusTotal ? (point.count / statusTotal) * 100 : 0
                  return (
                    <div key={point.status}>
                      <div className="mb-1 flex items-center justify-between text-xs">
                        <span className="font-mono">{point.status}</span>
                        <span>{point.count}</span>
                      </div>
                      <div className="h-2 rounded bg-slate-200 dark:bg-[#072019]">
                        <div className="h-2 rounded bg-cyan" style={{ width: `${Math.max(2, pct)}%` }} />
                      </div>
                    </div>
                  )
                })}
              </div>
            </section>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
              <h3 className="font-display text-lg">Daily Volume ({statsQuery.data.window_days}d)</h3>
              <div className="mt-3 max-h-80 space-y-2 overflow-auto">
                {statsQuery.data.daily_volume.map((point) => (
                  <BarRow
                    key={point.date}
                    label={point.date}
                    value={point.count}
                    widthPct={(point.count / maxDaily) * 100}
                    monoLabel
                  />
                ))}
                {!statsQuery.data.daily_volume.length && <p className="text-sm text-slate dark:text-slate-300">No activity in selected window.</p>}
              </div>
            </section>

            <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
              <h3 className="font-display text-lg">Top Domains</h3>
              <div className="mt-3 max-h-80 space-y-2 overflow-auto">
                {statsQuery.data.top_domains.map((domain) => (
                  <BarRow
                    key={domain.domain}
                    label={domain.domain}
                    value={domain.count}
                    widthPct={(domain.count / maxDomain) * 100}
                    monoLabel
                  />
                ))}
                {!statsQuery.data.top_domains.length && <p className="text-sm text-slate dark:text-slate-300">No domains captured yet.</p>}
              </div>
            </section>
          </div>

          <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
            <h3 className="font-display text-lg">Feed Share ({statsQuery.data.window_days}d)</h3>
            <div className="mt-3 max-h-64 space-y-2 overflow-auto">
              {statsQuery.data.feed_breakdown.map((feed) => (
                <BarRow
                  key={feed.feed_id}
                  label={feed.feed_name}
                  value={feed.items_in_window}
                  widthPct={(feed.items_in_window / maxFeedWindow) * 100}
                />
              ))}
              {!statsQuery.data.feed_breakdown.length && <p className="text-sm text-slate dark:text-slate-300">No feed data yet.</p>}
            </div>
          </section>

          <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
            <h3 className="font-display text-lg">Feed Contribution</h3>
            <div className="mt-3 overflow-x-auto">
              <table className="min-w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-slate/20 dark:border-cyan-950/40">
                    <th className="px-2 py-2">Feed</th>
                    <th className="px-2 py-2">Total Items</th>
                    <th className="px-2 py-2">Window Items</th>
                    <th className="px-2 py-2">Fetched</th>
                    <th className="px-2 py-2">Errors</th>
                    <th className="px-2 py-2">Last Seen</th>
                  </tr>
                </thead>
                <tbody>
                  {statsQuery.data.feed_breakdown.map((feed) => (
                    <tr key={feed.feed_id} className="border-b border-slate/10 dark:border-cyan-950/40">
                      <td className="px-2 py-2">{feed.feed_name}</td>
                      <td className="px-2 py-2">{feed.total_items}</td>
                      <td className="px-2 py-2">{feed.items_in_window}</td>
                      <td className="px-2 py-2">{feed.content_fetched_items}</td>
                      <td className="px-2 py-2">{feed.error_items}</td>
                      <td className="px-2 py-2">{feed.last_seen_at ? new Date(feed.last_seen_at).toLocaleString() : 'Never'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </div>
  )
}

function FeedTimeSeriesChart({ data }: { data: StatsFeedTimeSeriesResponse }) {
  const [hiddenFeedIds, setHiddenFeedIds] = useState<string[]>([])
  const [hoverIndex, setHoverIndex] = useState<number | null>(null)

  useEffect(() => {
    setHiddenFeedIds((current) => current.filter((feedId) => data.series.some((series) => series.feed_id === feedId)))
  }, [data.series])

  const visibleSeries = useMemo(
    () => data.series.filter((series) => !hiddenFeedIds.includes(series.feed_id)),
    [data.series, hiddenFeedIds],
  )

  const dates = data.series[0]?.points.map((point) => point.date) ?? []
  const yMax = Math.max(
    1,
    ...visibleSeries.flatMap((series) => series.points.map((point) => point.count)),
  )

  const chartWidth = 920
  const chartHeight = 320
  const paddingX = 52
  const paddingY = 26
  const innerWidth = chartWidth - paddingX * 2
  const innerHeight = chartHeight - paddingY * 2

  if (!data.series.length) {
    return <p className="mt-3 text-sm text-slate dark:text-slate-300">No feed time-series data in this window.</p>
  }

  const xForIndex = (index: number) =>
    paddingX + (dates.length <= 1 ? 0 : (index / (dates.length - 1)) * innerWidth)

  const yForCount = (count: number) => paddingY + innerHeight - (count / yMax) * innerHeight

  const buildPath = (counts: number[]) =>
    counts
      .map((count, index) => `${index === 0 ? 'M' : 'L'} ${xForIndex(index)} ${yForCount(count)}`)
      .join(' ')

  const hoverDate = hoverIndex !== null ? dates[hoverIndex] : null

  return (
    <div className="mt-3">
      <div className="mb-2 flex flex-wrap gap-2">
        {data.series.map((series, index) => {
          const hidden = hiddenFeedIds.includes(series.feed_id)
          const color = FEED_CHART_COLORS[index % FEED_CHART_COLORS.length]
          return (
            <button
              key={series.feed_id}
              type="button"
              className={`rounded-full border px-2.5 py-1 text-xs ${
                hidden ? 'border-slate/30 text-slate dark:border-cyan-900/40 dark:text-slate-300' : 'text-ink dark:text-slate-100'
              }`}
              style={hidden ? undefined : { borderColor: color, color }}
              onClick={() =>
                setHiddenFeedIds((current) =>
                  current.includes(series.feed_id)
                    ? current.filter((entry) => entry !== series.feed_id)
                    : [...current, series.feed_id],
                )
              }
            >
              {series.feed_name}
            </button>
          )
        })}
      </div>

      <div className="overflow-x-auto rounded border border-slate/20 bg-white/70 p-2 dark:border-cyan-900/40 dark:bg-[#072019]/70">
        <svg
          viewBox={`0 0 ${chartWidth} ${chartHeight}`}
          className="h-[320px] min-w-[900px] w-full"
          onMouseMove={(event) => {
            const bounds = event.currentTarget.getBoundingClientRect()
            const relativeX = event.clientX - bounds.left
            const normalizedX = (relativeX / bounds.width) * chartWidth
            const clamped = Math.max(paddingX, Math.min(chartWidth - paddingX, normalizedX))
            const index = Math.round(((clamped - paddingX) / innerWidth) * Math.max(0, dates.length - 1))
            setHoverIndex(Math.max(0, Math.min(dates.length - 1, index)))
          }}
          onMouseLeave={() => setHoverIndex(null)}
        >
          <rect x={paddingX} y={paddingY} width={innerWidth} height={innerHeight} fill="transparent" />

          {[0, 0.25, 0.5, 0.75, 1].map((ratio) => {
            const y = paddingY + innerHeight - innerHeight * ratio
            const value = Math.round(yMax * ratio)
            return (
              <g key={ratio}>
                <line x1={paddingX} y1={y} x2={paddingX + innerWidth} y2={y} stroke="rgba(148, 163, 184, 0.25)" strokeWidth={1} />
                <text x={paddingX - 8} y={y + 4} textAnchor="end" fontSize={11} fill="#64748b">
                  {value}
                </text>
              </g>
            )
          })}

          {visibleSeries.map((series, index) => {
            const color = FEED_CHART_COLORS[data.series.findIndex((entry) => entry.feed_id === series.feed_id) % FEED_CHART_COLORS.length]
            return (
              <path
                key={series.feed_id}
                d={buildPath(series.points.map((point) => point.count))}
                fill="none"
                stroke={color}
                strokeWidth={2.3}
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            )
          })}

          {hoverIndex !== null && dates.length > 0 && (
            <line
              x1={xForIndex(hoverIndex)}
              y1={paddingY}
              x2={xForIndex(hoverIndex)}
              y2={paddingY + innerHeight}
              stroke="rgba(6, 182, 212, 0.55)"
              strokeDasharray="4 3"
              strokeWidth={1.2}
            />
          )}

          {dates.length > 0 && (
            <>
              <text x={paddingX} y={chartHeight - 8} fontSize={11} fill="#64748b">
                {dates[0]}
              </text>
              <text x={paddingX + innerWidth} y={chartHeight - 8} textAnchor="end" fontSize={11} fill="#64748b">
                {dates[dates.length - 1]}
              </text>
            </>
          )}
        </svg>
      </div>

      {hoverDate && (
        <div className="mt-2 rounded border border-slate/20 bg-white/70 p-2 text-xs dark:border-cyan-900/40 dark:bg-[#072019]/70">
          <p className="font-semibold">{hoverDate}</p>
          <div className="mt-1 flex flex-wrap gap-2">
            {visibleSeries.map((series) => {
              const count = series.points[hoverIndex ?? 0]?.count ?? 0
              return (
                <span key={series.feed_id} className="rounded border border-slate/30 px-2 py-0.5 dark:border-cyan-900/40">
                  {series.feed_name}: {count}
                </span>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

function StatCard({ label, value }: { label: string; value: number | string }) {
  return (
    <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
      <p className="text-xs uppercase tracking-wide text-slate dark:text-slate-400">{label}</p>
      <p className="mt-2 text-2xl font-bold text-cyan dark:text-cyan-300">{value}</p>
    </section>
  )
}

function Metric({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded border border-slate/20 px-3 py-2 dark:border-cyan-950/40">
      <p className="text-xs text-slate dark:text-slate-400">{label}</p>
      <p className="mt-1 text-lg font-semibold">{value}</p>
    </div>
  )
}

function BarRow({
  label,
  value,
  widthPct,
  monoLabel,
}: {
  label: string
  value: number
  widthPct: number
  monoLabel?: boolean
}) {
  return (
    <div className="rounded border border-slate/15 px-3 py-2 text-sm dark:border-cyan-950/40">
      <div className="mb-1 flex items-center justify-between gap-3">
        <span className={monoLabel ? 'font-mono text-xs' : ''}>{label}</span>
        <span className="font-semibold">{value}</span>
      </div>
      <div className="h-2 rounded bg-slate-200 dark:bg-[#072019]">
        <div className="h-2 rounded bg-cyan" style={{ width: value > 0 ? `${Math.max(2, widthPct)}%` : '0%' }} />
      </div>
    </div>
  )
}
