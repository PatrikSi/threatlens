import { ChangeEvent, FormEvent, useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, apiFetch } from '../api/client'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { Feed, FeedExportResponse, FeedImportEntry, FeedImportResponse, FeedMetadataResponse } from '../types/api'
import { formatDateTime } from '../utils/datetime'
import { feedHealthBadgeClass, resolveFeedHealth } from '../utils/feedHealth'

type FeedSort = 'name_asc' | 'name_desc' | 'last_fetch_desc' | 'last_fetch_asc' | 'created_desc'
type FeedFetchMode = 'interval' | 'schedule'
type FeedSaveStatus = 'idle' | 'pending' | 'saving' | 'saved' | 'error'

type FeedScheduleDraft = {
  fetchMode: FeedFetchMode
  intervalSeconds: string
  scheduleCron: string
}

type FeedSaveState = {
  status: FeedSaveStatus
  message?: string
}

type DetectedFeedMetadata = {
  sourceUrl: string
  name: string
  description: string
  siteUrl: string
  language: string
}

const FEED_AUTOSAVE_DELAY_MS = 700
const DEFAULT_SCHEDULE_CRON = '0 * * * *'

export function FeedsPage() {
  const queryClient = useQueryClient()
  const meQuery = useCurrentUser()
  const canManage = meQuery.data?.role === 'admin' || meQuery.data?.role === 'analyst'
  const canDelete = meQuery.data?.role === 'admin'

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
  const [importWarning, setImportWarning] = useState<string>('')
  const [lastImportResult, setLastImportResult] = useState<FeedImportResponse | null>(null)
  const [managementNotice, setManagementNotice] = useState('')
  const [pendingDeleteFeed, setPendingDeleteFeed] = useState<Feed | null>(null)
  const [pendingBulkDeleteFeeds, setPendingBulkDeleteFeeds] = useState<Feed[] | null>(null)
  const [feedDrafts, setFeedDrafts] = useState<Record<string, FeedScheduleDraft>>({})
  const [feedSaveState, setFeedSaveState] = useState<Record<string, FeedSaveState>>({})
  const [detectedMetadata, setDetectedMetadata] = useState<DetectedFeedMetadata | null>(null)
  const autosaveTimersRef = useRef<Record<string, ReturnType<typeof setTimeout>>>({})

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
    onSuccess: (metadata, feedUrl) => {
      const nextDetectedMetadata: DetectedFeedMetadata = {
        sourceUrl: feedUrl.trim(),
        name: metadata.name?.trim() ?? '',
        description: metadata.description?.trim() ?? '',
        siteUrl: metadata.site_url?.trim() ?? '',
        language: metadata.language?.trim() ?? '',
      }
      setDetectedMetadata(nextDetectedMetadata)
      if (!name.trim() && metadata.name) {
        setName(metadata.name)
      }
      if (!description.trim() && metadata.description) {
        setDescription(metadata.description)
      }
      if (!siteUrl.trim() && metadata.site_url) {
        setSiteUrl(metadata.site_url)
      }
      if (!language.trim() && metadata.language) {
        setLanguage(metadata.language)
      }
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
      setDetectedMetadata(null)
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

  const deleteFeed = useMutation({
    mutationFn: (id: string) => apiFetch<void>(`/feeds/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      setManagementNotice('Feed deleted.')
      void queryClient.invalidateQueries({ queryKey: ['feeds'] })
    },
  })

  const bulkRefreshFeeds = useMutation({
    mutationFn: async (ids: string[]) => {
      const settled = await Promise.allSettled(ids.map((id) => apiFetch(`/feeds/${id}/refresh`, { method: 'POST' })))
      return summarizeBulkResults(settled)
    },
    onSuccess: (result) => {
      setManagementNotice(`Refresh queued for ${result.succeeded}/${result.attempted} feeds.`)
      void queryClient.invalidateQueries({ queryKey: ['feeds'] })
    },
  })

  const bulkSetEnabled = useMutation({
    mutationFn: async (payload: { ids: string[]; enabled: boolean }) => {
      const settled = await Promise.allSettled(
        payload.ids.map((id) =>
          apiFetch<Feed>(`/feeds/${id}`, {
            method: 'PATCH',
            body: JSON.stringify({ enabled: payload.enabled }),
          }),
        ),
      )
      return { enabled: payload.enabled, ...summarizeBulkResults(settled) }
    },
    onSuccess: (result) => {
      const actionLabel = result.enabled ? 'Enabled' : 'Disabled'
      setManagementNotice(`${actionLabel} ${result.succeeded}/${result.attempted} feeds.`)
      void queryClient.invalidateQueries({ queryKey: ['feeds'] })
    },
  })

  const bulkDeleteFeeds = useMutation({
    mutationFn: async (ids: string[]) => {
      const settled = await Promise.allSettled(ids.map((id) => apiFetch<void>(`/feeds/${id}`, { method: 'DELETE' })))
      return summarizeBulkResults(settled)
    },
    onSuccess: (result) => {
      setManagementNotice(`Deleted ${result.succeeded}/${result.attempted} feeds.`)
      void queryClient.invalidateQueries({ queryKey: ['feeds'] })
    },
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
      setImportWarning('')
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

  const feedStats = useMemo(() => {
    const allFeeds = feedsQuery.data ?? []
    const enabled = allFeeds.filter((feed) => feed.enabled).length
    const unhealthy = allFeeds.filter((feed) => Boolean(feed.last_error) || feed.error_count > 0).length
    return {
      total: allFeeds.length,
      enabled,
      disabled: allFeeds.length - enabled,
      unhealthy,
    }
  }, [feedsQuery.data])

  useEffect(() => {
    const autosaveTimers = autosaveTimersRef.current
    return () => {
      for (const timer of Object.values(autosaveTimers)) {
        clearTimeout(timer)
      }
    }
  }, [])

  useEffect(() => {
    const feeds = feedsQuery.data ?? []
    const validIds = new Set(feeds.map((feed) => feed.id))

    for (const [feedId, timer] of Object.entries(autosaveTimersRef.current)) {
      if (!validIds.has(feedId)) {
        clearTimeout(timer)
        delete autosaveTimersRef.current[feedId]
      }
    }

    setFeedDrafts((previous) => {
      const next: Record<string, FeedScheduleDraft> = {}
      for (const feed of feeds) {
        next[feed.id] = previous[feed.id] ?? feedToScheduleDraft(feed)
      }
      return next
    })

    setFeedSaveState((previous) => {
      const next: Record<string, FeedSaveState> = {}
      for (const [feedId, state] of Object.entries(previous)) {
        if (validIds.has(feedId)) {
          next[feedId] = state
        }
      }
      return next
    })
  }, [feedsQuery.data])

  useEffect(() => {
    if (!detectedMetadata) {
      return
    }

    const trimmedUrl = url.trim()
    if (!trimmedUrl || trimmedUrl === detectedMetadata.sourceUrl) {
      return
    }

    if (name === detectedMetadata.name) {
      setName('')
    }
    if (description === detectedMetadata.description) {
      setDescription('')
    }
    if (siteUrl === detectedMetadata.siteUrl) {
      setSiteUrl('')
    }
    if (language === detectedMetadata.language) {
      setLanguage('')
    }
    setDetectedMetadata(null)
  }, [description, detectedMetadata, language, name, siteUrl, url])

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    createFeed.mutate()
  }

  const onDetectMetadata = () => {
    if (!url.trim()) return
    detectMetadata.mutate(url.trim())
  }

  const onConfirmDeleteFeed = () => {
    if (!pendingDeleteFeed) {
      return
    }

    const feedId = pendingDeleteFeed.id
    setPendingDeleteFeed(null)
    setManagementNotice('')
    deleteFeed.mutate(feedId)
  }

  const onConfirmBulkDeleteFeeds = () => {
    if (!pendingBulkDeleteFeeds?.length) {
      return
    }

    const feedIds = pendingBulkDeleteFeeds.map((feed) => feed.id)
    setPendingBulkDeleteFeeds(null)
    setManagementNotice('')
    bulkDeleteFeeds.mutate(feedIds)
  }

  const onImportFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return

    setImportError('')
    setImportWarning('')
    setLastImportResult(null)

    try {
      const text = await file.text()
      const parsed = JSON.parse(text) as unknown
      const entries = parseImportEntries(parsed)
      const duplicateUrls = findDuplicateUrls(entries)
      setImportData(entries)
      setImportFilename(file.name)
      if (duplicateUrls.length) {
        setImportWarning(`Duplicate feed URLs in import file: ${duplicateUrls.join(', ')}`)
      }
    } catch (error) {
      setImportData(null)
      setImportFilename('')
      setImportError((error as Error).message)
    } finally {
      event.target.value = ''
    }
  }

  const persistFeedSchedule = async (feedId: string, draft: FeedScheduleDraft) => {
    const feed = (feedsQuery.data ?? []).find((entry) => entry.id === feedId)
    if (!feed) return

    const body: Record<string, unknown> = { fetch_mode: draft.fetchMode }
    if (draft.fetchMode === 'interval') {
      const parsed = Number(draft.intervalSeconds)
      if (!Number.isFinite(parsed) || parsed < 60) {
        setFeedSaveState((previous) => ({
          ...previous,
          [feedId]: { status: 'error', message: 'Interval must be at least 60 seconds.' },
        }))
        return
      }
      const intervalSeconds = Math.floor(parsed)
      body.fetch_interval_seconds = intervalSeconds

      if (feed.fetch_mode === 'interval' && feed.fetch_interval_seconds === intervalSeconds) {
        setFeedSaveState((previous) => ({ ...previous, [feedId]: { status: 'saved' } }))
        return
      }

      setFeedDrafts((previous) => ({
        ...previous,
        [feedId]: { ...draft, intervalSeconds: String(intervalSeconds) },
      }))
    } else {
      const scheduleCron = draft.scheduleCron.trim()
      if (!scheduleCron) {
        setFeedSaveState((previous) => ({
          ...previous,
          [feedId]: { status: 'error', message: 'Schedule cannot be empty.' },
        }))
        return
      }
      body.schedule_cron = scheduleCron
      if (feed.fetch_mode === 'schedule' && (feed.schedule_cron || '') === scheduleCron) {
        setFeedSaveState((previous) => ({ ...previous, [feedId]: { status: 'saved' } }))
        return
      }
      setFeedDrafts((previous) => ({
        ...previous,
        [feedId]: { ...draft, scheduleCron },
      }))
    }

    setFeedSaveState((previous) => ({ ...previous, [feedId]: { status: 'saving' } }))

    try {
      await updateFeed.mutateAsync({ id: feedId, body })
      setFeedSaveState((previous) => ({ ...previous, [feedId]: { status: 'saved' } }))
    } catch (error) {
      setFeedSaveState((previous) => ({
        ...previous,
        [feedId]: { status: 'error', message: resolveMutationError(error) },
      }))
    }
  }

  const queueFeedAutosave = (feedId: string, draft: FeedScheduleDraft) => {
    const existing = autosaveTimersRef.current[feedId]
    if (existing) {
      clearTimeout(existing)
    }

    setFeedSaveState((previous) => ({ ...previous, [feedId]: { status: 'pending' } }))
    autosaveTimersRef.current[feedId] = window.setTimeout(() => {
      void persistFeedSchedule(feedId, draft)
    }, FEED_AUTOSAVE_DELAY_MS)
  }

  const updateFeedDraft = (feed: Feed, patch: Partial<FeedScheduleDraft>) => {
    const currentDraft = feedDrafts[feed.id] ?? feedToScheduleDraft(feed)
    const nextDraft = { ...currentDraft, ...patch }
    setFeedDrafts((previous) => ({ ...previous, [feed.id]: nextDraft }))
    queueFeedAutosave(feed.id, nextDraft)
  }

  const visibleFeedIds = filteredFeeds.map((feed) => feed.id)
  const visibleDisabledFeedIds = filteredFeeds.filter((feed) => !feed.enabled).map((feed) => feed.id)
  const visibleEnabledFeedIds = filteredFeeds.filter((feed) => feed.enabled).map((feed) => feed.id)

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
          <h2 className="font-display text-xl">Configured Feeds ({feedStats.total})</h2>
          <div className="grid w-full grid-cols-2 gap-2 sm:flex sm:w-auto sm:flex-wrap sm:items-center">
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
              className="col-span-2 rounded bg-ink px-3 py-1.5 text-xs text-white disabled:opacity-50 sm:col-auto dark:bg-cyan dark:text-[#053c2e]"
              disabled={!canManage || !importData || importFeeds.isPending}
              onClick={() => importFeeds.mutate()}
            >
              Run Import
            </button>
          </div>
        </div>

        <p className="mt-2 text-xs text-slate dark:text-slate-300">
          Showing {filteredFeeds.length} of {feedStats.total} feeds · {feedStats.enabled} enabled · {feedStats.disabled} disabled · {feedStats.unhealthy} with errors
        </p>

        <div className="mt-2 flex flex-wrap items-center gap-3">
          <input
            className="w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm sm:min-w-64 sm:flex-1 dark:border-cyan-900/40 dark:bg-[#072019]"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search feeds"
          />
          <select
            className="w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm sm:w-auto dark:border-cyan-900/40 dark:bg-[#072019]"
            value={sort}
            onChange={(event) => setSort(event.target.value as FeedSort)}
          >
            <option value="created_desc">Newest created</option>
            <option value="name_asc">Name A-Z</option>
            <option value="name_desc">Name Z-A</option>
            <option value="last_fetch_desc">Last fetched newest</option>
            <option value="last_fetch_asc">Last fetched oldest</option>
          </select>
          <label className="flex w-full items-center gap-2 text-xs text-slate sm:w-auto dark:text-slate-300">
            <input
              type="checkbox"
              checked={overwriteExisting}
              onChange={(event) => setOverwriteExisting(event.target.checked)}
              disabled={!canManage}
            />
            Overwrite existing on import
          </label>
        </div>

        <div className="mt-2 grid gap-2 sm:flex sm:flex-wrap sm:items-center">
          <button
            type="button"
            className="rounded border border-slate/30 px-3 py-1.5 text-xs dark:border-cyan-900/40"
            disabled={!canManage || !visibleFeedIds.length || bulkRefreshFeeds.isPending}
            onClick={() => {
              setManagementNotice('')
              bulkRefreshFeeds.mutate(visibleFeedIds)
            }}
          >
            Refresh Filtered
          </button>
          <button
            type="button"
            className="rounded border border-slate/30 px-3 py-1.5 text-xs dark:border-cyan-900/40"
            disabled={!canManage || !visibleDisabledFeedIds.length || bulkSetEnabled.isPending}
            onClick={() => {
              setManagementNotice('')
              bulkSetEnabled.mutate({ ids: visibleDisabledFeedIds, enabled: true })
            }}
          >
            Enable Disabled (Filtered)
          </button>
          <button
            type="button"
            className="rounded border border-slate/30 px-3 py-1.5 text-xs dark:border-cyan-900/40"
            disabled={!canManage || !visibleEnabledFeedIds.length || bulkSetEnabled.isPending}
            onClick={() => {
              setManagementNotice('')
              bulkSetEnabled.mutate({ ids: visibleEnabledFeedIds, enabled: false })
            }}
          >
            Disable Enabled (Filtered)
          </button>
          {canDelete && (
            <button
              type="button"
              className="rounded border border-red-300 px-3 py-1.5 text-xs text-red-700 dark:border-red-800 dark:text-red-300"
              disabled={
                !visibleDisabledFeedIds.length ||
                bulkDeleteFeeds.isPending ||
                Boolean(pendingDeleteFeed) ||
                Boolean(pendingBulkDeleteFeeds)
              }
              onClick={() => {
                setPendingBulkDeleteFeeds(filteredFeeds.filter((feed) => !feed.enabled))
              }}
            >
              Delete Disabled (Filtered)
            </button>
          )}
        </div>

        {importFilename && (
          <p className="mt-2 text-xs text-slate dark:text-slate-300">
            Loaded: {importFilename} ({importData?.length ?? 0} entries)
          </p>
        )}
        {importError && <p className="mt-2 text-xs text-red-600">Import parse error: {importError}</p>}
        {importWarning && <p className="mt-2 text-xs text-amber-600">{importWarning}</p>}
        {lastImportResult && (
          <div className="mt-2 rounded border border-slate/20 bg-white/60 px-2 py-2 text-xs text-slate dark:border-cyan-900/40 dark:bg-[#072019] dark:text-slate-200">
            <p>
              Import result: created {lastImportResult.created}, updated {lastImportResult.updated}, skipped {lastImportResult.skipped}, errors {lastImportResult.errors.length}
            </p>
            {lastImportResult.created + lastImportResult.updated === 0 && (
              <p className="mt-1 text-amber-600">
                No feeds were created or updated. This usually means all entries already existed and overwrite was disabled, or entries were rejected.
              </p>
            )}
            {lastImportResult.errors.length > 0 && (
              <ul className="mt-1 list-disc pl-4 text-red-600">
                {lastImportResult.errors.map((entry, index) => (
                  <li key={`${entry}-${index}`}>{entry}</li>
                ))}
              </ul>
            )}
          </div>
        )}
        {importFeeds.isError && (
          <p className="mt-2 text-xs text-red-600">Import failed: {resolveMutationError(importFeeds.error)}</p>
        )}
        {managementNotice && <p className="mt-2 text-xs text-emerald-700 dark:text-emerald-300">{managementNotice}</p>}
        {(bulkRefreshFeeds.isError || bulkSetEnabled.isError || bulkDeleteFeeds.isError || deleteFeed.isError) && (
          <p className="mt-2 text-xs text-red-600">One or more management actions failed.</p>
        )}

        <div className="mt-3 space-y-2">
          {filteredFeeds.map((feed) => {
            const health = resolveFeedHealth(feed)
            const draft = feedDrafts[feed.id] ?? feedToScheduleDraft(feed)
            const saveState = feedSaveState[feed.id]?.status ?? 'idle'
            const saveMessage = feedSaveState[feed.id]?.message
            return (
            <div key={feed.id} className="rounded border border-slate/20 p-3 dark:border-cyan-900/40">
              <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
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
                <div className="flex flex-wrap gap-2">
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
                  {canDelete && (
                    <button
                      className="rounded border border-red-300 px-2 py-1 text-xs text-red-700 dark:border-red-800 dark:text-red-300"
                      onClick={() => setPendingDeleteFeed(feed)}
                      disabled={deleteFeed.isPending || Boolean(pendingDeleteFeed) || Boolean(pendingBulkDeleteFeeds)}
                    >
                      Delete
                    </button>
                  )}
                </div>
              </div>

              <div className="mt-3 grid gap-2 md:grid-cols-[180px_1fr]">
                <select
                  className="rounded border border-slate/30 bg-white px-2 py-1 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                  value={draft.fetchMode}
                  disabled={!canManage}
                  onChange={(event) => {
                    const nextMode = event.target.value as FeedFetchMode
                    updateFeedDraft(feed, {
                      fetchMode: nextMode,
                      intervalSeconds: draft.intervalSeconds || '1800',
                      scheduleCron: nextMode === 'schedule' ? draft.scheduleCron || DEFAULT_SCHEDULE_CRON : draft.scheduleCron,
                    })
                  }}
                >
                  <option value="interval">Interval</option>
                  <option value="schedule">Schedule</option>
                </select>

                {draft.fetchMode === 'interval' ? (
                  <div className="flex flex-wrap items-center gap-2">
                    <label className="text-xs font-semibold">Every</label>
                    <input
                      className="w-28 rounded border border-slate/30 bg-white px-2 py-1 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                      type="number"
                      min={60}
                      value={draft.intervalSeconds}
                      onChange={(event) => {
                        updateFeedDraft(feed, { fetchMode: 'interval', intervalSeconds: event.target.value })
                      }}
                      disabled={!canManage}
                    />
                    <span className="text-xs text-slate dark:text-slate-300">seconds</span>
                  </div>
                ) : (
                  <input
                    className="rounded border border-slate/30 bg-white px-2 py-1 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                    value={draft.scheduleCron}
                    onChange={(event) => {
                      updateFeedDraft(feed, { fetchMode: 'schedule', scheduleCron: event.target.value })
                    }}
                    disabled={!canManage}
                  />
                )}
              </div>

              {canManage && saveState !== 'idle' && (
                <p className={`mt-1 text-[11px] ${feedSaveStatusClass(saveState)}`}>
                  {saveMessage || feedSaveStatusText(saveState)}
                </p>
              )}

              {feed.last_error && <p className="mt-2 text-xs text-red-600">Last error: {feed.last_error}</p>}
            </div>
            )
          })}

          {feedsQuery.isLoading && <p className="text-sm text-slate dark:text-slate-300">Loading feeds...</p>}
          {feedsQuery.isError && <p className="text-sm text-red-600">Failed to load feeds.</p>}
          {!feedsQuery.isLoading && !filteredFeeds.length && <p className="text-sm text-slate dark:text-slate-300">No feeds match your search.</p>}
        </div>
      </section>

      <ConfirmDialog
        open={Boolean(pendingDeleteFeed)}
        title="Delete feed?"
        description="This removes the feed, its related items, and its fetch history."
        confirmLabel="Delete feed"
        onCancel={() => setPendingDeleteFeed(null)}
        onConfirm={onConfirmDeleteFeed}
        confirmDisabled={deleteFeed.isPending}
        isConfirming={deleteFeed.isPending}
      >
        {pendingDeleteFeed && (
          <div className="space-y-2">
            <p className="font-semibold text-ink dark:text-white">{pendingDeleteFeed.name}</p>
            <p className="break-all font-mono text-xs text-slate dark:text-white/65">{pendingDeleteFeed.url}</p>
          </div>
        )}
      </ConfirmDialog>

      <ConfirmDialog
        open={Boolean(pendingBulkDeleteFeeds?.length)}
        title="Delete filtered disabled feeds?"
        description="This permanently removes every disabled feed in the current filtered view."
        confirmLabel="Delete feeds"
        onCancel={() => setPendingBulkDeleteFeeds(null)}
        onConfirm={onConfirmBulkDeleteFeeds}
        confirmDisabled={bulkDeleteFeeds.isPending}
        isConfirming={bulkDeleteFeeds.isPending}
      >
        {pendingBulkDeleteFeeds && (
          <div className="space-y-3">
            <p>
              You are about to delete{' '}
              <span className="font-semibold text-ink dark:text-white">{pendingBulkDeleteFeeds.length}</span> disabled feed
              {pendingBulkDeleteFeeds.length === 1 ? '' : 's'} from this view.
            </p>
            <div className="max-h-48 overflow-auto rounded border border-slate/20 bg-slate/5 p-3 dark:border-cyan-900/40 dark:bg-white/[0.03]">
              <ul className="space-y-1">
                {pendingBulkDeleteFeeds.map((feed) => (
                  <li key={feed.id} className="break-all font-mono text-xs text-slate-700 dark:text-white/70">
                    {feed.name}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        )}
      </ConfirmDialog>
    </div>
  )
}

type BulkSummary = {
  attempted: number
  succeeded: number
  failed: number
}

function feedToScheduleDraft(feed: Feed): FeedScheduleDraft {
  return {
    fetchMode: feed.fetch_mode,
    intervalSeconds: String(feed.fetch_interval_seconds || 1800),
    scheduleCron: feed.schedule_cron || DEFAULT_SCHEDULE_CRON,
  }
}

function feedSaveStatusText(status: FeedSaveStatus): string {
  if (status === 'pending') return 'Unsaved changes. Autosaving...'
  if (status === 'saving') return 'Saving...'
  if (status === 'saved') return 'Saved.'
  return 'Save failed.'
}

function feedSaveStatusClass(status: FeedSaveStatus): string {
  if (status === 'error') return 'text-red-600'
  if (status === 'saved') return 'text-emerald-700 dark:text-emerald-300'
  return 'text-slate dark:text-slate-300'
}

function summarizeBulkResults(results: PromiseSettledResult<unknown>[]): BulkSummary {
  const attempted = results.length
  const failed = results.filter((result) => result.status === 'rejected').length
  return {
    attempted,
    failed,
    succeeded: attempted - failed,
  }
}

function timestamp(value: string | null): number {
  if (!value) return 0
  const parsed = new Date(value).getTime()
  return Number.isNaN(parsed) ? 0 : parsed
}

function formatDate(value: string | null): string {
  return value ? formatDateTime(value) : 'Never'
}

function parseImportEntries(payload: unknown): FeedImportEntry[] {
  const entries = Array.isArray(payload)
    ? payload
    : typeof payload === 'object' && payload !== null && Array.isArray((payload as { feeds?: unknown }).feeds)
      ? (payload as { feeds: unknown[] }).feeds
      : null

  if (!entries) {
    throw new Error('JSON must be an array of feeds or an object with a feeds array')
  }

  return entries.map((rawEntry, index) => {
    if (typeof rawEntry !== 'object' || rawEntry === null) {
      throw new Error(`Entry ${index + 1} must be an object`)
    }
    const entry = rawEntry as Record<string, unknown>
    const url = typeof entry.url === 'string' ? entry.url.trim() : ''
    if (!url) {
      throw new Error(`Entry ${index + 1} is missing a valid url`)
    }

    const fetchMode = entry.fetch_mode === 'schedule' ? 'schedule' : 'interval'
    const fetchInterval = Number(entry.fetch_interval_seconds)
    const parsedInterval = Number.isFinite(fetchInterval) && fetchInterval >= 60 ? Math.floor(fetchInterval) : 1800
    const scheduleCron = typeof entry.schedule_cron === 'string' && entry.schedule_cron.trim() ? entry.schedule_cron.trim() : null

    return {
      name: stringOrNull(entry.name),
      url,
      description: stringOrNull(entry.description),
      site_url: stringOrNull(entry.site_url),
      language: stringOrNull(entry.language),
      enabled: typeof entry.enabled === 'boolean' ? entry.enabled : true,
      fetch_mode: fetchMode,
      fetch_interval_seconds: fetchMode === 'interval' ? parsedInterval : null,
      schedule_cron: fetchMode === 'schedule' ? scheduleCron || '0 * * * *' : null,
    }
  })
}

function stringOrNull(value: unknown): string | null {
  if (typeof value !== 'string') return null
  const trimmed = value.trim()
  return trimmed ? trimmed : null
}

function findDuplicateUrls(entries: FeedImportEntry[]): string[] {
  const counts = new Map<string, number>()
  for (const entry of entries) {
    const key = entry.url.toLowerCase()
    counts.set(key, (counts.get(key) || 0) + 1)
  }
  return Array.from(counts.entries())
    .filter(([, count]) => count > 1)
    .map(([url]) => url)
}

function resolveMutationError(error: unknown): string {
  if (error instanceof ApiError) {
    return error.message
  }
  if (error instanceof Error && error.message.trim()) {
    return error.message
  }
  return 'Unknown error'
}
