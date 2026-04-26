import type {
  SavedViewAlertFilters,
  SavedViewMode,
  SavedViewPanelRect,
  SavedViewPreview,
  SavedViewQueryPayload,
  SavedViewReadStatus,
  SavedViewRssFilters,
  SavedView,
  SavedViewSort,
  SavedViewStarStatus,
  SavedViewTimeRange,
  SavedViewWindow,
  SavedViewWindowAlertFilters,
  SavedViewWindowRssFilters,
  SavedViewWindowSnap,
  SavedViewWindowTimeFilter,
  SavedViewWindowType,
} from '../types/api'

export type TimeRangeFilter = SavedViewTimeRange
export type ReadStatusFilter = SavedViewReadStatus
export type StarStatusFilter = SavedViewStarStatus
export type TimeSort = SavedViewSort
export type DashboardViewMode = SavedViewMode
export type AlertViewMode = SavedViewMode
export type DashboardWindowType = SavedViewWindowType
export type DashboardWindowSnap = SavedViewWindowSnap
export type WindowTimeFilter = SavedViewWindowTimeFilter
export type PanelRect = SavedViewPanelRect
export type DashboardWindow = SavedViewWindow
export type DashboardRssWindowFilters = SavedViewWindowRssFilters
export type DashboardAlertWindowFilters = SavedViewWindowAlertFilters
export type DashboardSavedViewQuery = SavedViewRssFilters
export type DashboardAlertViewQuery = SavedViewAlertFilters
export type DashboardSavedViewState = SavedViewQueryPayload
export type DashboardSavedViewPreview = SavedViewPreview

export type SavedViewSelectionChange =
  | { kind: 'noop' }
  | { kind: 'clear' }
  | { kind: 'load'; viewId: string }
  | { kind: 'confirm_load'; viewId: string }

export interface ImportedSavedViewEntry {
  name: string
  query_json: Record<string, unknown>
}

export const DASHBOARD_SAVED_VIEW_SCHEMA_VERSION = 1
export const DASHBOARD_VIEW_VERSION = 6
export const WINDOW_MIN_WIDTH = 460
export const WINDOW_MIN_HEIGHT = 320
export const DEFAULT_ROLLING_DAYS = '7'
export const HIDDEN_TAGS = new Set(['content_fetched', 'priority'])
export const PAGE_SIZE_OPTIONS = [10, 25, 50, 100] as const
export const MAX_IMPORTED_VIEWS = 250

export function isTimeRangeFilter(value: unknown): value is TimeRangeFilter {
  return value === 'all' || value === '24h' || value === '7d' || value === '30d' || value === 'days' || value === 'custom'
}

export function isTimeSort(value: unknown): value is TimeSort {
  return (
    value === 'published_at_desc' ||
    value === 'published_at_asc' ||
    value === 'first_seen_desc' ||
    value === 'first_seen_asc'
  )
}

export function normalizeRollingDaysInput(value: string) {
  const numeric = value.replace(/[^\d]/g, '')
  if (!numeric) {
    return DEFAULT_ROLLING_DAYS
  }
  return String(clamp(Number(numeric), 1, 365))
}

export function createDefaultRssWindowFilters(showAdvancedFilters = false): DashboardRssWindowFilters {
  return {
    selected_feed_ids: [],
    selected_tags: [],
    q: '',
    read_status: 'all',
    star_status: 'all',
    view_mode: 'compact',
    page: 1,
    page_size: 25,
    sort: 'published_at_desc',
    show_advanced_filters: showAdvancedFilters,
  }
}

export function createDefaultAlertWindowFilters(): DashboardAlertWindowFilters {
  return {
    selected_alert_ids: [],
    selected_categories: [],
    q: '',
    view_mode: 'expanded',
    page: 1,
    page_size: 25,
    sort: 'published_at_desc',
  }
}

