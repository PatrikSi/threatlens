import { ChangeEvent, FormEvent, useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, apiFetch } from '../api/client'
import { ConfirmDialog, DialogSurface } from '../components/ConfirmDialog'
import { useCurrentUser } from '../hooks/useCurrentUser'
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
import { feedHealthBadgeClass, resolveFeedHealth } from '../utils/feedHealth'
import {
  FeedEditDraft,
  buildFeedUpdatePayload,
  feedToEditDraft,
  isFeedEditDraftDirty,
  validateFeedEditDraft,
} from './feedEditDraft'
import {
  collectDirtyFeedScheduleDrafts,
  DEFAULT_SCHEDULE_CRON,
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
  feedSaveStatusClass,
  feedSaveStatusText,
  findDuplicateUrls,
  formatBulkResultNotice,
  formatDate,
  formatFeedExportNotice,
  isNewFeedFormDirty,
  parseImportEntries,
  resolveMutationError,
  summarizeBulkResults,
  timestamp,
  type FeedImportPreviewSummary,
  type FeedSaveState,
} from './feedPageUtils'

type FeedSort = 'name_asc' | 'name_desc' | 'last_fetch_desc' | 'last_fetch_asc' | 'created_desc'
type FeedFetchMode = FeedScheduleDraft['fetchMode']
type FeedStatusFilter = 'all' | 'enabled' | 'disabled' | 'broken'
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

function shouldShowMobileFeedForm(open: boolean, feedCount: number) {
  return open || feedCount === 0
}

function mobileDisclosureClass(open: boolean) {
  return open ? 'block' : 'hidden'
}

function mobileFeedToggleLabel(open: boolean) {
  return open ? 'Hide' : 'New feed'
}

function mobileFeedToggleVisibilityClass(feedCount: number) {
  return feedCount === 0 ? 'hidden' : 'block'
}

function mobileImportActionVisibilityClass(hasImportData: boolean) {
  return hasImportData ? 'block' : 'hidden'
}

