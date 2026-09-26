export type IndicatorVerdict =
  | 'unreviewed'
  | 'malicious'
  | 'benign'
  | 'reference'
  | 'example'
  | 'retracted'
export interface IndicatorAssessment {
  version: number
  verdict: IndicatorVerdict
  reason: string
  source_revision: number
  extraction_revision: number
  expires_at: string | null
  expired: boolean
  current: boolean
  updated_at: string
}
export interface Indicator {
  id: string
  type: string
  value: string
  raw: string
  occurrences?: number
  evidence_truncated?: boolean
  extraction_confidence: number
  evidence: {
    source: string
    raw: string
    start: number | null
    end: number | null
    quote: string | null
    transformations: string[]
  }[]
  ai: {
    role: string
    assertion: string
    evidence: { source: string; quote: string }[]
    maliciousness_confidence: number | null
  } | null
  ai_current: boolean
  assessment: IndicatorAssessment | null
  excluded: boolean
  exclusion_reasons: string[]
  suppressed: boolean
}
export interface IndicatorPage {
  items: Indicator[]
  total: number
  page: number
  page_size: number
  source_revision: number
  extraction_revision: number
  can_review: boolean
  can_manage_suppressions: boolean
  extraction_current: boolean
}
export interface IndicatorHistoryPage {
  items: {
    version: number
    snapshot: Record<string, unknown>
    actor_user_id: string | null
    created_at: string
  }[]
  total: number
  page: number
  page_size: number
}
export interface IndicatorSuppression {
  id: string
  team_id: string
  ioc_type: string
  value: string
  version: number
  reason: string
  active: boolean
  expires_at: string | null
  expired: boolean
  updated_at: string
}
export interface IndicatorSuppressionPage {
  items: IndicatorSuppression[]
  total: number
  page: number
  page_size: number
  can_manage: boolean
}
