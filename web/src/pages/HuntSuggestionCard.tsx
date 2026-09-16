import { Link } from 'react-router-dom'
import type { HuntSuggestion } from '../types/articleIntelligence'
import { EvidencePassages, IntelligenceList } from './ArticleEvidenceView'
import { TEAM_BUTTON } from './teamPresentation'

export function HuntSuggestionCard({ hunt, note, draftStale, readOnly, pending, canCreate, onNoteChange, onReview, onCreate }: {
  hunt: HuntSuggestion
  note: string
  draftStale: boolean
  readOnly: boolean
  pending: boolean
  canCreate: boolean
  onNoteChange: (note: string) => void
  onReview: (status: 'accepted' | 'rejected') => void
  onCreate: () => void
}) {
  return <article aria-label={`Hunt suggestion: ${hunt.title}`} className="space-y-3 rounded border border-slate/30 p-3">
    <div className="flex flex-wrap items-start justify-between gap-2">
      <h4 className="font-semibold">{hunt.title}</h4>
      <span className="tl-chip capitalize">{hunt.review_status}</span>
    </div>
    <div><h5 className="text-sm font-semibold">Hypothesis</h5><p className="mt-1 text-sm">{hunt.hypothesis}</p></div>
    <div><h5 className="text-sm font-semibold">Why this matters to the team</h5><p className="mt-1 text-sm">{hunt.rationale}</p></div>
    <EvidencePassages evidence={hunt.evidence} />
    <IntelligenceList title="Required telemetry" values={hunt.required_logs} />
    <IntelligenceList title="Expected benign explanations" values={hunt.benign_explanations} />
    <IntelligenceList title="Information gaps" values={hunt.information_gaps} />
    {(hunt.attack_technique_ids.length > 0 || hunt.detection_strategy_ids.length > 0) && <div className="flex flex-wrap gap-3 text-sm">
      {hunt.attack_technique_ids.filter((id) => /^T\d{4}(\.\d{3})?$/.test(id)).map((id) => <a key={id} className="font-semibold text-cyan underline" href={`https://attack.mitre.org/techniques/${id.replace('.', '/')}/`} target="_blank" rel="noreferrer">ATT&amp;CK {id}</a>)}
      {hunt.detection_strategy_ids.filter((id) => /^DET\d{4}$/.test(id)).map((id) => <a key={id} className="font-semibold text-cyan underline" href={`https://attack.mitre.org/detectionstrategies/${id}/`} target="_blank" rel="noreferrer">Detection strategy {id}</a>)}
    </div>}
    <fieldset disabled={readOnly || pending} className="space-y-2">
      <label className="block text-sm">Review note for {hunt.title}
        <textarea className="mt-1 w-full rounded border border-slate/30 bg-white p-2 dark:bg-[#072019]" rows={2} maxLength={2000} value={note} onChange={(event) => onNoteChange(event.target.value)} />
      </label>
      {draftStale && <p role="status" className="text-xs">This review draft uses an earlier assessment revision. Reload saved reviews before submitting it.</p>}
      <div className="flex flex-wrap gap-2">
        <button className={TEAM_BUTTON} disabled={draftStale} onClick={() => onReview('accepted')}>Accept suggestion</button>
        <button className={TEAM_BUTTON} disabled={draftStale} onClick={() => onReview('rejected')}>Reject suggestion</button>
      </div>
    </fieldset>
    {hunt.investigation_id ? <div className="space-y-1">
      <Link className="font-semibold text-cyan underline" to={`/investigations/${hunt.investigation_id}`}>Open investigation</Link>
      <p className="text-xs">The investigation retains the evidence and review captured when it was created.</p>
    </div> : hunt.review_status === 'accepted' && <button className={TEAM_BUTTON}
      disabled={readOnly || pending || !canCreate || draftStale || note !== (hunt.review_note ?? '')}
      onClick={onCreate}>Create team investigation</button>}

    {hunt.review_status === 'accepted' && !canCreate && !hunt.investigation_id && <p className="text-xs">Creating an investigation requires investigation write access.</p>}
  </article>
}
