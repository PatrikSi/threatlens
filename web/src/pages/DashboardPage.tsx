import { ReactNode, useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
import { useDebouncedValue } from '../hooks/useDebouncedValue'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { Feed, ItemDetail, ItemListEntry, ItemListResponse, SavedView, Tag } from '../types/api'

type TimeRangeFilter = 'all' | '24h' | '7d' | '30d' | 'custom'
type ReadStatusFilter = 'all' | 'read' | 'unread'
type StarStatusFilter = 'all' | 'starred' | 'unstarred'
type TimeSort = 'published_at_desc' | 'published_at_asc' | 'first_seen_desc' | 'first_seen_asc'
type DashboardViewMode = 'expanded' | 'compact'

type PanelRect = {
  x: number
  y: number
  width: number
  height: number
}

interface DashboardSavedViewQuery {
  selected_feed_ids: string[]
  selected_tags: string[]
  q: string
  read_status: ReadStatusFilter
  star_status: StarStatusFilter
  view_mode: DashboardViewMode
  page_size: number
  time_range: TimeRangeFilter
  custom_since_date: string
  custom_until_date: string
  sort: TimeSort
}

interface DashboardSavedViewLayout {
  windows: Record<string, PanelRect>
}

interface DashboardSavedViewState {
  version: number
  filters: DashboardSavedViewQuery
  layout: DashboardSavedViewLayout
  ui: {
    show_advanced_filters: boolean
  }
}

const PANEL_STORAGE_KEY = 'threatlens.dashboard.panel.v1'
const DASHBOARD_VIEW_VERSION = 2
const FEEDS_WINDOW_ID = 'feeds'
const LEGACY_PANEL_WIDTH = 1180
const PANEL_OUTER_GAP = 12
const PANEL_MIN_WIDTH = 860
const PANEL_MIN_HEIGHT = 520
const HIDDEN_TAGS = new Set(['content_fetched', 'priority'])
const PAGE_SIZE_OPTIONS = [10, 25, 50, 100]

export function DashboardPage() {
  const queryClient = useQueryClient()
  const meQuery = useCurrentUser()
  const rootRef = useRef<HTMLDivElement | null>(null)

  const [selectedFeedIds, setSelectedFeedIds] = useState<string[]>([])
  const [selectedTags, setSelectedTags] = useState<string[]>([])
  const [q, setQ] = useState('')
  const [readStatus, setReadStatus] = useState<ReadStatusFilter>('all')
  const [starStatus, setStarStatus] = useState<StarStatusFilter>('all')
  const [viewMode, setViewMode] = useState<DashboardViewMode>('expanded')
  const [timeRange, setTimeRange] = useState<TimeRangeFilter>('all')
  const [customSinceDate, setCustomSinceDate] = useState('')
  const [customUntilDate, setCustomUntilDate] = useState('')
  const [sort, setSort] = useState<TimeSort>('published_at_desc')
  const [savedViewName, setSavedViewName] = useState('')
  const [activeSavedViewId, setActiveSavedViewId] = useState<string | null>(null)
  const [showAdvancedFilters, setShowAdvancedFilters] = useState(false)

  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState<number>(25)
  const [expandedItemId, setExpandedItemId] = useState<string>('')
  const [noteDraft, setNoteDraft] = useState('')

  const [panelRect, setPanelRect] = useState<PanelRect>(() => loadPanelRect())
  const [isWideLayout, setIsWideLayout] = useState<boolean>(typeof window !== 'undefined' ? window.innerWidth >= 1024 : true)

  const canManage = meQuery.data?.role === 'admin' || meQuery.data?.role === 'analyst'

  useEffect(() => {
    const syncLayout = () => {
      const nextIsWide = window.innerWidth >= 1024
      setIsWideLayout(nextIsWide)
      if (nextIsWide) {
        const { width, height } = getPanelContainerDimensions(rootRef.current)
        setPanelRect((current) => normalizePanelRect(current, width, height))
      }
    }

    syncLayout()
    window.addEventListener('resize', syncLayout)
    return () => window.removeEventListener('resize', syncLayout)
  }, [])

  useEffect(() => {
    if (typeof window === 'undefined') {
      return
    }
    window.localStorage.setItem(PANEL_STORAGE_KEY, JSON.stringify(panelRect))
  }, [panelRect])

  const debouncedQ = useDebouncedValue(q)
  const feedIdsParam = useMemo(() => selectedFeedIds.slice().sort().join(','), [selectedFeedIds])
  const selectedTagsParam = useMemo(
    () =>
      selectedTags
        .filter((tagName) => !HIDDEN_TAGS.has(tagName))
        .slice()
        .sort()
        .join(','),
    [selectedTags],
  )

  const { sinceIso, untilIso } = useMemo(
    () => deriveTimeWindow(timeRange, customSinceDate, customUntilDate),
    [timeRange, customSinceDate, customUntilDate],
  )

  const feedsQuery = useQuery({
    queryKey: ['feeds'],
    queryFn: () => apiFetch<Feed[]>('/feeds'),
  })

  const viewsQuery = useQuery({
    queryKey: ['views'],
    queryFn: () => apiFetch<SavedView[]>('/views'),
  })

  const tagsQuery = useQuery({
    queryKey: ['tags'],
    queryFn: () => apiFetch<Tag[]>('/tags'),
  })

  const saveView = useMutation({
    mutationFn: (payload: { name: string; query: DashboardSavedViewState }) =>
      apiFetch<SavedView>('/views', {
        method: 'POST',
        body: JSON.stringify({
          name: payload.name,
          query_json: payload.query,
        }),
      }),
    onSuccess: (view) => {
      setSavedViewName('')
      setActiveSavedViewId(view.id)
      queryClient.invalidateQueries({ queryKey: ['views'] })
    },
  })

  const deleteView = useMutation({
    mutationFn: (viewId: string) =>
      apiFetch(`/views/${viewId}`, {
        method: 'DELETE',
      }),
    onSuccess: () => {
      setActiveSavedViewId(null)
      queryClient.invalidateQueries({ queryKey: ['views'] })
    },
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
    queryKey: ['items', feedIdsParam, selectedTagsParam, debouncedQ, readStatus, starStatus, sinceIso, untilIso, sort, page, pageSize],
    retry: 1,
    queryFn: () => {
      const params = new URLSearchParams()
      params.set('page', String(page))
      params.set('page_size', String(pageSize))
      params.set('sort', sort)

      if (feedIdsParam) params.set('feed_ids', feedIdsParam)
      if (selectedTagsParam) {
        params.set('tags', selectedTagsParam)
      }
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
      setExpandedItemId('')
      return
    }

    if (!expandedItemId) {
      return
    }

    const selectedExists = items.some((item) => item.id === expandedItemId)
    if (!selectedExists) {
      setExpandedItemId('')
    }
  }, [itemsQuery.data?.items, expandedItemId])

  const detailQuery = useQuery({
    queryKey: ['item', expandedItemId],
    enabled: Boolean(expandedItemId),
    queryFn: () => apiFetch<ItemDetail>(`/items/${expandedItemId}`),
  })

  useEffect(() => {
    setNoteDraft(detailQuery.data?.state.note ?? '')
  }, [detailQuery.data?.state.note])

  const totalPages = useMemo(() => {
    const total = itemsQuery.data?.total ?? 0
    return Math.max(1, Math.ceil(total / pageSize))
  }, [itemsQuery.data?.total, pageSize])

  const handleToggleItem = (itemId: string, isRead: boolean) => {
    if (expandedItemId === itemId) {
      setExpandedItemId('')
      return
    }

    setExpandedItemId(itemId)
    if (!isRead && canManage) {
      updateRead.mutate({ itemId, isRead: true })
    }
  }

  const saveCurrentView = () => {
    const name = savedViewName.trim()
    if (!name) return

    saveView.mutate({
      name,
      query: buildDashboardSavedViewState(
        {
          selected_feed_ids: selectedFeedIds,
          selected_tags: selectedTags,
          q,
          read_status: readStatus,
          star_status: starStatus,
          view_mode: viewMode,
          page_size: pageSize,
          time_range: timeRange,
          custom_since_date: customSinceDate,
          custom_until_date: customUntilDate,
          sort,
        },
        panelRect,
        showAdvancedFilters,
      ),
    })
  }

  const applySavedView = (view: SavedView) => {
    const parsed = parseDashboardSavedView(view.query_json, panelRect)
    setPage(1)
    setSelectedFeedIds(parsed.filters.selected_feed_ids)
    setSelectedTags(parsed.filters.selected_tags)
    setQ(parsed.filters.q)
    setReadStatus(parsed.filters.read_status)
    setStarStatus(parsed.filters.star_status)
    setViewMode(parsed.filters.view_mode)
    setPageSize(parsed.filters.page_size)
    setTimeRange(parsed.filters.time_range)
    setCustomSinceDate(parsed.filters.custom_since_date)
    setCustomUntilDate(parsed.filters.custom_until_date)
    setSort(parsed.filters.sort)
    setShowAdvancedFilters(parsed.ui.show_advanced_filters)
    const nextFeedWindow = parsed.layout.windows[FEEDS_WINDOW_ID]
    if (nextFeedWindow) {
      const { width, height } = getPanelContainerDimensions(rootRef.current)
      setPanelRect(normalizePanelRect(nextFeedWindow, width, height))
    }
    setActiveSavedViewId(view.id)
  }

  const startPanelDrag = (event: React.MouseEvent<HTMLDivElement>) => {
    if (!isWideLayout) return

    const rootBounds = rootRef.current?.getBoundingClientRect()
    if (!rootBounds) return

    event.preventDefault()

    const startMouseX = event.clientX
    const startMouseY = event.clientY
    const startRect = panelRect

    const onMove = (moveEvent: MouseEvent) => {
      const deltaX = moveEvent.clientX - startMouseX
      const deltaY = moveEvent.clientY - startMouseY

      const maxX = Math.max(0, rootBounds.width - startRect.width)
      const maxY = Math.max(0, rootBounds.height - startRect.height)
      setActiveSavedViewId(null)

      setPanelRect((current) => ({
        ...current,
        x: clamp(startRect.x + deltaX, 0, maxX),
        y: clamp(startRect.y + deltaY, 0, maxY),
      }))
    }

    const onUp = () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }

    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }

  const startPanelResize = (event: React.MouseEvent<HTMLButtonElement>) => {
    if (!isWideLayout) return

    const rootBounds = rootRef.current?.getBoundingClientRect()
    if (!rootBounds) return

    event.preventDefault()
    event.stopPropagation()

    const startMouseX = event.clientX
    const startMouseY = event.clientY
    const startRect = panelRect

    const onMove = (moveEvent: MouseEvent) => {
      const deltaX = moveEvent.clientX - startMouseX
      const deltaY = moveEvent.clientY - startMouseY

      const maxWidth = rootBounds.width - startRect.x
      const maxHeight = rootBounds.height - startRect.y
      setActiveSavedViewId(null)

      setPanelRect((current) => ({
        ...current,
        width: clamp(startRect.width + deltaX, PANEL_MIN_WIDTH, maxWidth),
        height: clamp(startRect.height + deltaY, PANEL_MIN_HEIGHT, maxHeight),
      }))
    }

    const onUp = () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }

    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }

  return (
    <div className="w-full">
      <div className="mb-3 rounded-xl border border-slate/20 bg-white/85 px-3 py-2 text-[13px] shadow-sm dark:border-cyan-900/40 dark:bg-[#041612]/92">
        <div className="grid gap-2 md:grid-cols-[180px_1fr_auto_auto] md:items-center">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate dark:text-slate-300">Dashboard Views</p>
          <input
            value={savedViewName}
            onChange={(event) => setSavedViewName(event.target.value)}
            placeholder="Save dashboard view as..."
            className="rounded border border-slate/25 bg-white px-2 py-1.5 text-sm dark:border-cyan-900/40 dark:bg-[#041612]"
          />
          <button
            type="button"
            className="rounded bg-ink px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-50 dark:bg-cyan dark:text-slate-950"
            onClick={saveCurrentView}
            disabled={saveView.isPending || !savedViewName.trim()}
          >
            Save View
          </button>
          <select
            className="rounded border border-slate/25 bg-white px-2 py-1.5 text-sm dark:border-cyan-900/40 dark:bg-[#041612]"
            value={activeSavedViewId ?? ''}
            onChange={(event) => {
              const value = event.target.value
              if (!value) {
                setActiveSavedViewId(null)
                return
              }
              const selected = viewsQuery.data?.find((view) => view.id === value)
              if (selected) {
                applySavedView(selected)
              }
            }}
          >
            <option value="">Load Dashboard View</option>
            {viewsQuery.data?.map((view) => (
              <option key={view.id} value={view.id}>
                {view.name}
              </option>
            ))}
          </select>
        </div>
        {activeSavedViewId && (
          <div className="mt-2 flex justify-end">
            <button
              type="button"
              className="rounded border border-slate/25 px-2 py-1 text-xs dark:border-cyan-900/40"
              onClick={() => deleteView.mutate(activeSavedViewId)}
              disabled={deleteView.isPending}
            >
              Delete Active View
            </button>
          </div>
        )}
      </div>

      <div ref={rootRef} className="relative min-h-[calc(100vh-170px)]">
      <section
        className="flex h-[calc(100vh-180px)] flex-col overflow-hidden rounded-xl border border-slate/20 bg-white/85 text-[13px] shadow-lg shadow-slate-400/15 dark:border-cyan-900/40 dark:bg-[#041612]/96 dark:shadow-cyan-950/40 lg:absolute lg:h-auto"
        style={
          isWideLayout
            ? {
                left: panelRect.x,
                top: panelRect.y,
                width: panelRect.width,
                height: panelRect.height,
              }
            : undefined
        }
      >
        <div
          className="flex items-center justify-between border-b border-slate/20 px-3 py-2 dark:border-cyan-900/40"
          onMouseDown={startPanelDrag}
        >
          <div>
            <h2 className="font-display text-lg leading-tight">Feeds</h2>
            <p className="text-xs text-slate dark:text-slate-300">Drag this window to reposition. Expand items inline to read full content.</p>
          </div>
          <span className="rounded border border-slate/25 px-2 py-0.5 text-[11px] text-slate dark:border-cyan-900/40 dark:text-slate-300">
            {itemsQuery.data?.total ?? 0} items
          </span>
        </div>

        <div className="border-b border-slate/20 px-3 py-2 dark:border-cyan-900/40">
          <div className="flex items-center gap-2 overflow-x-auto pb-1">
            <button
              type="button"
              className={`rounded-full border px-3 py-1 text-xs font-semibold ${
                selectedFeedIds.length === 0
                  ? 'border-cyan bg-cyan/15 text-cyan dark:bg-cyan-950/55'
                  : 'border-slate/25 dark:border-cyan-900/40'
              }`}
              onClick={() => {
                setPage(1)
                setActiveSavedViewId(null)
                setSelectedFeedIds([])
              }}
            >
              All Feeds
            </button>
            {feedsQuery.data?.map((feed) => {
              const active = selectedFeedIds.includes(feed.id)
              return (
                <button
                  key={feed.id}
                  type="button"
                  className={`whitespace-nowrap rounded-full border px-3 py-1 text-xs font-semibold ${
                    active
                      ? 'border-cyan bg-cyan/15 text-cyan dark:bg-cyan-950/55'
                      : 'border-slate/25 dark:border-cyan-900/40'
                  }`}
                  onClick={() => {
                    setPage(1)
                    setActiveSavedViewId(null)
                    setSelectedFeedIds((current) =>
                      current.includes(feed.id) ? current.filter((id) => id !== feed.id) : [...current, feed.id],
                    )
                  }}
                >
                  {feed.name}
                </button>
              )
            })}
          </div>
          <div className="mt-2 flex items-center gap-2 overflow-x-auto pb-1">
            <button
              type="button"
              className={`rounded-full border px-3 py-1 text-xs font-semibold ${
                selectedTags.length === 0
                  ? 'border-violet-500 bg-violet-500/15 text-violet-700 dark:bg-violet-950/45 dark:text-violet-300'
                  : 'border-slate/25 dark:border-cyan-900/40'
              }`}
              onClick={() => {
                setPage(1)
                setActiveSavedViewId(null)
                setSelectedTags([])
              }}
            >
              All Tags
            </button>
            {tagsQuery.data
              ?.filter((tag) => !HIDDEN_TAGS.has(tag.name))
              .map((tag) => {
                const active = selectedTags.includes(tag.name)
                return (
                  <button
                    key={tag.id}
                    type="button"
                    className={`whitespace-nowrap rounded-full border px-3 py-1 text-xs font-semibold ${
                      active
                        ? 'border-violet-500 bg-violet-500/15 text-violet-700 dark:bg-violet-950/45 dark:text-violet-300'
                        : 'border-slate/25 dark:border-cyan-900/40'
                    }`}
                    onClick={() => {
                      setPage(1)
                      setActiveSavedViewId(null)
                      setSelectedTags((current) =>
                        current.includes(tag.name) ? current.filter((entry) => entry !== tag.name) : [...current, tag.name],
                      )
                    }}
                  >
                    #{tag.name}
                  </button>
                )
              })}
          </div>
          {tagsQuery.isError && <p className="mt-1 text-xs text-red-600">Failed to load tags.</p>}

          <div className="mt-2 flex flex-wrap items-center gap-2">
            <input
              value={q}
              onChange={(event) => {
                setPage(1)
                setActiveSavedViewId(null)
                setQ(event.target.value)
              }}
              placeholder="Search title, summary, URL"
              className="min-w-64 flex-1 rounded border border-slate/25 bg-white px-2 py-1.5 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
            />
            <select
              className="rounded border border-slate/25 bg-white px-2 py-1.5 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
              value={timeRange}
              onChange={(event) => {
                setPage(1)
                setActiveSavedViewId(null)
                setTimeRange(event.target.value as TimeRangeFilter)
              }}
            >
              <option value="all">All time</option>
              <option value="24h">24h</option>
              <option value="7d">7d</option>
              <option value="30d">30d</option>
              <option value="custom">Custom</option>
            </select>
            <select
              className="rounded border border-slate/25 bg-white px-2 py-1.5 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
              value={sort}
              onChange={(event) => {
                setPage(1)
                setActiveSavedViewId(null)
                setSort(event.target.value as TimeSort)
              }}
            >
              <option value="published_at_desc">Newest</option>
              <option value="published_at_asc">Oldest</option>
              <option value="first_seen_desc">Seen newest</option>
              <option value="first_seen_asc">Seen oldest</option>
            </select>
            <div className="flex rounded border border-slate/25 p-0.5 dark:border-cyan-900/40">
              <button
                type="button"
                className={`rounded px-2 py-1 text-xs font-semibold ${viewMode === 'expanded' ? 'bg-cyan/15 text-cyan' : ''}`}
                onClick={() => {
                  setPage(1)
                  setActiveSavedViewId(null)
                  setViewMode('expanded')
                }}
              >
                Expanded
              </button>
              <button
                type="button"
                className={`rounded px-2 py-1 text-xs font-semibold ${viewMode === 'compact' ? 'bg-cyan/15 text-cyan' : ''}`}
                onClick={() => {
                  setPage(1)
                  setActiveSavedViewId(null)
                  setViewMode('compact')
                }}
              >
                Compact
              </button>
            </div>
            <button
              type="button"
              className="rounded border border-slate/25 px-3 py-1.5 text-xs font-semibold dark:border-cyan-900/40"
              onClick={() => {
                setActiveSavedViewId(null)
                setShowAdvancedFilters((current) => !current)
              }}
            >
              {showAdvancedFilters ? 'Hide Filters' : 'More Filters'}
            </button>
          </div>

          {showAdvancedFilters && (
            <div className="mt-2 grid gap-2 rounded border border-slate/20 bg-sand/40 p-2 dark:border-cyan-900/40 dark:bg-[#072019]/70 md:grid-cols-2 lg:grid-cols-3">
              <select
                className="rounded border border-slate/25 bg-white px-2 py-1.5 text-sm dark:border-cyan-900/40 dark:bg-[#041612]"
                value={readStatus}
                onChange={(event) => {
                  setPage(1)
                  setActiveSavedViewId(null)
                  setReadStatus(event.target.value as ReadStatusFilter)
                }}
              >
                <option value="all">Read: All</option>
                <option value="unread">Read: Unread</option>
                <option value="read">Read: Read</option>
              </select>
              <select
                className="rounded border border-slate/25 bg-white px-2 py-1.5 text-sm dark:border-cyan-900/40 dark:bg-[#041612]"
                value={starStatus}
                onChange={(event) => {
                  setPage(1)
                  setActiveSavedViewId(null)
                  setStarStatus(event.target.value as StarStatusFilter)
                }}
              >
                <option value="all">Stars: All</option>
                <option value="starred">Stars: Starred</option>
                <option value="unstarred">Stars: Unstarred</option>
              </select>
              <div className="flex gap-2">
                <input
                  type="date"
                  className="w-full rounded border border-slate/25 bg-white px-2 py-1.5 text-sm disabled:opacity-50 dark:border-cyan-900/40 dark:bg-[#041612]"
                  value={customSinceDate}
                  onChange={(event) => {
                    setPage(1)
                    setActiveSavedViewId(null)
                    setCustomSinceDate(event.target.value)
                  }}
                  disabled={timeRange !== 'custom'}
                />
                <input
                  type="date"
                  className="w-full rounded border border-slate/25 bg-white px-2 py-1.5 text-sm disabled:opacity-50 dark:border-cyan-900/40 dark:bg-[#041612]"
                  value={customUntilDate}
                  onChange={(event) => {
                    setPage(1)
                    setActiveSavedViewId(null)
                    setCustomUntilDate(event.target.value)
                  }}
                  disabled={timeRange !== 'custom'}
                />
              </div>
            </div>
          )}
        </div>

        <div className="flex-1 overflow-auto p-3">
          <div className="space-y-2">
            {itemsQuery.data?.items.map((item) => {
              const expanded = expandedItemId === item.id
              const detail = expanded ? detailQuery.data : null
              const compact = viewMode === 'compact'
              const density = computeInformationDensity(item)

              return (
                <article
                  key={item.id}
                  className={`rounded border ${compact ? 'p-2' : 'p-3'} transition ${
                    expanded
                      ? 'border-cyan bg-cyan/5 dark:border-cyan-700/50 dark:bg-cyan-950/25'
                      : 'border-slate/20 dark:border-cyan-900/40'
                  } ${item.is_read ? 'opacity-85' : ''}`}
                >
                  <div className="w-full text-left">
                    <div className="flex items-start justify-between gap-3">
                      <h3 className={`${compact ? 'text-[14px]' : 'text-[15px]'} font-semibold leading-snug`}>
                        <a
                          href={item.canonical_url || item.url}
                          target="_blank"
                          rel="noreferrer"
                          className="hover:text-cyan hover:underline"
                          onClick={(event) => event.stopPropagation()}
                        >
                          {item.title}
                        </a>
                      </h3>
                      <div className="flex shrink-0 items-center gap-2">
                        <span className="rounded bg-teal-100 px-1.5 py-0.5 text-[11px] text-teal-800 dark:bg-teal-900/35 dark:text-teal-200">
                          {density.label} {density.score}
                        </span>
                        <span className="text-xs text-slate dark:text-slate-300">{item.feed_name}</span>
                      </div>
                    </div>
                    <button type="button" className="mt-1 w-full text-left" onClick={() => handleToggleItem(item.id, item.is_read)}>
                      <div className="flex flex-wrap items-center gap-2 text-[11px] text-slate dark:text-slate-300">
                        <span>Published {formatPublishedAt(item.published_at)}</span>
                        {item.status !== 'content_fetched' && (
                          <span className="rounded bg-slate/15 px-1.5 py-0.5 dark:bg-[#0b1a33]">{item.status}</span>
                        )}
                        {!item.is_read && <span className="rounded bg-cyan/20 px-1.5 py-0.5 text-cyan">Unread</span>}
                        {item.is_starred && <span className="rounded bg-amber-100 px-1.5 py-0.5 text-amber-700">Starred</span>}
                        {item.tags
                          .filter((tagName) => !HIDDEN_TAGS.has(tagName))
                          .slice(0, 3)
                          .map((tagName) => (
                          <span key={`${item.id}-${tagName}`} className="rounded bg-violet-100 px-1.5 py-0.5 text-violet-800 dark:bg-violet-900/35 dark:text-violet-200">
                            #{tagName}
                          </span>
                        ))}
                      </div>
                      {!compact && <p className="mt-2 line-clamp-2 text-[13px] leading-5 text-slate dark:text-slate-300">{item.summary || 'No summary available.'}</p>}
                    </button>
                  </div>

                  {expanded && (
                    <div className="mt-3 border-t border-slate/20 pt-3 dark:border-cyan-900/40">
                      {detailQuery.isLoading && <p className="text-sm text-slate dark:text-slate-300">Loading article content...</p>}
                      {detailQuery.isError && <p className="text-sm text-red-600">Failed to load item details.</p>}

                      {detail && detail.id === item.id && (
                        <>
                          <div className="flex flex-wrap items-center gap-2">
                            <a
                              className="rounded border border-slate/30 px-2 py-1 text-xs hover:border-cyan hover:text-cyan dark:border-cyan-900/40"
                              href={detail.article?.final_url || detail.url}
                              target="_blank"
                              rel="noreferrer"
                            >
                              Open Source Link
                            </a>
                            <button
                              className="rounded border border-slate/30 px-2 py-1 text-xs dark:border-cyan-900/40"
                              disabled={!canManage}
                              onClick={() =>
                                updateRead.mutate({
                                  itemId: detail.id,
                                  isRead: !detail.state.is_read,
                                })
                              }
                            >
                              {detail.state.is_read ? 'Mark Unread' : 'Mark Read'}
                            </button>
                            <button
                              className="rounded border border-slate/30 px-2 py-1 text-xs dark:border-cyan-900/40"
                              disabled={!canManage}
                              onClick={() =>
                                updateStar.mutate({
                                  itemId: detail.id,
                                  isStarred: !detail.state.is_starred,
                                })
                              }
                            >
                              {detail.state.is_starred ? 'Unstar' : 'Star'}
                            </button>
                            {!canManage && <span className="text-xs text-amber-600">Viewer role is read-only.</span>}
                          </div>

                          <div className="mt-3 rounded border border-slate/20 bg-sand/50 p-3 dark:border-cyan-900/40 dark:bg-[#072019]/90">
                            <p className="text-xs font-bold uppercase tracking-wide text-slate dark:text-slate-300">RSS Summary</p>
                            {detail.classification && (
                              <p className="mt-1 text-xs text-slate dark:text-slate-300">
                                Classification: <span className="font-semibold">{formatClassificationLabel(detail.classification.primary_category)}</span>{' '}
                                ({Math.round(detail.classification.confidence * 100)}% confidence)
                              </p>
                            )}
                            <div className="rss-reader mt-2 rounded bg-white/70 p-3 dark:bg-[#041612]/80">
                              {renderRichContent(detail.summary || 'No summary.', detail.id, 'summary')}
                            </div>
                          </div>

                          <div className="mt-3 rounded border border-slate/20 bg-white p-3 dark:border-cyan-900/40 dark:bg-[#072019]/90">
                            <p className="text-xs font-bold uppercase tracking-wide text-slate dark:text-slate-300">Full Article</p>
                            {detail.article?.text ? (
                              <div className="rss-reader mt-2 rounded bg-white/70 p-3 dark:bg-[#041612]/80">
                                {renderRichContent(detail.article.text, detail.id, 'article')}
                              </div>
                            ) : (
                              <p className="mt-2 text-sm text-slate dark:text-slate-300">No extracted article text available yet.</p>
                            )}
                            {detail.article?.error && <p className="mt-2 text-sm text-red-600">Extraction error: {detail.article.error}</p>}
                          </div>

                          <div className="mt-3 rounded border border-slate/20 bg-white p-3 dark:border-cyan-900/40 dark:bg-[#072019]/90">
                            <label className="text-xs font-semibold uppercase tracking-wide text-slate dark:text-slate-300">Notes</label>
                            <textarea
                              className="mt-1 h-20 w-full rounded border border-slate/30 bg-white px-2 py-1.5 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                              value={noteDraft}
                              onChange={(event) => setNoteDraft(event.target.value)}
                              disabled={!canManage}
                            />
                            <div className="mt-2 flex items-center gap-2">
                              <button
                                className="rounded bg-ink px-2.5 py-1 text-xs font-semibold text-white disabled:opacity-50 dark:bg-cyan dark:text-slate-950"
                                onClick={() => updateNote.mutate({ itemId: detail.id, note: noteDraft || null })}
                                disabled={!canManage}
                              >
                                Save Notes
                              </button>
                              {!canManage && <span className="text-xs text-slate dark:text-slate-300">Read-only for viewer role.</span>}
                            </div>
                          </div>
                        </>
                      )}
                    </div>
                  )}
                </article>
              )
            })}

            {itemsQuery.isLoading && <p className="text-sm text-slate dark:text-slate-300">Loading items...</p>}
            {itemsQuery.isError && (
              <p className="text-sm text-red-600">
                Failed to load items. {(itemsQuery.error as Error | undefined)?.message ?? ''}
              </p>
            )}
            {!itemsQuery.isLoading && !itemsQuery.data?.items.length && <p className="text-sm text-slate dark:text-slate-300">No items match current filters.</p>}
          </div>
        </div>

        <div className="flex items-center justify-between border-t border-slate/20 px-3 py-2 text-xs dark:border-cyan-900/40">
          <button
            className="rounded border border-slate/30 px-2 py-1 disabled:opacity-50 dark:border-cyan-900/40"
            disabled={page <= 1}
            onClick={() => setPage((current) => current - 1)}
          >
            Prev
          </button>
          <span>
            Page {page} / {totalPages}
          </span>
          <div className="flex items-center gap-2">
            <label className="text-xs text-slate dark:text-slate-300">Per page</label>
            <select
              className="rounded border border-slate/30 bg-white px-2 py-1 text-xs dark:border-cyan-900/40 dark:bg-[#072019]"
              value={pageSize}
              onChange={(event) => {
                setActiveSavedViewId(null)
                setPage(1)
                setPageSize(Number(event.target.value))
              }}
            >
              {PAGE_SIZE_OPTIONS.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
            <button
              className="rounded border border-slate/30 px-2 py-1 disabled:opacity-50 dark:border-cyan-900/40"
              disabled={page >= totalPages}
              onClick={() => setPage((current) => current + 1)}
            >
              Next
            </button>
          </div>
        </div>

        {isWideLayout && (
          <button
            type="button"
            className="absolute bottom-1 right-1 h-4 w-4 cursor-se-resize rounded border border-slate/30 bg-white/85 dark:border-cyan-900/40 dark:bg-[#0b2a23]"
            aria-label="Resize dashboard window"
            onMouseDown={startPanelResize}
          />
        )}
      </section>
      </div>
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
  queryClient.invalidateQueries({ queryKey: ['items'] })
  queryClient.invalidateQueries({ queryKey: ['item', itemId] })
}

function formatPublishedAt(value: string | null) {
  if (!value) return 'Unknown'
  const dt = new Date(value)
  if (Number.isNaN(dt.getTime())) return value
  return dt.toLocaleString()
}

function formatClassificationLabel(value: string): string {
  return value
    .split('_')
    .map((part) => (part ? part[0].toUpperCase() + part.slice(1) : part))
    .join(' ')
}

function computeInformationDensity(item: ItemListEntry): { score: number; label: string } {
  const titleWords = item.title.trim().split(/\s+/).filter(Boolean).length
  const summaryLength = (item.summary || '').trim().length
  const visibleTagCount = item.tags.filter((tagName) => !HIDDEN_TAGS.has(tagName)).length

  let score = 0
  score += Math.min(24, titleWords * 2)
  score += Math.min(40, Math.floor(summaryLength / 8))
  score += Math.min(18, visibleTagCount * 6)
  if (item.classification) score += 10
  if (item.status === 'error') score += 6
  if (item.published_at) score += 4

  const normalized = Math.max(1, Math.min(100, score))
  if (normalized >= 70) return { score: normalized, label: 'High' }
  if (normalized >= 40) return { score: normalized, label: 'Medium' }
  return { score: normalized, label: 'Low' }
}

function parseDashboardSavedView(raw: Record<string, unknown>, fallbackPanelRect: PanelRect): DashboardSavedViewState {
  const rawFilters = isRecord(raw.filters) ? raw.filters : raw
  const rawUi = isRecord(raw.ui) ? raw.ui : {}

  const selectedFeedIds = Array.isArray(rawFilters.selected_feed_ids)
    ? rawFilters.selected_feed_ids.filter((entry): entry is string => typeof entry === 'string')
    : []
  const selectedTags = Array.isArray(rawFilters.selected_tags)
    ? rawFilters.selected_tags.filter((entry): entry is string => typeof entry === 'string' && !HIDDEN_TAGS.has(entry))
    : []

  const readStatus = rawFilters.read_status === 'read' || rawFilters.read_status === 'unread' ? rawFilters.read_status : 'all'
  const starStatus =
    rawFilters.star_status === 'starred' || rawFilters.star_status === 'unstarred' ? rawFilters.star_status : 'all'
  const viewMode = rawFilters.view_mode === 'compact' ? 'compact' : 'expanded'
  const pageSize =
    typeof rawFilters.page_size === 'number' && PAGE_SIZE_OPTIONS.includes(rawFilters.page_size)
      ? rawFilters.page_size
      : 25
  const timeRange =
    rawFilters.time_range === '24h' ||
    rawFilters.time_range === '7d' ||
    rawFilters.time_range === '30d' ||
    rawFilters.time_range === 'custom'
      ? rawFilters.time_range
      : 'all'

  const sortValues: TimeSort[] = ['published_at_desc', 'published_at_asc', 'first_seen_desc', 'first_seen_asc']
  const sort =
    typeof rawFilters.sort === 'string' && sortValues.includes(rawFilters.sort as TimeSort)
      ? (rawFilters.sort as TimeSort)
      : 'published_at_desc'

  const parsedLayoutWindows: Record<string, PanelRect> = {}
  const rawLayout = isRecord(raw.layout) ? raw.layout : {}
  const rawWindows = isRecord(rawLayout.windows) ? rawLayout.windows : {}

  for (const [windowId, value] of Object.entries(rawWindows)) {
    const parsedRect = parsePanelRectCandidate(value)
    if (parsedRect) {
      parsedLayoutWindows[windowId] = parsedRect
    }
  }

  const legacyRect = parsePanelRectCandidate(raw.panel_rect)
  if (legacyRect && !parsedLayoutWindows[FEEDS_WINDOW_ID]) {
    parsedLayoutWindows[FEEDS_WINDOW_ID] = legacyRect
  }

  if (!parsedLayoutWindows[FEEDS_WINDOW_ID]) {
    parsedLayoutWindows[FEEDS_WINDOW_ID] = { ...fallbackPanelRect }
  }

  return {
    version: DASHBOARD_VIEW_VERSION,
    filters: {
      selected_feed_ids: selectedFeedIds,
      selected_tags: selectedTags,
      q: typeof rawFilters.q === 'string' ? rawFilters.q : '',
      read_status: readStatus,
      star_status: starStatus,
      view_mode: viewMode,
      page_size: pageSize,
      time_range: timeRange,
      custom_since_date: typeof rawFilters.custom_since_date === 'string' ? rawFilters.custom_since_date : '',
      custom_until_date: typeof rawFilters.custom_until_date === 'string' ? rawFilters.custom_until_date : '',
      sort,
    },
    layout: {
      windows: parsedLayoutWindows,
    },
    ui: {
      show_advanced_filters: typeof rawUi.show_advanced_filters === 'boolean' ? rawUi.show_advanced_filters : false,
    },
  }
}

function buildDashboardSavedViewState(filters: DashboardSavedViewQuery, panelRect: PanelRect, showAdvancedFilters: boolean): DashboardSavedViewState {
  return {
    version: DASHBOARD_VIEW_VERSION,
    filters: {
      ...filters,
      selected_feed_ids: [...filters.selected_feed_ids],
      selected_tags: [...filters.selected_tags],
    },
    layout: {
      windows: {
        [FEEDS_WINDOW_ID]: {
          ...panelRect,
        },
      },
    },
    ui: {
      show_advanced_filters: showAdvancedFilters,
    },
  }
}

type ArticleBlock =
  | { kind: 'heading'; text: string }
  | { kind: 'paragraph'; text: string }
  | { kind: 'bullet-list'; items: string[] }
  | { kind: 'numbered-list'; items: string[] }
  | { kind: 'quote'; text: string }

function renderRichContent(content: string, itemId: string, section: 'summary' | 'article'): ReactNode {
  const trimmed = content.trim()
  if (!trimmed) {
    return <p>No content.</p>
  }

  if (!looksLikeHtml(trimmed)) {
    return renderArticleBlocks(trimmed, `${itemId}-${section}`)
  }

  const sanitized = sanitizeHtmlFragment(trimmed)
  if (!sanitized) {
    return renderArticleBlocks(stripHtml(trimmed), `${itemId}-${section}`)
  }

  return <div className="rss-rich" dangerouslySetInnerHTML={{ __html: sanitized }} />
}

function renderArticleBlocks(text: string, itemId: string) {
  const blocks = parseArticleBlocks(text)

  return blocks.map((block, index) => {
    if (block.kind === 'heading') {
      return (
        <h4 key={`${itemId}-heading-${index}`} className="rss-heading">
          {block.text}
        </h4>
      )
    }

    if (block.kind === 'bullet-list') {
      return (
        <ul key={`${itemId}-ul-${index}`} className="rss-list">
          {block.items.map((entry, entryIndex) => (
            <li key={`${itemId}-ul-${index}-${entryIndex}`}>{entry}</li>
          ))}
        </ul>
      )
    }

    if (block.kind === 'numbered-list') {
      return (
        <ol key={`${itemId}-ol-${index}`} className="rss-list rss-list-ordered">
          {block.items.map((entry, entryIndex) => (
            <li key={`${itemId}-ol-${index}-${entryIndex}`}>{entry}</li>
          ))}
        </ol>
      )
    }

    if (block.kind === 'quote') {
      return (
        <blockquote key={`${itemId}-quote-${index}`} className="rss-quote">
          {block.text}
        </blockquote>
      )
    }

    return <p key={`${itemId}-paragraph-${index}`}>{block.text}</p>
  })
}

function parseArticleBlocks(text: string): ArticleBlock[] {
  const lines = text.replace(/\r/g, '').split('\n')
  const blocks: ArticleBlock[] = []
  const nonEmptyCount = lines.filter((line) => line.trim()).length
  const blankCount = lines.length - nonEmptyCount
  const useLineOrientedMode = nonEmptyCount >= 10 && blankCount <= Math.ceil(nonEmptyCount * 0.12)

  let index = 0
  while (index < lines.length) {
    const raw = lines[index]
    const line = raw.trim()

    if (!line) {
      index += 1
      continue
    }

    if (isHeadingLine(line)) {
      blocks.push({ kind: 'heading', text: cleanHeading(line) })
      index += 1
      continue
    }

    if (looksLikeSectionHeading(line)) {
      blocks.push({ kind: 'heading', text: line })
      index += 1
      continue
    }

    if (isBulletLine(line)) {
      const items: string[] = []
      while (index < lines.length && isBulletLine(lines[index].trim())) {
        items.push(cleanBullet(lines[index].trim()))
        index += 1
      }
      if (items.length) {
        blocks.push({ kind: 'bullet-list', items })
      }
      continue
    }

    if (line.includes(' • ')) {
      const items: string[] = []
      while (index < lines.length) {
        const bulletLine = lines[index].trim()
        if (!bulletLine || !bulletLine.includes(' • ')) {
          break
        }
        items.push(bulletLine)
        index += 1
      }
      if (items.length) {
        blocks.push({ kind: 'bullet-list', items })
      }
      continue
    }

    if (isNumberedLine(line)) {
      const items: string[] = []
      while (index < lines.length && isNumberedLine(lines[index].trim())) {
        items.push(cleanNumbered(lines[index].trim()))
        index += 1
      }
      if (items.length) {
        blocks.push({ kind: 'numbered-list', items })
      }
      continue
    }

    if (line.startsWith('>')) {
      const quoteLines: string[] = []
      while (index < lines.length && lines[index].trim().startsWith('>')) {
        quoteLines.push(lines[index].trim().replace(/^>\s*/, ''))
        index += 1
      }
      const quoteText = quoteLines.join(' ').replace(/\s{2,}/g, ' ').trim()
      if (quoteText) {
        blocks.push({ kind: 'quote', text: quoteText })
      }
      continue
    }

    if (useLineOrientedMode) {
      blocks.push({ kind: 'paragraph', text: line })
      index += 1
      continue
    }

    const paragraphLines: string[] = []
    while (index < lines.length) {
      const paragraphLine = lines[index].trim()
      if (!paragraphLine || isHeadingLine(paragraphLine) || isBulletLine(paragraphLine) || isNumberedLine(paragraphLine) || paragraphLine.startsWith('>')) {
        break
      }
      paragraphLines.push(paragraphLine)
      index += 1
    }

    if (paragraphLines.length) {
      blocks.push({
        kind: 'paragraph',
        text: paragraphLines.join(' ').replace(/\s{2,}/g, ' ').trim(),
      })
      continue
    }

    index += 1
  }

  return blocks
}

function isHeadingLine(line: string): boolean {
  if (/^#{1,4}\s+/.test(line)) {
    return true
  }

  return /^[A-Z][A-Z0-9\s\-:]{8,}$/.test(line) && line === line.toUpperCase()
}

function cleanHeading(line: string): string {
  return line.replace(/^#{1,4}\s*/, '').trim()
}

function isBulletLine(line: string): boolean {
  return /^[-*•]\s+/.test(line)
}

function cleanBullet(line: string): string {
  return line.replace(/^[-*•]\s+/, '').trim()
}

function isNumberedLine(line: string): boolean {
  return /^\d+[\.\)]\s+/.test(line)
}

function cleanNumbered(line: string): string {
  return line.replace(/^\d+[\.\)]\s+/, '').trim()
}

