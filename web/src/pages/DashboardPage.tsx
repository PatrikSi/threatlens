import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
import { useDebouncedValue } from '../hooks/useDebouncedValue'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { Feed, ItemDetail, ItemListResponse } from '../types/api'

type TimeRangeFilter = 'all' | '24h' | '7d' | '30d' | 'custom'
type ReadStatusFilter = 'all' | 'read' | 'unread'
type StarStatusFilter = 'all' | 'starred' | 'unstarred'
type TimeSort = 'published_at_desc' | 'published_at_asc' | 'first_seen_desc' | 'first_seen_asc'

export function DashboardPage() {
  const queryClient = useQueryClient()
  const meQuery = useCurrentUser()
  const [selectedFeedId, setSelectedFeedId] = useState<string>('')
  const [q, setQ] = useState('')
  const [readStatus, setReadStatus] = useState<ReadStatusFilter>('all')
  const [starStatus, setStarStatus] = useState<StarStatusFilter>('all')
  const [timeRange, setTimeRange] = useState<TimeRangeFilter>('all')
  const [customSinceDate, setCustomSinceDate] = useState('')
  const [customUntilDate, setCustomUntilDate] = useState('')
  const [sort, setSort] = useState<TimeSort>('published_at_desc')
  const [page, setPage] = useState(1)
  const pageSize = 25
  const [selectedItemId, setSelectedItemId] = useState<string>('')
  const [noteDraft, setNoteDraft] = useState('')
  const canManage = meQuery.data?.role === 'admin' || meQuery.data?.role === 'analyst'

  const debouncedQ = useDebouncedValue(q)

  const { sinceIso, untilIso } = useMemo(
    () => deriveTimeWindow(timeRange, customSinceDate, customUntilDate),
    [timeRange, customSinceDate, customUntilDate],
  )

  const feedsQuery = useQuery({
    queryKey: ['feeds'],
    queryFn: () => apiFetch<Feed[]>('/feeds'),
  })

  const updateRead = useMutation({
    mutationFn: (payload: { itemId: string; isRead: boolean }) =>
      apiFetch(`/items/${payload.itemId}/read`, {
        method: 'POST',
        body: JSON.stringify({ is_read: payload.isRead }),
      }),
    onSuccess: (_data, variables) => invalidateLists(queryClient, variables.itemId),
  })

  const updateStar = useMutation({
    mutationFn: (payload: { itemId: string; isStarred: boolean }) =>
      apiFetch(`/items/${payload.itemId}/star`, {
        method: 'POST',
        body: JSON.stringify({ is_starred: payload.isStarred }),
      }),
    onSuccess: (_data, variables) => invalidateLists(queryClient, variables.itemId),
  })

  const updateNote = useMutation({
    mutationFn: (payload: { itemId: string; note: string | null }) =>
      apiFetch(`/items/${payload.itemId}/note`, {
        method: 'POST',
        body: JSON.stringify({ note: payload.note }),
      }),
    onSuccess: (_data, variables) => invalidateLists(queryClient, variables.itemId),
  })

  const itemsQuery = useQuery({
    queryKey: ['items', selectedFeedId, debouncedQ, readStatus, starStatus, sinceIso, untilIso, sort, page],
    queryFn: () => {
      const params = new URLSearchParams()
      params.set('page', String(page))
      params.set('page_size', String(pageSize))
      params.set('sort', sort)

      if (selectedFeedId) params.set('feed_id', selectedFeedId)
      if (debouncedQ) params.set('q', debouncedQ)
      if (sinceIso) params.set('since', sinceIso)
      if (untilIso) params.set('until', untilIso)

      if (readStatus === 'read') params.set('is_read', 'true')
      if (readStatus === 'unread') params.set('is_read', 'false')
      if (starStatus === 'starred') params.set('is_starred', 'true')
      if (starStatus === 'unstarred') params.set('is_starred', 'false')

      return apiFetch<ItemListResponse>(`/items?${params.toString()}`)
    },
  })

  useEffect(() => {
    const items = itemsQuery.data?.items ?? []
    if (!items.length) {
      setSelectedItemId('')
      return
    }

    const selectedExists = items.some((item) => item.id === selectedItemId)
    if (!selectedExists) {
      setSelectedItemId(items[0].id)
    }
  }, [itemsQuery.data?.items, selectedItemId])

  const detailQuery = useQuery({
    queryKey: ['item', selectedItemId],
    enabled: Boolean(selectedItemId),
    queryFn: () => apiFetch<ItemDetail>(`/items/${selectedItemId}`),
  })

  useEffect(() => {
    setNoteDraft(detailQuery.data?.state.note ?? '')
  }, [detailQuery.data?.state.note])

  const totalPages = useMemo(() => {
    const total = itemsQuery.data?.total ?? 0
    return Math.max(1, Math.ceil(total / pageSize))
  }, [itemsQuery.data?.total])

  const handleSelectItem = (itemId: string, isRead: boolean) => {
    setSelectedItemId(itemId)
    if (!isRead && canManage) {
      updateRead.mutate({ itemId, isRead: true })
    }
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[280px_1fr_1.2fr]">
      <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#040913]/90">
        <h2 className="font-display text-xl">Filters</h2>

        <label className="mt-3 block text-xs font-bold uppercase tracking-wide text-slate">Feed</label>
        <select
          className="mt-1 w-full rounded border border-slate/25 bg-white px-2 py-2 dark:border-cyan-900/40 dark:bg-[#060d19]"
          value={selectedFeedId}
          onChange={(event) => {
            setPage(1)
            setSelectedFeedId(event.target.value)
          }}
        >
          <option value="">All feeds</option>
          {feedsQuery.data?.map((feed) => (
            <option key={feed.id} value={feed.id}>
              {feed.name}
            </option>
          ))}
        </select>

        <label className="mt-3 block text-xs font-bold uppercase tracking-wide text-slate">Keyword</label>
        <input
          value={q}
          onChange={(event) => {
            setPage(1)
            setQ(event.target.value)
          }}
          placeholder="ransomware, cve..."
          className="mt-1 w-full rounded border border-slate/25 bg-white px-2 py-2 dark:border-cyan-900/40 dark:bg-[#060d19]"
        />

        <label className="mt-3 block text-xs font-bold uppercase tracking-wide text-slate">Time Range</label>
        <select
          className="mt-1 w-full rounded border border-slate/25 bg-white px-2 py-2 dark:border-cyan-900/40 dark:bg-[#060d19]"
          value={timeRange}
          onChange={(event) => {
            setPage(1)
            setTimeRange(event.target.value as TimeRangeFilter)
          }}
        >
          <option value="all">All time</option>
          <option value="24h">Last 24 hours</option>
          <option value="7d">Last 7 days</option>
          <option value="30d">Last 30 days</option>
          <option value="custom">Custom range</option>
        </select>

        {timeRange === 'custom' && (
          <>
            <label className="mt-3 block text-xs font-bold uppercase tracking-wide text-slate">From</label>
            <input
              type="date"
              className="mt-1 w-full rounded border border-slate/25 bg-white px-2 py-2 dark:border-cyan-900/40 dark:bg-[#060d19]"
              value={customSinceDate}
              onChange={(event) => {
                setPage(1)
                setCustomSinceDate(event.target.value)
              }}
            />
            <label className="mt-3 block text-xs font-bold uppercase tracking-wide text-slate">To</label>
            <input
              type="date"
              className="mt-1 w-full rounded border border-slate/25 bg-white px-2 py-2 dark:border-cyan-900/40 dark:bg-[#060d19]"
              value={customUntilDate}
              onChange={(event) => {
                setPage(1)
                setCustomUntilDate(event.target.value)
              }}
            />
          </>
        )}

        <label className="mt-3 block text-xs font-bold uppercase tracking-wide text-slate">Read Status</label>
        <select
          className="mt-1 w-full rounded border border-slate/25 bg-white px-2 py-2 dark:border-cyan-900/40 dark:bg-[#060d19]"
          value={readStatus}
          onChange={(event) => {
            setPage(1)
            setReadStatus(event.target.value as ReadStatusFilter)
          }}
        >
          <option value="all">All</option>
          <option value="unread">Unread only</option>
          <option value="read">Read only</option>
        </select>

        <label className="mt-3 block text-xs font-bold uppercase tracking-wide text-slate">Starred</label>
        <select
          className="mt-1 w-full rounded border border-slate/25 bg-white px-2 py-2 dark:border-cyan-900/40 dark:bg-[#060d19]"
          value={starStatus}
          onChange={(event) => {
            setPage(1)
            setStarStatus(event.target.value as StarStatusFilter)
          }}
        >
          <option value="all">All</option>
          <option value="starred">Starred only</option>
          <option value="unstarred">Unstarred only</option>
        </select>

        <label className="mt-3 block text-xs font-bold uppercase tracking-wide text-slate">Sort</label>
        <select
          className="mt-1 w-full rounded border border-slate/25 bg-white px-2 py-2 dark:border-cyan-900/40 dark:bg-[#060d19]"
          value={sort}
          onChange={(event) => {
            setPage(1)
            setSort(event.target.value as TimeSort)
          }}
        >
          <option value="published_at_desc">Published newest first</option>
          <option value="published_at_asc">Published oldest first</option>
          <option value="first_seen_desc">Seen newest first</option>
          <option value="first_seen_asc">Seen oldest first</option>
        </select>
      </section>

      <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#040913]/90">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="font-display text-xl">Items</h2>
          <span className="text-xs text-slate dark:text-slate-300">{itemsQuery.data?.total ?? 0} total</span>
        </div>

        <div className="space-y-2">
          {itemsQuery.data?.items.map((item) => (
            <button
              key={item.id}
              className={`w-full rounded border p-3 text-left transition ${
                selectedItemId === item.id
                  ? 'border-cyan bg-cyan/5 dark:bg-cyan/10'
                  : 'border-slate/20 hover:border-slate/40 dark:border-cyan-900/40 dark:hover:border-slate-500'
              } ${item.is_read ? 'opacity-70' : ''}`}
              onClick={() => handleSelectItem(item.id, item.is_read)}
            >
              <div className="flex items-center justify-between gap-2">
                <p className="line-clamp-1 font-semibold">{item.title}</p>
                <span className="text-xs text-slate dark:text-slate-300">{item.feed_name}</span>
              </div>
              <p className="mt-1 text-xs text-slate dark:text-slate-300">
                Published: {formatPublishedAt(item.published_at)}
              </p>
              <p className="mt-1 line-clamp-2 text-sm text-slate dark:text-slate-300">{item.summary || 'No summary available.'}</p>
              <div className="mt-2 flex items-center gap-2 text-xs">
                {item.is_starred && <span className="rounded bg-amber-100 px-2 py-0.5 text-amber-700">Starred</span>}
                {!item.is_read && <span className="rounded bg-cyan/20 px-2 py-0.5 text-cyan">Unread</span>}
                <span className="rounded bg-slate/10 px-2 py-0.5 text-slate">{item.status}</span>
              </div>
            </button>
          ))}

          {itemsQuery.isLoading && <p className="text-sm text-slate dark:text-slate-300">Loading items...</p>}
          {itemsQuery.isError && <p className="text-sm text-red-600">Failed to load items.</p>}
        </div>

        <div className="mt-4 flex items-center justify-between text-sm">
          <button
            className="rounded border border-slate/30 px-2 py-1 disabled:opacity-50 dark:border-cyan-900/40"
            disabled={page <= 1}
            onClick={() => setPage((p) => p - 1)}
          >
            Prev
          </button>
          <span>
            Page {page} / {totalPages}
          </span>
          <button
            className="rounded border border-slate/30 px-2 py-1 disabled:opacity-50 dark:border-cyan-900/40"
            disabled={page >= totalPages}
            onClick={() => setPage((p) => p + 1)}
          >
            Next
          </button>
        </div>
      </section>

      <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#040913]/90">
        {!detailQuery.data && <p className="text-sm text-slate dark:text-slate-300">Select an item to inspect details.</p>}

        {detailQuery.data && (
          <>
            <h2 className="font-display text-2xl">{detailQuery.data.title}</h2>
            <p className="mt-1 text-sm text-slate dark:text-slate-300">{detailQuery.data.feed_name}</p>
            {!canManage && <p className="mt-2 text-sm text-amber-600">Viewer role is read-only.</p>}

            <div className="mt-3 flex flex-wrap gap-2">
              <button
                className="rounded border border-slate/30 px-2 py-1 text-sm dark:border-cyan-900/40"
                disabled={!canManage}
                onClick={() =>
                  updateRead.mutate({
                    itemId: detailQuery.data.id,
                    isRead: !detailQuery.data.state.is_read,
                  })
                }
              >
                {detailQuery.data.state.is_read ? 'Mark Unread' : 'Mark Read'}
              </button>
              <button
                className="rounded border border-slate/30 px-2 py-1 text-sm dark:border-cyan-900/40"
                disabled={!canManage}
                onClick={() =>
                  updateStar.mutate({
                    itemId: detailQuery.data.id,
                    isStarred: !detailQuery.data.state.is_starred,
                  })
                }
              >
                {detailQuery.data.state.is_starred ? 'Unstar' : 'Star'}
              </button>
            </div>

            <label className="mt-3 block text-xs font-bold uppercase tracking-wide text-slate">Analyst Note</label>
            <textarea
              className="mt-1 h-24 w-full rounded border border-slate/30 bg-white px-2 py-2 dark:border-cyan-900/40 dark:bg-[#060d19]"
              value={noteDraft}
              onChange={(event) => setNoteDraft(event.target.value)}
              disabled={!canManage}
            />
            <button
              className="mt-2 rounded bg-ink px-3 py-1.5 text-sm font-semibold text-white dark:bg-cyan dark:text-ink"
              onClick={() => updateNote.mutate({ itemId: detailQuery.data.id, note: noteDraft || null })}
              disabled={!canManage}
            >
              Save Note
            </button>

            <div className="mt-4 rounded border border-slate/20 bg-sand/50 p-3 dark:border-cyan-900/40 dark:bg-[#060d19]/90">
              <p className="text-xs font-bold uppercase tracking-wide text-slate dark:text-slate-300">RSS Summary</p>
              <p className="mt-1 whitespace-pre-wrap text-sm">{detailQuery.data.summary || 'No summary.'}</p>
            </div>

            <div className="mt-4 rounded border border-slate/20 bg-white p-3 dark:border-cyan-900/40 dark:bg-[#060d19]/90">
              <p className="text-xs font-bold uppercase tracking-wide text-slate dark:text-slate-300">Extracted Full Text</p>
              {detailQuery.data.article?.text ? (
                <pre className="mt-2 max-h-[450px] overflow-auto whitespace-pre-wrap text-sm leading-6">
                  {detailQuery.data.article.text}
                </pre>
              ) : (
                <p className="mt-2 text-sm text-slate dark:text-slate-300">No extracted article text available yet.</p>
              )}
              {detailQuery.data.article?.error && (
                <p className="mt-2 text-sm text-red-600">Extraction error: {detailQuery.data.article.error}</p>
              )}
            </div>
          </>
        )}
      </section>
    </div>
  )
}

