export type WebhookConditionField =
  | 'feed_id'
  | 'tag_id'
  | 'tag'
  | 'alert_rule_id'
  | 'team_id'
  | 'ioc_type'
  | 'ioc_role'
  | 'extraction_confidence'
  | 'maliciousness_confidence'
  | 'freshness_seconds'
  | 'attack_technique'
  | 'hunt_review_status'

export type WebhookCondition =
  | WebhookConditionGroup
  | {
      field: WebhookConditionField
      operator: 'in' | 'not_in' | 'gte' | 'lte'
      value: string[] | number
    }

export interface WebhookConditionGroup {
  op: 'all' | 'any' | 'not'
  conditions: WebhookCondition[]
}

export interface WebhookCredentialProfile {
  id: string
  name: string
  enabled: boolean
  auth_type: 'none' | 'bearer' | 'header'
  header_name: string | null
  auth_configured: boolean
  signing_configured: boolean
  revision: number
}

export interface WebhookMatchPreview {
  matches: boolean
  checks: { field: string; matched: boolean; reason: string }[]
  missing_fields: string[]
  automation_payload: Record<string, unknown> | null
}

export interface WebhookPreviewEvent {
  id: string
  event_type: string
  created_at: string
  label: string
}