export function parseRssWindowFiltersCandidate(
  value: unknown,
  fallback?: Partial<DashboardRssWindowFilters>,
  showAdvancedFallback = false,
): DashboardRssWindowFilters {
  const source = isRecord(value) ? value : {}
  return {
    ...createDefaultRssWindowFilters(showAdvancedFallback),
    ...fallback,
    selected_feed_ids: Array.isArray(source.selected_feed_ids)
      ? source.selected_feed_ids.filter((entry): entry is string => typeof entry === 'string')
      : fallback?.selected_feed_ids ?? [],
    selected_tags: Array.isArray(source.selected_tags)
      ? source.selected_tags.filter((entry): entry is string => typeof entry === 'string' && !HIDDEN_TAGS.has(entry))
      : fallback?.selected_tags ?? [],
    q: typeof source.q === 'string' ? source.q : fallback?.q ?? '',
    read_status:
      source.read_status === 'read' || source.read_status === 'unread'
        ? source.read_status
        : fallback?.read_status ?? 'all',
    star_status:
      source.star_status === 'starred' || source.star_status === 'unstarred'
        ? source.star_status
        : fallback?.star_status ?? 'all',
    view_mode: source.view_mode === 'expanded' ? 'expanded' : fallback?.view_mode ?? 'compact',
    page:
      typeof source.page === 'number' && Number.isFinite(source.page) && source.page >= 1
        ? Math.floor(source.page)
        : fallback?.page ?? 1,
    page_size:
      typeof source.page_size === 'number' && PAGE_SIZE_OPTIONS.includes(source.page_size as (typeof PAGE_SIZE_OPTIONS)[number])
        ? (source.page_size as DashboardRssWindowFilters['page_size'])
        : fallback?.page_size ?? 25,
    sort: typeof source.sort === 'string' && isTimeSort(source.sort) ? source.sort : fallback?.sort ?? 'published_at_desc',
    show_advanced_filters:
      typeof source.show_advanced_filters === 'boolean'
        ? source.show_advanced_filters
        : fallback?.show_advanced_filters ?? showAdvancedFallback,
  }
}

export function parseAlertWindowFiltersCandidate(
  value: unknown,
  fallback?: Partial<DashboardAlertWindowFilters>,
): DashboardAlertWindowFilters {
  const source = isRecord(value) ? value : {}
  return {
    ...createDefaultAlertWindowFilters(),
    ...fallback,
    selected_alert_ids: Array.isArray(source.selected_alert_ids)
      ? source.selected_alert_ids.filter((entry): entry is string => typeof entry === 'string')
      : fallback?.selected_alert_ids ?? [],
    selected_categories: Array.isArray(source.selected_categories)
      ? source.selected_categories.filter((entry): entry is string => typeof entry === 'string')
      : fallback?.selected_categories ?? [],
    q: typeof source.q === 'string' ? source.q : fallback?.q ?? '',
    view_mode: source.view_mode === 'compact' ? 'compact' : fallback?.view_mode ?? 'expanded',
    page:
      typeof source.page === 'number' && Number.isFinite(source.page) && source.page >= 1
        ? Math.floor(source.page)
        : fallback?.page ?? 1,
    page_size:
      typeof source.page_size === 'number' && PAGE_SIZE_OPTIONS.includes(source.page_size as (typeof PAGE_SIZE_OPTIONS)[number])
        ? (source.page_size as DashboardAlertWindowFilters['page_size'])
        : fallback?.page_size ?? 25,
    sort: typeof source.sort === 'string' && isTimeSort(source.sort) ? source.sort : fallback?.sort ?? 'published_at_desc',
  }
}

export function parseWindowTimeFilterCandidate(value: unknown): WindowTimeFilter | null {
  if (!isRecord(value)) return null
  if (!isTimeRangeFilter(value.time_range)) return null
  return {
    time_range: value.time_range,
    custom_since_date: typeof value.custom_since_date === 'string' ? value.custom_since_date : '',
    custom_until_date: typeof value.custom_until_date === 'string' ? value.custom_until_date : '',
    rolling_days:
      typeof value.rolling_days === 'string' ? normalizeRollingDaysInput(value.rolling_days) : DEFAULT_ROLLING_DAYS,
  }
}