export function FeedsPage() {
  const queryClient = useQueryClient()
  const meQuery = useCurrentUser()
  const canManage = meQuery.data?.role === 'admin' || meQuery.data?.role === 'analyst'
  const canDelete = meQuery.data?.role === 'admin'
  const canBackup = meQuery.data?.role === 'admin'
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
  const [feedEditDraft, setFeedEditDraft] = useState<FeedEditDraft | null>(null)
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

  const invalidateFeedDependentQueries = () => {
    void queryClient.invalidateQueries({ queryKey: ['feeds'] })
    void queryClient.invalidateQueries({ queryKey: ['items'] })
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
    queryFn: () => apiFetch<Feed[]>('/feeds'),
    refetchInterval: (query) => {
      const feeds = query.state.data as Feed[] | undefined
      const hasRefreshableUnhealthyFeeds =
        feeds?.some((feed) => {
          const status = resolveFeedHealth(feed).status
          return status === 'stale' || status === 'failing'
        }) ?? false
      return hasRefreshableUnhealthyFeeds && Date.now() < feedStatusPollUntil ? FEED_STATUS_POLL_INTERVAL_MS : false
    },
  })

  const encryptedDataHealthQuery = useQuery({
    queryKey: ['health', 'encrypted-data'],
    queryFn: async () => {
      try {
        return await apiFetch<EncryptedDataInventoryResponse>('/health/encrypted-data')
      } catch (error) {
        if (error instanceof ApiError && error.status === 503 && error.detail && typeof error.detail === 'object') {
          return error.detail as EncryptedDataInventoryResponse
        }
        throw error
      }
    },
    enabled: canDelete,
    refetchInterval: 60_000,
  })

  const feedArticlesQuery = useQuery({
    queryKey: ['items', 'feed-detail', editingFeedId],
    queryFn: () =>
      apiFetch<ItemListResponse>(
        `/items?feed_id=${encodeURIComponent(editingFeedId ?? '')}&page=1&page_size=10&sort=published_at_desc`,
      ),
    enabled: Boolean(editingFeedId),
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

  const updateFeedDetails = useMutation({
    mutationKey: ['feeds', 'detail-update'],
    mutationFn: ({ feed, draft }: { feed: Feed; draft: FeedEditDraft }) =>
      apiFetch<Feed>(`/feeds/${feed.id}`, {
        method: 'PATCH',
        body: JSON.stringify(buildFeedUpdatePayload(feed, draft)),
      }),
    onSuccess: (updatedFeed) => {
      setManagementNotice('Feed updated.')
      queryClient.setQueryData<Feed[]>(['feeds'], (current) =>
        current?.map((feed) => (feed.id === updatedFeed.id ? updatedFeed : feed)) ?? current,
      )
      setFeedEditDraft(feedToEditDraft(updatedFeed))
      setFeedDrafts((previous) => ({
        ...previous,
        [updatedFeed.id]: feedToScheduleDraft(updatedFeed),
      }))
      setFeedSaveState((previous) => ({
        ...previous,
        [updatedFeed.id]: { status: 'idle' },
      }))
      invalidateFeedDependentQueries()
    },
  })

  const refreshFeed = useMutation({
    mutationFn: (id: string) => apiFetch(`/feeds/${id}/refresh`, { method: 'POST' }),
    onSuccess: () => {
      setManagementNotice('Refresh queued. Feed health will update automatically as the worker finishes.')
      invalidateFeedDependentQueries()
      scheduleFeedRefreshFollowUps()
    },
  })

  const deleteFeed = useMutation({
    mutationKey: ['feeds', 'delete'],
    mutationFn: (id: string) => apiFetch<void>(`/feeds/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      setManagementNotice('Feed deleted.')
      void queryClient.invalidateQueries({ queryKey: ['feeds'] })
      void queryClient.invalidateQueries({ queryKey: ['health', 'encrypted-data'] })
    },
  })

  const bulkRefreshFeeds = useMutation({
    mutationKey: ['feeds', 'bulk-refresh'],
    mutationFn: async (feeds: Feed[]) => {
      const settled = await Promise.allSettled(feeds.map((feed) => apiFetch(`/feeds/${feed.id}/refresh`, { method: 'POST' })))
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
      const settled = await Promise.allSettled(
        payload.feeds.map((feed) =>
          apiFetch<Feed>(`/feeds/${feed.id}`, {
            method: 'PATCH',
            body: JSON.stringify({ enabled: payload.enabled }),
          }),
        ),
      )
      return { enabled: payload.enabled, ...summarizeBulkResults(payload.feeds, settled) }
    },
    onSuccess: (result) => {
      const actionLabel = result.enabled ? 'Enabled' : 'Disabled'
      setManagementNotice(formatBulkResultNotice(actionLabel, result))
      void queryClient.invalidateQueries({ queryKey: ['feeds'] })
    },
  })

  const bulkDeleteFeeds = useMutation({
    mutationKey: ['feeds', 'bulk-delete'],
    mutationFn: async (feeds: Feed[]) => {
      const settled = await Promise.allSettled(feeds.map((feed) => apiFetch<void>(`/feeds/${feed.id}`, { method: 'DELETE' })))
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
      void queryClient.invalidateQueries({ queryKey: ['feeds'] })
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
  const feedEditDirty = editingFeed && feedEditDraft ? isFeedEditDraftDirty(editingFeed, feedEditDraft) : false

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

    const feeds = feedsQuery.data ?? []
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
    if (!pendingBulkDeleteFeeds?.feeds.length) {
      return
    }

    const { feeds, kind } = pendingBulkDeleteFeeds
    setPendingBulkDeleteFeeds(null)
    setManagementNotice('')
    bulkDeleteFeeds.mutate(feeds, {
      onSuccess: (result) => {
        const actionLabel = kind === 'broken' ? 'Deleted broken' : 'Deleted'
        setManagementNotice(formatBulkResultNotice(actionLabel, result))
        void queryClient.invalidateQueries({ queryKey: ['feeds'] })
        void queryClient.invalidateQueries({ queryKey: ['health', 'encrypted-data'] })
      },
    })
  }

  const onConfirmBulkSetEnabled = () => {
    if (!pendingBulkSetEnabled?.feeds.length) {
      return
    }

    const feeds = pendingBulkSetEnabled.feeds
    const enabled = pendingBulkSetEnabled.enabled
    setPendingBulkSetEnabled(null)
    setManagementNotice('')
    bulkSetEnabled.mutate({ feeds, enabled })
  }

  const onImportFile = async (event: ChangeEvent<HTMLInputElement>) => {
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
      setFeedSaveState((previous) => ({ ...previous, [feedId]: { status: 'saved' } }))
      await queryClient.invalidateQueries({ queryKey: ['feeds'] })
    } catch (error) {
      setFeedSaveState((previous) => ({
        ...previous,
        [feedId]: { status: 'error', message: resolveMutationError(error) },
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
  const hasUnsavedFeedScheduleChanges = (feedsQuery.data ?? []).some((feed) =>
    isFeedScheduleDraftDirty(feed, feedDrafts[feed.id] ?? feedToScheduleDraft(feed)),
  )
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
    if (!pendingImportReview) {
      return
    }
    setPendingImportReview(null)
    importFeeds.mutate()
  }

  const openFeedDetail = (feed: Feed) => {
    setEditingFeedId(feed.id)
    setFeedEditDraft(feedToEditDraft(feed))
  }

  const closeFeedDetail = () => {
    if (feedEditDirty && typeof window !== 'undefined' && !window.confirm('Discard unsaved feed edits?')) {
      return
    }
    setEditingFeedId(null)
    setFeedEditDraft(null)
  }

  const updateFeedEditDraft = (patch: Partial<FeedEditDraft>) => {
    setFeedEditDraft((current) => (current ? { ...current, ...patch } : current))
  }

  const onSaveFeedDetail = () => {
    if (!editingFeed || !feedEditDraft || feedEditValidation || !feedEditDirty) {
      return
    }
    updateFeedDetails.mutate({ feed: editingFeed, draft: feedEditDraft })
  }

  const showMobileAddFeedForm = shouldShowMobileFeedForm(mobileAddFeedOpen, feedStats.total)

  return (
    <div className="grid gap-4 lg:grid-cols-[460px_1fr]">
      <section className="order-2 rounded-xl border border-slate/20 bg-white/80 p-4 sm:order-none dark:border-cyan-900/40 dark:bg-[#041612]/90">
        <div className="flex items-center justify-between gap-3">
          <h2 className="font-display text-xl">Add Feed</h2>
          <button
            type="button"
            className={`${mobileFeedToggleVisibilityClass(feedStats.total)} rounded border border-slate/20 px-3 py-1.5 text-xs font-semibold sm:hidden dark:border-cyan-900/40`}
            aria-expanded={showMobileAddFeedForm}
            aria-controls="add-feed-form"
            onClick={() => setMobileAddFeedOpen((current) => !current)}
          >
            {mobileFeedToggleLabel(mobileAddFeedOpen)}
          </button>
        </div>
        {!canManage && <p className="mt-2 text-sm text-amber-600">Viewer role cannot create or modify feeds.</p>}

        <form
          id="add-feed-form"
          className={`${mobileDisclosureClass(showMobileAddFeedForm)} mt-3 space-y-3 sm:block`}
          onSubmit={onSubmit}
        >
          <div>
            <label htmlFor="feed-rss-url" className="text-sm font-semibold">
              RSS URL
            </label>
            <div className="mt-1 flex gap-2">
              <input
                id="feed-rss-url"
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
            {detectMetadata.isError && (
              <p role="alert" aria-live="assertive" aria-atomic="true" className="mt-1 text-xs text-red-600">
                Failed to detect feed metadata.
              </p>
            )}
          </div>

          <div>
            <label htmlFor="feed-name" className="text-sm font-semibold">
              Name (auto-filled)
            </label>
            <input
              id="feed-name"
              className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
              value={name}
              onChange={(event) => setName(event.target.value)}
              disabled={!canManage}
              placeholder="Leave blank to auto-detect"
            />
          </div>

          <div>
            <label htmlFor="feed-description" className="text-sm font-semibold">
              Description
            </label>
            <textarea
              id="feed-description"
              className="mt-1 h-20 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              disabled={!canManage}
              placeholder="Detected from feed metadata"
            />
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label htmlFor="feed-site-url" className="text-sm font-semibold">
                Site URL
              </label>
              <input
                id="feed-site-url"
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={siteUrl}
                onChange={(event) => setSiteUrl(event.target.value)}
                disabled={!canManage}
              />
            </div>
            <div>
              <label htmlFor="feed-language" className="text-sm font-semibold">
                Language
              </label>
              <input
                id="feed-language"
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={language}
                onChange={(event) => setLanguage(event.target.value)}
                disabled={!canManage}
                placeholder="en-US"
              />
            </div>
          </div>

          <div>
            <label htmlFor="feed-fetch-mode" className="text-sm font-semibold">
              Fetch Mode
            </label>
            <select
              id="feed-fetch-mode"
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
              <label htmlFor="feed-fetch-interval" className="text-sm font-semibold">
                Fetch Interval (seconds)
              </label>
              <input
                id="feed-fetch-interval"
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
              <label htmlFor="feed-schedule-cron" className="text-sm font-semibold">
                Cron Schedule
              </label>
              <input
                id="feed-schedule-cron"
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
          {createFeed.isError && (
            <p role="alert" aria-live="assertive" aria-atomic="true" className="text-sm text-red-600">
              Failed to add feed.
            </p>
          )}
        </form>
      </section>

      <section className="order-1 rounded-xl border border-slate/20 bg-white/80 p-4 sm:order-none dark:border-cyan-900/40 dark:bg-[#041612]/90">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="font-display text-xl">Configured Feeds ({feedStats.total})</h2>
          <div className="grid w-full grid-cols-2 gap-2 sm:flex sm:w-auto sm:flex-wrap sm:items-center">
            <button
              type="button"
              className="rounded border border-slate/30 px-3 py-1.5 text-xs disabled:opacity-50 dark:border-cyan-900/40"
              onClick={() => exportFeeds.mutate()}
              disabled={!canBackup || exportFeeds.isPending}
              title="Export full feed URLs for backup and restore"
            >
              Export JSON
            </button>
            <button
              type="button"
              className="rounded border border-slate/30 px-3 py-1.5 text-xs disabled:opacity-50 dark:border-cyan-900/40"
              onClick={() => importFileInputRef.current?.click()}
              disabled={!canManage}
            >
              Import JSON
            </button>
            <input
              ref={importFileInputRef}
              type="file"
              accept="application/json"
              className="sr-only"
              onChange={onImportFile}
              disabled={!canManage}
              tabIndex={-1}
            />
            <button
              type="button"
              className={`${mobileImportActionVisibilityClass(Boolean(importData))} col-span-2 rounded bg-ink px-3 py-1.5 text-xs text-white disabled:opacity-50 sm:col-auto sm:block dark:bg-cyan dark:text-[#053c2e]`}
              disabled={!canManage || !importData || importFeeds.isPending}
              onClick={onRequestImportReview}
            >
              Run Import
            </button>
          </div>
        </div>

        <p className="mt-2 text-xs text-slate dark:text-slate-300">
          Showing {filteredFeeds.length} of {feedStats.total} feeds · {feedStats.enabled} enabled · {feedStats.disabled} disabled
          · {feedStats.unhealthy} with errors · {feedStats.broken} unreadable URL
        </p>

        {canDelete && hasUnreadableFeedWarning && (
          <div className="mt-3 rounded-lg border border-amber-300/70 bg-amber-50 px-3 py-3 text-sm text-amber-950 dark:border-amber-800/60 dark:bg-amber-950/30 dark:text-amber-100">
            <p className="font-semibold">
              {unreadableFeedInventoryCount} stored feed{unreadableFeedInventoryCount === 1 ? ' has' : 's have'} unreadable
              encrypted URLs.
            </p>
            <p className="mt-1 text-xs text-amber-900/90 dark:text-amber-200/90">
              ThreatLens can keep running, but those feeds cannot refresh until the original `APP_DATA_ENCRYPTION_KEY` is
              restored through `APP_DATA_ENCRYPTION_PREVIOUS_KEYS` or the feeds are recreated.
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                className="rounded border border-amber-400/80 bg-white px-3 py-1.5 text-xs font-semibold text-amber-900 dark:border-amber-700 dark:bg-transparent dark:text-amber-100"
                onClick={() => setStatusFilter('broken')}
              >
                Show Broken Feeds
              </button>
              <button
                type="button"
                className="rounded border border-red-400 px-3 py-1.5 text-xs font-semibold text-red-700 disabled:opacity-50 dark:border-red-700 dark:text-red-300"
                disabled={!brokenFeeds.length || bulkDeleteFeeds.isPending || Boolean(pendingDeleteFeed) || Boolean(pendingBulkDeleteFeeds)}
                onClick={() => onRequestBulkDeleteBrokenFeeds(brokenFeeds)}
              >
                Delete Broken Feeds
              </button>
            </div>
          </div>
        )}

        {showDerivedKeyWarning && (
          <div className="mt-3 rounded-lg border border-sky-300/70 bg-sky-50 px-3 py-3 text-sm text-sky-950 dark:border-sky-900/60 dark:bg-sky-950/25 dark:text-sky-100">
            <p className="font-semibold">This deployment is using a derived development encryption key.</p>
            <p className="mt-1 text-xs text-sky-900/90 dark:text-sky-200/90">
              Set an explicit persistent `APP_DATA_ENCRYPTION_KEY` before relying on durable data. The bundled compose
              deployment now expects that key to be configured on purpose.
            </p>
          </div>
        )}

        <div className="mt-2">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-1 sm:gap-3 md:grid-cols-[minmax(0,1fr)_180px_180px_auto]">
            <div className="col-span-2 sm:col-span-1">
              <label htmlFor="feed-search" className="text-xs font-semibold uppercase text-slate dark:text-slate-300">
                Search
              </label>
              <input
                id="feed-search"
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Name, URL, language, or error"
              />
            </div>
            <div>
              <label htmlFor="feed-status-filter" className="text-xs font-semibold uppercase text-slate dark:text-slate-300">
                Status
              </label>
              <select
                id="feed-status-filter"
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                value={statusFilter}
                onChange={(event) => setStatusFilter(event.target.value as FeedStatusFilter)}
              >
                <option value="all">All feeds</option>
                <option value="enabled">Enabled only</option>
                <option value="disabled">Disabled only</option>
                <option value="broken">Unreadable URL only</option>
              </select>
            </div>
            <div>
              <label htmlFor="feed-sort" className="text-xs font-semibold uppercase text-slate dark:text-slate-300">
                Sort
              </label>
              <select
                id="feed-sort"
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                value={sort}
                onChange={(event) => setSort(event.target.value as FeedSort)}
              >
                <option value="created_desc">Newest created</option>
                <option value="name_asc">Name A-Z</option>
                <option value="name_desc">Name Z-A</option>
                <option value="last_fetch_desc">Last fetched newest</option>
                <option value="last_fetch_asc">Last fetched oldest</option>
              </select>
            </div>
            <label className="col-span-2 flex items-end gap-2 text-xs text-slate sm:col-span-1 dark:text-slate-300">
              <input
                type="checkbox"
                checked={overwriteExisting}
                onChange={(event) => setOverwriteExisting(event.target.checked)}
                disabled={!canManage}
              />
              Overwrite existing on import
            </label>
          </div>
        </div>

        <button
          type="button"
          className="mt-2 flex w-full items-center justify-between rounded border border-slate/30 px-3 py-2 text-left text-sm font-semibold sm:hidden dark:border-cyan-900/40"
          aria-expanded={mobileBulkActionsOpen}
          aria-controls="feed-bulk-actions"
          onClick={() => setMobileBulkActionsOpen((current) => !current)}
        >
          <span>Bulk actions</span>
          <span className="text-xs font-normal text-slate dark:text-slate-300">Filtered feeds</span>
        </button>

        <div
          id="feed-bulk-actions"
          className={`${mobileDisclosureClass(mobileBulkActionsOpen)} mt-2 grid gap-2 sm:flex sm:flex-wrap sm:items-center`}
        >
          <button
            type="button"
            className="rounded border border-slate/30 px-3 py-1.5 text-xs dark:border-cyan-900/40"
            disabled={!canManage || !visibleFeedIds.length || bulkRefreshFeeds.isPending}
            onClick={() => {
              setManagementNotice('')
              bulkRefreshFeeds.mutate(filteredFeeds)
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
              setPendingBulkSetEnabled({
                enabled: true,
                feeds: filteredFeeds.filter((feed) => !feed.enabled),
              })
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
              setPendingBulkSetEnabled({
                enabled: false,
                feeds: filteredFeeds.filter((feed) => feed.enabled),
              })
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
              onClick={() => onRequestBulkDeleteFeeds(filteredFeeds.filter((feed) => !feed.enabled))}
            >
              Delete Disabled (Filtered)
            </button>
          )}
          {canDelete && (
            <button
              type="button"
              className="rounded border border-red-300 px-3 py-1.5 text-xs text-red-700 dark:border-red-800 dark:text-red-300"
              disabled={
                !visibleBrokenFeedIds.length ||
                bulkDeleteFeeds.isPending ||
                Boolean(pendingDeleteFeed) ||
                Boolean(pendingBulkDeleteFeeds)
              }
              onClick={() => onRequestBulkDeleteBrokenFeeds(filteredFeeds.filter((feed) => feed.has_unreadable_url))}
            >
              Delete Broken (Filtered)
            </button>
          )}
        </div>

        {importFilename && (
          <p className="mt-2 text-xs text-slate dark:text-slate-300">
            Loaded: {importFilename} ({importData?.length ?? 0} entries)
          </p>
        )}
        {importPreviewSummary && (
          <div className="mt-2 rounded border border-slate/20 bg-white/60 px-2 py-2 text-xs text-slate dark:border-cyan-900/40 dark:bg-[#072019] dark:text-slate-200">
            <p>
              Import preflight: {importPreviewSummary.createCount} new, {importPreviewSummary.overwriteCount} overwrite,{' '}
              {importPreviewSummary.skipCount} skip, {importPreviewSummary.duplicateEntries} duplicate entr
              {importPreviewSummary.duplicateEntries === 1 ? 'y' : 'ies'} ignored from {importPreviewSummary.uniqueEntries} unique URL
              {importPreviewSummary.uniqueEntries === 1 ? '' : 's'}.
            </p>
            {importPreviewSummary.matchingExistingFeeds.length > 0 && (
              <p className="mt-1 text-slate dark:text-slate-300">
                {overwriteExisting
                  ? 'Existing feeds below will be rewritten from the import file after confirmation.'
                  : 'Existing feeds below will be skipped unless overwrite is enabled.'}
              </p>
            )}
          </div>
        )}
        {importError && (
          <p role="alert" aria-live="assertive" aria-atomic="true" className="mt-2 text-xs text-red-600">
            Import parse error: {importError}
          </p>
        )}
        {importWarning && (
          <p role="status" aria-live="polite" aria-atomic="true" className="mt-2 text-xs text-amber-600">
            {importWarning}
          </p>
        )}
        {lastImportResult && (
          <div
            role="status"
            aria-live="polite"
            aria-atomic="true"
            className="mt-2 rounded border border-slate/20 bg-white/60 px-2 py-2 text-xs text-slate dark:border-cyan-900/40 dark:bg-[#072019] dark:text-slate-200"
          >
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
          <p role="alert" aria-live="assertive" aria-atomic="true" className="mt-2 text-xs text-red-600">
            Import failed: {resolveMutationError(importFeeds.error)}
          </p>
        )}
        {managementNotice && (
          <p role="status" aria-live="polite" aria-atomic="true" className="mt-2 text-xs text-emerald-700 dark:text-emerald-300">
            {managementNotice}
          </p>
        )}
        {exportNotice && (
          <p role="status" aria-live="polite" aria-atomic="true" className="mt-2 text-xs text-amber-700 dark:text-amber-300">
            {exportNotice}
          </p>
        )}
        {exportFeeds.isError && (
          <p role="alert" aria-live="assertive" aria-atomic="true" className="mt-2 text-xs text-red-600">
            Export failed: {resolveMutationError(exportFeeds.error)}
          </p>
        )}
        {canDelete && encryptedDataHealthQuery.isError && (
          <p role="alert" aria-live="assertive" aria-atomic="true" className="mt-2 text-xs text-red-600">
            Failed to load encrypted data health.
          </p>
        )}
        {(bulkRefreshFeeds.isError || bulkSetEnabled.isError || bulkDeleteFeeds.isError || deleteFeed.isError) && (
          <p role="alert" aria-live="assertive" aria-atomic="true" className="mt-2 text-xs text-red-600">
            One or more management actions failed.
          </p>
        )}

        <div className="mt-3 space-y-2">
          {filteredFeeds.map((feed) => {
            const health = resolveFeedHealth(feed)
            const draft = feedDrafts[feed.id] ?? feedToScheduleDraft(feed)
            const saveState = feedSaveState[feed.id]?.status ?? 'idle'
            const saveMessage = feedSaveState[feed.id]?.message
            const validationMessage = validateFeedScheduleDraft(draft)
            const isDirty = isFeedScheduleDraftDirty(feed, draft)
            const scheduleNotice =
              validationMessage ?? (saveState !== 'idle' ? saveMessage || feedSaveStatusText(saveState) : null)
            const scheduleHint =
              !scheduleNotice && isDirty ? 'Unsaved schedule changes. Save or reset before leaving this page.' : null
            const displayUrl = feed.url.trim() || 'URL unavailable until the original encryption key is restored.'
            const scheduleExpanded = mobileScheduleFeedId === feed.id || isDirty
            return (
            <div key={feed.id} className="rounded border border-slate/20 p-3 dark:border-cyan-900/40">
              <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                <div>
                  <div className="flex items-center gap-2">
                    <p className="font-semibold">{feed.name}</p>
                    {feed.has_unreadable_url && (
                      <span className="tl-chip tl-chip-danger">
                        Broken URL
                      </span>
                    )}
                    {isDirty && (
                      <span className="tl-chip tl-chip-warning">
                        Unsaved schedule
                      </span>
                    )}
                    <span className={`tl-chip ${feedHealthBadgeClass(health.status)}`}>
                      {health.label}
                    </span>
                  </div>
                  <p className="text-xs text-slate dark:text-slate-300">{displayUrl}</p>
                  {feed.description && <p className="mt-1 line-clamp-2 text-xs text-slate sm:line-clamp-none dark:text-slate-300">{feed.description}</p>}
                  <div className="mt-1 flex flex-wrap gap-2 text-[11px] text-slate dark:text-slate-300">
                    {feed.site_url && <span>Site: {feed.site_url}</span>}
                    {feed.language && <span>Lang: {feed.language}</span>}
                    <span>Last fetch: {formatDate(feed.last_fetch_at)}</span>
                    <span>Last success: {formatDate(feed.last_success_at)}</span>
                  </div>
                </div>
                <div className="grid w-full grid-cols-3 gap-1.5 sm:flex sm:w-auto sm:flex-wrap sm:gap-2">
                  <button
                    className="rounded border border-slate/30 px-2 py-1 text-xs dark:border-cyan-900/40"
                    onClick={() => openFeedDetail(feed)}
                  >
                    {canManage ? 'Edit' : 'Details'}
                  </button>
                  <button
                    type="button"
                    className="rounded border border-slate/30 px-2 py-1 text-xs sm:hidden dark:border-cyan-900/40"
                    aria-expanded={scheduleExpanded}
                    aria-controls={`feed-schedule-${feed.id}`}
                    onClick={() => setMobileScheduleFeedId((current) => (current === feed.id ? null : feed.id))}
                  >
                    {scheduleExpanded ? 'Hide schedule' : 'Schedule'}
                  </button>
                  <button
                    className="rounded border border-slate/30 px-2 py-1 text-xs dark:border-cyan-900/40"
                    onClick={() => refreshFeed.mutate(feed.id)}
                    disabled={!canManage || feed.has_unreadable_url}
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
                      onClick={() => onRequestDeleteFeed(feed)}
                      disabled={deleteFeed.isPending || Boolean(pendingDeleteFeed) || Boolean(pendingBulkDeleteFeeds)}
                    >
                      Delete
                    </button>
                  )}
                </div>
              </div>

              <div id={`feed-schedule-${feed.id}`} className={`${scheduleExpanded ? 'block' : 'hidden'} sm:block`}>
              <div className="mt-3 grid gap-2 md:grid-cols-[180px_1fr]">
                <label htmlFor={`feed-fetch-mode-${feed.id}`} className="sr-only">
                  Fetch mode for {feed.name}
                </label>
                <select
                  id={`feed-fetch-mode-${feed.id}`}
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
                    <label htmlFor={`feed-interval-seconds-${feed.id}`} className="sr-only">
                      Interval seconds for {feed.name}
                    </label>
                    <label htmlFor={`feed-interval-seconds-${feed.id}`} className="text-xs font-semibold">
                      Every
                    </label>
                    <input
                      id={`feed-interval-seconds-${feed.id}`}
                      className="w-28 rounded border border-slate/30 bg-white px-2 py-1 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                      type="number"
                      min={60}
                      value={draft.intervalSeconds}
                      onChange={(event) => {
                        updateFeedDraft(feed, { fetchMode: 'interval', intervalSeconds: event.target.value })
                      }}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter' && canManage && isDirty && !validationMessage && saveState !== 'saving') {
                          event.preventDefault()
                          void persistFeedSchedule(feed.id, draft)
                        }
                      }}
                      disabled={!canManage}
                    />
                    <span className="text-xs text-slate dark:text-slate-300">seconds</span>
                  </div>
                ) : (
                  <>
                    <label htmlFor={`feed-schedule-cron-${feed.id}`} className="sr-only">
                      Cron schedule for {feed.name}
                    </label>
                    <input
                      id={`feed-schedule-cron-${feed.id}`}
                      className="rounded border border-slate/30 bg-white px-2 py-1 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                      value={draft.scheduleCron}
                      onChange={(event) => {
                        updateFeedDraft(feed, { fetchMode: 'schedule', scheduleCron: event.target.value })
                      }}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter' && canManage && isDirty && !validationMessage && saveState !== 'saving') {
                          event.preventDefault()
                          void persistFeedSchedule(feed.id, draft)
                        }
                      }}
                      disabled={!canManage}
                    />
                  </>
                )}
              </div>

              {canManage && (
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    className="rounded border border-slate/30 px-3 py-1 text-xs font-semibold disabled:opacity-50 dark:border-cyan-900/40"
                    disabled={!isDirty || saveState === 'saving' || Boolean(validationMessage)}
                    onClick={() => void persistFeedSchedule(feed.id, draft)}
                  >
                    Save schedule
                  </button>
                  <button
                    type="button"
                    className="rounded border border-slate/30 px-3 py-1 text-xs disabled:opacity-50 dark:border-cyan-900/40"
                    disabled={!isDirty && saveState === 'idle'}
                    onClick={() => resetFeedDraft(feed)}
                  >
                    Reset draft
                  </button>
                </div>
              )}

              {canManage && scheduleHint && (
                <p className={`mt-1 text-[11px] ${feedSaveStatusClass(saveState, isDirty)}`}>
                  {scheduleHint}
                </p>
              )}

              {canManage && scheduleNotice && (
                <p
                  role={saveState === 'error' ? 'alert' : 'status'}
                  aria-live={saveState === 'error' ? 'assertive' : 'polite'}
                  aria-atomic="true"
                  className={`mt-1 text-[11px] ${validationMessage ? 'text-red-600' : feedSaveStatusClass(saveState, isDirty)}`}
                >
                  {scheduleNotice}
                </p>
              )}
              </div>

              {feed.last_error && <p className="mt-2 text-xs text-red-600">Last error: {feed.last_error}</p>}
            </div>
            )
          })}

          {feedsQuery.isLoading && <p className="text-sm text-slate dark:text-slate-300">Loading feeds...</p>}
          {feedsQuery.isError && <p className="text-sm text-red-600">Failed to load feeds.</p>}
          {!feedsQuery.isLoading && !filteredFeeds.length && (
            <p className="text-sm text-slate dark:text-slate-300">No feeds match your current filters.</p>
          )}
        </div>
      </section>

      <DialogSurface
        open={Boolean(editingFeed && feedEditDraft)}
        title={editingFeed?.name ?? 'Feed Details'}
        description="Update feed settings and review the latest ingested articles from this source."
        panelClassName="flex max-h-[90vh] max-w-5xl flex-col overflow-hidden"
        bodyClassName="mt-4 min-h-0 space-y-5 overflow-auto text-sm text-slate dark:text-white/75"
        footerClassName="mt-5 flex flex-wrap items-center justify-between gap-2"
        ariaBusy={updateFeedDetails.isPending}
        dismissDisabled={updateFeedDetails.isPending}
        onClose={closeFeedDetail}
        footer={
          <>
            <div className="min-h-5 text-xs">
              {feedEditValidation && <span className="text-red-600">{feedEditValidation}</span>}
              {!feedEditValidation && feedEditDirty && (
                <span className="text-amber-700 dark:text-amber-300">Unsaved feed edits.</span>
              )}
              {!feedEditValidation && !feedEditDirty && (
                <span className="text-slate dark:text-slate-300">No unsaved feed edits.</span>
              )}
            </div>
            <div className="flex flex-wrap justify-end gap-2">
              <button
                type="button"
                className="rounded border border-slate/20 px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-slate/5 dark:border-cyan-900/40 dark:text-slate-100 dark:hover:bg-white/[0.04]"
                onClick={closeFeedDetail}
                disabled={updateFeedDetails.isPending}
              >
                Cancel
              </button>
              {canManage && (
                <button
                  type="button"
                  className="rounded bg-ink px-3 py-2 text-sm font-semibold text-white hover:bg-slate-900 disabled:cursor-not-allowed disabled:opacity-60 dark:bg-cyan dark:text-[#053c2e] dark:hover:bg-cyan/90"
                  onClick={onSaveFeedDetail}
                  disabled={updateFeedDetails.isPending || Boolean(feedEditValidation) || !feedEditDirty}
                >
                  {updateFeedDetails.isPending ? 'Saving...' : 'Save feed'}
                </button>
              )}
            </div>
          </>
        }
      >
        {editingFeed && feedEditDraft && (
          <>
            <section className="space-y-3 rounded border border-slate/20 bg-slate/5 p-3 dark:border-cyan-900/40 dark:bg-white/[0.03]">
              <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_180px]">
                <div>
                  <label htmlFor="feed-edit-url" className="text-xs font-semibold uppercase text-slate dark:text-slate-300">
                    RSS URL
                  </label>
                  <input
                    id="feed-edit-url"
                    className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 font-mono text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                    value={feedEditDraft.url}
                    onChange={(event) => updateFeedEditDraft({ url: event.target.value })}
                    disabled={!canManage}
                    placeholder={editingFeed.has_unreadable_url ? 'Enter a replacement RSS URL' : 'https://example.com/feed.xml'}
                  />
                  {editingFeed.url.includes('REDACTED') && (
                    <p className="mt-1 text-xs text-amber-700 dark:text-amber-300">
                      Sensitive URL parts are redacted. Leave this field unchanged unless replacing the full feed URL.
                    </p>
                  )}
                </div>
                <label className="flex items-end gap-2 text-sm font-semibold text-slate dark:text-slate-200">
                  <input
                    type="checkbox"
                    checked={feedEditDraft.enabled}
                    onChange={(event) => updateFeedEditDraft({ enabled: event.target.checked })}
                    disabled={!canManage}
                  />
                  Enabled
                </label>
              </div>

              <div className="grid gap-3 md:grid-cols-2">
                <div>
                  <label htmlFor="feed-edit-name" className="text-xs font-semibold uppercase text-slate dark:text-slate-300">
                    Name
                  </label>
                  <input
                    id="feed-edit-name"
                    className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                    value={feedEditDraft.name}
                    onChange={(event) => updateFeedEditDraft({ name: event.target.value })}
                    disabled={!canManage}
                  />
                </div>
                <div>
                  <label htmlFor="feed-edit-site-url" className="text-xs font-semibold uppercase text-slate dark:text-slate-300">
                    Site URL
                  </label>
                  <input
                    id="feed-edit-site-url"
                    className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                    value={feedEditDraft.siteUrl}
                    onChange={(event) => updateFeedEditDraft({ siteUrl: event.target.value })}
                    disabled={!canManage}
                  />
                </div>
              </div>

              <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_140px]">
                <div>
                  <label htmlFor="feed-edit-description" className="text-xs font-semibold uppercase text-slate dark:text-slate-300">
                    Description
                  </label>
                  <textarea
                    id="feed-edit-description"
                    className="mt-1 h-20 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                    value={feedEditDraft.description}
                    onChange={(event) => updateFeedEditDraft({ description: event.target.value })}
                    disabled={!canManage}
                  />
                </div>
                <div>
                  <label htmlFor="feed-edit-language" className="text-xs font-semibold uppercase text-slate dark:text-slate-300">
                    Language
                  </label>
                  <input
                    id="feed-edit-language"
                    className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                    value={feedEditDraft.language}
                    onChange={(event) => updateFeedEditDraft({ language: event.target.value })}
                    disabled={!canManage}
                    placeholder="en-US"
                  />
                </div>
              </div>

              <div className="grid gap-3 md:grid-cols-[180px_1fr]">
                <div>
                  <label htmlFor="feed-edit-fetch-mode" className="text-xs font-semibold uppercase text-slate dark:text-slate-300">
                    Fetch Mode
                  </label>
                  <select
                    id="feed-edit-fetch-mode"
                    className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                    value={feedEditDraft.fetchMode}
                    onChange={(event) => {
                      const nextMode = event.target.value as FeedFetchMode
                      updateFeedEditDraft({
                        fetchMode: nextMode,
                        intervalSeconds: feedEditDraft.intervalSeconds || '1800',
                        scheduleCron:
                          nextMode === 'schedule'
                            ? feedEditDraft.scheduleCron || DEFAULT_SCHEDULE_CRON
                            : feedEditDraft.scheduleCron,
                      })
                    }}
                    disabled={!canManage}
                  >
                    <option value="interval">Interval</option>
                    <option value="schedule">Schedule</option>
                  </select>
                </div>

                {feedEditDraft.fetchMode === 'interval' ? (
                  <div>
                    <label htmlFor="feed-edit-interval" className="text-xs font-semibold uppercase text-slate dark:text-slate-300">
                      Fetch Interval
                    </label>
                    <div className="mt-1 flex items-center gap-2">
                      <input
                        id="feed-edit-interval"
                        className="w-36 rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                        type="number"
                        min={60}
                        value={feedEditDraft.intervalSeconds}
                        onChange={(event) => updateFeedEditDraft({ intervalSeconds: event.target.value })}
                        disabled={!canManage}
                      />
                      <span className="text-xs text-slate dark:text-slate-300">seconds</span>
                    </div>
                  </div>
                ) : (
                  <div>
                    <label htmlFor="feed-edit-cron" className="text-xs font-semibold uppercase text-slate dark:text-slate-300">
                      Cron Schedule
                    </label>
                    <input
                      id="feed-edit-cron"
                      className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 font-mono text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                      value={feedEditDraft.scheduleCron}
                      onChange={(event) => updateFeedEditDraft({ scheduleCron: event.target.value })}
                      disabled={!canManage}
                      placeholder="0 * * * *"
                    />
                  </div>
                )}
              </div>

              {updateFeedDetails.isError && (
                <p role="alert" aria-live="assertive" aria-atomic="true" className="text-sm text-red-600">
                  Feed update failed: {resolveMutationError(updateFeedDetails.error)}
                </p>
              )}
            </section>

            <section aria-labelledby="feed-recent-articles-heading" className="space-y-2">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h4 id="feed-recent-articles-heading" className="font-display text-lg text-ink dark:text-white">
                  Recent Articles
                </h4>
                {feedArticlesQuery.data && (
                  <span className="text-xs text-slate dark:text-slate-300">
                    Showing {feedArticlesQuery.data.items.length} of {feedArticlesQuery.data.total}
                  </span>
                )}
              </div>

              {feedArticlesQuery.isLoading && <p className="text-sm text-slate dark:text-slate-300">Loading recent articles...</p>}
              {feedArticlesQuery.isError && (
                <p role="alert" aria-live="assertive" aria-atomic="true" className="text-sm text-red-600">
                  Failed to load recent articles.
                </p>
              )}
              {!feedArticlesQuery.isLoading && !feedArticlesQuery.isError && !feedArticlesQuery.data?.items.length && (
                <p className="rounded border border-slate/20 bg-slate/5 p-3 text-sm text-slate dark:border-cyan-900/40 dark:bg-white/[0.03] dark:text-slate-300">
                  No articles have been ingested from this feed yet.
                </p>
              )}
              <div className="space-y-2">
                {(feedArticlesQuery.data?.items ?? []).map((item) => (
                  <article key={item.id} className="rounded border border-slate/20 bg-white/70 p-3 dark:border-cyan-900/40 dark:bg-[#072019]">
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div className="min-w-0">
                        <a
                          className="break-words text-sm font-semibold text-ink hover:text-cyan dark:text-white dark:hover:text-cyan-100"
                          href={item.url}
                          target="_blank"
                          rel="noreferrer"
                        >
                          {item.title}
                        </a>
                        <p className="mt-1 text-xs text-slate dark:text-slate-300">
                          Published {formatDate(item.published_at)} · Seen {formatDate(item.first_seen_at)}
                        </p>
                      </div>
                      <span className="tl-chip">{item.status}</span>
                    </div>
                    {item.summary && <p className="mt-2 line-clamp-2 text-sm text-slate dark:text-slate-300">{item.summary}</p>}
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {item.classification && <span className="tl-chip tl-chip-info">{item.classification}</span>}
                      {item.tags.slice(0, 5).map((tag) => (
                        <span key={tag} className="tl-chip">
                          {tag}
                        </span>
                      ))}
                    </div>
                  </article>
                ))}
              </div>
            </section>
          </>
        )}
      </DialogSurface>

      <ConfirmDialog
        open={Boolean(pendingImportReview)}
        title={pendingImportReview?.overwriteCount ? 'Overwrite existing feeds from import?' : 'Run feed import?'}
        description="Review the import preflight summary before applying the file to this workspace."
        confirmLabel={pendingImportReview?.overwriteCount ? 'Run overwrite import' : 'Run import'}
        confirmTone="primary"
        onCancel={() => setPendingImportReview(null)}
        onConfirm={onConfirmImportReview}
        confirmDisabled={importFeeds.isPending || !pendingImportReview}
        isConfirming={importFeeds.isPending}
      >
        {pendingImportReview && (
          <div className="space-y-3">
            <div className="grid gap-2 text-sm sm:grid-cols-2">
              <p>
                <span className="font-semibold text-ink dark:text-white">{pendingImportReview.totalEntries}</span> file entr
                {pendingImportReview.totalEntries === 1 ? 'y' : 'ies'}
              </p>
              <p>
                <span className="font-semibold text-ink dark:text-white">{pendingImportReview.uniqueEntries}</span> unique URL
                {pendingImportReview.uniqueEntries === 1 ? '' : 's'}
              </p>
              <p>
                <span className="font-semibold text-emerald-700 dark:text-emerald-300">{pendingImportReview.createCount}</span>{' '}
                new feed{pendingImportReview.createCount === 1 ? '' : 's'}
              </p>
              <p>
                <span className="font-semibold text-amber-700 dark:text-amber-300">{pendingImportReview.overwriteCount}</span>{' '}
                feed{pendingImportReview.overwriteCount === 1 ? '' : 's'} overwritten
              </p>
              <p>
                <span className="font-semibold text-slate-700 dark:text-slate-200">{pendingImportReview.skipCount}</span>{' '}
                feed{pendingImportReview.skipCount === 1 ? '' : 's'} skipped
              </p>
              <p>
                <span className="font-semibold text-slate-700 dark:text-slate-200">{pendingImportReview.duplicateEntries}</span>{' '}
                duplicate entr{pendingImportReview.duplicateEntries === 1 ? 'y' : 'ies'}
              </p>
            </div>
            {pendingImportReview.matchingExistingFeeds.length > 0 && (
              <div className="max-h-48 overflow-auto rounded border border-slate/20 bg-slate/5 p-3 dark:border-cyan-900/40 dark:bg-white/[0.03]">
                <p className="mb-2 text-xs font-semibold uppercase text-slate dark:text-white/60">
                  Existing feeds in scope
                </p>
                <ul className="space-y-1">
                  {pendingImportReview.matchingExistingFeeds.map((feed) => (
                    <li key={feed.id} className="space-y-0.5">
                      <p className="text-sm font-semibold text-ink dark:text-white">{feed.name}</p>
                      <p className="break-all font-mono text-[11px] text-slate dark:text-white/65">
                        {feed.url.trim() || 'URL unavailable until the original encryption key is restored.'}
                      </p>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </ConfirmDialog>

      <ConfirmDialog
        open={Boolean(pendingBulkSetEnabled?.feeds.length)}
        title={pendingBulkSetEnabled?.enabled ? 'Enable filtered feeds?' : 'Disable filtered feeds?'}
        description="Review the feeds in the current filtered view before applying this bulk status change."
        confirmLabel={pendingBulkSetEnabled?.enabled ? 'Enable feeds' : 'Disable feeds'}
        onCancel={() => setPendingBulkSetEnabled(null)}
        onConfirm={onConfirmBulkSetEnabled}
        confirmDisabled={bulkSetEnabled.isPending || !pendingBulkSetEnabled?.feeds.length}
        isConfirming={bulkSetEnabled.isPending}
      >
        {pendingBulkSetEnabled && (
          <div className="space-y-3">
            <p>
              You are about to {pendingBulkSetEnabled.enabled ? 'enable' : 'disable'}{' '}
              <span className="font-semibold text-ink dark:text-white">{pendingBulkSetEnabled.feeds.length}</span> filtered
              feed{pendingBulkSetEnabled.feeds.length === 1 ? '' : 's'}.
            </p>
            <div className="max-h-48 overflow-auto rounded border border-slate/20 bg-slate/5 p-3 dark:border-cyan-900/40 dark:bg-white/[0.03]">
              <ul className="space-y-1">
                {pendingBulkSetEnabled.feeds.map((feed) => (
                  <li key={feed.id} className="break-all font-mono text-xs text-slate-700 dark:text-white/70">
                    {feed.name}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        )}
      </ConfirmDialog>

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
            <p className="break-all font-mono text-xs text-slate dark:text-white/65">
              {pendingDeleteFeed.url.trim() || 'URL unavailable until the original encryption key is restored.'}
            </p>
          </div>
        )}
      </ConfirmDialog>

      <ConfirmDialog
        open={Boolean(pendingBulkDeleteFeeds?.feeds.length)}
        title={pendingBulkDeleteFeeds?.kind === 'broken' ? 'Delete broken feeds?' : 'Delete filtered disabled feeds?'}
        description={
          pendingBulkDeleteFeeds?.kind === 'broken'
            ? 'This permanently removes feeds whose stored URLs can no longer be decrypted.'
            : 'This permanently removes every disabled feed in the current filtered view.'
        }
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
              <span className="font-semibold text-ink dark:text-white">{pendingBulkDeleteFeeds.feeds.length}</span>{' '}
              {pendingBulkDeleteFeeds.kind === 'broken' ? 'broken' : 'disabled'} feed
              {pendingBulkDeleteFeeds.feeds.length === 1 ? '' : 's'} from this view.
            </p>
            <div className="max-h-48 overflow-auto rounded border border-slate/20 bg-slate/5 p-3 dark:border-cyan-900/40 dark:bg-white/[0.03]">
              <ul className="space-y-1">
                {pendingBulkDeleteFeeds.feeds.map((feed) => (
                  <li key={feed.id} className="break-all font-mono text-xs text-slate-700 dark:text-white/70">
                    {feed.name}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        )}
      </ConfirmDialog>
      {confirmDiscardUnsavedFeedScheduleChanges.discardDialog}
    </div>
  )
}
