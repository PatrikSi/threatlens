import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
import { Feed, StatsOverviewResponse } from '../types/api'

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
      <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#040913]/90">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="font-display text-2xl">Statistics</h2>
            <p className="text-sm text-slate dark:text-slate-300">Feed ingestion and article extraction analytics over time.</p>
          </div>
          <div className="grid gap-2 sm:grid-cols-2">
            <select
              value={days}
              onChange={(event) => setDays(Number(event.target.value))}
              className="rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#060d19]"
            >
              <option value={7}>Last 7 days</option>
              <option value={30}>Last 30 days</option>
              <option value={90}>Last 90 days</option>
              <option value={180}>Last 180 days</option>
            </select>
            <button
              type="button"
              className="rounded border border-slate/30 px-3 py-2 text-sm dark:border-cyan-900/40"
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
          className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#060d19]"
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
            className="underline"
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

          <div className="grid gap-4 lg:grid-cols-2">
            <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#040913]/90">
              <h3 className="font-display text-lg">Derived Health</h3>
              <div className="mt-3 grid gap-2 text-sm md:grid-cols-2">
                <Metric label="Extraction success" value={`${statsQuery.data.derived.extraction_success_rate_pct}%`} />
                <Metric label="Error rate" value={`${statsQuery.data.derived.error_rate_pct}%`} />
                <Metric label="Items last 24h" value={statsQuery.data.activity.items_last_24h} />
                <Metric label="Items last 7d" value={statsQuery.data.activity.items_last_7d} />
              </div>
            </section>

            <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#040913]/90">
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
                      <div className="h-2 rounded bg-slate-200 dark:bg-[#060d19]">
                        <div className="h-2 rounded bg-cyan" style={{ width: `${Math.max(2, pct)}%` }} />
                      </div>
                    </div>
                  )
                })}
              </div>
            </section>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#040913]/90">
              <h3 className="font-display text-lg">Daily Volume ({statsQuery.data.window_days}d)</h3>
              <div className="mt-3 max-h-80 overflow-auto space-y-2">
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

            <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#040913]/90">
              <h3 className="font-display text-lg">Top Domains</h3>
              <div className="mt-3 max-h-80 overflow-auto space-y-2">
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

          <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#040913]/90">
            <h3 className="font-display text-lg">Feed Share ({statsQuery.data.window_days}d)</h3>
            <div className="mt-3 max-h-64 overflow-auto space-y-2">
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

          <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#040913]/90">
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

function StatCard({ label, value }: { label: string; value: number | string }) {
  return (
    <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#040913]/90">
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
      <div className="h-2 rounded bg-slate-200 dark:bg-[#060d19]">
        <div className="h-2 rounded bg-cyan" style={{ width: value > 0 ? `${Math.max(2, widthPct)}%` : '0%' }} />
      </div>
    </div>
  )
}
