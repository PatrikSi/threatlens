import type { TeamAIContext } from '../../src/types/teams'
import type { TeamAssessmentResponse, StructuredExtraction } from '../../src/types/articleIntelligence'

export const contextFixture: TeamAIContext = { team_id: 'team-1', technology_stack: ['Linux'], priorities: ['Endpoint monitoring'], available_telemetry: ['Process events'], relevance_criteria: 'Production endpoints', version: 1, can_manage: true, created_at: null, updated_at: null }
export const assessmentFixture: TeamAssessmentResponse = {
  ai_enabled: true, configured: true, hunt_suggestions_enabled: true, can_generate: true,
  assessment: { id: 'assessment-1', team_id: 'team-1', item_id: 'item-1', version: 1, status: 'ready', stale: false, error: null, generated_at: '2026-09-16T10:00:00Z', context_version: 1,
    result: { relevance_score: 0.8, relevance_reasons: ['Affects the team stack'], information_gaps: ['Unknown prevalence'], hunts: [{ id: 'hunt-1', title: 'Review unusual services', hypothesis: 'Unexpected service creation merits review', rationale: 'The team has process events', required_logs: ['Process events'], benign_explanations: ['Approved software deployment'], information_gaps: ['Baseline unavailable'], evidence: [{ source: 'article_text', quote: 'A new service was observed.' }], attack_technique_ids: ['T1543.003'], detection_strategy_ids: ['DET0001'], review_status: 'suggested', review_note: null, investigation_id: null }] } },
}
export const extractionFixture: StructuredExtraction = {
  schema_version: 1, article_id: 'article-1', source_version: 1, article_retrieved_at: null, source_hash: 'sha', input_sha256: 'sha', truncated: true,
  entities: [
    { id: 'reference', name: 'reference.example', kind: 'indicator', description: 'A citation to a reference site.', assertion: 'reported', versions: [], indicator_role: 'reference', evidence: [{ source: 'article_text', quote: '<script>not executable</script>', start: 2, end: 36 }] },
    { id: 'actor', name: 'Possible actor', kind: 'actor', description: 'The source implies an attribution.', assertion: 'inferred', versions: [], indicator_role: null, evidence: [{ source: 'summary', quote: 'Attribution is uncertain.' }] },
  ], relationships: [{ source_entity_id: 'actor', target_entity_id: 'reference', relationship: 'references', description: 'Contextual mention.', assertion: 'inferred', evidence: [] }], information_gaps: ['No independent validation'],
}