export function normalizePanelRect(panel: PanelRect, containerWidth: number, containerHeight: number): PanelRect {
  const maxWidth = Math.max(WINDOW_MIN_WIDTH, containerWidth)
  const maxHeight = Math.max(WINDOW_MIN_HEIGHT, containerHeight)

  const width = clamp(panel.width, WINDOW_MIN_WIDTH, maxWidth)
  const height = clamp(panel.height, WINDOW_MIN_HEIGHT, maxHeight)

  const maxX = Math.max(0, maxWidth - width)
  const maxY = Math.max(0, maxHeight - height)

  return {
    x: clamp(panel.x, 0, maxX),
    y: clamp(panel.y, 0, maxY),
    width,
    height,
  }
}

export function getSnapRect(snap: DashboardWindowSnap, containerWidth: number, containerHeight: number): PanelRect {
  const width = Math.max(WINDOW_MIN_WIDTH, containerWidth)
  const height = Math.max(WINDOW_MIN_HEIGHT, containerHeight)

  const halfWidth = Math.floor(width / 2)
  const halfHeight = Math.floor(height / 2)

  if (snap === 'full') {
    return { x: 0, y: 0, width, height }
  }

  if (snap === 'left') {
    return { x: 0, y: 0, width: halfWidth, height }
  }

  if (snap === 'right') {
    return { x: halfWidth, y: 0, width: width - halfWidth, height }
  }

  if (snap === 'top_left') {
    return { x: 0, y: 0, width: halfWidth, height: halfHeight }
  }

  if (snap === 'top_right') {
    return { x: halfWidth, y: 0, width: width - halfWidth, height: halfHeight }
  }

  if (snap === 'bottom_left') {
    return { x: 0, y: halfHeight, width: halfWidth, height: height - halfHeight }
  }

  if (snap === 'bottom_right') {
    return { x: halfWidth, y: halfHeight, width: width - halfWidth, height: height - halfHeight }
  }

  return {
    x: 0,
    y: 0,
    width,
    height,
  }
}

export function resolveWindowRect(windowLayout: DashboardWindow, containerWidth: number, containerHeight: number): PanelRect {
  if (windowLayout.snap === 'free') {
    return normalizePanelRect(windowLayout.rect, containerWidth, containerHeight)
  }
  return getSnapRect(windowLayout.snap, containerWidth, containerHeight)
}

export function defaultWindowTitle(type: DashboardWindowType, index: number): string {
  if (type === 'rss') return `RSS Panel ${index}`
  if (type === 'alerts') return `Alerts Panel ${index}`
  if (type === 'daily_brief') return `Daily Brief Panel ${index}`
  return `Notes Panel ${index}`
}

export function createWindowLayout(
  type: DashboardWindowType,
  index: number,
  containerWidth: number,
  containerHeight: number,
  snap: DashboardWindowSnap = 'free',
): DashboardWindow {
  const id = `${type}-${Date.now()}-${Math.random().toString(16).slice(2, 7)}`
  const width = clamp(Math.round(containerWidth * 0.68), WINDOW_MIN_WIDTH, containerWidth)
  const height = clamp(Math.round(containerHeight * 0.74), WINDOW_MIN_HEIGHT, containerHeight)

  const maxX = Math.max(0, containerWidth - width)
  const maxY = Math.max(0, containerHeight - height)

  const rect: PanelRect = {
    x: clamp((index * 28) % Math.max(1, maxX + 1), 0, maxX),
    y: clamp((index * 22) % Math.max(1, maxY + 1), 0, maxY),
    width,
    height,
  }

  const base = {
    id,
    title: defaultWindowTitle(type, index),
    snap,
    rect: snap === 'free' ? rect : getSnapRect(snap, containerWidth, containerHeight),
    controls_collapsed: false,
    scratch_note: '',
  }

  if (type === 'rss') {
    return {
      ...base,
      type,
      time_override: null,
      rss_filters: createDefaultRssWindowFilters(),
      alert_filters: null,
      selected_daily_brief_id: null,
    }
  }

  if (type === 'alerts') {
    return {
      ...base,
      type,
      time_override: null,
      rss_filters: null,
      alert_filters: createDefaultAlertWindowFilters(),
      selected_daily_brief_id: null,
    }
  }

  if (type === 'daily_brief') {
    return {
      ...base,
      type,
      time_override: null,
      rss_filters: null,
      alert_filters: null,
      selected_daily_brief_id: null,
    }
  }

  return {
    ...base,
    type,
    time_override: null,
    rss_filters: null,
    alert_filters: null,
    selected_daily_brief_id: null,
  }
}

