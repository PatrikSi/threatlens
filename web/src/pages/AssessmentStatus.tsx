import type { TeamAssessmentResponse } from '../types/articleIntelligence'

export function AssessmentStatus({ data, canWrite }: { data: TeamAssessmentResponse; canWrite: boolean }) {
  const assessment = data.assessment
  const busy = ['queued', 'running'].includes(assessment?.status ?? '')
  return <>
    {!data.ai_enabled && <p className="text-sm">AI is disabled on this server. Saved assessments remain available.</p>}
    {data.ai_enabled && !data.configured && <p className="text-sm">The article AI provider is not configured. An administrator can configure it in AI settings.</p>}
    {!data.hunt_suggestions_enabled && <p className="text-sm">Suggested hunt cards are off in AI settings. Existing results remain available for review.</p>}
    {!canWrite && <p className="text-sm">Your team access currently allows reading assessments. Generating and reviewing suggestions requires verified team write access.</p>}
    {!assessment && <p className="text-sm">No assessment has been generated for this team and article.</p>}
    {busy && <p role="status">Team assessment {assessment?.status}. This view updates as processing completes.</p>}
    {assessment?.stale && <p role="status" className="text-sm text-amber-700 dark:text-amber-300">The article or team context has changed. Generate a new assessment before reviewing or creating investigations.</p>}
    {assessment?.status === 'skipped' && <p role="status">This assessment was skipped or canceled. {assessment.error || 'Refresh the article and team context before requesting another assessment.'}</p>}
    {assessment?.status === 'error' && <p role="alert">Assessment failed: {assessment.error || 'No provider result was published. Try again.'}</p>}
  </>
}
