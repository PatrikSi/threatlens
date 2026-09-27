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
  const synthesisOnly = coverage.uncovered_chars === 0 && ['failed', 'pending'].includes(coverage.synthesis_status ?? '')
  if (!coverage.progress_revision || (coverage.uncovered_chars === 0 && !synthesisOnly)) return null
  const synthesisHasBudget = coverage.reserved_tokens < coverage.token_budget
  const atCeiling = coverage.call_limit >= 32 && coverage.token_budget >= 256000
  const canRequestRecovery = atCeiling && coverage.reserved_tokens < coverage.token_budget
    && coverage.sections.some((section) => section.status === 'started')
  const accepted = continuation.isSuccess && continuation.variables === coverage.progress_revision
  return <div className="space-y-2 rounded border border-slate/25 p-3">
    {synthesisOnly
      ? <p>Retry only the combined summary after its delivery is confirmed not sent. Completed section calls are reused. This grants no additional sections or tokens and uses only the remaining authorized budget.</p>
      : canRequestRecovery
      ? <p>Retry an interrupted section only after its provider receipt is reconciled as confirmed not sent. This uses the remaining authorized token budget and grants no additional sections or tokens. Completed sections are reused; delivery history is checked before the retry is accepted.</p>
      : <p>Authorize up to 8 additional sections and 64,000 estimated input/output tokens. Completed sections are reused; the article and provider revision must still match. Overall ceiling: 32 sections and 256,000 tokens.</p>}
    <button className={TEAM_BUTTON} disabled={disabled || continuation.isPending || accepted || (synthesisOnly ? !synthesisHasBudget : atCeiling && !canRequestRecovery)}
      onClick={() => { if (coverage.progress_revision) continuation.mutate(coverage.progress_revision) }}>
      {continuation.isPending ? 'Authorizing processing…' : synthesisOnly ? 'Retry combined summary' : canRequestRecovery ? 'Retry reconciled section' : 'Authorize additional article sections'}
    </button>
    {synthesisOnly && !synthesisHasBudget && <p role="status">No authorized token budget remains for synthesis recovery. Review the verified section results.</p>}
    {!synthesisOnly && atCeiling && !canRequestRecovery && <p role="status">This article reached its authorized processing ceiling. Review the remaining source manually.</p>}
    {accepted && <p role="status">Additional processing was queued. Refresh evidence to follow its progress.</p>}
    {continuation.isError && continuation.variables === coverage.progress_revision && <p role="alert">{resolveApiErrorMessage(
      continuation.error,
      synthesisOnly
        ? 'The combined summary could not be queued. Review provider receipts and refresh evidence.'
        : canRequestRecovery
        ? 'The reconciled section could not be queued. Refresh evidence and retry.'
        : 'Additional sections could not be queued. Refresh evidence and retry.',
    )}</p>}
  </div>
}
