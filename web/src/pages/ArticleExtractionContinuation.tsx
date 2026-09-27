import { useRef } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { createSecureRequestId } from '../utils/secureRandomId'
import type { ExtractionCoverage } from '../types/articleIntelligence'
import { TEAM_BUTTON } from './teamPresentation'

export function ArticleExtractionContinuation({ itemId, coverage, disabled }: {
  itemId: string; coverage: ExtractionCoverage; disabled: boolean
}) {
  const client = useQueryClient()
  const requestIdentity = useRef<{ revision: string; id: string } | null>(null)
  const continuation = useMutation({
    mutationFn: async (revision: string) => {
      if (requestIdentity.current?.revision !== revision) requestIdentity.current = { revision, id: createSecureRequestId() }
      return apiFetch(`/ai/articles/${itemId}/continue`, { method: 'POST',
        body: JSON.stringify({ progress_revision: revision, request_id: requestIdentity.current.id }) })
    },
    onSuccess: () => { void client.invalidateQueries({ queryKey: ['item', itemId] }) },
  })
  if (!coverage.progress_revision || coverage.uncovered_chars === 0) return null
  const accepted = continuation.isSuccess && continuation.variables === coverage.progress_revision
  return <div className="space-y-2 rounded border border-slate/25 p-3">
    <p>Authorize up to 8 additional sections and 64,000 estimated input/output tokens. Completed sections are reused; the article and provider revision must still match. Overall ceiling: 32 sections and 256,000 tokens.</p>
    <button className={TEAM_BUTTON} disabled={disabled || continuation.isPending || accepted}
      onClick={() => { if (coverage.progress_revision) continuation.mutate(coverage.progress_revision) }}>
      {continuation.isPending ? 'Authorizing sections…' : 'Authorize additional article sections'}
    </button>
    {accepted && <p role="status">Additional processing was queued. Refresh evidence to follow its progress.</p>}
    {continuation.isError && <p role="alert">{resolveApiErrorMessage(continuation.error, 'Additional sections could not be queued. Refresh evidence and retry.')}</p>}
  </div>
}
