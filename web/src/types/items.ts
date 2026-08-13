export interface ItemListEntry {
  id: string
  feed_id: string
  feed_name: string
  url: string
  canonical_url: string | null
  title: string
  summary: string | null
  published_at: string | null
  first_seen_at: string
  status: string
  classification: string | null
  is_read: boolean
  is_starred: boolean
  tags: string[]
  ai_relevance_score: number | null
  ai_relevance_label: 'low' | 'medium' | 'high' | null
  ai_status: string | null
}

export interface ItemListResponse {
  items: ItemListEntry[]
  total: number
  page: number
  page_size: number
}

export interface Article {
  final_url: string
  retrieved_at: string
  http_status: number
  content_type: string | null
  title_extracted: string | null
  text: string | null
  extraction_method: string | null
  language: string | null
  word_count: number | null
  fetch_ms: number | null
  error: string | null
}

export interface ItemState {
  is_read: boolean
  is_starred: boolean
  note: string | null
  updated_at: string | null
}

export interface ItemDetail {
  id: string
  feed_id: string
  feed_name: string
  source_guid: string | null
  url: string
  canonical_url: string | null
  title: string
  summary: string | null
  published_at: string | null
  first_seen_at: string
  status: string
  classification: {
    primary_category: string
    secondary_categories: string[]
    confidence: number
    scores: Record<string, number>
    rules_version: string
    classified_at: string
  } | null
  last_error: string | null
  tags: string[]
  ai_insight: {
    status: string
    summary_text: string | null
    relevance_score: number | null
    relevance_label: 'low' | 'medium' | 'high' | null
    relevance_reasons: string[]
    model: string | null
    generated_at: string | null
    error: string | null
  } | null
  article: Article | null
  state: ItemState
}

export interface ItemGraphNode {
  id: string
  type: string
  label: string
  metadata: Record<string, unknown>
}

export interface ItemGraphEdge {
  source: string
  target: string
  relation: string
  weight: number
}

export interface ItemGraphResponse {
  nodes: ItemGraphNode[]
  edges: ItemGraphEdge[]
  focus_node_id: string | null
  root_item_id: string | null
}

export interface Tag {
  id: string
  name: string
}