export function normalizeDashboardWindows(windows: DashboardWindow[], containerWidth: number, containerHeight: number) {
  if (!windows.length) {
    return [createWindowLayout('rss', 1, containerWidth, containerHeight, 'full')]
  }

  return windows.map((window) => {
    if (window.snap === 'free') {
      return {
        ...window,
        rect: normalizePanelRect(window.rect, containerWidth, containerHeight),
      }
    }

    return {
      ...window,
      rect: getSnapRect(window.snap, containerWidth, containerHeight),
    }
  })
}

export function parsePanelRectCandidate(value: unknown): PanelRect | null {
  if (!isRecord(value)) return null
  const x = value.x
  const y = value.y
  const width = value.width
  const height = value.height
  if ([x, y, width, height].some((entry) => typeof entry !== 'number' || !Number.isFinite(entry))) {
    return null
  }
  const normalizedX = x as number
  const normalizedY = y as number
  const normalizedWidth = width as number
  const normalizedHeight = height as number
  if (normalizedWidth <= 0 || normalizedHeight <= 0) {
    return null
  }
  return {
    x: Math.round(normalizedX),
    y: Math.round(normalizedY),
    width: Math.round(normalizedWidth),
    height: Math.round(normalizedHeight),
  }
}

function createDefaultDashboardWindow(containerWidth: number, containerHeight: number) {
  return createWindowLayout('rss', 1, containerWidth, containerHeight, 'full')
}

function parseDashboardWindowCandidate(
  value: unknown,
  index: number,
  {
    alertFiltersFallback,
    rssFiltersFallback,
    showAdvancedFilters = false,
  }: {
    alertFiltersFallback?: Partial<DashboardAlertWindowFilters>
    rssFiltersFallback?: Partial<DashboardRssWindowFilters>
    showAdvancedFilters?: boolean
  } = {},
): DashboardWindow | null {
  if (!isRecord(value)) return null
  if (!isWindowType(value.type)) return null
  if (!isWindowSnap(value.snap)) return null

  const rect = parsePanelRectCandidate(value.rect)
  if (!rect) return null

  const base = {
    id: typeof value.id === 'string' && value.id ? value.id : crypto.randomUUID(),
    title: typeof value.title === 'string' && value.title ? value.title : defaultWindowTitle(value.type, index),
    snap: value.snap,
    rect,
    controls_collapsed: value.controls_collapsed === true,
    scratch_note: typeof value.scratch_note === 'string' ? value.scratch_note : '',
  }

  if (value.type === 'rss') {
    return {
      ...base,
      type: value.type,
      time_override: parseWindowTimeFilterCandidate(value.time_override),
      rss_filters: parseRssWindowFiltersCandidate(value.rss_filters, rssFiltersFallback, showAdvancedFilters),
      alert_filters: null,
      selected_daily_brief_id: null,
    }
  }

  if (value.type === 'alerts') {
    return {
      ...base,
      type: value.type,
      time_override: parseWindowTimeFilterCandidate(value.time_override),
      rss_filters: null,
      alert_filters: parseAlertWindowFiltersCandidate(value.alert_filters, alertFiltersFallback),
      selected_daily_brief_id: null,
    }
  }

  if (value.type === 'daily_brief') {
    return {
      ...base,
      type: value.type,
      time_override: null,
      rss_filters: null,
      alert_filters: null,
      selected_daily_brief_id:
        typeof value.selected_daily_brief_id === 'string' && value.selected_daily_brief_id ? value.selected_daily_brief_id : null,
    }
  }

  return {
    ...base,
    type: value.type,
    time_override: null,
    rss_filters: null,
    alert_filters: null,
    selected_daily_brief_id: null,
  }
}

