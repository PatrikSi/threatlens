import type { QueryClient } from '@tanstack/react-query'
import type { HuntSuggestion, TeamAssessment } from '../types/articleIntelligence'

export const HUNT_DRAFT_PREFIX = 'team-assessment-drafts'

export interface HuntReviewDraft {
  note: string
  version: number
  baseline: string
}
export type HuntReviewDrafts = Record<string, HuntReviewDraft>

// Pin both the generated evidence revision and this hunt's saved review. A review
// of another hunt may change the aggregate version without changing this baseline.
export function huntDraftBaseline(assessment: TeamAssessment, hunt: HuntSuggestion): string {
  return JSON.stringify([
    assessment.id, assessment.context_version, assessment.generated_at,
    hunt.id, hunt.title, hunt.hypothesis, hunt.rationale, hunt.required_logs,
    hunt.benign_explanations, hunt.information_gaps, hunt.attack_technique_ids,
    hunt.detection_strategy_ids, hunt.review_status, hunt.review_note,
    hunt.investigation_id, hunt.evidence.map(({ source, quote, start, end }) => [source, quote, start, end]),
  ])
}

export function reconcileHuntDrafts(drafts: HuntReviewDrafts, assessment: TeamAssessment): HuntReviewDrafts {
  let next = drafts
  for (const hunt of assessment.result?.hunts ?? []) {
    const draft = drafts[hunt.id]
    if (draft && draft.version < assessment.version && draft.baseline === huntDraftBaseline(assessment, hunt)) {
      if (next === drafts) next = { ...drafts }
      next[hunt.id] = { ...draft, version: assessment.version }
    }
  }
  return next
}

export function huntDraftEntries(client: QueryClient) {
  return client.getQueriesData<HuntReviewDrafts>({ queryKey: [HUNT_DRAFT_PREFIX] })
    .filter((entry) => entry[1] && Object.keys(entry[1]).length > 0)
}

export function updateHuntDraft(drafts: HuntReviewDrafts, assessment: TeamAssessment, hunt: HuntSuggestion, note: string): HuntReviewDrafts {
  const next = { ...drafts }
  if (note === (hunt.review_note ?? '')) delete next[hunt.id]
  else next[hunt.id] = {
    note,
    version: drafts[hunt.id]?.version ?? assessment.version,
    baseline: drafts[hunt.id]?.baseline ?? huntDraftBaseline(assessment, hunt),
  }
  return next
}

export function settleHuntDrafts(drafts: HuntReviewDrafts, submitted: HuntReviewDrafts, action: { kind: string; huntId?: string }, assessment: TeamAssessment | null): HuntReviewDrafts {
  const next = { ...drafts }
  for (const [id, snapshot] of Object.entries(submitted)) {
    if ((action.kind === 'generate' || action.kind === 'review' && id === action.huntId)
      && next[id]?.note === snapshot.note && next[id]?.baseline === snapshot.baseline) delete next[id]
  }
  return assessment ? reconcileHuntDrafts(next, assessment) : next
}
