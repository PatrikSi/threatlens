import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
import { Feed, StatsActivityHeatmapResponse, StatsFeedTimeSeriesResponse, StatsOverviewResponse } from '../types/api'

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

  const activityHeatmapQuery = useQuery({
    queryKey: ['stats', 'activity-heatmap', feedIdsParam],
    queryFn: () => {
      const params = new URLSearchParams()
      if (feedIdsParam) {
        params.set('feed_ids', feedIdsParam)
      }
      return apiFetch<StatsActivityHeatmapResponse>(`/stats/activity-heatmap?${params.toString()}`)
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

          <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
            <h3 className="font-display text-lg">Activity Heatmap (24h / 7d)</h3>
            <p className="mt-1 text-xs text-slate dark:text-slate-300">
              Publication-time density by hour. Darker cells indicate higher post volume.
            </p>
            {activityHeatmapQuery.isLoading && <p className="mt-3 text-sm text-slate dark:text-slate-300">Loading activity heatmap...</p>}
            {activityHeatmapQuery.isError && <p className="mt-3 text-sm text-red-600">Failed to load activity heatmap.</p>}
            {activityHeatmapQuery.data && <ActivityHeatmapPanel data={activityHeatmapQuery.data} />}
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
  const hostRef = useRef<HTMLDivElement | null>(null)
  const [hiddenFeedIds, setHiddenFeedIds] = useState<string[]>([])
  const [hoverIndex, setHoverIndex] = useState<number | null>(null)
  const [chartWidth, setChartWidth] = useState(980)

  useEffect(() => {
    setHiddenFeedIds((current) => current.filter((feedId) => data.series.some((series) => series.feed_id === feedId)))
  }, [data.series])

  useEffect(() => {
    if (typeof window === 'undefined') return
    const target = hostRef.current
    if (!target) return

    const updateWidth = () => {
      setChartWidth(Math.max(560, Math.floor(target.clientWidth)))
    }

    updateWidth()
    const observer = new ResizeObserver(updateWidth)
    observer.observe(target)
    return () => observer.disconnect()
  }, [])

  const visibleSeries = useMemo(
    () => data.series.filter((series) => !hiddenFeedIds.includes(series.feed_id)),
    [data.series, hiddenFeedIds],
  )

  const dates = data.series[0]?.points.map((point) => point.date) ?? []
  const yMax = Math.max(1, ...visibleSeries.flatMap((series) => series.points.map((point) => point.count)))

  const chartHeight = 320
  const paddingLeft = 38
  const paddingRight = 14
  const paddingTop = 14
  const paddingBottom = 24
  const innerWidth = Math.max(1, chartWidth - paddingLeft - paddingRight)
  const innerHeight = Math.max(1, chartHeight - paddingTop - paddingBottom)

  if (!data.series.length) {
    return <p className="mt-3 text-sm text-slate dark:text-slate-300">No feed time-series data in this window.</p>
  }

  if (!visibleSeries.length) {
    return (
      <div className="mt-3 rounded border border-slate/20 p-3 text-sm text-slate dark:border-cyan-900/40 dark:text-slate-300">
        All feed series are hidden. Re-enable at least one feed chip to view the graph.
      </div>
    )
  }

  const xForIndex = (index: number) => paddingLeft + (dates.length <= 1 ? 0 : (index / (dates.length - 1)) * innerWidth)

  const yForCount = (count: number) => paddingTop + innerHeight - (count / yMax) * innerHeight

  const buildPath = (counts: number[]) =>
    counts
      .map((count, index) => `${index === 0 ? 'M' : 'L'} ${xForIndex(index)} ${yForCount(count)}`)
      .join(' ')

  const hoverDate = hoverIndex !== null ? dates[hoverIndex] : null
  const hoverLegend =
    hoverIndex === null
      ? []
      : visibleSeries
          .map((series, index) => ({
            series,
            count: series.points[hoverIndex]?.count ?? 0,
            color: FEED_CHART_COLORS[data.series.findIndex((entry) => entry.feed_id === series.feed_id) % FEED_CHART_COLORS.length],
            sortOrder: index,
          }))
          .sort((a, b) => (b.count === a.count ? a.sortOrder - b.sortOrder : b.count - a.count))

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

      <div
        ref={hostRef}
        className="relative rounded border border-slate/20 bg-white/70 p-2 dark:border-cyan-900/40 dark:bg-[#072019]/70"
      >
        <svg
          viewBox={`0 0 ${chartWidth} ${chartHeight}`}
          className="h-[320px] w-full"
          onMouseMove={(event) => {
            const bounds = event.currentTarget.getBoundingClientRect()
            const relativeX = event.clientX - bounds.left
            const normalizedX = (relativeX / bounds.width) * chartWidth
            const clamped = Math.max(paddingLeft, Math.min(chartWidth - paddingRight, normalizedX))
            const index = Math.round(((clamped - paddingLeft) / innerWidth) * Math.max(0, dates.length - 1))
            setHoverIndex(Math.max(0, Math.min(dates.length - 1, index)))
          }}
          onMouseLeave={() => setHoverIndex(null)}
        >
          <rect
            x={paddingLeft}
            y={paddingTop}
            width={innerWidth}
            height={innerHeight}
            fill="rgba(2, 6, 23, 0.03)"
            className="dark:fill-[rgba(2,6,23,0.45)]"
          />

          {[0, 0.2, 0.4, 0.6, 0.8, 1].map((ratio) => {
            const y = paddingTop + innerHeight - innerHeight * ratio
            const value = Math.round(yMax * ratio)
            return (
              <g key={ratio}>
                <line
                  x1={paddingLeft}
                  y1={y}
                  x2={paddingLeft + innerWidth}
                  y2={y}
                  stroke="rgba(148, 163, 184, 0.2)"
                  strokeWidth={1}
                />
                <text x={paddingLeft - 8} y={y + 4} textAnchor="end" fontSize={11} fill="#64748b">
                  {value}
                </text>
              </g>
            )
          })}

          {visibleSeries.map((series) => {
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
              y1={paddingTop}
              x2={xForIndex(hoverIndex)}
              y2={paddingTop + innerHeight}
              stroke="rgba(6, 182, 212, 0.55)"
              strokeDasharray="4 3"
              strokeWidth={1.2}
            />
          )}

          {hoverIndex !== null &&
            dates.length > 0 &&
            visibleSeries.map((series) => {
              const color = FEED_CHART_COLORS[data.series.findIndex((entry) => entry.feed_id === series.feed_id) % FEED_CHART_COLORS.length]
              const count = series.points[hoverIndex]?.count ?? 0
              return <circle key={`hover-${series.feed_id}`} cx={xForIndex(hoverIndex)} cy={yForCount(count)} r={3} fill={color} />
            })}
        </svg>
        {hoverDate && hoverLegend.length > 0 && (
          <div className="pointer-events-none absolute right-4 top-4 min-w-48 rounded border border-slate/25 bg-white/95 p-2 text-xs shadow-lg dark:border-cyan-900/40 dark:bg-[#041612]/95">
            <p className="font-semibold">{hoverDate}</p>
            <div className="mt-1 space-y-1">
              {hoverLegend.map(({ series, count, color }) => (
                <div key={`legend-${series.feed_id}`} className="flex items-center justify-between gap-2">
                  <span className="inline-flex items-center gap-1.5">
                    <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: color }} />
                    <span className="max-w-40 truncate">{series.feed_name}</span>
                  </span>
                  <span className="font-semibold">{count}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
      {dates.length > 0 && (
        <div className="mt-1 flex items-center justify-between text-[11px] text-slate dark:text-slate-300">
          <span>{dates[0]}</span>
          <span>{dates[dates.length - 1]}</span>
        </div>
      )}
    </div>
  )
}

function ActivityHeatmapPanel({ data }: { data: StatsActivityHeatmapResponse }) {
  const max24 = Math.max(1, data.last_24h_max)
  const max7d = Math.max(1, data.last_7d_max)

  return (
    <div className="mt-3 space-y-4">
      <div>
        <p className="text-xs font-semibold uppercase tracking-wide text-slate dark:text-slate-300">Last 24 Hours</p>
        <div className="mt-1 rounded border border-slate/20 bg-white/70 p-2 dark:border-cyan-900/40 dark:bg-[#072019]/70">
          <div
            className="grid gap-1"
            style={{ gridTemplateColumns: 'repeat(24, minmax(0, 1fr))' }}
          >
            {data.last_24h.map((point) => (
              <div
                key={point.hour_start}
                className="h-6 rounded"
                style={heatCellStyle(point.count, max24)}
                title={`${new Date(point.hour_start).toLocaleString()} — ${point.count} posts`}
              />
            ))}
          </div>
          <div className="mt-1 flex items-center justify-between text-[11px] text-slate dark:text-slate-300">
            <span>{data.last_24h[0] ? formatHourAxis(data.last_24h[0].hour_start) : '-'}</span>
            <span>{data.last_24h[data.last_24h.length - 1] ? formatHourAxis(data.last_24h[data.last_24h.length - 1].hour_start) : '-'}</span>
          </div>
        </div>
      </div>

      <div>
        <p className="text-xs font-semibold uppercase tracking-wide text-slate dark:text-slate-300">Last 7 Days</p>
        <div className="mt-1 rounded border border-slate/20 bg-white/70 p-2 dark:border-cyan-900/40 dark:bg-[#072019]/70">
          <div className="mb-1 grid grid-cols-[82px_1fr] items-center gap-2 text-[10px] text-slate dark:text-slate-300">
            <span />
            <div className="grid gap-1" style={{ gridTemplateColumns: 'repeat(24, minmax(0, 1fr))' }}>
              {Array.from({ length: 24 }, (_, hour) => (
                <span key={`hour-${hour}`} className="text-center">
                  {hour % 3 === 0 ? String(hour).padStart(2, '0') : ''}
                </span>
              ))}
            </div>
          </div>

          <div className="space-y-1">
            {data.last_7d.map((row) => (
              <div key={row.day} className="grid grid-cols-[82px_1fr] items-center gap-2">
                <span className="font-mono text-[11px] text-slate dark:text-slate-300">{row.day.slice(5)}</span>
                <div className="grid gap-1" style={{ gridTemplateColumns: 'repeat(24, minmax(0, 1fr))' }}>
                  {row.counts.map((count, hour) => (
                    <div
                      key={`${row.day}-${hour}`}
                      className="h-4 rounded"
                      style={heatCellStyle(count, max7d)}
                      title={`${row.day} ${String(hour).padStart(2, '0')}:00 — ${count} posts`}
                    />
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="flex items-center gap-2 text-[11px] text-slate dark:text-slate-300">
        <span>Low</span>
        <div className="h-2 w-28 rounded" style={{ background: 'linear-gradient(90deg, rgba(6,182,212,0.1), rgba(6,182,212,0.95))' }} />
        <span>High</span>
      </div>
    </div>
  )
}

function heatCellStyle(count: number, maxCount: number) {
  if (count <= 0) {
    return { backgroundColor: 'rgba(148, 163, 184, 0.14)' }
  }
  const intensity = Math.min(1, count / Math.max(1, maxCount))
  const alpha = 0.2 + intensity * 0.75
  return { backgroundColor: `rgba(6, 182, 212, ${alpha.toFixed(3)})` }
}

function formatHourAxis(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString([], { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
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