export function parseDashboardSavedView(raw: unknown, containerWidth: number, containerHeight: number): DashboardSavedViewState {
  const source = isRecord(raw) ? raw : {}
  const fallback = createDefaultDashboardWindow(containerWidth, containerHeight)

  const legacyFilters = isRecord(source.filters) ? source.filters : source
  const legacyLayout = isRecord(source.layout) ? source.layout : {}
  const legacyWindows = isRecord(legacyLayout.windows) ? legacyLayout.windows : {}
  const legacyFeedRect =
    parsePanelRectCandidate(legacyWindows.feeds) || parsePanelRectCandidate(source.panel_rect) || fallback.rect
  const rssSource = isRecord(source.rss_filters) ? source.rss_filters : legacyFilters
  const alertSource = isRecord(source.alert_filters) ? source.alert_filters : {}
  const uiSource = isRecord(source.ui) ? source.ui : {}

  const rssFilters = parseSavedViewRssFilters(rssSource)
  const alertFilters = parseSavedViewAlertFilters(alertSource)
  const showAdvancedFilters = typeof uiSource.show_advanced_filters === 'boolean' ? uiSource.show_advanced_filters : false

  const parsedWindows: DashboardWindow[] = []
  if (Array.isArray(source.windows)) {
    for (const entry of source.windows) {
      const parsed = parseDashboardWindowCandidate(entry, parsedWindows.length + 1, {
        rssFiltersFallback: { ...rssFilters, page: 1 },
        alertFiltersFallback: { ...alertFilters, page: 1 },
        showAdvancedFilters,
      })
      if (parsed) {
        parsedWindows.push(parsed)
      }
    }
  }

  if (!parsedWindows.length) {
    parsedWindows.push({
      id: fallback.id,
      type: 'rss',
      title: defaultWindowTitle('rss', 1),
      snap: 'free',
      rect: legacyFeedRect,
      controls_collapsed: false,
      scratch_note: '',
      time_override: null,
      rss_filters: parseRssWindowFiltersCandidate(null, { ...rssFilters, page: 1 }, showAdvancedFilters),
      alert_filters: null,
      selected_daily_brief_id: null,
    })
  }

  return {
    schema_version: DASHBOARD_SAVED_VIEW_SCHEMA_VERSION,
    version: DASHBOARD_VIEW_VERSION,
    rss_filters: rssFilters,
    alert_filters: alertFilters,
    windows: normalizeDashboardWindows(parsedWindows, containerWidth, containerHeight),
    ui: {
      show_advanced_filters: showAdvancedFilters,
    },
  }
}

export function buildDashboardSavedViewState(
  windows: DashboardWindow[],
  dashboardTimeFilter: WindowTimeFilter,
): DashboardSavedViewState {
  const firstRssWindow = windows.find((window): window is DashboardWindow & { type: 'rss' } => window.type === 'rss')
  const firstAlertWindow = windows.find((window): window is DashboardWindow & { type: 'alerts' } => window.type === 'alerts')
  const rssWindowFilters = parseRssWindowFiltersCandidate(firstRssWindow?.rss_filters ?? null)
  const alertWindowFilters = parseAlertWindowFiltersCandidate(firstAlertWindow?.alert_filters ?? null)

  return {
    schema_version: DASHBOARD_SAVED_VIEW_SCHEMA_VERSION,
    version: DASHBOARD_VIEW_VERSION,
    rss_filters: buildSavedViewRssFilters(rssWindowFilters, dashboardTimeFilter),
    alert_filters: buildSavedViewAlertFilters(alertWindowFilters, dashboardTimeFilter),
    windows: windows.map(buildSavedViewWindowState),
    ui: {
      show_advanced_filters: rssWindowFilters.show_advanced_filters,
    },
  }
}

