export interface ReviewedIndicator {
  assessment_id: string
  item_id: string
  ioc_id: string
  type: string
  value: string
  title: string
  assessment_version: number
  source_revision: number
  extraction_revision: number
  expires_at: string | null
  evidence_count: number
}

export interface ReviewedPublicationPreview {
  fingerprint: string
  indicators: ReviewedIndicator[]
  matched_articles: number
  excluded_or_unreviewed: number
  max_articles: number
  max_indicators: number
}

export interface IndicatorPublication {
  id: string
  team_id: string
  format: 'stix' | 'misp'
  marking: string
  status: 'active' | 'partially_withdrawn' | 'withdrawn'
  revision: number
  indicator_count: number
  withdrawn_count: number
  created_at: string
  updated_at: string
}

export interface IndicatorPublicationPage {
  items: IndicatorPublication[]
  next_cursor: string | null
  has_more: boolean
}
