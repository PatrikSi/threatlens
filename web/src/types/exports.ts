export type ArticleExportFormat = 'csv' | 'jsonl' | 'threat_bundle' | 'stix' | 'misp' | 'pdf_bundle'
export type ArticleExportDateBasis = 'first_seen_at' | 'published_at_or_first_seen_at'
export type ArticleExportSort = 'published_at_desc' | 'published_at_asc' | 'first_seen_desc' | 'first_seen_asc'
export type ArticleExportTagsMode = 'any' | 'all'
export type ArticleExportTLPMarking = 'none' | 'TLP:WHITE' | 'TLP:GREEN' | 'TLP:AMBER' | 'TLP:RED'
export type ArticleExportRelevanceLabel = 'low' | 'medium' | 'high'

export interface ArticleExportFilters {
  q: string | null
  feed_ids: string[]
  tag_ids: string[]
  tags_mode: ArticleExportTagsMode
  classifications: string[]
  ai_relevance_labels: ArticleExportRelevanceLabel[]
  ai_score_min: number | null
  ai_score_max: number | null
  is_read: boolean | null
  is_starred: boolean | null
  has_article_text: boolean | null
  since: string | null
  until: string | null
  date_basis: ArticleExportDateBasis
  sort: ArticleExportSort
}

export interface ArticleExportOptions {
  include_article_text: boolean
  csv_include_article_text: boolean
  include_ai_details: boolean
  include_tag_metadata: boolean
  include_iocs: boolean
  include_ioc_csv: boolean
  include_user_state: boolean
  include_user_notes: boolean
  pdf_include_article_text: boolean
  stix_marking: ArticleExportTLPMarking
  misp_distribution: number
  filename_prefix: string | null
}

export interface ArticleExportPreviewRequest {
  filters: ArticleExportFilters
}

export interface ArticleExportRequest {
  format: ArticleExportFormat
  filters: ArticleExportFilters
  options: ArticleExportOptions
}

export interface ArticleExportOptionEntry {
  id: string
  name: string
}

export interface ArticleExportFormatCapability {
  id: ArticleExportFormat
  label: string
  extension: string
  media_type: string
  description: string
  supports_article_text: boolean
  supports_iocs: boolean
  supports_user_state: boolean
}

export interface ArticleExportCapabilities {
  formats: ArticleExportFormatCapability[]
  feeds: ArticleExportOptionEntry[]
  tags: ArticleExportOptionEntry[]
  classifications: string[]
  max_items: number
  max_pdf_items: number
  max_uncompressed_bytes: number
  preview_limit: number
}

export interface ArticleExportPreviewItem {
  id: string
  title: string
  url: string
  feed_name: string
  published_at: string | null
  first_seen_at: string
  classification: string | null
  ai_relevance_score: number | null
  ai_relevance_label: ArticleExportRelevanceLabel | null
  tags: string[]
  is_read: boolean
  is_starred: boolean
  has_article_text: boolean
  ioc_count: number
}

export interface ArticleExportPreview {
  total_matches: number
  articles_with_text: number
  items_with_iocs: number
  preview_limit: number
  exceeds_export_limit: boolean
  exceeds_pdf_limit: boolean
  items: ArticleExportPreviewItem[]
}
