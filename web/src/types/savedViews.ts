export type SavedViewTimeRange = 'all' | '24h' | '7d' | '30d' | 'days' | 'custom'
export type SavedViewReadStatus = 'all' | 'read' | 'unread'
export type SavedViewStarStatus = 'all' | 'starred' | 'unstarred'
export type SavedViewAIRelevanceFilter = 'all' | 'low' | 'medium' | 'high'
export type SavedViewSort = 'published_at_desc' | 'published_at_asc' | 'first_seen_desc' | 'first_seen_asc'
export type SavedViewMode = 'expanded' | 'compact'
export type SavedViewWindowType = 'rss' | 'alerts' | 'notes' | 'daily_brief'
export type SavedViewWindowSnap =
  | 'free'
  | 'full'
  | 'left'
  | 'right'
  | 'top_left'
  | 'top_right'
  | 'bottom_left'
  | 'bottom_right'

export interface SavedViewWindowTimeFilter {
  time_range: SavedViewTimeRange
  custom_since_date: string
  custom_until_date: string
  rolling_days: string
}

export interface SavedViewRssFilters {
  selected_feed_ids: string[]
  selected_tags: string[]
  q: string
  read_status: SavedViewReadStatus
  star_status: SavedViewStarStatus
  ai_relevance: SavedViewAIRelevanceFilter
  view_mode: SavedViewMode
  page_size: 10 | 25 | 50 | 100
  time_range: SavedViewTimeRange
  custom_since_date: string
  custom_until_date: string
  rolling_days: string
  sort: SavedViewSort
}

export interface SavedViewAlertFilters {
  selected_alert_ids: string[]
  selected_categories: string[]
  q: string
  view_mode: SavedViewMode
  page_size: 10 | 25 | 50 | 100
  time_range: SavedViewTimeRange
  custom_since_date: string
  custom_until_date: string
  rolling_days: string
  sort: SavedViewSort
}

export interface SavedViewWindowRssFilters {
  selected_feed_ids: string[]
  selected_tags: string[]
  q: string
  read_status: SavedViewReadStatus
  star_status: SavedViewStarStatus
  ai_relevance: SavedViewAIRelevanceFilter
  view_mode: SavedViewMode
  page: number
  page_size: 10 | 25 | 50 | 100
  sort: SavedViewSort
  show_advanced_filters: boolean
}

export interface SavedViewWindowAlertFilters {
  selected_alert_ids: string[]
  selected_categories: string[]
  q: string
  view_mode: SavedViewMode
  page: number
  page_size: 10 | 25 | 50 | 100
  sort: SavedViewSort
}

export interface SavedViewPanelRect {
  x: number
  y: number
  width: number
  height: number
  xPct?: number | null
  yPct?: number | null
  widthPct?: number | null
  heightPct?: number | null
}

interface SavedViewWindowBase {
  id: string
  title: string
  snap: SavedViewWindowSnap
  rect: SavedViewPanelRect
  controls_collapsed: boolean
}

interface SavedViewSearchWindowBase extends SavedViewWindowBase {
  time_override: SavedViewWindowTimeFilter | null
}

export interface SavedViewRssWindow extends SavedViewSearchWindowBase {
  type: 'rss'
  scratch_note: string
  rss_filters: SavedViewWindowRssFilters
  alert_filters: null
  selected_daily_brief_id: null
}

export interface SavedViewAlertWindow extends SavedViewSearchWindowBase {
  type: 'alerts'
  scratch_note: string
  rss_filters: null
  alert_filters: SavedViewWindowAlertFilters
  selected_daily_brief_id: null
}

export interface SavedViewNotesWindow extends SavedViewWindowBase {
  type: 'notes'
  scratch_note: string
  time_override: null
  rss_filters: null
  alert_filters: null
  selected_daily_brief_id: null
}

export interface SavedViewDailyBriefWindow extends SavedViewWindowBase {
  type: 'daily_brief'
  scratch_note: string
  time_override: null
  rss_filters: null
  alert_filters: null
  selected_daily_brief_id: string | null
}

export type SavedViewWindow =
  | SavedViewRssWindow
  | SavedViewAlertWindow
  | SavedViewNotesWindow
  | SavedViewDailyBriefWindow

export interface SavedViewWindowSummary {
  rss: number
  alerts: number
  notes: number
  daily_brief: number
}

export interface SavedViewPreview {
  id: string
  name: string
  created_at: string
  windows: SavedViewWindow[]
  window_type_counts: SavedViewWindowSummary
}

export type SavedViewSearchWindow = SavedViewRssWindow | SavedViewAlertWindow
export type SavedViewNonSearchWindow = SavedViewNotesWindow | SavedViewDailyBriefWindow

export interface SavedViewQueryPayload {
  schema_version: 1
  version: number
  rss_filters: SavedViewRssFilters
  alert_filters: SavedViewAlertFilters
  windows: SavedViewWindow[]
  ui: {
    show_advanced_filters: boolean
  }
}

export interface SavedView {
  id: string
  user_id: string
  name: string
  query_json: SavedViewQueryPayload
  created_at: string
}
