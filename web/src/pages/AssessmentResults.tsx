import type { HuntSuggestion, TeamAssessment } from '../types/articleIntelligence'
import { IntelligenceList } from './ArticleEvidenceView'
import { HuntSuggestionCard } from './HuntSuggestionCard'
import { formatPublishedAt } from './dashboardPageUtils'
import { TEAM_BUTTON } from './teamPresentation'
import type { HuntReviewDrafts } from './huntReviewDrafts'

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
  const retained = Object.entries(drafts).filter(([id]) => !result?.hunts.some((hunt) => hunt.id === id))
  return <div className="space-y-3">
    {retained.length > 0 && <section aria-label="Notes from earlier hunt suggestions" className="space-y-2 rounded border border-amber-400/40 p-3">
      <p className="text-sm">These hunts are no longer in the current assessment. Your unsaved notes are retained for copying; they cannot be submitted against different evidence.</p>
      {retained.map(([id, draft]) => <label key={id} className="block text-sm">Retained note for hunt {id}
        <textarea readOnly className="mt-1 w-full rounded border border-slate/30 bg-white p-2 dark:bg-[#072019]" rows={2} value={draft.note} />
      </label>)}
    </section>}
    {result && <>
    <p className="text-sm">Team relevance: {Math.round(result.relevance_score * 100)}% · Context revision {assessment.context_version} · Generated {formatPublishedAt(assessment.generated_at)}</p>
    <IntelligenceList title="Relevance to this team" values={result.relevance_reasons} />
    {result.information_gaps.length > 0 && <IntelligenceList title="Assessment gaps" values={result.information_gaps} />}
    {result.hunts.length === 0 && <p className="text-sm">No supported hunt suggestions were generated.</p>}
    {result.hunts.map((hunt) => <HuntSuggestionCard key={hunt.id} hunt={hunt}
      note={drafts[hunt.id]?.note ?? hunt.review_note ?? ''} draftStale={Boolean(drafts[hunt.id] && drafts[hunt.id].version !== assessment.version)}
      readOnly={readOnly} pending={pending} canCreate={canCreate}
      onNoteChange={(note) => onNoteChange(hunt, note)}
      onReview={(status) => onReview(hunt, status)} onCreate={() => onCreate(hunt)} />)}
    </>}
    {Object.keys(drafts).length > 0 && <p className="text-xs">Unsubmitted review notes are retained in this signed-in session when you navigate or collapse the article.</p>}
    {Object.keys(drafts).length > 0 && <button className={TEAM_BUTTON} disabled={pending} onClick={onReload}>Reload saved reviews</button>}
  </div>
}
