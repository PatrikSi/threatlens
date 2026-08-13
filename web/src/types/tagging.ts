export interface TaggingSettings {
  id: string
  enabled_categories: string[]
  min_auto_tag_confidence: number
  secondary_tag_limit: number
  created_at: string
  updated_at: string
}

export interface TaggingRule {
  id: string
  name: string
  tag_name: string
  enabled: boolean
  match_type: 'contains' | 'regex'
  pattern: string
  case_sensitive: boolean
  applies_to: Array<'title' | 'summary' | 'article_text' | 'feed_name'>
  required_categories: string[]
  feed_scope: 'all' | 'selected'
  feed_ids: string[]
  min_classification_confidence: number | null
  created_at: string
  updated_at: string
}

export interface TaggingSettingsBundleResponse {
  settings: TaggingSettings
  rules: TaggingRule[]
}

export interface TaggingRuleWriteRequest {
  name: string
  tag_name: string
  enabled: boolean
  match_type: 'contains' | 'regex'
  pattern: string
  case_sensitive: boolean
  applies_to: Array<'title' | 'summary' | 'article_text' | 'feed_name'>
  required_categories: string[]
  feed_scope: 'all' | 'selected'
  feed_ids: string[]
  min_classification_confidence: number | null
}

export interface TaggingRulePreviewItem {
  id: string
  title: string
  feed_name: string
  classification: string | null
  first_seen_at: string
  current_tags: string[]
  matched_sections: string[]
}

export interface TaggingRulePreviewResponse {
  total: number
  items: TaggingRulePreviewItem[]
}

export interface TaggingReapplyResponse {
  task_id: string
  queued: boolean
  celery_task_id: string | null
  dispatch_token: string | null
}
