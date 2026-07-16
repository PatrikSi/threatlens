import { type Dispatch, type SetStateAction } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { ApiError } from '../api/client'
import { AIDailyBrief, ItemDetail, ItemListResponse } from '../types/api'
import { formatDateOnly, formatDateTime } from '../utils/datetime'
import {
  DEFAULT_ROLLING_DAYS,
  WINDOW_MIN_HEIGHT,
  WINDOW_MIN_WIDTH,
  type AIRelevanceFilter,
  type DashboardAlertWindowFilters,
  type DashboardRssWindowFilters,
  type DashboardWindow,
  type DashboardWindowSnap,
  type PanelRect,
  type ReadStatusFilter,
  type StarStatusFilter,
  type TimeRangeFilter,
  type TimeSort,
  type WindowTimeFilter,
} from './dashboardSavedViews'

export const ARTICLE_PREVIEW_WIDTH_STORAGE_KEY = 'threatlens.article-preview.width.v1'
export const ARTICLE_PREVIEW_DEFAULT_WIDTH = 704
export const ARTICLE_PREVIEW_MIN_WIDTH = 420
export const ARTICLE_PREVIEW_MAX_WIDTH = 1120

const DRAG_EDGE_SNAP_THRESHOLD = 12
const DRAG_MIDLINE_SNAP_THRESHOLD = 8

const WINDOW_SNAP_OPTIONS: Array<{ value: DashboardWindowSnap; label: string }> = [
  { value: 'free', label: 'Floating' },
  { value: 'full', label: 'Full' },
  { value: 'left', label: 'Left Half' },
  { value: 'right', label: 'Right Half' },
  { value: 'top_left', label: 'Top Left' },
  { value: 'top_right', label: 'Top Right' },
  { value: 'bottom_left', label: 'Bottom Left' },
  { value: 'bottom_right', label: 'Bottom Right' },
]

export function getRelativeTimeAnchorMs() {
  return Math.floor(Date.now() / 60_000) * 60_000
}

