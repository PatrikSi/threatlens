import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
import { useDebouncedValue } from '../hooks/useDebouncedValue'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { Feed, ItemDetail, ItemListResponse, SavedView } from '../types/api'

type TimeRangeFilter = 'all' | '24h' | '7d' | '30d' | 'custom'
type ReadStatusFilter = 'all' | 'read' | 'unread'
type StarStatusFilter = 'all' | 'starred' | 'unstarred'
type TimeSort = 'published_at_desc' | 'published_at_asc' | 'first_seen_desc' | 'first_seen_asc'

type PanelRect = {
  x: number
  y: number
  width: number
  height: number
}

interface DashboardSavedViewQuery {
  selected_feed_ids: string[]
  q: string
  read_status: ReadStatusFilter
  star_status: StarStatusFilter
  time_range: TimeRangeFilter
  custom_since_date: string
  custom_until_date: string
  sort: TimeSort
}

const PANEL_STORAGE_KEY = 'threatlens.dashboard.panel.v1'
const DEFAULT_PANEL: PanelRect = { x: 12, y: 12, width: 1180, height: 760 }
const PANEL_MIN_WIDTH = 860
const PANEL_MIN_HEIGHT = 520

export function DashboardPage() {
  const queryClient = useQueryClient()
  const meQuery = useCurrentUser()
  const rootRef = useRef<HTMLDivElement | null>(null)

  const [selectedFeedIds, setSelectedFeedIds] = useState<string[]>([])
  const [q, setQ] = useState('')
  const [readStatus, setReadStatus] = useState<ReadStatusFilter>('all')
  const [starStatus, setStarStatus] = useState<StarStatusFilter>('all')
  const [timeRange, setTimeRange] = useState<TimeRangeFilter>('all')
  const [customSinceDate, setCustomSinceDate] = useState('')
  const [customUntilDate, setCustomUntilDate] = useState('')
  const [sort, setSort] = useState<TimeSort>('published_at_desc')
  const [savedViewName, setSavedViewName] = useState('')
  const [activeSavedViewId, setActiveSavedViewId] = useState<string | null>(null)
  const [showAdvancedFilters, setShowAdvancedFilters] = useState(false)

  const [page, setPage] = useState(1)
  const pageSize = 25
  const [expandedItemId, setExpandedItemId] = useState<string>('')
  const [noteDraft, setNoteDraft] = useState('')

  const [panelRect, setPanelRect] = useState<PanelRect>(() => loadPanelRect())
  const [isWideLayout, setIsWideLayout] = useState<boolean>(typeof window !== 'undefined' ? window.innerWidth >= 1024 : true)

  const canManage = meQuery.data?.role === 'admin' || meQuery.data?.role === 'analyst'

  useEffect(() => {
    const onResize = () => {
      setIsWideLayout(window.innerWidth >= 1024)
    }
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  useEffect(() => {
    if (typeof window === 'undefined') {
      return
    }
    window.localStorage.setItem(PANEL_STORAGE_KEY, JSON.stringify(panelRect))
  }, [panelRect])

  const debouncedQ = useDebouncedValue(q)
  const feedIdsParam = useMemo(() => selectedFeedIds.slice().sort().join(','), [selectedFeedIds])

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

  const saveView = useMutation({
    mutationFn: (payload: { name: string; query: DashboardSavedViewQuery }) =>
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
    queryKey: ['items', feedIdsParam, debouncedQ, readStatus, starStatus, sinceIso, untilIso, sort, page],
    queryFn: () => {
      const params = new URLSearchParams()
      params.set('page', String(page))
      params.set('page_size', String(pageSize))
      params.set('sort', sort)

      if (feedIdsParam) params.set('feed_ids', feedIdsParam)
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
  }, [itemsQuery.data?.total])

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
      query: {
        selected_feed_ids: selectedFeedIds,
        q,
        read_status: readStatus,
        star_status: starStatus,
        time_range: timeRange,
        custom_since_date: customSinceDate,
        custom_until_date: customUntilDate,
        sort,
      },
    })
  }

  const applySavedView = (view: SavedView) => {
    const parsed = parseDashboardSavedView(view.query_json)
    setPage(1)
    setSelectedFeedIds(parsed.selected_feed_ids)
    setQ(parsed.q)
    setReadStatus(parsed.read_status)
    setStarStatus(parsed.star_status)
    setTimeRange(parsed.time_range)
    setCustomSinceDate(parsed.custom_since_date)
    setCustomUntilDate(parsed.custom_until_date)
    setSort(parsed.sort)
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
    <div ref={rootRef} className="relative min-h-[calc(100vh-110px)]">
      <section
        className="flex h-[calc(100vh-120px)] flex-col overflow-hidden rounded-xl border border-slate/20 bg-white/85 text-[13px] shadow-lg shadow-slate-400/15 dark:border-cyan-900/40 dark:bg-[#040913]/96 dark:shadow-cyan-950/40 lg:absolute lg:h-auto"
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

          <div className="mt-2 flex flex-wrap items-center gap-2">
            <input
              value={q}
              onChange={(event) => {
                setPage(1)
                setActiveSavedViewId(null)
                setQ(event.target.value)
              }}
              placeholder="Search title, summary, URL"
              className="min-w-64 flex-1 rounded border border-slate/25 bg-white px-2 py-1.5 text-sm dark:border-cyan-900/40 dark:bg-[#060d19]"
            />
            <select
              className="rounded border border-slate/25 bg-white px-2 py-1.5 text-sm dark:border-cyan-900/40 dark:bg-[#060d19]"
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
              className="rounded border border-slate/25 bg-white px-2 py-1.5 text-sm dark:border-cyan-900/40 dark:bg-[#060d19]"
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
            <button
              type="button"
              className="rounded border border-slate/25 px-3 py-1.5 text-xs font-semibold dark:border-cyan-900/40"
              onClick={() => setShowAdvancedFilters((current) => !current)}
            >
              {showAdvancedFilters ? 'Hide Filters' : 'More Filters'}
            </button>
          </div>

          {showAdvancedFilters && (
            <div className="mt-2 grid gap-2 rounded border border-slate/20 bg-sand/40 p-2 dark:border-cyan-900/40 dark:bg-[#060d19]/70 md:grid-cols-2 lg:grid-cols-3">
              <select
                className="rounded border border-slate/25 bg-white px-2 py-1.5 text-sm dark:border-cyan-900/40 dark:bg-[#040913]"
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
                className="rounded border border-slate/25 bg-white px-2 py-1.5 text-sm dark:border-cyan-900/40 dark:bg-[#040913]"
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
                  className="w-full rounded border border-slate/25 bg-white px-2 py-1.5 text-sm disabled:opacity-50 dark:border-cyan-900/40 dark:bg-[#040913]"
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
                  className="w-full rounded border border-slate/25 bg-white px-2 py-1.5 text-sm disabled:opacity-50 dark:border-cyan-900/40 dark:bg-[#040913]"
                  value={customUntilDate}
                  onChange={(event) => {
                    setPage(1)
                    setActiveSavedViewId(null)
                    setCustomUntilDate(event.target.value)
                  }}
                  disabled={timeRange !== 'custom'}
                />
              </div>
              <div className="col-span-full grid gap-2 md:grid-cols-[1fr_auto_auto]">
                <input
                  value={savedViewName}
                  onChange={(event) => setSavedViewName(event.target.value)}
                  placeholder="Save current filters as..."
                  className="rounded border border-slate/25 bg-white px-2 py-1.5 text-sm dark:border-cyan-900/40 dark:bg-[#040913]"
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
                  className="rounded border border-slate/25 bg-white px-2 py-1.5 text-sm dark:border-cyan-900/40 dark:bg-[#040913]"
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
                  <option value="">Load Saved View</option>
                  {viewsQuery.data?.map((view) => (
                    <option key={view.id} value={view.id}>
                      {view.name}
                    </option>
                  ))}
                </select>
              </div>
              {activeSavedViewId && (
                <div className="col-span-full flex justify-end">
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
          )}
        </div>

        <div className="flex-1 overflow-auto p-3">
          <div className="space-y-2">
            {itemsQuery.data?.items.map((item) => {
              const expanded = expandedItemId === item.id
              const detail = expanded ? detailQuery.data : null

              return (
                <article
                  key={item.id}
                  className={`rounded border p-3 transition ${
                    expanded
                      ? 'border-cyan bg-cyan/5 dark:border-cyan-700/50 dark:bg-cyan-950/25'
                      : 'border-slate/20 dark:border-cyan-900/40'
                  } ${item.is_read ? 'opacity-85' : ''}`}
                >
                  <button className="w-full text-left" onClick={() => handleToggleItem(item.id, item.is_read)}>
                    <div className="flex items-start justify-between gap-3">
                      <h3 className="text-[15px] font-semibold leading-snug">{item.title}</h3>
                      <span className="shrink-0 text-xs text-slate dark:text-slate-300">{item.feed_name}</span>
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-slate dark:text-slate-300">
                      <span>Published {formatPublishedAt(item.published_at)}</span>
                      <span className="rounded bg-slate/15 px-1.5 py-0.5 dark:bg-[#0b1a33]">{item.status}</span>
                      {!item.is_read && <span className="rounded bg-cyan/20 px-1.5 py-0.5 text-cyan">Unread</span>}
                      {item.is_starred && <span className="rounded bg-amber-100 px-1.5 py-0.5 text-amber-700">Starred</span>}
                    </div>
                    <p className="mt-2 line-clamp-2 text-[13px] leading-5 text-slate dark:text-slate-300">{item.summary || 'No summary available.'}</p>
                  </button>

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

                          {canManage && (
                            <div className="mt-3">
                              <label className="text-xs font-semibold uppercase tracking-wide text-slate dark:text-slate-300">Analyst Note</label>
                              <textarea
                                className="mt-1 h-20 w-full rounded border border-slate/30 bg-white px-2 py-1.5 text-sm dark:border-cyan-900/40 dark:bg-[#060d19]"
                                value={noteDraft}
                                onChange={(event) => setNoteDraft(event.target.value)}
                              />
                              <button
                                className="mt-2 rounded bg-ink px-2.5 py-1 text-xs font-semibold text-white dark:bg-cyan dark:text-slate-950"
                                onClick={() => updateNote.mutate({ itemId: detail.id, note: noteDraft || null })}
                              >
                                Save Note
                              </button>
                            </div>
                          )}

                          <div className="mt-3 rounded border border-slate/20 bg-sand/50 p-3 dark:border-cyan-900/40 dark:bg-[#060d19]/90">
                            <p className="text-xs font-bold uppercase tracking-wide text-slate dark:text-slate-300">RSS Summary</p>
                            <p className="mt-1 whitespace-pre-wrap text-sm leading-6">{detail.summary || 'No summary.'}</p>
                          </div>

                          <div className="mt-3 rounded border border-slate/20 bg-white p-3 dark:border-cyan-900/40 dark:bg-[#060d19]/90">
                            <p className="text-xs font-bold uppercase tracking-wide text-slate dark:text-slate-300">Full Article</p>
                            {detail.article?.text ? (
                              <div className="rss-reader mt-2 rounded bg-white/70 p-3 dark:bg-[#040913]/80">
                                {renderArticleParagraphs(detail.article.text).map((paragraph, index) => (
                                  <p key={`${detail.id}-paragraph-${index}`}>{paragraph}</p>
                                ))}
                              </div>
                            ) : (
                              <p className="mt-2 text-sm text-slate dark:text-slate-300">No extracted article text available yet.</p>
                            )}
                            {detail.article?.error && <p className="mt-2 text-sm text-red-600">Extraction error: {detail.article.error}</p>}
                          </div>
                        </>
                      )}
                    </div>
                  )}
                </article>
              )
            })}

            {itemsQuery.isLoading && <p className="text-sm text-slate dark:text-slate-300">Loading items...</p>}
            {itemsQuery.isError && <p className="text-sm text-red-600">Failed to load items.</p>}
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
          <button
            className="rounded border border-slate/30 px-2 py-1 disabled:opacity-50 dark:border-cyan-900/40"
            disabled={page >= totalPages}
            onClick={() => setPage((current) => current + 1)}
          >
            Next
          </button>
        </div>

        {isWideLayout && (
          <button
            type="button"
            className="absolute bottom-1 right-1 h-4 w-4 cursor-se-resize rounded border border-slate/30 bg-white/85 dark:border-cyan-900/40 dark:bg-[#0b1629]"
            aria-label="Resize dashboard window"
            onMouseDown={startPanelResize}
          />
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
  queryClient.invalidateQueries({ queryKey: ['items'] })
  queryClient.invalidateQueries({ queryKey: ['item', itemId] })
}

function formatPublishedAt(value: string | null) {
  if (!value) return 'Unknown'
  const dt = new Date(value)
  if (Number.isNaN(dt.getTime())) return value
  return dt.toLocaleString()
}

function parseDashboardSavedView(raw: Record<string, unknown>): DashboardSavedViewQuery {
  const selectedFeedIds = Array.isArray(raw.selected_feed_ids)
    ? raw.selected_feed_ids.filter((entry): entry is string => typeof entry === 'string')
    : []

  const readStatus = raw.read_status === 'read' || raw.read_status === 'unread' ? raw.read_status : 'all'
  const starStatus = raw.star_status === 'starred' || raw.star_status === 'unstarred' ? raw.star_status : 'all'
  const timeRange =
    raw.time_range === '24h' || raw.time_range === '7d' || raw.time_range === '30d' || raw.time_range === 'custom'
      ? raw.time_range
      : 'all'

  const sortValues: TimeSort[] = ['published_at_desc', 'published_at_asc', 'first_seen_desc', 'first_seen_asc']
  const sort = typeof raw.sort === 'string' && sortValues.includes(raw.sort as TimeSort) ? (raw.sort as TimeSort) : 'published_at_desc'

  return {
    selected_feed_ids: selectedFeedIds,
    q: typeof raw.q === 'string' ? raw.q : '',
    read_status: readStatus,
    star_status: starStatus,
    time_range: timeRange,
    custom_since_date: typeof raw.custom_since_date === 'string' ? raw.custom_since_date : '',
    custom_until_date: typeof raw.custom_until_date === 'string' ? raw.custom_until_date : '',
    sort,
  }
}

function renderArticleParagraphs(text: string): string[] {
  const normalized = text.replace(/\r/g, '').trim()
  if (!normalized) return []

  const paragraphBlocks = normalized
    .split(/\n{2,}/)
    .map((block) => block.split('\n').map((line) => line.trim()).filter(Boolean).join(' '))
    .map((block) => block.replace(/\s{2,}/g, ' ').trim())
    .filter(Boolean)

  return paragraphBlocks.length ? paragraphBlocks : [normalized]
}

function loadPanelRect(): PanelRect {
  if (typeof window === 'undefined') {
    return DEFAULT_PANEL
  }

  const raw = window.localStorage.getItem(PANEL_STORAGE_KEY)
  if (!raw) {
    return DEFAULT_PANEL
  }

  try {
    const parsed = JSON.parse(raw) as Partial<PanelRect>
    if (
      typeof parsed.x !== 'number' ||
      typeof parsed.y !== 'number' ||
      typeof parsed.width !== 'number' ||
      typeof parsed.height !== 'number'
    ) {
      return DEFAULT_PANEL
    }

    return {
      x: Math.max(0, parsed.x),
      y: Math.max(0, parsed.y),
      width: Math.max(PANEL_MIN_WIDTH, parsed.width),
      height: Math.max(PANEL_MIN_HEIGHT, parsed.height),
    }
  } catch {
    return DEFAULT_PANEL
  }
}

function clamp(value: number, min: number, max: number) {
  if (value < min) return min
  if (value > max) return max
  return value
}
