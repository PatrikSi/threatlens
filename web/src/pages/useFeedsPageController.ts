import { ChangeEvent, FormEvent, useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { captureSessionLease } from '../api/sessionLifecycle'
import { ApiError, apiFetch } from '../api/client'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { useQueryAccessScope } from '../hooks/useQueryAccessScope'
import { rebaseSavedDraft } from '../hooks/rebaseSavedDraft'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import {
  EncryptedDataInventoryResponse,
  Feed,
  FeedExportResponse,
  FeedImportEntry,
  FeedImportResponse,
  FeedMetadataResponse,
  ItemListResponse,
} from '../types/api'
import { resolveFeedHealth } from '../utils/feedHealth'
import { mapSettledWithConcurrency } from '../utils/boundedConcurrency'
import { hasRequiredPermissions } from '../workspace/workspaceModel'
import {
  FeedEditDraft,
  buildFeedUpdatePayload,
  feedToEditDraft,
  isFeedEditDraftDirty,
  validateFeedEditDraft,
} from './feedEditDraft'
import {
  collectDirtyFeedScheduleDrafts,
  FeedScheduleDraft,
  feedToScheduleDraft,
  getFeedScheduleDraftStorageKey,
  isFeedScheduleDraftDirty,
  migrateLegacyFeedScheduleDraftStorage,
  normalizeFeedScheduleDraft,
  readPersistedFeedScheduleDrafts,
  validateFeedScheduleDraft,
} from './feedScheduleDraft'
import {
  buildFeedImportPreviewSummary,
  downloadFeedExport,
  findDuplicateUrls,
  formatBulkResultNotice,
  formatFeedExportNotice,
  isNewFeedFormDirty,
  parseImportEntries,
  resolveMutationError,
  summarizeBulkResults,
  timestamp,
  type FeedImportPreviewSummary,
  type FeedSaveState,
} from './feedPageUtils'

export type FeedSort = 'name_asc' | 'name_desc' | 'last_fetch_desc' | 'last_fetch_asc' | 'created_desc'
export type FeedFetchMode = FeedScheduleDraft['fetchMode']
export type FeedStatusFilter = 'all' | 'enabled' | 'disabled' | 'broken'

type PendingBulkSetEnabledAction = {
  enabled: boolean
  feeds: Feed[]
}

type PendingBulkDeleteAction = {
  feeds: Feed[]
  kind: 'disabled' | 'broken'
}

type DetectedFeedMetadata = {
  sourceUrl: string
  name: string
  description: string
  siteUrl: string
  language: string
}

const MAX_FEED_IMPORT_FILE_BYTES = 2_000_000
const FEED_STATUS_BOOTSTRAP_POLL_MS = 60_000
const FEED_REFRESH_STATUS_POLL_MS = 45_000
const FEED_STATUS_POLL_INTERVAL_MS = 3_000
const FEED_REFRESH_FOLLOW_UP_DELAYS_MS = [2_000, 6_000, 12_000, 24_000] as const
const BULK_FEED_REQUEST_CONCURRENCY = 5

function shouldShowMobileFeedForm(open: boolean, feedCount: number) {
  return open || feedCount === 0
}

export function useFeedsPageController() {
  const queryClient = useQueryClient()
  const feedsAccess = useQueryAccessScope(['feeds'])
  const meQuery = useCurrentUser()
  const permissions = meQuery.data?.access?.permissions ?? []
  const feedReadFailed = queryClient.getQueryState(['feeds'])?.status === 'error' || feedsAccess.denied
  const canManage = !meQuery.isError && !feedReadFailed && hasRequiredPermissions(permissions, ['write:feeds'])
  const canDelete = !meQuery.isError && !feedReadFailed && hasRequiredPermissions(permissions, ['admin:feeds'])
  const canBackup = canDelete
  const canInspectEncryptedDataHealth = meQuery.data?.role === 'admin' &&
    hasRequiredPermissions(permissions, ['read:health'])
  const feedScheduleDraftStorageKey = meQuery.data?.id ? getFeedScheduleDraftStorageKey(meQuery.data.id) : null

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
  const [statusFilter, setStatusFilter] = useState<FeedStatusFilter>('all')

  const [overwriteExisting, setOverwriteExisting] = useState(false)
  const [importData, setImportData] = useState<FeedImportEntry[] | null>(null)
  const [importFilename, setImportFilename] = useState('')
  const [importError, setImportError] = useState<string>('')
  const [importWarning, setImportWarning] = useState<string>('')
  const [lastImportResult, setLastImportResult] = useState<FeedImportResponse | null>(null)
  const [managementNotice, setManagementNotice] = useState('')
  const [exportNotice, setExportNotice] = useState('')
  const [pendingDeleteFeed, setPendingDeleteFeed] = useState<Feed | null>(null)
  const [pendingBulkDeleteFeeds, setPendingBulkDeleteFeeds] = useState<PendingBulkDeleteAction | null>(null)
  const [pendingBulkSetEnabled, setPendingBulkSetEnabled] = useState<PendingBulkSetEnabledAction | null>(null)
  const [pendingImportReview, setPendingImportReview] = useState<FeedImportPreviewSummary | null>(null)
  const [feedDrafts, setFeedDrafts] = useState<Record<string, FeedScheduleDraft>>({})
  const [feedSaveState, setFeedSaveState] = useState<Record<string, FeedSaveState>>({})
  const [feedDraftHydratedStorageKey, setFeedDraftHydratedStorageKey] = useState<string | null>(null)
  const [feedStatusPollUntil, setFeedStatusPollUntil] = useState(() => Date.now() + FEED_STATUS_BOOTSTRAP_POLL_MS)
  const [detectedMetadata, setDetectedMetadata] = useState<DetectedFeedMetadata | null>(null)
  const [editingFeedId, setEditingFeedId] = useState<string | null>(null)
  const editingFeedIdRef = useRef(editingFeedId)
  editingFeedIdRef.current = editingFeedId
  const [feedEditDraft, setFeedEditDraft] = useState<FeedEditDraft | null>(null)
  const [feedEditBaseline, setFeedEditBaseline] = useState<Feed | null>(null)
  const [feedEditGeneration, setFeedEditGeneration] = useState(feedsAccess.generation)
  const [mobileAddFeedOpen, setMobileAddFeedOpen] = useState(false)
  const [mobileBulkActionsOpen, setMobileBulkActionsOpen] = useState(false)
  const [mobileScheduleFeedId, setMobileScheduleFeedId] = useState<string | null>(null)
  const persistedFeedDraftsRef = useRef<Record<string, FeedScheduleDraft>>({})
  const loadedFeedDraftStorageKeyRef = useRef<string | null>(null)
  const importFileInputRef = useRef<HTMLInputElement | null>(null)
  const feedRefreshFollowUpTimeoutsRef = useRef<number[]>([])

  const clearFeedRefreshFollowUps = () => {
    for (const timeoutId of feedRefreshFollowUpTimeoutsRef.current) {
      window.clearTimeout(timeoutId)
    }
    feedRefreshFollowUpTimeoutsRef.current = []
  }

  const invalidateFeedCatalogue = () => Promise.all([
    queryClient.invalidateQueries({ queryKey: ['feeds'] }),
    queryClient.invalidateQueries({ queryKey: ['dashboard', 'feeds'] }),
    queryClient.invalidateQueries({ queryKey: ['stats', 'feeds'] }),
    queryClient.invalidateQueries({ queryKey: ['notifications', 'condition-choices', 'feed_id'] }),
  ])
  const invalidateFeedDependentQueries = () => {
    void invalidateFeedCatalogue()
    void queryClient.invalidateQueries({ queryKey: ['items'] })
    void queryClient.invalidateQueries({ queryKey: ['dashboard', 'items'] })
    void queryClient.invalidateQueries({ queryKey: ['ai', 'reprocess-items'] })
  }

  const scheduleFeedRefreshFollowUps = () => {
    if (typeof window === 'undefined') {
      return
    }

    clearFeedRefreshFollowUps()
    setFeedStatusPollUntil(Date.now() + FEED_REFRESH_STATUS_POLL_MS)
    for (const delayMs of FEED_REFRESH_FOLLOW_UP_DELAYS_MS) {
      const timeoutId = window.setTimeout(invalidateFeedDependentQueries, delayMs)
      feedRefreshFollowUpTimeoutsRef.current.push(timeoutId)
    }
  }

  const feedsQuery = useQuery({
    queryKey: ['feeds'],
    ...feedsAccess.queryOptions(() => apiFetch<Feed[]>('/feeds')),
    refetchInterval: (query) => {
      const feeds = feedsAccess.currentData(query.state.data)
      const hasRefreshableUnhealthyFeeds =
        feeds?.some((feed) => {
          const status = resolveFeedHealth(feed).status
          return status === 'stale' || status === 'failing'
        }) ?? false
      return hasRefreshableUnhealthyFeeds && Date.now() < feedStatusPollUntil ? FEED_STATUS_POLL_INTERVAL_MS : false
    },
  })

  const encryptedHealthAccess = useQueryAccessScope(['health', 'encrypted-data'])
  const encryptedDataHealthQuery = useQuery({
    queryKey: ['health', 'encrypted-data'],
    ...encryptedHealthAccess.queryOptions(async () => {
      try {
        return await apiFetch<EncryptedDataInventoryResponse>('/health/encrypted-data')
      } catch (error) {
        if (error instanceof ApiError && error.status === 503 && error.detail && typeof error.detail === 'object') {
          return error.detail as EncryptedDataInventoryResponse
        }
        throw error
      }
    }),
    enabled: canInspectEncryptedDataHealth,
    refetchInterval: 60_000,
  })

  const articleAccess = useQueryAccessScope(['feed-articles', editingFeedId ?? ''])
  const feedArticlesQuery = useQuery({
    queryKey: ['feed-articles', editingFeedId, feedsAccess.generation],
    ...articleAccess.queryOptions(() =>
      apiFetch<ItemListResponse>(
        `/items?feed_id=${encodeURIComponent(editingFeedId ?? '')}&page=1&page_size=10&sort=published_at_desc`,
      )),
    enabled: Boolean(editingFeedId) && !feedsAccess.denied,
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
    mutationFn: (submitted: Parameters<typeof isNewFeedFormDirty>[0]) =>
      apiFetch<Feed>('/feeds', {
        method: 'POST',
        body: JSON.stringify({
          name: submitted.name.trim() || null,
          url: submitted.url,
          description: submitted.description.trim() || null,
          site_url: submitted.siteUrl.trim() || null,
          language: submitted.language.trim() || null,
          fetch_mode: submitted.fetchMode,
          fetch_interval_seconds: submitted.fetchMode === 'interval' ? submitted.interval : null,
          schedule_cron: submitted.fetchMode === 'schedule' ? submitted.scheduleCron.trim() : null,
          enabled: true,
        }),
      }),
    onSuccess: (_saved, submitted) => {
      setName((current) => current === submitted.name ? '' : current)
      setUrl((current) => current === submitted.url ? '' : current)
      setDescription((current) => current === submitted.description ? '' : current)
      setSiteUrl((current) => current === submitted.siteUrl ? '' : current)
      setLanguage((current) => current === submitted.language ? '' : current)
      setFetchMode((current) => current === submitted.fetchMode ? 'interval' : current)
      setInterval((current) => current === submitted.interval ? 1800 : current)
      setScheduleCron((current) => current === submitted.scheduleCron ? '0 * * * *' : current)
      setDetectedMetadata(null)
      void invalidateFeedCatalogue()
    },
  })

  const updateFeed = useMutation({
    mutationFn: (payload: { id: string; body: Record<string, unknown> }) => {
      assertFeedWritable(payload.id)
      return apiFetch<Feed>(`/feeds/${payload.id}`, {
        method: 'PATCH',
        body: JSON.stringify(payload.body),
      })
    },
    onSuccess: () => void invalidateFeedCatalogue(),
  })

  const updateFeedDetails = useMutation({
    mutationKey: ['feeds', 'detail-update'],
    mutationFn: ({ feed, draft, generation }: { feed: Feed; draft: FeedEditDraft; generation: number }) => {
      assertFeedWritable(feed.id)
      if (!feedsAccess.currentData({ generation, value: true })) throw new Error('Reload the saved feed before saving this draft.')
      const capture = feedsAccess.captureResult()
      return apiFetch<Feed>(`/feeds/${feed.id}`, {
        method: 'PATCH',
        body: JSON.stringify(buildFeedUpdatePayload(feed, draft)),
      }).then(capture)
    },
    onSuccess: (result, variables) => {
      const updatedFeed = feedsAccess.currentData(result)
      if (!updatedFeed || queryClient.getQueryState(['feeds'])?.status === 'error') { invalidateFeedDependentQueries(); return }
      setManagementNotice('Feed updated.')
      // Refresh canonical reads; a write response cannot clear a read failure.
      if (editingFeedIdRef.current === variables.feed.id) {
        setFeedEditDraft((current) => current ? rebaseSavedDraft(variables.draft, current, feedToEditDraft(updatedFeed)) : current)
        setFeedEditBaseline(updatedFeed)
      }
      setFeedDrafts((previous) => ({
        ...previous,
        [updatedFeed.id]: rebaseSavedDraft(feedToScheduleDraft(variables.feed), previous[updatedFeed.id] ?? feedToScheduleDraft(variables.feed), feedToScheduleDraft(updatedFeed)),
      }))
      setFeedSaveState((previous) => ({
        ...previous,
        [updatedFeed.id]: { status: 'idle' },
      }))
      invalidateFeedDependentQueries()
    },
  })

  const refreshFeed = useMutation({
    mutationFn: (id: string) => { assertFeedWritable(id); return apiFetch(`/feeds/${id}/refresh`, { method: 'POST' }) },
    onSuccess: () => {
      setManagementNotice('Refresh queued. Feed health will update automatically as the worker finishes.')
      invalidateFeedDependentQueries()
      scheduleFeedRefreshFollowUps()
    },
  })

  const deleteFeed = useMutation({
    mutationKey: ['feeds', 'delete'],
    mutationFn: (id: string) => {
      assertFeedWritable(id)
      if (!canDelete) throw new Error('Feed deletion requires administration permission.')
      return apiFetch<void>(`/feeds/${id}`, { method: 'DELETE' })
    },
    onSuccess: () => {
      setManagementNotice('Feed deleted.')
      void invalidateFeedCatalogue()
      void queryClient.invalidateQueries({ queryKey: ['health', 'encrypted-data'] })
    },
  })

  const bulkRefreshFeeds = useMutation({
    mutationKey: ['feeds', 'bulk-refresh'],
    mutationFn: async (feeds: Feed[]) => {
      const lease = captureSessionLease()
      const settled = await mapSettledWithConcurrency(feeds, BULK_FEED_REQUEST_CONCURRENCY, (feed) => {
        lease.assertCurrent()
        assertFeedWritable(feed.id)
        return apiFetch(`/feeds/${feed.id}/refresh`, { method: 'POST' })
      },
      )
      lease.assertCurrent()
      return summarizeBulkResults(feeds, settled)
    },
    onSuccess: (result) => {
      const followUpHint = result.succeeded > 0 ? ' Feed health will update automatically as workers finish.' : ''
      setManagementNotice(`${formatBulkResultNotice('Refresh queued for', result)}${followUpHint}`)
      invalidateFeedDependentQueries()
      if (result.succeeded > 0) {
        scheduleFeedRefreshFollowUps()
      }
    },
  })

  const bulkSetEnabled = useMutation({
    mutationKey: ['feeds', 'bulk-set-enabled'],
    mutationFn: async (payload: { feeds: Feed[]; enabled: boolean }) => {
      const lease = captureSessionLease()
      const settled = await mapSettledWithConcurrency(
        payload.feeds,
        BULK_FEED_REQUEST_CONCURRENCY,
        (feed) => {
          lease.assertCurrent()
          assertFeedWritable(feed.id)
          return apiFetch<Feed>(`/feeds/${feed.id}`, {
            method: 'PATCH',
            body: JSON.stringify({ enabled: payload.enabled }),
          })
        },
      )
      lease.assertCurrent()
      return { enabled: payload.enabled, ...summarizeBulkResults(payload.feeds, settled) }
    },
    onSuccess: (result) => {
      const actionLabel = result.enabled ? 'Enabled' : 'Disabled'
      setManagementNotice(formatBulkResultNotice(actionLabel, result))
      void invalidateFeedCatalogue()
    },
  })

  const bulkDeleteFeeds = useMutation({
    mutationKey: ['feeds', 'bulk-delete'],
    mutationFn: async (feeds: Feed[]) => {
      const lease = captureSessionLease()
      const settled = await mapSettledWithConcurrency(feeds, BULK_FEED_REQUEST_CONCURRENCY, (feed) => {
        lease.assertCurrent()
        assertFeedWritable(feed.id)
        if (!canDelete) throw new Error('Feed deletion requires administration permission.')
        return apiFetch<void>(`/feeds/${feed.id}`, { method: 'DELETE' })
      },
      )
      lease.assertCurrent()
      return summarizeBulkResults(feeds, settled)
    },
  })

  const importFeeds = useMutation({
    mutationKey: ['feeds', 'import'],
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
      setPendingImportReview(null)
      void invalidateFeedCatalogue()
    },
  })

  const exportFeeds = useMutation({
    mutationFn: () => apiFetch<FeedExportResponse>('/feeds/export/backup'),
    onSuccess: (payload) => {
      downloadFeedExport(payload)
      setExportNotice(formatFeedExportNotice(payload))
    },
  })

  useEffect(() => {
    return () => {
      for (const timeoutId of feedRefreshFollowUpTimeoutsRef.current) {
        window.clearTimeout(timeoutId)
      }
      feedRefreshFollowUpTimeoutsRef.current = []
    }
  }, [])

  const filteredFeeds = useMemo(() => {
    const term = search.trim().toLowerCase()
    const source = feedsQuery.data ?? []
    const filteredByStatus = source.filter((feed) => {
      if (statusFilter === 'enabled') return feed.enabled
      if (statusFilter === 'disabled') return !feed.enabled
      if (statusFilter === 'broken') return feed.has_unreadable_url
      return true
    })

    const filtered = term
      ? filteredByStatus.filter((feed) => {
          const haystack = [
            feed.name,
            feed.url,
            feed.description || '',
            feed.site_url || '',
            feed.language || '',
            feed.last_error || '',
          ]
            .join(' ')
            .toLowerCase()
          return haystack.includes(term)
        })
      : filteredByStatus.slice()

    filtered.sort((a, b) => {
      if (sort === 'name_asc') return a.name.localeCompare(b.name)
      if (sort === 'name_desc') return b.name.localeCompare(a.name)
      if (sort === 'last_fetch_asc') return timestamp(a.last_fetch_at) - timestamp(b.last_fetch_at)
      if (sort === 'last_fetch_desc') return timestamp(b.last_fetch_at) - timestamp(a.last_fetch_at)
      return timestamp(b.created_at) - timestamp(a.created_at)
    })

    return filtered
  }, [feedsQuery.data, search, sort, statusFilter])

  const feedStats = useMemo(() => {
    const allFeeds = feedsQuery.data ?? []
    const enabled = allFeeds.filter((feed) => feed.enabled).length
    const unhealthy = allFeeds.filter((feed) => Boolean(feed.last_error) || feed.error_count > 0).length
    const broken = allFeeds.filter((feed) => feed.has_unreadable_url).length
    return {
      total: allFeeds.length,
      enabled,
      disabled: allFeeds.length - enabled,
      unhealthy,
      broken,
    }
  }, [feedsQuery.data])

  const editingFeed = useMemo(
    () => (feedsQuery.data ?? []).find((feed) => feed.id === editingFeedId) ?? null,
    [editingFeedId, feedsQuery.data],
  )
  const feedEditValidation = editingFeed && feedEditDraft ? validateFeedEditDraft(editingFeed, feedEditDraft) : null
  const feedEditDirty = feedEditBaseline && feedEditDraft ? isFeedEditDraftDirty(feedEditBaseline, feedEditDraft) : false

  useEffect(() => {
    if (!feedsAccess.denied) return
    setPendingDeleteFeed(null)
    setPendingBulkDeleteFeeds(null)
    setPendingBulkSetEnabled(null)
    setPendingImportReview(null)
    if (!feedEditDirty) { setEditingFeedId(null); setFeedEditDraft(null); setFeedEditBaseline(null) }
  }, [feedsAccess.denied, feedEditDirty])

  useEffect(() => {
    if (typeof window === 'undefined') {
      return
    }

    if (!feedScheduleDraftStorageKey || !meQuery.data?.id) {
      persistedFeedDraftsRef.current = {}
      loadedFeedDraftStorageKeyRef.current = null
      setFeedDraftHydratedStorageKey(null)
      setFeedDrafts({})
      setFeedSaveState({})
      return
    }

    if (loadedFeedDraftStorageKeyRef.current === feedScheduleDraftStorageKey) {
      return
    }

    migrateLegacyFeedScheduleDraftStorage(window.sessionStorage, meQuery.data.id)
    persistedFeedDraftsRef.current = readPersistedFeedScheduleDrafts(window.sessionStorage, feedScheduleDraftStorageKey)
    loadedFeedDraftStorageKeyRef.current = feedScheduleDraftStorageKey
    setFeedDraftHydratedStorageKey(null)
    setFeedDrafts({})
    setFeedSaveState({})
  }, [feedScheduleDraftStorageKey, meQuery.data?.id])

  useEffect(() => {
    if (feedScheduleDraftStorageKey && loadedFeedDraftStorageKeyRef.current !== feedScheduleDraftStorageKey) {
      return
    }

    if (!feedsQuery.data) return
    const feeds = feedsQuery.data
    const validIds = new Set(feeds.map((feed) => feed.id))

    setFeedDrafts((previous) => {
      const next: Record<string, FeedScheduleDraft> = {}
      for (const feed of feeds) {
        const previousDraft =
          feedDraftHydratedStorageKey === feedScheduleDraftStorageKey ? previous[feed.id] : undefined
        const draft = persistedFeedDraftsRef.current[feed.id] ?? previousDraft
        next[feed.id] = draft && isFeedScheduleDraftDirty(feed, draft) ? draft : feedToScheduleDraft(feed)
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

    setFeedDraftHydratedStorageKey(feedScheduleDraftStorageKey)
  }, [feedDraftHydratedStorageKey, feedScheduleDraftStorageKey, feedsQuery.data])

  useEffect(() => {
    if (typeof window === 'undefined') {
      return
    }
    if (!feedsQuery.data || !feedScheduleDraftStorageKey || feedDraftHydratedStorageKey !== feedScheduleDraftStorageKey) {
      return
    }

    const dirtyDrafts = collectDirtyFeedScheduleDrafts(feedsQuery.data, feedDrafts)
    persistedFeedDraftsRef.current = dirtyDrafts

    try {
      if (Object.keys(dirtyDrafts).length > 0) {
        window.sessionStorage.setItem(feedScheduleDraftStorageKey, JSON.stringify(dirtyDrafts))
      } else {
        window.sessionStorage.removeItem(feedScheduleDraftStorageKey)
      }
    } catch {
      // Ignore storage write failures and keep editing in memory.
    }
  }, [feedDraftHydratedStorageKey, feedDrafts, feedScheduleDraftStorageKey, feedsQuery.data])

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
    createFeed.mutate({ name, url, description, siteUrl, language, fetchMode, interval, scheduleCron })
  }

  const onDetectMetadata = () => {
    if (!url.trim()) return
    detectMetadata.mutate(url.trim())
  }

  const onConfirmDeleteFeed = () => {
    if (!pendingDeleteFeed || !canDelete || !currentFeed(pendingDeleteFeed.id)) {
      return
    }

    const feedId = pendingDeleteFeed.id
    setPendingDeleteFeed(null)
    setManagementNotice('')
    deleteFeed.mutate(feedId)
  }

  const onConfirmBulkDeleteFeeds = () => {
    if (!canDelete || !pendingBulkDeleteFeeds?.feeds.length || !pendingBulkDeleteFeeds.feeds.every((feed) => currentFeed(feed.id))) {
      return
    }

    const { feeds, kind } = pendingBulkDeleteFeeds
    setPendingBulkDeleteFeeds(null)
    setManagementNotice('')
    bulkDeleteFeeds.mutate(feeds, {
      onSuccess: (result) => {
        const actionLabel = kind === 'broken' ? 'Deleted broken' : 'Deleted'
        setManagementNotice(formatBulkResultNotice(actionLabel, result))
        void invalidateFeedCatalogue()
        void queryClient.invalidateQueries({ queryKey: ['health', 'encrypted-data'] })
      },
    })
  }

  const onConfirmBulkSetEnabled = () => {
    if (!canManage || !pendingBulkSetEnabled?.feeds.length || !pendingBulkSetEnabled.feeds.every((feed) => currentFeed(feed.id))) {
      return
    }

    const feeds = pendingBulkSetEnabled.feeds
    const enabled = pendingBulkSetEnabled.enabled
    setPendingBulkSetEnabled(null)
    setManagementNotice('')
    bulkSetEnabled.mutate({ feeds, enabled })
  }

  const onImportFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const lease = captureSessionLease()
    const file = event.target.files?.[0]
    if (!file) return

    setImportError('')
    setImportWarning('')
    setLastImportResult(null)

    if (file.size > MAX_FEED_IMPORT_FILE_BYTES) {
      setImportData(null)
      setImportFilename('')
      setImportError('Import file is too large. Maximum supported size is 2 MB.')
      event.target.value = ''
      return
    }

    try {
      const text = await file.text()
      lease.assertCurrent()
      const parsed = JSON.parse(text) as unknown
      const entries = parseImportEntries(parsed)
      const duplicateUrls = findDuplicateUrls(entries)
      setImportData(entries)
      setImportFilename(file.name)
      if (duplicateUrls.length) {
        setImportWarning(`Duplicate feed URLs in import file: ${duplicateUrls.join(', ')}`)
      }
    } catch (error) {
      if (lease.signal.aborted) return
      setImportData(null)
      setImportFilename('')
      setImportError((error as Error).message)
    } finally {
      event.target.value = ''
    }
  }

  const persistFeedSchedule = async (feedId: string, draft: FeedScheduleDraft) => {
    if (!canManage || !currentFeed(feedId)) return
    const lease = captureSessionLease()
    const feed = (feedsQuery.data ?? []).find((entry) => entry.id === feedId)
    if (!feed) return

    const validationError = validateFeedScheduleDraft(draft)
    if (validationError) {
      setFeedSaveState((previous) => ({
        ...previous,
        [feedId]: { status: 'error', message: validationError },
      }))
      return
    }

    const normalizedDraft = normalizeFeedScheduleDraft(draft)
    const body: Record<string, unknown> = { fetch_mode: normalizedDraft.fetchMode }
    if (normalizedDraft.fetchMode === 'interval') {
      body.fetch_interval_seconds = Number(normalizedDraft.intervalSeconds)
    } else {
      body.schedule_cron = normalizedDraft.scheduleCron
    }

    setFeedDrafts((previous) => ({
      ...previous,
      [feedId]: normalizedDraft,
    }))

    if (!isFeedScheduleDraftDirty(feed, normalizedDraft)) {
      setFeedSaveState((previous) => ({ ...previous, [feedId]: { status: 'saved' } }))
      return
    }

    setFeedSaveState((previous) => ({ ...previous, [feedId]: { status: 'saving' } }))

    try {
      await apiFetch<Feed>(`/feeds/${feedId}`, {
        method: 'PATCH',
        body: JSON.stringify(body),
      })
      lease.assertCurrent()
      setFeedSaveState((previous) => ({ ...previous, [feedId]: { status: 'saved' } }))
      await invalidateFeedCatalogue()
    } catch (error) {
      if (lease.signal.aborted) return
      setFeedSaveState((previous) => ({
        ...previous,
        [feedId]: { status: 'error', message: resolveMutationError(error, 'Feed schedule could not be updated') },
      }))
    }
  }

  const updateFeedDraft = (feed: Feed, patch: Partial<FeedScheduleDraft>) => {
    const currentDraft = feedDrafts[feed.id] ?? feedToScheduleDraft(feed)
    const nextDraft = { ...currentDraft, ...patch }
    setFeedDrafts((previous) => ({ ...previous, [feed.id]: nextDraft }))
    setFeedSaveState((previous) => ({ ...previous, [feed.id]: { status: 'idle' } }))
  }

  const resetFeedDraft = (feed: Feed) => {
    setFeedDrafts((previous) => ({ ...previous, [feed.id]: feedToScheduleDraft(feed) }))
    setFeedSaveState((previous) => ({ ...previous, [feed.id]: { status: 'idle' } }))
  }

  const visibleFeedIds = filteredFeeds.map((feed) => feed.id)
  const visibleDisabledFeedIds = filteredFeeds.filter((feed) => !feed.enabled).map((feed) => feed.id)
  const visibleEnabledFeedIds = filteredFeeds.filter((feed) => feed.enabled).map((feed) => feed.id)
  const visibleBrokenFeedIds = filteredFeeds.filter((feed) => feed.has_unreadable_url).map((feed) => feed.id)
  const brokenFeeds = (feedsQuery.data ?? []).filter((feed) => feed.has_unreadable_url)
  const unreadableFeedInventoryCount = encryptedDataHealthQuery.data?.feeds.unreadable_records ?? brokenFeeds.length
  const hasUnreadableFeedWarning = unreadableFeedInventoryCount > 0
  const showDerivedKeyWarning =
    canDelete && encryptedDataHealthQuery.data?.using_derived_app_data_encryption_key && !encryptedDataHealthQuery.isError
  const importPreviewSummary = useMemo(
    () => buildFeedImportPreviewSummary(importData, feedsQuery.data ?? [], overwriteExisting),
    [feedsQuery.data, importData, overwriteExisting],
  )
  const hasUnsavedCreateFeedChanges = isNewFeedFormDirty({
    name,
    url,
    description,
    siteUrl,
    language,
    fetchMode,
    interval,
    scheduleCron,
  })
  const hasUnsavedFeedScheduleChanges = feedsQuery.data
    ? feedsQuery.data.some((feed) => isFeedScheduleDraftDirty(feed, feedDrafts[feed.id] ?? feedToScheduleDraft(feed)))
    : Object.keys(persistedFeedDraftsRef.current).length > 0
  const confirmDiscardUnsavedFeedScheduleChanges = useUnsavedChangesWarning(
    hasUnsavedFeedScheduleChanges || hasUnsavedCreateFeedChanges || feedEditDirty,
    'You have unsaved feed changes. Leave without saving?',
  )

  const onRequestDeleteFeed = (feed: Feed) => {
    confirmDiscardUnsavedFeedScheduleChanges(() => {
      setPendingDeleteFeed(feed)
    })
  }

  const onRequestBulkDeleteFeeds = (feeds: Feed[]) => {
    confirmDiscardUnsavedFeedScheduleChanges(() => {
      setPendingBulkDeleteFeeds({ feeds, kind: 'disabled' })
    })
  }

  const onRequestBulkDeleteBrokenFeeds = (feeds: Feed[]) => {
    confirmDiscardUnsavedFeedScheduleChanges(() => {
      setPendingBulkDeleteFeeds({ feeds, kind: 'broken' })
    })
  }

  const onRequestImportReview = () => {
    if (!importPreviewSummary) {
      return
    }

    confirmDiscardUnsavedFeedScheduleChanges(() => {
      setPendingImportReview(importPreviewSummary)
    })
  }

  const onConfirmImportReview = () => {
    if (!pendingImportReview || !canManage) {
      return
    }
    setPendingImportReview(null)
    importFeeds.mutate()
  }

  const openFeedDetail = (feed: Feed) => {
    const saved = currentFeed(feed.id)
    if (!saved) return
    setEditingFeedId(saved.id)
    setFeedEditDraft(feedToEditDraft(saved))
    setFeedEditBaseline(saved)
    setFeedEditGeneration(feedsAccess.generation)
  }

  const closeFeedDetail = () => {
    if (feedEditDirty && typeof window !== 'undefined' && !window.confirm('Discard unsaved feed edits?')) {
      return
    }
    setEditingFeedId(null)
    setFeedEditDraft(null)
    setFeedEditBaseline(null)
  }

  const updateFeedEditDraft = (patch: Partial<FeedEditDraft>) => {
    setFeedEditDraft((current) => (current ? { ...current, ...patch } : current))
  }

  const onSaveFeedDetail = () => {
    if (!canManage || feedEditGeneration !== feedsAccess.generation || !editingFeed || !currentFeed(editingFeed.id) || !feedEditDraft || feedEditValidation || !feedEditDirty) {
      return
    }
    updateFeedDetails.mutate({ feed: editingFeed, draft: feedEditDraft, generation: feedEditGeneration })
  }

  function currentFeed(id: string) {
    if (queryClient.getQueryState(['feeds'])?.status === 'error') return undefined
    return feedsAccess.currentData<Feed[]>(queryClient.getQueryData(['feeds']))?.find((feed) => feed.id === id)
  }
  function assertFeedWritable(id: string) {
    if (!canManage || !currentFeed(id)) throw new Error('Refresh the feed inventory and verify access before continuing.')
  }

  const showMobileAddFeedForm = shouldShowMobileFeedForm(mobileAddFeedOpen, feedStats.total)
  const managementError =
    bulkRefreshFeeds.error || bulkSetEnabled.error || bulkDeleteFeeds.error || deleteFeed.error

  return {
    canManage,
    feedEditNeedsReload: feedEditGeneration !== feedsAccess.generation,
    onReloadFeedDetail: () => {
      const saved = editingFeedId && currentFeed(editingFeedId)
      if (saved) confirmDiscardUnsavedFeedScheduleChanges(() => openFeedDetail(saved))
    },
    canDelete,
    canBackup,
    name,
    setName,
    url,
    setUrl,
    description,
    setDescription,
    siteUrl,
    setSiteUrl,
    language,
    setLanguage,
    fetchMode,
    setFetchMode,
    interval,
    setInterval,
    scheduleCron,
    setScheduleCron,
    search,
    setSearch,
    sort,
    setSort,
    statusFilter,
    setStatusFilter,
    overwriteExisting,
    setOverwriteExisting,
    importData,
    importFilename,
    importError,
    importWarning,
    lastImportResult,
    managementNotice,
    setManagementNotice,
    exportNotice,
    pendingDeleteFeed,
    setPendingDeleteFeed,
    pendingBulkDeleteFeeds,
    setPendingBulkDeleteFeeds,
    pendingBulkSetEnabled,
    setPendingBulkSetEnabled,
    pendingImportReview,
    setPendingImportReview,
    feedDrafts,
    feedSaveState,
    feedEditDraft,
    mobileAddFeedOpen,
    setMobileAddFeedOpen,
    mobileBulkActionsOpen,
    setMobileBulkActionsOpen,
    mobileScheduleFeedId,
    setMobileScheduleFeedId,
    importFileInputRef,
    feedsQuery,
    encryptedDataHealthQuery,
    feedArticlesQuery,
    detectMetadata,
    createFeed,
    updateFeed,
    updateFeedDetails,
    refreshFeed,
    deleteFeed,
    bulkRefreshFeeds,
    bulkSetEnabled,
    bulkDeleteFeeds,
    importFeeds,
    exportFeeds,
    filteredFeeds,
    feedStats,
    editingFeed,
    feedEditValidation,
    feedEditDirty,
    onSubmit,
    onDetectMetadata,
    onConfirmDeleteFeed,
    onConfirmBulkDeleteFeeds,
    onConfirmBulkSetEnabled,
    onImportFile,
    persistFeedSchedule,
    updateFeedDraft,
    resetFeedDraft,
    visibleFeedIds,
    visibleDisabledFeedIds,
    visibleEnabledFeedIds,
    visibleBrokenFeedIds,
    brokenFeeds,
    unreadableFeedInventoryCount,
    hasUnreadableFeedWarning,
    showDerivedKeyWarning,
    importPreviewSummary,
    confirmDiscardUnsavedFeedScheduleChanges,
    onRequestDeleteFeed,
    onRequestBulkDeleteFeeds,
    onRequestBulkDeleteBrokenFeeds,
    onRequestImportReview,
    onConfirmImportReview,
    closeFeedDetail,
    updateFeedEditDraft,
    onSaveFeedDetail,
    openFeedDetail,
    showMobileAddFeedForm,
    managementError,
  }
}
