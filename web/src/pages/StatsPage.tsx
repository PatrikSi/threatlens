import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
import { formatDateOnly, formatDateTime } from '../utils/datetime'
import {
  Feed,
  StatsActivityHeatmapResponse,
  StatsFeedTimeSeriesResponse,
  StatsOverviewResponse,
  StatsSignalRadarResponse,
} from '../types/api'

const FEED_CHART_COLORS = ['#0e7490', '#2563eb', '#0f766e', '#64748b', '#7c3aed', '#b45309', '#4f46e5', '#059669']
const FEED_TABLE_PREVIEW_LIMIT = 50

export function StatsPage() {
  const [days, setDays] = useState(30)
  const [selectedFeedIds, setSelectedFeedIds] = useState<string[]>([])
  const [showAllFeedRows, setShowAllFeedRows] = useState(false)

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
      if (feedIdsParam) {
        params.set('feed_ids', feedIdsParam)
      }
      return apiFetch<StatsFeedTimeSeriesResponse>(`/stats/feed-timeseries?${params.toString()}`)
    },
  })

  const activityHeatmapQuery = useQuery({
    queryKey: ['stats', 'activity-heatmap', days, feedIdsParam],
    queryFn: () => {
      const params = new URLSearchParams()
      params.set('days', String(days))
      if (feedIdsParam) {
        params.set('feed_ids', feedIdsParam)
      }
      return apiFetch<StatsActivityHeatmapResponse>(`/stats/activity-heatmap?${params.toString()}`)
    },
  })

  const signalRadarQuery = useQuery({
    queryKey: ['stats', 'signal-radar', days, feedIdsParam],
    queryFn: () => {
      const params = new URLSearchParams()
      params.set('days', String(days))
      if (feedIdsParam) {
        params.set('feed_ids', feedIdsParam)
      }
      return apiFetch<StatsSignalRadarResponse>(`/stats/signal-radar?${params.toString()}`)
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

  const dailyVolumeNewestFirst = useMemo(() => {
    return [...(statsQuery.data?.daily_volume ?? [])].sort((left, right) => right.date.localeCompare(left.date))
  }, [statsQuery.data?.daily_volume])

  const maxDomain = useMemo(() => {
    const counts = (statsQuery.data?.top_domains ?? []).map((point) => point.count)
    return counts.length ? Math.max(...counts, 1) : 1
  }, [statsQuery.data?.top_domains])

  const maxFeedWindow = useMemo(() => {
    const counts = (statsQuery.data?.feed_breakdown ?? []).map((point) => point.items_in_window)
    return counts.length ? Math.max(...counts, 1) : 1
  }, [statsQuery.data?.feed_breakdown])

  const visibleFeedBreakdown = useMemo(() => {
    const rows = statsQuery.data?.feed_breakdown ?? []
    return showAllFeedRows ? rows : rows.slice(0, FEED_TABLE_PREVIEW_LIMIT)
  }, [showAllFeedRows, statsQuery.data?.feed_breakdown])

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
              className="w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
            >
              <option value={7}>Last 7 days</option>
              <option value={30}>Last 30 days</option>
              <option value={90}>Last 90 days</option>
              <option value={180}>Last 180 days</option>
            </select>
            <button
              type="button"
              className="w-full rounded border border-slate/30 px-3 py-2 text-sm text-slate-700 dark:border-cyan-900/40 dark:text-slate-100"
              onClick={() => setSelectedFeedIds([])}
            >
              All feeds
            </button>
          </div>
        </div>

        <label className="mt-4 block text-xs font-bold uppercase text-slate dark:text-slate-300">Feeds</label>
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
        <div className="mt-1 flex flex-wrap items-center justify-between gap-2 text-xs text-slate dark:text-slate-300">
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
            <h3 className="font-display text-lg">Activity Heatmap</h3>
            <p className="mt-1 text-xs text-slate dark:text-slate-300">
              {activityHeatmapQuery.data?.bucket_unit === 'day'
                ? 'Publication-time density aggregated by day for longer windows. Darker cells indicate higher post volume.'
                : 'Publication-time density by hour across short windows. Darker cells indicate higher post volume.'}
            </p>
            {activityHeatmapQuery.isLoading && <p className="mt-3 text-sm text-slate dark:text-slate-300">Loading activity heatmap...</p>}
            {activityHeatmapQuery.isError && <p className="mt-3 text-sm text-red-600">Failed to load activity heatmap.</p>}
            {activityHeatmapQuery.data && <ActivityHeatmapPanel data={activityHeatmapQuery.data} />}
          </section>

          <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
            <h3 className="font-display text-lg">Signal Radar View</h3>
            <p className="mt-1 text-xs text-slate dark:text-slate-300">
              Classification signal intensity across threat categories for the selected feed/time window.
            </p>
            {signalRadarQuery.isLoading && <p className="mt-3 text-sm text-slate dark:text-slate-300">Loading signal radar...</p>}
            {signalRadarQuery.isError && <p className="mt-3 text-sm text-red-600">Failed to load signal radar.</p>}
            {signalRadarQuery.data && <SignalRadarChart data={signalRadarQuery.data} />}
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
                {dailyVolumeNewestFirst.map((point) => (
                  <BarRow
                    key={point.date}
                    label={formatDateOnly(point.date)}
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
                  {visibleFeedBreakdown.map((feed) => (
                    <tr key={feed.feed_id} className="border-b border-slate/10 dark:border-cyan-950/40">
                      <td className="px-2 py-2">{feed.feed_name}</td>
                      <td className="px-2 py-2">{feed.total_items}</td>
                      <td className="px-2 py-2">{feed.items_in_window}</td>
                      <td className="px-2 py-2">{feed.content_fetched_items}</td>
                      <td className="px-2 py-2">{feed.error_items}</td>
                      <td className="px-2 py-2">{feed.last_seen_at ? formatDateTime(feed.last_seen_at) : 'Never'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {statsQuery.data.feed_breakdown.length > FEED_TABLE_PREVIEW_LIMIT && (
                <div className="mt-3 flex items-center justify-between gap-3 text-sm text-slate dark:text-slate-300">
                  <span>
                    Showing {visibleFeedBreakdown.length} of {statsQuery.data.feed_breakdown.length} feeds
                  </span>
                  <button
                    type="button"
                    className="rounded border border-slate/30 px-3 py-1.5 font-semibold text-slate-700 dark:border-cyan-900/40 dark:text-slate-100"
                    onClick={() => setShowAllFeedRows((current) => !current)}
                  >
                    {showAllFeedRows ? 'Show top feeds' : 'Show all feeds'}
                  </button>
                </div>
              )}
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
  const [hoverPointer, setHoverPointer] = useState<{ x: number; y: number } | null>(null)
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
  const displayDates = dates.map((date) => formatDateOnly(date))
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

  const hoverDate = hoverIndex !== null ? displayDates[hoverIndex] : null
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
  const hostWidth = hostRef.current?.clientWidth ?? chartWidth
  const hostHeight = hostRef.current?.clientHeight ?? chartHeight
  const hoverLegendPosition =
    hoverPointer && hoverDate && hoverLegend.length > 0
      ? positionTooltipNearCursor(hoverPointer.x, hoverPointer.y, hostWidth, hostHeight, 220, 150)
      : null

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
              className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs text-slate-700 dark:text-slate-200 ${
                hidden ? 'border-slate/30 opacity-60 dark:border-cyan-900/40' : 'border-slate/25 dark:border-white/10'
              }`}
              style={hidden ? undefined : { borderColor: `${color}66` }}
              onClick={() =>
                setHiddenFeedIds((current) =>
                  current.includes(series.feed_id)
                    ? current.filter((entry) => entry !== series.feed_id)
                    : [...current, series.feed_id],
                )
              }
            >
              <span className="h-2 w-2 rounded-full" style={{ backgroundColor: color }} />
              <span>{series.feed_name}</span>
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
            const hostBounds = hostRef.current?.getBoundingClientRect()
            if (hostBounds) {
              setHoverPointer({
                x: event.clientX - hostBounds.left,
                y: event.clientY - hostBounds.top,
              })
            }
          }}
          onMouseLeave={() => {
            setHoverIndex(null)
            setHoverPointer(null)
          }}
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
                <text x={paddingLeft - 8} y={y + 4} textAnchor="end" fontSize={11} className="fill-slate-500 dark:fill-[var(--tl-text-dim)]">
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
        {hoverDate && hoverLegend.length > 0 && hoverLegendPosition && (
          <div
            className="pointer-events-none absolute min-w-48 rounded border border-slate/25 bg-white/95 p-2 text-xs shadow-lg dark:border-cyan-900/40 dark:bg-[#041612]/95"
            style={{ left: hoverLegendPosition.left, top: hoverLegendPosition.top }}
          >
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
          <span>{displayDates[0]}</span>
          <span>{displayDates[displayDates.length - 1]}</span>
        </div>
      )}
    </div>
  )
}

function ActivityHeatmapPanel({ data }: { data: StatsActivityHeatmapResponse }) {
  const panelRef = useRef<HTMLDivElement | null>(null)
  const maxCount = Math.max(1, data.max_count)
  const isHourly = data.bucket_unit === 'hour'
  const columnCount = Math.max(1, data.bucket_labels.length || data.rows[0]?.counts.length || 1)
  const bucketLabels =
    data.bucket_labels.length === columnCount
      ? data.bucket_labels
      : Array.from({ length: columnCount }, (_, index) => `Bucket ${index + 1}`)
  const calendar = isHourly ? null : buildDailyCalendar(data.rows)
  const calendarWeekCount = Math.max(1, calendar?.weekCount ?? 1)
  const [hovered, setHovered] = useState<{
    label: string
    count: number
    intensityPct: number
    x: number
    y: number
  } | null>(null)
  const panelWidth = panelRef.current?.clientWidth ?? 560
  const panelHeight = panelRef.current?.clientHeight ?? (isHourly ? 300 : 380)
  const heatmapTooltipPosition = hovered
    ? positionTooltipNearCursor(hovered.x, hovered.y, panelWidth, panelHeight, 220, 116)
    : null

  return (
    <div ref={panelRef} className="relative mt-3 space-y-4" onMouseLeave={() => setHovered(null)}>
      {hovered && heatmapTooltipPosition && (
        <div
          className="pointer-events-none absolute z-10 min-w-48 rounded border border-slate/25 bg-white/95 p-2 text-xs shadow-lg dark:border-cyan-900/40 dark:bg-[#041612]/95"
          style={{ left: heatmapTooltipPosition.left, top: heatmapTooltipPosition.top }}
        >
          <p className="font-semibold">Activity</p>
          <p className="mt-0.5">{hovered.label}</p>
          <div className="mt-1 flex items-center justify-between gap-4">
            <span className="text-slate dark:text-slate-300">Posts</span>
            <span className="font-semibold">{hovered.count}</span>
          </div>
          <div className="mt-0.5 flex items-center justify-between gap-4">
            <span className="text-slate dark:text-slate-300">Intensity</span>
            <span className="font-semibold">{hovered.intensityPct.toFixed(1)}%</span>
          </div>
        </div>
      )}
      <div>
        <p className="text-xs font-semibold uppercase text-slate dark:text-slate-300">
          Last {data.window_days} Days ({isHourly ? 'Hourly' : 'Daily'})
        </p>
        <div className="mt-1 rounded border border-slate/20 bg-white/70 p-2 dark:border-cyan-900/40 dark:bg-[#072019]/70">
          {isHourly ? (
            <>
              <div className="mb-1 grid items-center gap-2 text-[10px] text-slate dark:text-slate-300" style={{ gridTemplateColumns: '82px minmax(0, 1fr)' }}>
                <span />
                <div className="grid gap-1" style={{ gridTemplateColumns: `repeat(${columnCount}, minmax(0, 1fr))` }}>
                  {bucketLabels.map((bucketLabel, index) => (
                    <span key={`${bucketLabel}-${index}`} className="text-center">
                      {index % 3 === 0 ? bucketLabel.slice(0, 2) : ''}
                    </span>
                  ))}
                </div>
              </div>

              <div className="max-h-[520px] space-y-1 overflow-auto pr-1">
                {data.rows.map((row) => (
                  <div key={row.day} className="grid items-center gap-2" style={{ gridTemplateColumns: '82px minmax(0, 1fr)' }}>
                    <span className="font-mono text-[11px] text-slate dark:text-slate-300">{formatDateOnly(row.day).slice(0, 5)}</span>
                    <div className="grid gap-1" style={{ gridTemplateColumns: `repeat(${columnCount}, minmax(0, 1fr))` }}>
                      {row.counts.slice(0, columnCount).map((count, bucketIndex) => (
                        <div
                          key={`${row.day}-${bucketIndex}`}
                          className="h-4 rounded"
                          style={heatCellStyle(count, maxCount)}
                          onMouseMove={(event) => {
                            const bounds = panelRef.current?.getBoundingClientRect()
                            if (!bounds) return
                            const bucketLabel = bucketLabels[bucketIndex] ?? `Bucket ${bucketIndex + 1}`
                            setHovered({
                              label: `${formatDateOnly(row.day)} ${bucketLabel}`,
                              count,
                              intensityPct: (count / maxCount) * 100,
                              x: event.clientX - bounds.left,
                              y: event.clientY - bounds.top,
                            })
                          }}
                        />
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <div className="pb-1">
              <div className="grid items-start gap-2" style={{ gridTemplateColumns: '82px minmax(0, 1fr)' }}>
                <div className="mt-5 grid grid-rows-7 gap-1 text-[10px] text-slate dark:text-slate-300">
                  <span className="h-4 leading-4" />
                  <span className="h-4 leading-4">Mon</span>
                  <span className="h-4 leading-4" />
                  <span className="h-4 leading-4">Wed</span>
                  <span className="h-4 leading-4" />
                  <span className="h-4 leading-4">Fri</span>
                  <span className="h-4 leading-4" />
                </div>

                <div className="min-w-0 space-y-1">
                  <div className="grid gap-1 text-[10px] text-slate dark:text-slate-300" style={{ gridTemplateColumns: `repeat(${calendarWeekCount}, minmax(0, 1fr))` }}>
                    {Array.from({ length: calendarWeekCount }, (_, weekIndex) => (
                      <span key={`month-${weekIndex}`} className="h-3 overflow-visible leading-3">
                        {calendar?.monthLabels.get(weekIndex) ?? ''}
                      </span>
                    ))}
                  </div>

                  <div className="grid grid-flow-col grid-rows-7 gap-1" style={{ gridTemplateColumns: `repeat(${calendarWeekCount}, minmax(0, 1fr))` }}>
                    {(calendar?.cells ?? []).map((cell, index) => {
                      if (!cell) {
                        return <div key={`pad-${index}`} className="h-4 rounded bg-transparent" />
                      }
                      return (
                        <div
                          key={cell.day}
                          className="h-4 rounded"
                          style={heatCellStyle(cell.count, maxCount)}
                          onMouseMove={(event) => {
                            const bounds = panelRef.current?.getBoundingClientRect()
                            if (!bounds) return
                            setHovered({
                              label: formatDateOnly(cell.day),
                              count: cell.count,
                              intensityPct: (cell.count / maxCount) * 100,
                              x: event.clientX - bounds.left,
                              y: event.clientY - bounds.top,
                            })
                          }}
                        />
                      )
                    })}
                  </div>
                </div>
              </div>
            </div>
          )}
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

function SignalRadarChart({ data }: { data: StatsSignalRadarResponse }) {
  const radarRef = useRef<HTMLDivElement | null>(null)
  const [hoveredCategory, setHoveredCategory] = useState<string | null>(null)
  const [hoveredPopup, setHoveredPopup] = useState<{ category: string; x: number; y: number } | null>(null)
  const axes = data.axes
  if (!axes.length || data.total <= 0 || data.max_count <= 0) {
    return <p className="mt-3 text-sm text-slate dark:text-slate-300">No classified signal data in selected window.</p>
  }

  const size = 460
  const center = size / 2
  const radius = 162
  const rings = [0.2, 0.4, 0.6, 0.8, 1]
  const step = (Math.PI * 2) / axes.length

  const coordinates = axes.map((axis, index) => {
    const angle = -Math.PI / 2 + index * step
    const normalized = axis.count / Math.max(1, data.max_count)
    const pointRadius = radius * normalized
    return {
      axis,
      index,
      angle,
      x: center + Math.cos(angle) * pointRadius,
      y: center + Math.sin(angle) * pointRadius,
      labelX: center + Math.cos(angle) * (radius + 20),
      labelY: center + Math.sin(angle) * (radius + 20),
    }
  })

  const polygonPoints = coordinates.map((point) => `${point.x},${point.y}`).join(' ')
  const hovered = coordinates.find((point) => point.axis.category === (hoveredPopup?.category ?? hoveredCategory)) ?? null
  const radarWidth = radarRef.current?.clientWidth ?? 520
  const radarHeight = radarRef.current?.clientHeight ?? 430
  const radarTooltipPosition = hoveredPopup
    ? positionTooltipNearCursor(hoveredPopup.x, hoveredPopup.y, radarWidth, radarHeight, 240, 70)
    : null

  return (
    <div className="mt-3 grid gap-4 lg:grid-cols-[1fr_280px]">
      <div
        ref={radarRef}
        className="relative rounded border border-slate/20 bg-white/70 p-2 dark:border-cyan-900/40 dark:bg-[#072019]/70"
        onMouseLeave={() => {
          setHoveredCategory(null)
          setHoveredPopup(null)
        }}
      >
        {hovered && hoveredPopup && radarTooltipPosition && (
          <div
            className="pointer-events-none absolute rounded border border-slate/25 bg-white/95 px-2 py-1.5 text-xs shadow dark:border-cyan-900/40 dark:bg-[#041612]/95"
            style={{ left: radarTooltipPosition.left, top: radarTooltipPosition.top }}
          >
            <p className="font-semibold">{formatCategoryLabel(hovered.axis.category)}</p>
            <p className="text-slate dark:text-slate-300">{hovered.axis.count} posts ({hovered.axis.pct.toFixed(1)}%)</p>
          </div>
        )}

        <svg viewBox={`0 0 ${size} ${size}`} className="mx-auto h-[420px] w-full max-w-[520px]">
          {rings.map((ring) => (
            <circle
              key={ring}
              cx={center}
              cy={center}
              r={radius * ring}
              fill="none"
              stroke="rgba(148, 163, 184, 0.28)"
              strokeWidth={1}
            />
          ))}

          {coordinates.map((point) => (
            <line
              key={`axis-line-${point.axis.category}`}
              x1={center}
              y1={center}
              x2={center + Math.cos(point.angle) * radius}
              y2={center + Math.sin(point.angle) * radius}
              stroke="rgba(148, 163, 184, 0.35)"
              strokeWidth={1}
            />
          ))}

          <polygon points={polygonPoints} fill="rgba(6,182,212,0.18)" stroke="rgba(6,182,212,0.9)" strokeWidth={2} />

          {coordinates.map((point) => {
            const isHovered = hoveredCategory === point.axis.category
            return (
              <circle
                key={`point-${point.axis.category}`}
                cx={point.x}
                cy={point.y}
                r={isHovered ? 6 : 4}
                fill={isHovered ? 'rgba(14, 165, 233, 1)' : 'rgba(6, 182, 212, 0.95)'}
                onMouseMove={(event) => {
                  const bounds = radarRef.current?.getBoundingClientRect()
                  if (!bounds) return
                  setHoveredCategory(point.axis.category)
                  setHoveredPopup({
                    category: point.axis.category,
                    x: event.clientX - bounds.left,
                    y: event.clientY - bounds.top,
                  })
                }}
              />
            )
          })}

          {coordinates.map((point) => (
            <text
              key={`label-${point.axis.category}`}
              x={point.labelX}
              y={point.labelY}
              fontSize={11}
              textAnchor={point.labelX >= center ? 'start' : 'end'}
              alignmentBaseline="middle"
              className={
                hoveredCategory === point.axis.category
                  ? 'fill-cyan dark:fill-[var(--tl-accent)]'
                  : 'fill-slate-500 dark:fill-[var(--tl-text-dim)]'
              }
            >
              {formatCategoryLabel(point.axis.category)}
            </text>
          ))}
        </svg>
      </div>

      <div className="rounded border border-slate/20 bg-white/70 p-3 dark:border-cyan-900/40 dark:bg-[#072019]/70">
        <p className="text-xs font-semibold uppercase text-slate dark:text-slate-300">
          Window: Last {data.window_days}d ({data.total} classified posts)
        </p>
        <div className="mt-2 space-y-1.5">
          {axes
            .slice()
            .sort((a, b) => b.count - a.count)
            .map((axis) => (
              <button
                key={axis.category}
                type="button"
                className={`w-full rounded border px-2 py-1.5 text-left text-xs ${
                  hoveredCategory === axis.category
                    ? 'border-cyan/60 bg-cyan/10 text-cyan dark:bg-cyan-950/40'
                    : 'border-slate/20 dark:border-cyan-900/40'
                }`}
                onMouseEnter={() => setHoveredCategory(axis.category)}
                onMouseLeave={() => setHoveredCategory(null)}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate">{formatCategoryLabel(axis.category)}</span>
                  <span className="font-semibold">{axis.count}</span>
                </div>
                <div className="mt-1 h-1.5 rounded bg-slate-200 dark:bg-[#0b2a23]">
                  <div
                    className="h-1.5 rounded bg-cyan"
                    style={{ width: axis.count > 0 ? `${Math.max(3, (axis.count / Math.max(1, data.max_count)) * 100)}%` : '0%' }}
                  />
                </div>
              </button>
            ))}
        </div>
      </div>
    </div>
  )
}

interface DailyCalendarCell {
  day: string
  count: number
}

interface DailyCalendarLayout {
  cells: Array<DailyCalendarCell | null>
  weekCount: number
  monthLabels: Map<number, string>
}

function buildDailyCalendar(rows: StatsActivityHeatmapResponse['rows']): DailyCalendarLayout {
  const dayCells: DailyCalendarCell[] = rows.map((row) => ({
    day: row.day,
    count: row.counts[0] ?? 0,
  }))

  if (!dayCells.length) {
    return { cells: [], weekCount: 0, monthLabels: new Map() }
  }

  const firstDate = parseIsoDay(dayCells[0].day)
  const leadingEmpty = firstDate.getUTCDay()
  const cells: Array<DailyCalendarCell | null> = [...Array.from({ length: leadingEmpty }, () => null), ...dayCells]
  const weekCount = Math.ceil(cells.length / 7)
  const trailingEmpty = weekCount * 7 - cells.length
  if (trailingEmpty > 0) {
    cells.push(...Array.from({ length: trailingEmpty }, () => null))
  }

  const monthLabels = new Map<number, string>()
  let lastMonthKey = ''
  for (let weekIndex = 0; weekIndex < weekCount; weekIndex += 1) {
    const weekStart = weekIndex * 7
    const candidate = cells.slice(weekStart, weekStart + 7).find((entry): entry is DailyCalendarCell => Boolean(entry))
    if (!candidate) continue
    const candidateDate = parseIsoDay(candidate.day)
    const monthKey = `${candidateDate.getUTCFullYear()}-${candidateDate.getUTCMonth()}`
    if (monthKey === lastMonthKey) continue
    monthLabels.set(weekIndex, new Intl.DateTimeFormat('en-GB', { month: 'short', timeZone: 'UTC' }).format(candidateDate))
    lastMonthKey = monthKey
  }

  return { cells, weekCount, monthLabels }
}

function parseIsoDay(value: string): Date {
  return new Date(`${value}T00:00:00Z`)
}

function heatCellStyle(count: number, maxCount: number) {
  if (count <= 0) {
    return { backgroundColor: 'rgba(148, 163, 184, 0.14)' }
  }
  const intensity = Math.min(1, count / Math.max(1, maxCount))
  const alpha = 0.2 + intensity * 0.75
  return { backgroundColor: `rgba(6, 182, 212, ${alpha.toFixed(3)})` }
}

function formatCategoryLabel(category: string) {
  return category
    .split('_')
    .map((part) => (part ? part[0].toUpperCase() + part.slice(1) : part))
    .join(' ')
}

function positionTooltipNearCursor(
  cursorX: number,
  cursorY: number,
  containerWidth: number,
  containerHeight: number,
  tooltipWidth: number,
  tooltipHeight: number,
) {
  const offset = 14
  const maxLeft = Math.max(8, containerWidth - tooltipWidth - 8)
  const maxTop = Math.max(8, containerHeight - tooltipHeight - 8)
  const left = Math.min(Math.max(8, cursorX + offset), maxLeft)
  const top = Math.min(Math.max(8, cursorY + offset), maxTop)
  return { left, top }
}

function StatCard({ label, value }: { label: string; value: number | string }) {
  return (
    <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
      <p className="text-xs uppercase text-slate dark:text-slate-400">{label}</p>
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
