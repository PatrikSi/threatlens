import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { DialogSurface } from '../components/ConfirmDialog'
import type { ReviewedIndicator } from '../types/indicatorPublications'
import type { IndicatorPage } from '../types/indicators'
import { IndicatorEvidence } from './IndicatorEvidence'
import { TEAM_BUTTON } from './teamPresentation'

export function ReviewedIndicatorEvidenceDialog({ teamId, previewFingerprint, reviewed, onClose, onRefresh }: {
  teamId: string
  previewFingerprint: string
  reviewed: ReviewedIndicator
  onClose: () => void
  onRefresh: () => void
}) {
  const query = useQuery({
    queryKey: ['reviewed-indicator-evidence', teamId, previewFingerprint, reviewed.item_id, reviewed.ioc_id],
    queryFn: ({ signal }) => {
      const params = new URLSearchParams({ team_id: teamId, ioc_id: reviewed.ioc_id, page_size: '1' })
      return apiFetch<IndicatorPage>(`/items/${encodeURIComponent(reviewed.item_id)}/indicators?${params}`, { signal })
    },
    gcTime: 0,
    staleTime: 0,
    retry: false,
    refetchOnMount: 'always',
  })
  // A failed refresh cannot present its cached passages as verified evidence.
  const page = query.isError ? undefined : query.data
  const indicator = page?.items.find((entry) => entry.id === reviewed.ioc_id)
  const assessment = indicator?.assessment
  const matches = Boolean(page?.extraction_current
    && page.source_revision === reviewed.source_revision
    && page.extraction_revision === reviewed.extraction_revision
    && indicator?.type === reviewed.type && indicator.value === reviewed.value
    && !indicator.excluded && !indicator.suppressed
    && assessment?.current && !assessment.expired && assessment.verdict === 'malicious'
    && assessment.version === reviewed.assessment_version
    && assessment.source_revision === reviewed.source_revision
    && assessment.extraction_revision === reviewed.extraction_revision
    && assessment.expires_at === reviewed.expires_at)
  return <DialogSurface open title="Reviewed indicator evidence" onClose={onClose} describeBody={false}
    description="Only evidence matching the exact source, extraction and analyst review in your publication preview is shown."
    panelClassName="max-w-3xl" ariaBusy={query.isFetching}
    footer={<button type="button" className={TEAM_BUTTON} onClick={onClose}>Close evidence</button>}>
    {query.isPending && <p role="status">Loading reviewed evidence…</p>}
    {query.isError && <div role="alert" className="space-y-2">
      <p>{resolveApiErrorMessage(query.error, 'Reviewed evidence could not be loaded.')}</p>
      <button type="button" className={TEAM_BUTTON} disabled={query.isFetching}
        onClick={() => { void query.refetch() }}>Retry evidence</button>
    </div>}
    {page && !matches && <div role="status" className="space-y-2">
      <p>The evidence or analyst review changed, expired or is no longer available. Refresh the publication preview before reviewing or publishing it.</p>
      <button type="button" className={TEAM_BUTTON} onClick={() => { onClose(); onRefresh() }}>Refresh publication preview</button>
    </div>}
    {matches && indicator && <>
      <p className="break-all font-semibold">{reviewed.type}: {reviewed.value}</p>
      <p>{reviewed.title}</p>
      <p className="text-xs">Source revision {reviewed.source_revision} · extraction revision {reviewed.extraction_revision} · review version {reviewed.assessment_version}</p>
      <p className="whitespace-pre-wrap break-words">Analyst review: {assessment?.reason}</p>
      <IndicatorEvidence indicator={indicator} />
    </>}
  </DialogSurface>
}
