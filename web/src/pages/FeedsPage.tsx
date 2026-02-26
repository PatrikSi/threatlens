import { FormEvent, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { Feed } from '../types/api'

export function FeedsPage() {
  const queryClient = useQueryClient()
  const meQuery = useCurrentUser()
  const [name, setName] = useState('')
  const [url, setUrl] = useState('')
  const [interval, setInterval] = useState(1800)
  const canManage = meQuery.data?.role === 'admin' || meQuery.data?.role === 'analyst'

  const feedsQuery = useQuery({
    queryKey: ['feeds'],
    queryFn: () => apiFetch<Feed[]>('/feeds'),
  })

  const createFeed = useMutation({
    mutationFn: () =>
      apiFetch<Feed>('/feeds', {
        method: 'POST',
        body: JSON.stringify({
          name,
          url,
          fetch_interval_seconds: interval,
          enabled: true,
        }),
      }),
    onSuccess: () => {
      setName('')
      setUrl('')
      setInterval(1800)
      void queryClient.invalidateQueries({ queryKey: ['feeds'] })
    },
  })

  const updateFeed = useMutation({
    mutationFn: (payload: { id: string; body: Record<string, unknown> }) =>
      apiFetch<Feed>(`/feeds/${payload.id}`, {
        method: 'PATCH',
        body: JSON.stringify(payload.body),
      }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['feeds'] }),
  })

  const refreshFeed = useMutation({
    mutationFn: (id: string) => apiFetch(`/feeds/${id}/refresh`, { method: 'POST' }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['feeds'] }),
  })

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    createFeed.mutate()
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[420px_1fr]">
      <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
        <h2 className="font-display text-xl">Add Feed</h2>
        {!canManage && <p className="mt-2 text-sm text-amber-600">Viewer role cannot create or modify feeds.</p>}
        <form className="mt-3 space-y-3" onSubmit={onSubmit}>
          <div>
            <label className="text-sm font-semibold">Name</label>
            <input
              className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              disabled={!canManage}
            />
          </div>
          <div>
            <label className="text-sm font-semibold">URL</label>
            <input
              className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              required
              disabled={!canManage}
            />
          </div>
          <div>
            <label className="text-sm font-semibold">Fetch Interval (seconds)</label>
            <input
              className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
              type="number"
              min={60}
              value={interval}
              onChange={(e) => setInterval(Number(e.target.value))}
              required
              disabled={!canManage}
            />
          </div>
          <button
            className="rounded bg-ink px-3 py-2 text-white dark:bg-cyan dark:text-[#053c2e]"
            type="submit"
            disabled={createFeed.isPending || !canManage}
          >
            Add Feed
          </button>
        </form>
      </section>

      <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
        <h2 className="font-display text-xl">Configured Feeds</h2>
        <div className="mt-3 space-y-2">
          {feedsQuery.data?.map((feed) => (
            <div key={feed.id} className="rounded border border-slate/20 p-3 dark:border-cyan-900/40">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <p className="font-semibold">{feed.name}</p>
                  <p className="text-xs text-slate dark:text-slate-300">{feed.url}</p>
                </div>
                <div className="flex gap-2">
                  <button
                    className="rounded border border-slate/30 px-2 py-1 text-xs dark:border-cyan-900/40"
                    onClick={() => refreshFeed.mutate(feed.id)}
                    disabled={!canManage}
                  >
                    Refresh
                  </button>
                  <button
                    className="rounded border border-slate/30 px-2 py-1 text-xs dark:border-cyan-900/40"
                    onClick={() => updateFeed.mutate({ id: feed.id, body: { enabled: !feed.enabled } })}
                    disabled={!canManage}
                  >
                    {feed.enabled ? 'Disable' : 'Enable'}
                  </button>
                </div>
              </div>

              <div className="mt-3 flex items-center gap-2">
                <label className="text-xs font-semibold">Interval</label>
                <input
                  className="w-28 rounded border border-slate/30 bg-white px-2 py-1 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                  type="number"
                  min={60}
                  defaultValue={feed.fetch_interval_seconds}
                  onBlur={(e) => {
                    const value = Number(e.target.value)
                    if (Number.isFinite(value) && value >= 60 && value !== feed.fetch_interval_seconds) {
                      updateFeed.mutate({ id: feed.id, body: { fetch_interval_seconds: value } })
                    }
                  }}
                  disabled={!canManage}
                />
                <span className="text-xs text-slate dark:text-slate-300">s</span>
              </div>

              {feed.last_error && <p className="mt-2 text-xs text-red-600">Last error: {feed.last_error}</p>}
            </div>
          ))}

          {feedsQuery.isLoading && <p className="text-sm text-slate dark:text-slate-300">Loading feeds...</p>}
          {feedsQuery.isError && <p className="text-sm text-red-600">Failed to load feeds.</p>}
        </div>
      </section>
    </div>
  )
}