function buildSavedViewWindowRssFilters(value: DashboardRssWindowFilters | null): SavedViewWindowRssFilters {
  const filters = parseRssWindowFiltersCandidate(value)
  return {
    selected_feed_ids: [...filters.selected_feed_ids],
    selected_tags: [...filters.selected_tags],
    q: filters.q,
    read_status: filters.read_status,
    star_status: filters.star_status,
    view_mode: filters.view_mode,
    page: 1,
    page_size: filters.page_size,
    sort: filters.sort,
    show_advanced_filters: filters.show_advanced_filters,
  }
}

function buildSavedViewWindowAlertFilters(value: DashboardAlertWindowFilters | null): SavedViewWindowAlertFilters {
  const filters = parseAlertWindowFiltersCandidate(value)
  return {
    selected_alert_ids: [...filters.selected_alert_ids],
    selected_categories: [...filters.selected_categories],
    q: filters.q,
    view_mode: filters.view_mode,
    page: 1,
    page_size: filters.page_size,
    sort: filters.sort,
  }
}

function buildSavedViewWindowState(window: DashboardWindow): SavedViewWindow {
  const base = {
    id: window.id,
    title: window.title,
    snap: window.snap,
    rect: { ...window.rect },
    controls_collapsed: window.controls_collapsed,
    scratch_note: window.scratch_note,
  }

  if (window.type === 'rss') {
    return {
      ...base,
      type: 'rss',
      time_override: window.time_override ? { ...window.time_override } : null,
      rss_filters: buildSavedViewWindowRssFilters(window.rss_filters),
      alert_filters: null,
      selected_daily_brief_id: null,
    }
  }

  if (window.type === 'alerts') {
    return {
      ...base,
      type: 'alerts',
      time_override: window.time_override ? { ...window.time_override } : null,
      rss_filters: null,
      alert_filters: buildSavedViewWindowAlertFilters(window.alert_filters),
      selected_daily_brief_id: null,
    }
  }

  if (window.type === 'daily_brief') {
    return {
      ...base,
      type: 'daily_brief',
      time_override: null,
      rss_filters: null,
      alert_filters: null,
      selected_daily_brief_id: window.selected_daily_brief_id,
    }
  }

  return {
    ...base,
    type: 'notes',
    time_override: null,
    rss_filters: null,
    alert_filters: null,
    selected_daily_brief_id: null,
  }
}

export function loadDashboardWindows(storageKey: string, containerWidth: number, containerHeight: number): DashboardWindow[] {
  if (typeof window === 'undefined') {
    return [createDefaultDashboardWindow(containerWidth, containerHeight)]
  }

  const raw = window.localStorage.getItem(storageKey)
  if (!raw) {
    return [createDefaultDashboardWindow(containerWidth, containerHeight)]
  }

  try {
    const parsed = JSON.parse(raw) as unknown
    if (!Array.isArray(parsed)) {
      return [createDefaultDashboardWindow(containerWidth, containerHeight)]
    }

    const windows = parsed
      .map((entry, index) => parseDashboardWindowCandidate(entry, index + 1))
      .filter((entry): entry is DashboardWindow => entry !== null)

    if (!windows.length) {
      return [createDefaultDashboardWindow(containerWidth, containerHeight)]
    }

    return normalizeDashboardWindows(windows, containerWidth, containerHeight)
  } catch {
    return [createDefaultDashboardWindow(containerWidth, containerHeight)]
  }
}