function looksLikeSectionHeading(line: string): boolean {
  if (line.length < 3 || line.length > 72) return false
  if (/^https?:\/\//i.test(line)) return false
  if (line.includes(' • ')) return false
  if (/[.!?]$/.test(line)) return false

  const withoutEmoji = line
    .replace(/[\p{Emoji_Presentation}\p{Extended_Pictographic}]/gu, '')
    .replace(/\uFE0F/g, '')
    .trim()

  if (!withoutEmoji) return false
  const words = withoutEmoji.split(/\s+/)
  if (words.length > 10) return false
  if (words.every((word) => word.length <= 2)) return false

  return true
}

const ALLOWED_HTML_TAGS = new Set([
  'a',
  'b',
  'blockquote',
  'br',
  'code',
  'em',
  'h1',
  'h2',
  'h3',
  'h4',
  'h5',
  'h6',
  'hr',
  'i',
  'li',
  'ol',
  'p',
  'pre',
  'strong',
  'u',
  'ul',
])

function looksLikeHtml(value: string): boolean {
  return /<([a-z][a-z0-9]*)\b[^>]*>/i.test(value)
}

function sanitizeHtmlFragment(html: string): string {
  if (typeof window === 'undefined') {
    return ''
  }

  const parser = new DOMParser()
  const document = parser.parseFromString(html, 'text/html')
  const sanitized = Array.from(document.body.childNodes)
    .map((node) => sanitizeNode(node))
    .join('')
    .trim()

  return sanitized
}

function sanitizeNode(node: Node): string {
  if (node.nodeType === Node.TEXT_NODE) {
    return escapeHtml(node.textContent ?? '')
  }

  if (node.nodeType !== Node.ELEMENT_NODE) {
    return ''
  }

  const element = node as HTMLElement
  const tag = element.tagName.toLowerCase()
  const children = Array.from(element.childNodes)
    .map((child) => sanitizeNode(child))
    .join('')

  if (!ALLOWED_HTML_TAGS.has(tag)) {
    return children
  }

  if (tag === 'br' || tag === 'hr') {
    return `<${tag}>`
  }

  if (tag === 'a') {
    const href = sanitizeHref(element.getAttribute('href'))
    if (!href) {
      return children
    }
    return `<a href="${escapeAttribute(href)}" target="_blank" rel="noopener noreferrer">${children}</a>`
  }

  return `<${tag}>${children}</${tag}>`
}

function sanitizeHref(rawHref: string | null): string | null {
  if (!rawHref) return null
  const href = rawHref.trim()
  if (/^https?:\/\//i.test(href)) return href
  if (/^mailto:/i.test(href)) return href
  return null
}

function stripHtml(value: string): string {
  if (typeof window === 'undefined') return value
  const parser = new DOMParser()
  const document = parser.parseFromString(value, 'text/html')
  return document.body.textContent?.trim() ?? ''
}

function escapeHtml(value: string): string {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;')
}

function escapeAttribute(value: string): string {
  return escapeHtml(value)
}

function loadPanelRect(): PanelRect {
  if (typeof window === 'undefined') {
    return createDefaultPanel(1380, 760)
  }

  const { width: containerWidth, height: containerHeight } = getPanelContainerDimensions(null)
  const defaultPanel = createDefaultPanel(containerWidth, containerHeight)
  const raw = window.localStorage.getItem(PANEL_STORAGE_KEY)
  if (!raw) {
    return defaultPanel
  }

  try {
    const parsed = JSON.parse(raw) as Partial<PanelRect>
    if (
      typeof parsed.x !== 'number' ||
      typeof parsed.y !== 'number' ||
      typeof parsed.width !== 'number' ||
      typeof parsed.height !== 'number'
    ) {
      return defaultPanel
    }

    if (parsed.width === LEGACY_PANEL_WIDTH && parsed.x <= PANEL_OUTER_GAP) {
      return defaultPanel
    }

    return normalizePanelRect(
      {
        x: parsed.x,
        y: parsed.y,
        width: parsed.width,
        height: parsed.height,
      },
      containerWidth,
      containerHeight,
    )
  } catch {
    return defaultPanel
  }
}

function parsePanelRectCandidate(value: unknown): PanelRect | null {
  if (!isRecord(value)) return null
  if (
    typeof value.x !== 'number' ||
    typeof value.y !== 'number' ||
    typeof value.width !== 'number' ||
    typeof value.height !== 'number'
  ) {
    return null
  }

  if (!Number.isFinite(value.x) || !Number.isFinite(value.y) || !Number.isFinite(value.width) || !Number.isFinite(value.height)) {
    return null
  }

  return {
    x: value.x,
    y: value.y,
    width: value.width,
    height: value.height,
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function getPanelContainerDimensions(rootElement: HTMLDivElement | null): { width: number; height: number } {
  if (typeof window === 'undefined') {
    return { width: 1380, height: 760 }
  }

  const rootBounds = rootElement?.getBoundingClientRect()
  const width = Math.max(PANEL_MIN_WIDTH, Math.floor(rootBounds?.width ?? window.innerWidth - PANEL_OUTER_GAP * 2))
  const height = Math.max(PANEL_MIN_HEIGHT, Math.floor(rootBounds?.height ?? window.innerHeight - 170))
  return { width, height }
}

function clamp(value: number, min: number, max: number) {
  if (value < min) return min
  if (value > max) return max
  return value
}

function createDefaultPanel(containerWidth: number, containerHeight: number): PanelRect {
  const maxWidth = Math.max(PANEL_MIN_WIDTH, containerWidth)
  const maxHeight = Math.max(PANEL_MIN_HEIGHT, containerHeight)

  return {
    x: 0,
    y: 8,
    width: maxWidth,
    height: clamp(760, PANEL_MIN_HEIGHT, maxHeight),
  }
}

function normalizePanelRect(panel: PanelRect, containerWidth: number, containerHeight: number): PanelRect {
  const maxWidth = Math.max(PANEL_MIN_WIDTH, containerWidth)
  const maxHeight = Math.max(PANEL_MIN_HEIGHT, containerHeight)

  const width = clamp(panel.width, PANEL_MIN_WIDTH, maxWidth)
  const height = clamp(panel.height, PANEL_MIN_HEIGHT, maxHeight)

  const maxX = Math.max(0, maxWidth - width)
  const maxY = Math.max(0, maxHeight - height)

  return {
    x: clamp(panel.x, 0, maxX),
    y: clamp(panel.y, 0, maxY),
    width,
    height,
  }
}
