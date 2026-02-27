import { ChangeEvent, FormEvent, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { Feed, FeedExportResponse, FeedImportEntry, FeedImportResponse, FeedMetadataResponse } from '../types/api'
import { feedHealthBadgeClass, resolveFeedHealth } from '../utils/feedHealth'

type FeedSort = 'name_asc' | 'name_desc' | 'last_fetch_desc' | 'last_fetch_asc' | 'created_desc'
type FeedFetchMode = 'interval' | 'schedule'

export function FeedsPage() {
  const queryClient = useQueryClient()
  const meQuery = useCurrentUser()
  const canManage = meQuery.data?.role === 'admin' || meQuery.data?.role === 'analyst'

  const [name, setName] = useState('')
  const [url, setUrl] = useState('')
  const [description, setDescription] = useState('')
  const [siteUrl, setSiteUrl] = useState('')
  const [language, setLanguage] = useState('')
  const [fetchMode, setFetchMode] = useState<FeedFetchMode>('interval')
  const [interval, setInterval] = useState(1800)
  const [scheduleCron, setScheduleCron] = useState('0 * * * *')

  const [search, setSearch] = useState('')
  const [sort, setSort] = useState<FeedSort>('created_desc')

  const [overwriteExisting, setOverwriteExisting] = useState(false)
  const [importData, setImportData] = useState<FeedImportEntry[] | null>(null)
  const [importFilename, setImportFilename] = useState('')
  const [importError, setImportError] = useState<string>('')
  const [lastImportResult, setLastImportResult] = useState<FeedImportResponse | null>(null)

  const feedsQuery = useQuery({
    queryKey: ['feeds'],
    queryFn: () => apiFetch<Feed[]>('/feeds'),
  })

  const detectMetadata = useMutation({
    mutationFn: (feedUrl: string) =>
      apiFetch<FeedMetadataResponse>('/feeds/metadata', {
        method: 'POST',
        body: JSON.stringify({ url: feedUrl }),
      }),
    onSuccess: (metadata) => {
      if (!name.trim() && metadata.name) {
        setName(metadata.name)
      }
      setDescription(metadata.description || '')
      setSiteUrl(metadata.site_url || '')
      setLanguage(metadata.language || '')
    },
  })

  const createFeed = useMutation({
    mutationFn: () =>
      apiFetch<Feed>('/feeds', {
        method: 'POST',
        body: JSON.stringify({
          name: name.trim() || null,
          url,
          description: description.trim() || null,
          site_url: siteUrl.trim() || null,
          language: language.trim() || null,
          fetch_mode: fetchMode,
          fetch_interval_seconds: fetchMode === 'interval' ? interval : null,
          schedule_cron: fetchMode === 'schedule' ? scheduleCron.trim() : null,
          enabled: true,
        }),
      }),
    onSuccess: () => {
      setName('')
      setUrl('')
      setDescription('')
      setSiteUrl('')
      setLanguage('')
      setFetchMode('interval')
      setInterval(1800)
      setScheduleCron('0 * * * *')
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

  const importFeeds = useMutation({
    mutationFn: () =>
      apiFetch<FeedImportResponse>('/feeds/import', {
        method: 'POST',
        body: JSON.stringify({
          feeds: importData ?? [],
          overwrite_existing: overwriteExisting,
        }),
      }),
    onSuccess: (result) => {
      setLastImportResult(result)
      setImportData(null)
      setImportFilename('')
      setImportError('')
      void queryClient.invalidateQueries({ queryKey: ['feeds'] })
    },
  })

  const exportFeeds = useMutation({
    mutationFn: () => apiFetch<FeedExportResponse>('/feeds/export'),
    onSuccess: (payload) => {
      const body = JSON.stringify(payload, null, 2)
      const blob = new Blob([body], { type: 'application/json' })
      const objectUrl = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = objectUrl
      anchor.download = `threatlens-feeds-${new Date().toISOString().slice(0, 10)}.json`
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      URL.revokeObjectURL(objectUrl)
    },
  })

  const filteredFeeds = useMemo(() => {
    const term = search.trim().toLowerCase()
    const source = feedsQuery.data ?? []

    const filtered = term
      ? source.filter((feed) => {
          const haystack = [feed.name, feed.url, feed.description || '', feed.site_url || '', feed.language || '']
            .join(' ')
            .toLowerCase()
          return haystack.includes(term)
        })
      : source.slice()

    filtered.sort((a, b) => {
      if (sort === 'name_asc') return a.name.localeCompare(b.name)
      if (sort === 'name_desc') return b.name.localeCompare(a.name)
      if (sort === 'last_fetch_asc') return timestamp(a.last_fetch_at) - timestamp(b.last_fetch_at)
      if (sort === 'last_fetch_desc') return timestamp(b.last_fetch_at) - timestamp(a.last_fetch_at)
      return timestamp(b.created_at) - timestamp(a.created_at)
    })

    return filtered
  }, [feedsQuery.data, search, sort])

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    createFeed.mutate()
  }

  const onDetectMetadata = () => {
    if (!url.trim()) return
    detectMetadata.mutate(url.trim())
  }

  const onImportFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return

    setImportError('')
    setLastImportResult(null)

    try {
      const text = await file.text()
      const parsed = JSON.parse(text) as unknown
      const entries = parseImportEntries(parsed)
      setImportData(entries)
      setImportFilename(file.name)
    } catch (error) {
      setImportData(null)
      setImportFilename('')
      setImportError((error as Error).message)
    }
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[460px_1fr]">
      <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
        <h2 className="font-display text-xl">Add Feed</h2>
        {!canManage && <p className="mt-2 text-sm text-amber-600">Viewer role cannot create or modify feeds.</p>}

        <form className="mt-3 space-y-3" onSubmit={onSubmit}>
          <div>
            <label className="text-sm font-semibold">RSS URL</label>
            <div className="mt-1 flex gap-2">
              <input
                className="w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={url}
                onChange={(event) => setUrl(event.target.value)}
                required
                disabled={!canManage}
                placeholder="https://example.com/feed.xml"
              />
              <button
                type="button"
                className="rounded border border-slate/30 px-3 py-2 text-xs dark:border-cyan-900/40"
                disabled={!canManage || !url.trim() || detectMetadata.isPending}
                onClick={onDetectMetadata}
              >
                Detect
              </button>
            </div>
            {detectMetadata.isError && <p className="mt-1 text-xs text-red-600">Failed to detect feed metadata.</p>}
          </div>

          <div>
            <label className="text-sm font-semibold">Name (auto-filled)</label>
            <input
              className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
              value={name}
              onChange={(event) => setName(event.target.value)}
              disabled={!canManage}
              placeholder="Leave blank to auto-detect"
            />
          </div>

          <div>
            <label className="text-sm font-semibold">Description</label>
            <textarea
              className="mt-1 h-20 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              disabled={!canManage}
              placeholder="Detected from feed metadata"
            />
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label className="text-sm font-semibold">Site URL</label>
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={siteUrl}
                onChange={(event) => setSiteUrl(event.target.value)}
                disabled={!canManage}
              />
            </div>
            <div>
              <label className="text-sm font-semibold">Language</label>
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={language}
                onChange={(event) => setLanguage(event.target.value)}
                disabled={!canManage}
                placeholder="en-US"
              />
            </div>
          </div>

          <div>
            <label className="text-sm font-semibold">Fetch Mode</label>
            <select
              className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
              value={fetchMode}
              onChange={(event) => setFetchMode(event.target.value as FeedFetchMode)}
              disabled={!canManage}
            >
              <option value="interval">Every X seconds</option>
              <option value="schedule">Cron schedule</option>
            </select>
          </div>

          {fetchMode === 'interval' ? (
            <div>
              <label className="text-sm font-semibold">Fetch Interval (seconds)</label>
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                type="number"
                min={60}
                value={interval}
                onChange={(event) => setInterval(Number(event.target.value))}
                required
                disabled={!canManage}
              />
            </div>
          ) : (
            <div>
              <label className="text-sm font-semibold">Cron Schedule</label>
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={scheduleCron}
                onChange={(event) => setScheduleCron(event.target.value)}
                required
                disabled={!canManage}
                placeholder="0 * * * *"
              />
              <p className="mt-1 text-xs text-slate dark:text-slate-300">Example: <code>*/15 * * * *</code> for every 15 minutes.</p>
            </div>
          )}

          <button
            className="rounded bg-ink px-3 py-2 text-white dark:bg-cyan dark:text-[#053c2e]"
            type="submit"
            disabled={createFeed.isPending || !canManage}
          >
            Add Feed
          </button>
          {createFeed.isError && <p className="text-sm text-red-600">Failed to add feed.</p>}
        </form>
      </section>

      <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="font-display text-xl">Configured Feeds</h2>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              className="rounded border border-slate/30 px-3 py-1.5 text-xs dark:border-cyan-900/40"
              onClick={() => exportFeeds.mutate()}
              disabled={exportFeeds.isPending}
            >
              Export JSON
            </button>
            <label className="rounded border border-slate/30 px-3 py-1.5 text-xs dark:border-cyan-900/40">
              Import JSON
              <input type="file" accept="application/json" className="hidden" onChange={onImportFile} disabled={!canManage} />
            </label>
            <button
              type="button"
              className="rounded bg-ink px-3 py-1.5 text-xs text-white disabled:opacity-50 dark:bg-cyan dark:text-[#053c2e]"
              disabled={!canManage || !importData || importFeeds.isPending}
              onClick={() => importFeeds.mutate()}
            >
              Run Import
            </button>
          </div>
        </div>

        <div className="mt-2 flex flex-wrap items-center gap-3">
          <input
            className="min-w-64 flex-1 rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search feeds"
          />
          <select
            className="rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
            value={sort}
            onChange={(event) => setSort(event.target.value as FeedSort)}
          >
            <option value="created_desc">Newest created</option>
            <option value="name_asc">Name A-Z</option>
            <option value="name_desc">Name Z-A</option>
            <option value="last_fetch_desc">Last fetched newest</option>
            <option value="last_fetch_asc">Last fetched oldest</option>
          </select>
          <label className="flex items-center gap-2 text-xs text-slate dark:text-slate-300">
            <input
              type="checkbox"
              checked={overwriteExisting}
              onChange={(event) => setOverwriteExisting(event.target.checked)}
              disabled={!canManage}
            />
            Overwrite existing on import
          </label>
        </div>

        {importFilename && <p className="mt-2 text-xs text-slate dark:text-slate-300">Loaded: {importFilename}</p>}
        {importError && <p className="mt-2 text-xs text-red-600">Import parse error: {importError}</p>}
        {lastImportResult && (
          <p className="mt-2 text-xs text-slate dark:text-slate-300">
            Import result: created {lastImportResult.created}, updated {lastImportResult.updated}, skipped {lastImportResult.skipped}, errors {lastImportResult.errors.length}
          </p>
        )}

        <div className="mt-3 space-y-2">
          {filteredFeeds.map((feed) => {
            const health = resolveFeedHealth(feed)
            return (
            <div key={feed.id} className="rounded border border-slate/20 p-3 dark:border-cyan-900/40">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <div className="flex items-center gap-2">
                    <p className="font-semibold">{feed.name}</p>
                    <span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${feedHealthBadgeClass(health.status)}`}>
                      {health.label}
                    </span>
                  </div>
                  <p className="text-xs text-slate dark:text-slate-300">{feed.url}</p>
                  {feed.description && <p className="mt-1 text-xs text-slate dark:text-slate-300">{feed.description}</p>}
                  <div className="mt-1 flex flex-wrap gap-2 text-[11px] text-slate dark:text-slate-300">
                    {feed.site_url && <span>Site: {feed.site_url}</span>}
                    {feed.language && <span>Lang: {feed.language}</span>}
                    <span>Last fetch: {formatDate(feed.last_fetch_at)}</span>
                    <span>Last success: {formatDate(feed.last_success_at)}</span>
                  </div>
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

              <div className="mt-3 grid gap-2 md:grid-cols-[180px_1fr]">
                <select
                  className="rounded border border-slate/30 bg-white px-2 py-1 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                  value={feed.fetch_mode}
                  disabled={!canManage}
                  onChange={(event) => {
                    const nextMode = event.target.value as FeedFetchMode
                    updateFeed.mutate({
                      id: feed.id,
                      body:
                        nextMode === 'interval'
                          ? { fetch_mode: 'interval', fetch_interval_seconds: feed.fetch_interval_seconds }
                          : { fetch_mode: 'schedule', schedule_cron: feed.schedule_cron || '0 * * * *' },
                    })
                  }}
                >
                  <option value="interval">Interval</option>
                  <option value="schedule">Schedule</option>
                </select>

                {feed.fetch_mode === 'interval' ? (
                  <div className="flex items-center gap-2">
                    <label className="text-xs font-semibold">Every</label>
                    <input
                      className="w-28 rounded border border-slate/30 bg-white px-2 py-1 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                      type="number"
                      min={60}
                      defaultValue={feed.fetch_interval_seconds}
                      onBlur={(event) => {
                        const value = Number(event.target.value)
                        if (Number.isFinite(value) && value >= 60 && value !== feed.fetch_interval_seconds) {
                          updateFeed.mutate({ id: feed.id, body: { fetch_mode: 'interval', fetch_interval_seconds: value } })
                        }
                      }}
                      disabled={!canManage}
                    />
                    <span className="text-xs text-slate dark:text-slate-300">seconds</span>
                  </div>
                ) : (
                  <input
                    className="rounded border border-slate/30 bg-white px-2 py-1 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                    defaultValue={feed.schedule_cron || '0 * * * *'}
                    onBlur={(event) => {
                      const value = event.target.value.trim()
                      if (value && value !== (feed.schedule_cron || '')) {
                        updateFeed.mutate({ id: feed.id, body: { fetch_mode: 'schedule', schedule_cron: value } })
                      }
                    }}
                    disabled={!canManage}
                  />
                )}
              </div>

              {feed.last_error && <p className="mt-2 text-xs text-red-600">Last error: {feed.last_error}</p>}
            </div>
            )
          })}

          {feedsQuery.isLoading && <p className="text-sm text-slate dark:text-slate-300">Loading feeds...</p>}
          {feedsQuery.isError && <p className="text-sm text-red-600">Failed to load feeds.</p>}
          {!feedsQuery.isLoading && !filteredFeeds.length && <p className="text-sm text-slate dark:text-slate-300">No feeds match your search.</p>}
        </div>
      </section>
    </div>
  )
}

function timestamp(value: string | null): number {
  if (!value) return 0
  const parsed = new Date(value).getTime()
  return Number.isNaN(parsed) ? 0 : parsed
}

function formatDate(value: string | null): string {
  if (!value) return 'Never'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleString()
}

function parseImportEntries(payload: unknown): FeedImportEntry[] {
  if (Array.isArray(payload)) {
    return payload as FeedImportEntry[]
  }

  if (typeof payload === 'object' && payload !== null && Array.isArray((payload as { feeds?: unknown }).feeds)) {
    return (payload as { feeds: FeedImportEntry[] }).feeds
  }

  throw new Error('JSON must be an array of feeds or an object with a feeds array')
}