export function buildSavedViewPreview(
  view: SavedView,
  containerWidth: number,
  containerHeight: number,
): DashboardSavedViewPreview {
  const parsed = parseDashboardSavedView(view.query_json, containerWidth, containerHeight)
  const counts: DashboardSavedViewPreview['window_type_counts'] = {
    rss: 0,
    alerts: 0,
    notes: 0,
    daily_brief: 0,
  }
  for (const window of parsed.windows) {
    counts[window.type] += 1
  }

  return {
    id: view.id,
    name: view.name,
    created_at: view.created_at,
    windows: parsed.windows,
    window_type_counts: counts,
  }
}

export function parseImportedSavedViews(raw: unknown): ImportedSavedViewEntry[] {
  const source = isRecord(raw) && Array.isArray(raw.views) ? raw.views : raw
  if (!Array.isArray(source)) {
    throw new Error('Expected a JSON array or an object with a "views" array')
  }

  const entries: ImportedSavedViewEntry[] = []
  for (const entry of source) {
    if (!isRecord(entry)) continue
    const name = typeof entry.name === 'string' ? entry.name.trim() : ''
    const queryJson = isRecord(entry.query_json) ? entry.query_json : null
    if (!name || !queryJson) {
      continue
    }

    entries.push({
      name,
      query_json: queryJson,
    })
  }

  if (entries.length > MAX_IMPORTED_VIEWS) {
    throw new Error(`Import file contains too many views. Maximum allowed is ${MAX_IMPORTED_VIEWS}.`)
  }

  return entries
}

export function resolveSavedViewSelectionChange({
  currentActiveSavedViewId,
  nextValue,
  hasProtectedEditSession,
}: {
  currentActiveSavedViewId: string | null
  nextValue: string
  hasProtectedEditSession: boolean
}): SavedViewSelectionChange {
  if (!nextValue) {
    return currentActiveSavedViewId ? { kind: 'clear' } : { kind: 'noop' }
  }

  if (nextValue === currentActiveSavedViewId) {
    return { kind: 'noop' }
  }

  return hasProtectedEditSession ? { kind: 'confirm_load', viewId: nextValue } : { kind: 'load', viewId: nextValue }
}

function parseSavedViewRssFilters(raw: Record<string, unknown>): DashboardSavedViewQuery {
  const selectedFeedIds = Array.isArray(raw.selected_feed_ids)
    ? raw.selected_feed_ids.filter((entry): entry is string => typeof entry === 'string')
    : []
  const selectedTags = Array.isArray(raw.selected_tags)
    ? raw.selected_tags.filter((entry): entry is string => typeof entry === 'string' && !HIDDEN_TAGS.has(entry))
    : []

  return {
    selected_feed_ids: selectedFeedIds,
    selected_tags: selectedTags,
    q: typeof raw.q === 'string' ? raw.q : '',
    read_status: raw.read_status === 'read' || raw.read_status === 'unread' ? raw.read_status : 'all',
    star_status: raw.star_status === 'starred' || raw.star_status === 'unstarred' ? raw.star_status : 'all',
    view_mode: raw.view_mode === 'expanded' ? 'expanded' : 'compact',
    page_size:
      typeof raw.page_size === 'number' && PAGE_SIZE_OPTIONS.includes(raw.page_size as (typeof PAGE_SIZE_OPTIONS)[number])
        ? (raw.page_size as DashboardSavedViewQuery['page_size'])
        : 25,
    time_range: isTimeRangeFilter(raw.time_range) ? raw.time_range : 'all',
    custom_since_date: typeof raw.custom_since_date === 'string' ? raw.custom_since_date : '',
    custom_until_date: typeof raw.custom_until_date === 'string' ? raw.custom_until_date : '',
    rolling_days: typeof raw.rolling_days === 'string' && raw.rolling_days.trim() ? raw.rolling_days.trim() : DEFAULT_ROLLING_DAYS,
    sort: typeof raw.sort === 'string' && isTimeSort(raw.sort) ? raw.sort : 'published_at_desc',
  }
}

