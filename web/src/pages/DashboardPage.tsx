import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
import { useDebouncedValue } from '../hooks/useDebouncedValue'
import { Feed, ItemDetail, ItemListResponse } from '../types/api'

export function DashboardPage() {
  const queryClient = useQueryClient()
  const [selectedFeedId, setSelectedFeedId] = useState<string>('')
  const [q, setQ] = useState('')
  const [unreadOnly, setUnreadOnly] = useState(false)
  const [starredOnly, setStarredOnly] = useState(false)
  const [page, setPage] = useState(1)
  const pageSize = 25
  const [selectedItemId, setSelectedItemId] = useState<string>('')
  const [noteDraft, setNoteDraft] = useState('')

  const debouncedQ = useDebouncedValue(q)

  const feedsQuery = useQuery({
    queryKey: ['feeds'],
    queryFn: () => apiFetch<Feed[]>('/feeds'),
  })

  const itemsQuery = useQuery({
    queryKey: ['items', selectedFeedId, debouncedQ, unreadOnly, starredOnly, page],
    queryFn: () => {
      const params = new URLSearchParams()
      params.set('page', String(page))
      params.set('page_size', String(pageSize))
      params.set('sort', 'published_at_desc')
      if (selectedFeedId) params.set('feed_id', selectedFeedId)
      if (debouncedQ) params.set('q', debouncedQ)
      if (unreadOnly) params.set('is_read', 'false')
      if (starredOnly) params.set('is_starred', 'true')

      return apiFetch<ItemListResponse>(`/items?${params.toString()}`)
    },
  })

  useEffect(() => {
    const first = itemsQuery.data?.items[0]
    if (first && !selectedItemId) {
      setSelectedItemId(first.id)
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

  const updateRead = useMutation({
    mutationFn: (payload: { itemId: string; isRead: boolean }) =>
      apiFetch(`/items/${payload.itemId}/read`, {
        method: 'POST',
        body: JSON.stringify({ is_read: payload.isRead }),
      }),
    onSuccess: () => invalidateLists(queryClient, selectedItemId),
  })

  const updateStar = useMutation({
    mutationFn: (payload: { itemId: string; isStarred: boolean }) =>
      apiFetch(`/items/${payload.itemId}/star`, {
        method: 'POST',
        body: JSON.stringify({ is_starred: payload.isStarred }),
      }),
    onSuccess: () => invalidateLists(queryClient, selectedItemId),
  })

  const updateNote = useMutation({
    mutationFn: (payload: { itemId: string; note: string | null }) =>
      apiFetch(`/items/${payload.itemId}/note`, {
        method: 'POST',
        body: JSON.stringify({ note: payload.note }),
      }),
    onSuccess: () => invalidateLists(queryClient, selectedItemId),
  })

  const totalPages = useMemo(() => {
    const total = itemsQuery.data?.total ?? 0
    return Math.max(1, Math.ceil(total / pageSize))
  }, [itemsQuery.data?.total])

  return (
    <div className="grid gap-4 lg:grid-cols-[280px_1fr_1.2fr]">
      <section className="rounded-xl border border-slate/20 bg-white/80 p-4">
        <h2 className="font-display text-xl">Filters</h2>

        <label className="mt-3 block text-xs font-bold uppercase tracking-wide text-slate">Feed</label>
        <select
          className="mt-1 w-full rounded border border-slate/25 px-2 py-2"
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
          className="mt-1 w-full rounded border border-slate/25 px-2 py-2"
        />

        <label className="mt-3 flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={unreadOnly}
            onChange={(event) => {
              setPage(1)
              setUnreadOnly(event.target.checked)
            }}
          />
          Unread only
        </label>
        <label className="mt-1 flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={starredOnly}
            onChange={(event) => {
              setPage(1)
              setStarredOnly(event.target.checked)
            }}
          />
          Starred only
        </label>
      </section>

      <section className="rounded-xl border border-slate/20 bg-white/80 p-4">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="font-display text-xl">Items</h2>
          <span className="text-xs text-slate">{itemsQuery.data?.total ?? 0} total</span>
        </div>

        <div className="space-y-2">
          {itemsQuery.data?.items.map((item) => (
            <button
              key={item.id}
              className={`w-full rounded border p-3 text-left transition ${
                selectedItemId === item.id ? 'border-cyan bg-cyan/5' : 'border-slate/20 hover:border-slate/40'
              } ${item.is_read ? 'opacity-70' : ''}`}
              onClick={() => setSelectedItemId(item.id)}
            >
              <div className="flex items-center justify-between gap-2">
                <p className="line-clamp-1 font-semibold">{item.title}</p>
                <span className="text-xs text-slate">{item.feed_name}</span>
              </div>
              <p className="mt-1 line-clamp-2 text-sm text-slate">{item.summary || 'No summary available.'}</p>
              <div className="mt-2 flex items-center gap-2 text-xs">
                {item.is_starred && <span className="rounded bg-amber-100 px-2 py-0.5 text-amber-700">Starred</span>}
                {!item.is_read && <span className="rounded bg-cyan/20 px-2 py-0.5 text-cyan">Unread</span>}
                <span className="rounded bg-slate/10 px-2 py-0.5 text-slate">{item.status}</span>
              </div>
            </button>
          ))}

          {itemsQuery.isLoading && <p className="text-sm text-slate">Loading items...</p>}
          {itemsQuery.isError && <p className="text-sm text-red-600">Failed to load items.</p>}
        </div>

        <div className="mt-4 flex items-center justify-between text-sm">
          <button
            className="rounded border border-slate/30 px-2 py-1 disabled:opacity-50"
            disabled={page <= 1}
            onClick={() => setPage((p) => p - 1)}
          >
            Prev
          </button>
          <span>
            Page {page} / {totalPages}
          </span>
          <button
            className="rounded border border-slate/30 px-2 py-1 disabled:opacity-50"
            disabled={page >= totalPages}
            onClick={() => setPage((p) => p + 1)}
          >
            Next
          </button>
        </div>
      </section>

      <section className="rounded-xl border border-slate/20 bg-white/80 p-4">
        {!detailQuery.data && <p className="text-sm text-slate">Select an item to inspect details.</p>}

        {detailQuery.data && (
          <>
            <h2 className="font-display text-2xl">{detailQuery.data.title}</h2>
            <p className="mt-1 text-sm text-slate">{detailQuery.data.feed_name}</p>

            <div className="mt-3 flex flex-wrap gap-2">
              <button
                className="rounded border border-slate/30 px-2 py-1 text-sm"
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
                className="rounded border border-slate/30 px-2 py-1 text-sm"
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
              className="mt-1 h-24 w-full rounded border border-slate/30 px-2 py-2"
              value={noteDraft}
              onChange={(event) => setNoteDraft(event.target.value)}
            />
            <button
              className="mt-2 rounded bg-ink px-3 py-1.5 text-sm font-semibold text-white"
              onClick={() => updateNote.mutate({ itemId: detailQuery.data.id, note: noteDraft || null })}
            >
              Save Note
            </button>

            <div className="mt-4 rounded border border-slate/20 bg-sand/50 p-3">
              <p className="text-xs font-bold uppercase tracking-wide text-slate">RSS Summary</p>
              <p className="mt-1 whitespace-pre-wrap text-sm">{detailQuery.data.summary || 'No summary.'}</p>
            </div>

            <div className="mt-4 rounded border border-slate/20 bg-white p-3">
              <p className="text-xs font-bold uppercase tracking-wide text-slate">Extracted Full Text</p>
              {detailQuery.data.article?.text ? (
                <pre className="mt-2 max-h-[450px] overflow-auto whitespace-pre-wrap text-sm leading-6">
                  {detailQuery.data.article.text}
                </pre>
              ) : (
                <p className="mt-2 text-sm text-slate">No extracted article text available yet.</p>
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

function invalidateLists(queryClient: ReturnType<typeof useQueryClient>, selectedItemId: string) {
  void queryClient.invalidateQueries({ queryKey: ['items'] })
  if (selectedItemId) {
    void queryClient.invalidateQueries({ queryKey: ['item', selectedItemId] })
  }
}