export function deriveTimeWindow(
  timeRange: TimeRangeFilter,
  customSinceDate: string,
  customUntilDate: string,
  rollingDays = DEFAULT_ROLLING_DAYS,
  relativeTimeAnchorMs = getRelativeTimeAnchorMs(),
) {
  if (timeRange === 'all') {
    return { sinceIso: '', untilIso: '' }
  }

  if (timeRange === 'days') {
    const dayCount = clamp(Number(rollingDays) || Number(DEFAULT_ROLLING_DAYS), 1, 365)
    const now = new Date(relativeTimeAnchorMs)
    const since = new Date(now)
    since.setTime(now.getTime() - dayCount * 24 * 60 * 60 * 1000)
    return { sinceIso: since.toISOString(), untilIso: now.toISOString() }
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

  const now = new Date(relativeTimeAnchorMs)
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

export function parseStartOfDay(date: string): Date | null {
  if (!date) return null
  const parsed = new Date(`${date}T00:00:00`)
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

export function parseEndOfDay(date: string): Date | null {
  if (!date) return null
  const parsed = new Date(`${date}T23:59:59.999`)
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

export function resolveWindowTimeFilter(windowLayout: DashboardWindow, dashboardTimeFilter: WindowTimeFilter): WindowTimeFilter {
  if (windowLayout.type === 'notes' || windowLayout.type === 'daily_brief') {
    return dashboardTimeFilter
  }
  return windowLayout.time_override ?? dashboardTimeFilter
}

export type ItemCachePatch = {
  isRead?: boolean
  isStarred?: boolean
  note?: string | null
}

export type DashboardItemsQueryKeyParams = {
  selected_feed_ids: string
  selected_tags: string
  q: string
  read_status: ReadStatusFilter
  star_status: StarStatusFilter
  ai_relevance: AIRelevanceFilter
  since: string | null
  until: string | null
  sort: TimeSort
  page: number
  page_size: number
}

export function buildDashboardItemsQueryKey(params: DashboardItemsQueryKeyParams) {
  return ['items', params] as const
}

export function resolveItemActionError(error: unknown, fallback: string) {
  if (error instanceof ApiError && error.message.trim()) {
    return error.message
  }
  if (error instanceof Error && error.message.trim()) {
    return error.message
  }
  return fallback
}

export function clearItemFeedback(
  setter: Dispatch<SetStateAction<Record<string, { tone: 'success' | 'error'; message: string }>>>,
  itemId: string,
) {
  setter((current) => {
    if (!(itemId in current)) {
      return current
    }
    const next = { ...current }
    delete next[itemId]
    return next
  })
}

export function syncItemStateInCache(queryClient: ReturnType<typeof useQueryClient>, itemId: string, patch: ItemCachePatch) {
  queryClient.setQueriesData<ItemListResponse>({ queryKey: ['items'] }, (current) => {
    if (!current) {
      return current
    }

    let changed = false
    const items = current.items.map((item) => {
      if (item.id !== itemId) {
        return item
      }

      changed = true
      return {
        ...item,
        is_read: patch.isRead ?? item.is_read,
        is_starred: patch.isStarred ?? item.is_starred,
      }
    })

    return changed ? { ...current, items } : current
  })

  queryClient.setQueryData<ItemDetail>(['item', itemId], (current) => {
    if (!current) {
      return current
    }

    return {
      ...current,
      state: {
        ...current.state,
        is_read: patch.isRead ?? current.state.is_read,
        is_starred: patch.isStarred ?? current.state.is_starred,
        note: patch.note !== undefined ? patch.note : current.state.note,
        updated_at: new Date().toISOString(),
      },
    }
  })

  if (patch.isRead === undefined && patch.isStarred === undefined) {
    return
  }

  queryClient.invalidateQueries({
    predicate: (query) => shouldRefreshFilteredItemList(query.queryKey, patch),
  })
}

export function shouldRefreshFilteredItemList(queryKey: readonly unknown[], patch: ItemCachePatch) {
  if (queryKey[0] !== 'items') {
    return false
  }

  const params = queryKey[1]
  if (!isDashboardItemsQueryKeyParams(params)) {
    return false
  }

  return (
    (patch.isRead !== undefined && params.read_status !== 'all') ||
    (patch.isStarred !== undefined && params.star_status !== 'all')
  )
}

export function isDashboardItemsQueryKeyParams(value: unknown): value is DashboardItemsQueryKeyParams {
  if (typeof value !== 'object' || value === null) {
    return false
  }

  const params = value as Record<string, unknown>
  const readStatus = params.read_status
  const starStatus = params.star_status
  const isDashboardReadStatus = readStatus === 'all' || readStatus === 'read' || readStatus === 'unread'
  const isDashboardStarStatus = starStatus === 'all' || starStatus === 'starred' || starStatus === 'unstarred'

  return (
    typeof params.selected_feed_ids === 'string' &&
    typeof params.selected_tags === 'string' &&
    typeof params.q === 'string' &&
    isDashboardReadStatus &&
    isDashboardStarStatus &&
    (params.ai_relevance === 'all' ||
      params.ai_relevance === 'low' ||
      params.ai_relevance === 'medium' ||
      params.ai_relevance === 'high') &&
    (params.since === null || typeof params.since === 'string') &&
    (params.until === null || typeof params.until === 'string') &&
    typeof params.sort === 'string' &&
    typeof params.page === 'number' &&
    typeof params.page_size === 'number'
  )
}

export function formatPublishedAt(value: string | null) {
  return formatDateTime(value)
}

export function formatDailyBriefOptionLabel(brief: AIDailyBrief) {
  return `${formatDateOnly(brief.brief_date)} · ${brief.item_count} items`
}

export function formatClassificationLabel(value: string): string {
  return value
    .split('_')
    .map((part) => (part ? part[0].toUpperCase() + part.slice(1) : part))
    .join(' ')
}

export function formatAiRelevanceLabel(value: 'low' | 'medium' | 'high'): string {
  if (value === 'high') return 'High'
  if (value === 'medium') return 'Medium'
  return 'Low'
}

export function aiRelevanceTone(value: 'low' | 'medium' | 'high'): string {
  if (value === 'high') {
    return 'tl-chip-ai-high'
  }
  if (value === 'medium') {
    return 'tl-chip-ai-medium'
  }
  return 'tl-chip-ai-low'
}

export function formatItemStatusLabel(value: string): string {
  const normalized = value.trim().toLowerCase()
  if (normalized === 'error') {
    return 'content error'
  }
  if (normalized === 'new') {
    return 'new item'
  }
  return normalized.replace(/_/g, ' ')
}

export function itemStatusTone(value: string): string {
  const normalized = value.trim().toLowerCase()
  if (normalized.includes('error') || normalized.includes('failed')) {
    return 'tl-chip-warning'
  }
  return 'tl-chip-neutral'
}


export function formatRollingWindowHint(rollingDays: string) {
  const dayCount = clamp(Number(rollingDays) || Number(DEFAULT_ROLLING_DAYS), 1, 365)
  const now = new Date()
  const since = new Date(now)
  since.setTime(now.getTime() - dayCount * 24 * 60 * 60 * 1000)
  return `Since ${formatDateOnly(since)}`
}

export function formatDashboardTimeRangeSummary(timeRange: TimeRangeFilter, customSinceDate: string, customUntilDate: string, rollingDays = DEFAULT_ROLLING_DAYS) {
  if (timeRange === 'all') return 'All time'
  if (timeRange === '24h') return 'Last 24h'
  if (timeRange === '7d') return 'Last 7 days'
  if (timeRange === '30d') return 'Last 30 days'
  if (timeRange === 'days') return `Last ${clamp(Number(rollingDays) || Number(DEFAULT_ROLLING_DAYS), 1, 365)} days`
  if (customSinceDate && customUntilDate) return `Custom ${formatDateOnly(customSinceDate)} to ${formatDateOnly(customUntilDate)}`
  if (customSinceDate) return `Custom from ${formatDateOnly(customSinceDate)}`
  if (customUntilDate) return `Custom until ${formatDateOnly(customUntilDate)}`
  return 'Custom window'
}

export function getArticlePreviewMaxWidth() {
  if (typeof window === 'undefined') {
    return ARTICLE_PREVIEW_MAX_WIDTH
  }

  return Math.max(ARTICLE_PREVIEW_MIN_WIDTH, Math.min(ARTICLE_PREVIEW_MAX_WIDTH, window.innerWidth - 48))
}

export function clampArticlePreviewWidth(width: number) {
  if (!Number.isFinite(width)) {
    return ARTICLE_PREVIEW_DEFAULT_WIDTH
  }

  return Math.round(Math.min(Math.max(width, ARTICLE_PREVIEW_MIN_WIDTH), getArticlePreviewMaxWidth()))
}

export function loadArticlePreviewWidth() {
  if (typeof window === 'undefined') {
    return ARTICLE_PREVIEW_DEFAULT_WIDTH
  }

  try {
    const stored = window.localStorage.getItem(ARTICLE_PREVIEW_WIDTH_STORAGE_KEY)
    if (!stored) {
      return clampArticlePreviewWidth(ARTICLE_PREVIEW_DEFAULT_WIDTH)
    }
    return clampArticlePreviewWidth(Number(stored))
  } catch {
    return clampArticlePreviewWidth(ARTICLE_PREVIEW_DEFAULT_WIDTH)
  }
}

export function persistArticlePreviewWidth(width: number) {
  if (typeof window === 'undefined') {
    return
  }

  try {
    window.localStorage.setItem(ARTICLE_PREVIEW_WIDTH_STORAGE_KEY, String(width))
  } catch {
    // Ignore storage failures; the in-memory width still works for this session.
  }
}

export function countActiveWindowFilters(
  windowLayout: DashboardWindow,
  rssFilters: DashboardRssWindowFilters,
  alertFilters: DashboardAlertWindowFilters,
  aiRelevanceEnabled: boolean,
) {
  if (windowLayout.type === 'rss') {
    return (
      rssFilters.selected_feed_ids.length +
      rssFilters.selected_tags.length +
      (rssFilters.q.trim() ? 1 : 0) +
      (rssFilters.read_status !== 'all' ? 1 : 0) +
      (rssFilters.star_status !== 'all' ? 1 : 0) +
      (aiRelevanceEnabled && rssFilters.ai_relevance !== 'all' ? 1 : 0)
    )
  }

  if (windowLayout.type === 'alerts') {
    return alertFilters.selected_alert_ids.length + alertFilters.selected_categories.length + (alertFilters.q.trim() ? 1 : 0)
  }

  return 0
}

export function formatWindowSnapLabel(snap: DashboardWindowSnap) {
  return WINDOW_SNAP_OPTIONS.find((option) => option.value === snap)?.label ?? 'Placement'
}

export function formatWindowTimeSummary(windowLayout: DashboardWindow, dashboardTimeFilter: WindowTimeFilter) {
  if (windowLayout.type === 'notes') {
    return 'Saved with view'
  }

  if (windowLayout.type === 'daily_brief') {
    return 'Brief snapshots'
  }

  if (!windowLayout.time_override) {
    return `Scope: ${formatDashboardTimeRangeSummary(
      dashboardTimeFilter.time_range,
      dashboardTimeFilter.custom_since_date,
      dashboardTimeFilter.custom_until_date,
      dashboardTimeFilter.rolling_days,
    )}`
  }

  return `Scope: ${formatDashboardTimeRangeSummary(
    windowLayout.time_override.time_range,
    windowLayout.time_override.custom_since_date,
    windowLayout.time_override.custom_until_date,
    windowLayout.time_override.rolling_days,
  )}`
}

export function loadWindowSeenState(storageKey: string): Record<string, string> {
  return loadStoredTimestampMap(storageKey)
}

export function loadStoredTimestampMap(storageKey: string): Record<string, string> {
  if (typeof window === 'undefined') {
    return {}
  }

  const raw = window.localStorage.getItem(storageKey)
  if (!raw) {
    return {}
  }

  try {
    const parsed = JSON.parse(raw) as unknown
    if (!isRecord(parsed)) return {}
    const next: Record<string, string> = {}
    for (const [key, value] of Object.entries(parsed)) {
      if (typeof value === 'string' && value.trim()) {
        next[key] = value
      }
    }
    return next
  } catch {
    return {}
  }
}

export function loadStoredTimestamp(storageKey: string): string {
  if (typeof window === 'undefined') {
    return ''
  }

  try {
    const value = window.localStorage.getItem(storageKey)
    return typeof value === 'string' ? value : ''
  } catch {
    return ''
  }
}

export function countNewEntriesSince<T extends { first_seen_at: string }>(entries: T[], lastSeenAtIso: string): number {
  if (!lastSeenAtIso) {
    return 0
  }

  const marker = Date.parse(lastSeenAtIso)
  if (Number.isNaN(marker)) {
    return 0
  }

  let count = 0
  for (const entry of entries) {
    const candidate = Date.parse(entry.first_seen_at)
    if (!Number.isNaN(candidate) && candidate > marker) {
      count += 1
    }
  }
  return count
}


export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

export function applyDragMagnetSnap(
  movingRect: PanelRect,
  otherRects: PanelRect[],
  containerWidth: number,
  containerHeight: number,
  maxX: number,
  maxY: number,
): Pick<PanelRect, 'x' | 'y'> {
  let snappedX = movingRect.x
  let snappedY = movingRect.y
  let bestXDistance = Number.POSITIVE_INFINITY
  let bestYDistance = Number.POSITIVE_INFINITY

  const maybeSnapX = (candidate: number, threshold: number) => {
    const normalized = clamp(candidate, 0, maxX)
    const distance = Math.abs(movingRect.x - normalized)
    if (distance <= threshold && distance < bestXDistance) {
      snappedX = normalized
      bestXDistance = distance
    }
  }

  const maybeSnapY = (candidate: number, threshold: number) => {
    const normalized = clamp(candidate, 0, maxY)
    const distance = Math.abs(movingRect.y - normalized)
    if (distance <= threshold && distance < bestYDistance) {
      snappedY = normalized
      bestYDistance = distance
    }
  }

  for (const rect of otherRects) {
    const left = rect.x
    const right = rect.x + rect.width
    const top = rect.y
    const bottom = rect.y + rect.height

    maybeSnapX(left, DRAG_EDGE_SNAP_THRESHOLD)
    maybeSnapX(right, DRAG_EDGE_SNAP_THRESHOLD)
    maybeSnapX(left - movingRect.width, DRAG_EDGE_SNAP_THRESHOLD)
    maybeSnapX(right - movingRect.width, DRAG_EDGE_SNAP_THRESHOLD)

    maybeSnapY(top, DRAG_EDGE_SNAP_THRESHOLD)
    maybeSnapY(bottom, DRAG_EDGE_SNAP_THRESHOLD)
    maybeSnapY(top - movingRect.height, DRAG_EDGE_SNAP_THRESHOLD)
    maybeSnapY(bottom - movingRect.height, DRAG_EDGE_SNAP_THRESHOLD)
  }

  const midX = containerWidth / 2
  const midY = containerHeight / 2

  maybeSnapX(midX, DRAG_MIDLINE_SNAP_THRESHOLD)
  maybeSnapX(midX - movingRect.width, DRAG_MIDLINE_SNAP_THRESHOLD)
  maybeSnapY(midY, DRAG_MIDLINE_SNAP_THRESHOLD)
  maybeSnapY(midY - movingRect.height, DRAG_MIDLINE_SNAP_THRESHOLD)

  return { x: snappedX, y: snappedY }
}

export function getWindowContainerDimensions(rootElement: HTMLDivElement | null): { width: number; height: number } {
  if (typeof window === 'undefined') {
    return { width: 1380, height: 760 }
  }

  const rootBounds = rootElement?.getBoundingClientRect()
  const width = Math.max(WINDOW_MIN_WIDTH, Math.floor(rootBounds?.width ?? window.innerWidth))
  const height = Math.max(WINDOW_MIN_HEIGHT, Math.floor(rootBounds?.height ?? window.innerHeight - 140))
  return { width, height }
}

export function clamp(value: number, min: number, max: number) {
  if (value < min) return min
  if (value > max) return max
  return value
}

