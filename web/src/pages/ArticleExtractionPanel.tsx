import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useRef } from 'react'
import { apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { createSecureRequestId } from '../utils/secureRandomId'
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
  const requestIdentity = useRef<{ revision: string; id: string } | null>(null)
  const continuation = useMutation({
    mutationFn: async (revision: string) => {
      if (requestIdentity.current?.revision !== revision) requestIdentity.current = { revision, id: createSecureRequestId() }
      return apiFetch(`/ai/articles/${detail.id}/continue`, { method: 'POST',
        body: JSON.stringify({ progress_revision: revision, request_id: requestIdentity.current.id }) })
    },
    onSuccess: () => { void client.invalidateQueries({ queryKey: ['item', detail.id] }) },
  })
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
      {canReprocess && coverage?.progress_revision && coverage.uncovered_chars > 0 && <div className="space-y-2 rounded border border-slate/25 p-3">
        <p>Authorize up to 8 additional sections and 64,000 estimated input/output tokens. Completed sections are reused; the article and provider revision must still match. Overall ceiling: 32 sections and 256,000 tokens.</p>
        <button className={TEAM_BUTTON} disabled={user.isError || pending || continuation.isPending || continuation.isSuccess}
          onClick={() => { if (coverage.progress_revision) continuation.mutate(coverage.progress_revision) }}>
          {continuation.isPending ? 'Authorizing sections…' : 'Authorize additional article sections'}
        </button>
        {continuation.isSuccess && <p role="status">Additional processing was queued. Refresh evidence to follow its progress.</p>}
        {continuation.isError && <p role="alert">{resolveApiErrorMessage(continuation.error, 'Additional sections could not be queued. Refresh evidence and retry.')}</p>}
      </div>}
      <div className="flex flex-wrap gap-3">
        <button className={TEAM_BUTTON} disabled={user.isError} onClick={() => void client.invalidateQueries({ queryKey: ['item', detail.id] })}>Refresh article evidence</button>
        {canReprocess && <Link className="font-semibold text-cyan underline" to="/settings/ai">Open AI settings</Link>}
      </div>
    </div>
  </>
}