function parseSavedViewAlertFilters(raw: Record<string, unknown>): DashboardAlertViewQuery {
  const selectedAlertIds = Array.isArray(raw.selected_alert_ids)
    ? raw.selected_alert_ids.filter((entry): entry is string => typeof entry === 'string')
    : []
  const selectedAlertCategories = Array.isArray(raw.selected_categories)
    ? raw.selected_categories.filter((entry): entry is string => typeof entry === 'string')
    : []

  return {
    selected_alert_ids: selectedAlertIds,
    selected_categories: selectedAlertCategories,
    q: typeof raw.q === 'string' ? raw.q : '',
    view_mode: raw.view_mode === 'compact' ? 'compact' : 'expanded',
    page_size:
      typeof raw.page_size === 'number' && PAGE_SIZE_OPTIONS.includes(raw.page_size as (typeof PAGE_SIZE_OPTIONS)[number])
        ? (raw.page_size as DashboardAlertViewQuery['page_size'])
        : 25,
    time_range: isTimeRangeFilter(raw.time_range) ? raw.time_range : 'all',
    custom_since_date: typeof raw.custom_since_date === 'string' ? raw.custom_since_date : '',
    custom_until_date: typeof raw.custom_until_date === 'string' ? raw.custom_until_date : '',
    rolling_days: typeof raw.rolling_days === 'string' && raw.rolling_days.trim() ? raw.rolling_days.trim() : DEFAULT_ROLLING_DAYS,
    sort: typeof raw.sort === 'string' && isTimeSort(raw.sort) ? raw.sort : 'published_at_desc',
  }
}

function buildSavedViewRssFilters(rssFilters: DashboardRssWindowFilters, dashboardTimeFilter: WindowTimeFilter): DashboardSavedViewQuery {
  return {
    selected_feed_ids: [...rssFilters.selected_feed_ids],
    selected_tags: [...rssFilters.selected_tags],
    q: rssFilters.q,
    read_status: rssFilters.read_status,
    star_status: rssFilters.star_status,
    view_mode: rssFilters.view_mode,
    page_size: rssFilters.page_size,
    time_range: dashboardTimeFilter.time_range,
    custom_since_date: dashboardTimeFilter.custom_since_date,
    custom_until_date: dashboardTimeFilter.custom_until_date,
    rolling_days: dashboardTimeFilter.rolling_days,
    sort: rssFilters.sort,
  }
}

function buildSavedViewAlertFilters(
  alertFilters: DashboardAlertWindowFilters,
  dashboardTimeFilter: WindowTimeFilter,
): DashboardAlertViewQuery {
  return {
    selected_alert_ids: [...alertFilters.selected_alert_ids],
    selected_categories: [...alertFilters.selected_categories],
    q: alertFilters.q,
    view_mode: alertFilters.view_mode,
    page_size: alertFilters.page_size,
    time_range: dashboardTimeFilter.time_range,
    custom_since_date: dashboardTimeFilter.custom_since_date,
    custom_until_date: dashboardTimeFilter.custom_until_date,
    rolling_days: dashboardTimeFilter.rolling_days,
    sort: alertFilters.sort,
  }
}

function clamp(value: number, min: number, max: number) {
  if (value < min) return min
  if (value > max) return max
  return value
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function isWindowType(value: unknown): value is DashboardWindowType {
  return value === 'rss' || value === 'alerts' || value === 'notes' || value === 'daily_brief'
}

function isWindowSnap(value: unknown): value is DashboardWindowSnap {
  return (
    value === 'free' ||
    value === 'full' ||
    value === 'left' ||
    value === 'right' ||
    value === 'top_left' ||
    value === 'top_right' ||
    value === 'bottom_left' ||
    value === 'bottom_right'
  )
}
