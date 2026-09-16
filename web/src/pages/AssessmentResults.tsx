import type { HuntSuggestion, TeamAssessment } from '../types/articleIntelligence'
import { IntelligenceList } from './ArticleEvidenceView'
import { HuntSuggestionCard } from './HuntSuggestionCard'
import { formatPublishedAt } from './dashboardPageUtils'
import { TEAM_BUTTON } from './teamPresentation'

export type HuntReviewDrafts = Record<string, { note: string; version: number }>

export function AssessmentResults({ assessment, drafts, readOnly, pending, canCreate, onNoteChange, onReview, onCreate, onReload }: {
  assessment: TeamAssessment
  drafts: HuntReviewDrafts
  readOnly: boolean
  pending: boolean
  canCreate: boolean
  onNoteChange: (hunt: HuntSuggestion, note: string) => void
  onReview: (hunt: HuntSuggestion, status: 'accepted' | 'rejected') => void
  onCreate: (hunt: HuntSuggestion) => void
  onReload: () => void
}) {
  const result = assessment.result
  if (!result) return null
  return <div className="space-y-3">
    <p className="text-sm">Team relevance: {Math.round(result.relevance_score * 100)}% · Context revision {assessment.context_version} · Generated {formatPublishedAt(assessment.generated_at)}</p>
    <IntelligenceList title="Relevance to this team" values={result.relevance_reasons} />
    {result.information_gaps.length > 0 && <IntelligenceList title="Assessment gaps" values={result.information_gaps} />}
    {result.hunts.length === 0 && <p className="text-sm">No supported hunt suggestions were generated.</p>}
    {result.hunts.map((hunt) => <HuntSuggestionCard key={hunt.id} hunt={hunt}
      note={drafts[hunt.id]?.note ?? hunt.review_note ?? ''} draftStale={Boolean(drafts[hunt.id] && drafts[hunt.id].version !== assessment.version)}
      readOnly={readOnly} pending={pending} canCreate={canCreate}
      onNoteChange={(note) => onNoteChange(hunt, note)}
      onReview={(status) => onReview(hunt, status)} onCreate={() => onCreate(hunt)} />)}
    {Object.keys(drafts).length > 0 && <p className="text-xs">Unsubmitted review notes are retained in this signed-in session when you navigate or collapse the article.</p>}
    {Object.keys(drafts).length > 0 && <button className={TEAM_BUTTON} disabled={pending} onClick={onReload}>Reload saved reviews</button>}
  </div>
}
