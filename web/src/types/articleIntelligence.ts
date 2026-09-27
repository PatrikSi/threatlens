export interface ArticleEvidence {
  source: 'title' | 'summary' | 'article_text'
  quote: string
  start?: number
  end?: number
}
export interface ExtractedEntity {
  id: string
  kind: 'actor' | 'malware' | 'product' | 'behavior' | 'indicator'
  name: string
  description: string
  assertion: 'reported' | 'inferred'
  versions: string[]
  indicator_role: 'malicious_infrastructure' | 'benign' | 'reference' | 'unknown' | null
  evidence: ArticleEvidence[]
}
export interface ExtractedRelationship {
  source_entity_id: string
  target_entity_id: string
  relationship: string
  description: string
  assertion: 'reported' | 'inferred'
  evidence: ArticleEvidence[]
}
export interface ExtractionCoverage {
  planner_version: number
  source_hash: string
  normalized_text_chars: number
  processed_chars: number
  uncovered_chars: number
  reserved_tokens: number
  token_budget: number
  call_limit: number
  output_limited: boolean
  progress_revision?: string | null
  summary_scope?: 'first_section' | 'processed_sections' | 'section_synthesis'
  sections: { index: number; start: number; end: number; status: 'pending' | 'started' | 'completed' }[]
}
export interface StructuredExtraction {
  schema_version: number
  article_id: string
  source_version: number
  article_retrieved_at: string | null
  source_hash: string
  input_sha256: string
  truncated: boolean
  coverage?: ExtractionCoverage | null
  entities: ExtractedEntity[]
  relationships: ExtractedRelationship[]
  information_gaps: string[]
}
export interface HuntSuggestion {
  id: string
  title: string
  hypothesis: string
  rationale: string
  required_logs: string[]
  benign_explanations: string[]
  information_gaps: string[]
  evidence: ArticleEvidence[]
  attack_technique_ids: string[]
  detection_strategy_ids: string[]
  review_status: 'suggested' | 'accepted' | 'rejected'
  review_note: string | null
  investigation_id: string | null
}
export interface TeamAssessment {
  id: string
  team_id: string
  item_id: string
  version: number
  status: string
  stale: boolean
  error: string | null
  generated_at: string | null
  context_version: number
  result: {
    relevance_score: number
    relevance_reasons: string[]
    information_gaps: string[]
    hunts: HuntSuggestion[]
    evidence_selection?: { selection: 'article_prefix' | 'verified_section_passages'; selected_passages: { start: number; end: number }[]; coverage?: ExtractionCoverage | null } | null
  } | null
}
export interface TeamAssessmentResponse {
  assessment: TeamAssessment | null
  ai_enabled: boolean
  configured: boolean
  hunt_suggestions_enabled: boolean
  can_generate: boolean
}
