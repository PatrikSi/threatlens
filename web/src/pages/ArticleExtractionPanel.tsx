import { useQueryClient } from '@tanstack/react-query'
import { ArticleExtractionContinuation } from './ArticleExtractionContinuation'
import { Link } from 'react-router-dom'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { accessibleQueryData } from '../api/queryData'
import type { ItemDetail } from '../types/items'
import { hasRequiredPermissions } from '../workspace/workspaceModel'
import { ArticleEvidenceView } from './ArticleEvidenceView'
import { ArticleExtractionCoverage } from './ArticleExtractionCoverage'
import { TEAM_BUTTON } from './teamPresentation'

export function ArticleExtractionPanel({ detail }: { detail: ItemDetail }) {
  const user = useCurrentUser()
  const client = useQueryClient()
  const identity = accessibleQueryData(user)
  const extraction = detail.ai_insight?.structured_extraction
  const coverage = detail.ai_insight?.extraction_progress ?? extraction?.coverage
  const enabled = identity?.features.ai_structured_extraction_enabled
  if (!extraction && !enabled) return null
  const status = detail.ai_insight?.status
  const pending = status === 'pending' || status === 'queued' || status === 'running'
  const canReprocess = identity?.role === 'admin' && hasRequiredPermissions(identity.access?.permissions ?? [], ['read:ai', 'write:ai'])
  return <>
    {extraction ? <ArticleEvidenceView extraction={extraction} stale={detail.ai_insight?.structured_extraction_stale ?? false} /> : <section aria-label="Shared article evidence" className="tl-surface-muted mt-3 space-y-2 rounded p-3">
      <h3 className="font-semibold">Shared article evidence</h3>
      <p className="text-sm">No structured extraction has been published for this article. An AI administrator can reprocess it using the Operations tab in AI settings.</p>
    </section>}
    <div className="mt-2 space-y-2 text-sm">
      {detail.ai_insight?.extraction_progress && status !== 'ready' && <div>
        <h4 className="font-semibold">Most recent section processing</h4>
        <ArticleExtractionCoverage coverage={detail.ai_insight.extraction_progress} />
      </div>}
      {pending && <p role="status">Article enrichment is {status}. Refresh the article to check for updated evidence.</p>}
      {status === 'error' && <p role="alert">The latest article enrichment attempt failed: {detail.ai_insight?.error || 'No new result was published.'}</p>}
      {canReprocess && coverage && <ArticleExtractionContinuation key={detail.id} itemId={detail.id} coverage={coverage} disabled={user.isError || pending} />}
      <div className="flex flex-wrap gap-3">
        <button className={TEAM_BUTTON} disabled={user.isError} onClick={() => void client.invalidateQueries({ queryKey: ['item', detail.id] })}>Refresh article evidence</button>
        {canReprocess && <Link className="font-semibold text-cyan underline" to="/settings/ai">Open AI settings</Link>}
      </div>
    </div>
  </>
}