function deriveTimeWindow(timeRange: TimeRangeFilter, customSinceDate: string, customUntilDate: string) {
  if (timeRange === 'all') {
    return { sinceIso: '', untilIso: '' }
  }

  if (timeRange === 'custom') {
    const since = parseStartOfDay(customSinceDate)
    const until = parseEndOfDay(customUntilDate)

    if (since && until && since > until) {
      return { sinceIso: until.toISOString(), untilIso: since.toISOString() }
    }

    return {
      sinceIso: since ? since.toISOString() : '',
      untilIso: until ? until.toISOString() : '',
    }
  }

  const now = new Date()
  const since = new Date(now)

  if (timeRange === '24h') {
    since.setTime(now.getTime() - 24 * 60 * 60 * 1000)
  }

  if (timeRange === '7d') {
    since.setTime(now.getTime() - 7 * 24 * 60 * 60 * 1000)
  }

  if (timeRange === '30d') {
    since.setTime(now.getTime() - 30 * 24 * 60 * 60 * 1000)
  }

  return { sinceIso: since.toISOString(), untilIso: now.toISOString() }
}

function parseStartOfDay(date: string): Date | null {
  if (!date) return null
  const parsed = new Date(`${date}T00:00:00`)
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

function parseEndOfDay(date: string): Date | null {
  if (!date) return null
  const parsed = new Date(`${date}T23:59:59.999`)
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

function invalidateLists(queryClient: ReturnType<typeof useQueryClient>, itemId: string) {
  void queryClient.invalidateQueries({ queryKey: ['items'] })
  if (itemId) {
    void queryClient.invalidateQueries({ queryKey: ['item', itemId] })
  }
}

function formatPublishedAt(publishedAt: string | null) {
  if (!publishedAt) {
    return 'Unknown'
  }

  const parsed = new Date(publishedAt)
  if (Number.isNaN(parsed.getTime())) {
    return 'Unknown'
  }

  return parsed.toLocaleString()
}
