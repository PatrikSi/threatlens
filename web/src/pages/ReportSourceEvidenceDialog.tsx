import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ApiError, apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { DialogSurface } from '../components/ConfirmDialog'

type EvidencePage = {
  report_id: string
  citation_key: string
  editorial_version: number
  source_revision: string
  evidence_text: string
  total_characters: number
  offset: number
  next_offset: number | null
}

const PAGE_SIZE = 8000
const BUTTON = 'rounded border border-slate/30 px-3 py-1.5 text-sm disabled:opacity-50'

export function ReportSourceEvidenceDialog({ reportId, citationKey, editorialVersion, onClose, onRefresh }: {
  reportId: string
  citationKey: string
  editorialVersion: number
  onClose: () => void
  onRefresh: () => void
}) {
  const [page, setPage] = useState({ offset: 0, revision: '' })
  const query = useQuery({
    queryKey: ['report-source-evidence', reportId, editorialVersion, citationKey, page],
    queryFn: ({ signal }) => {
      const params = new URLSearchParams({ editorial_version: String(editorialVersion), offset: String(page.offset), limit: String(PAGE_SIZE) })
      if (page.revision) params.set('source_revision', page.revision)
      return apiFetch<EvidencePage>(`/reports/${encodeURIComponent(reportId)}/sources/${encodeURIComponent(citationKey)}/evidence?${params}`, { signal })
    },
    gcTime: 0,
    retry: false,
    refetchOnMount: 'always',
  })
  // A failed refresh must not leave an apparently current passage on screen.
  const data = query.isError ? undefined : query.data
  const conflict = query.error instanceof ApiError && query.error.status === 409
  return <DialogSurface open title={`Retained evidence [${citationKey}]`} onClose={onClose} describeBody={false}
    description={`Report revision ${editorialVersion}. This is the passage saved for this report; publisher pages may have changed.`}
    panelClassName="max-w-3xl" ariaBusy={query.isFetching}
    footer={<>
      <button type="button" className={BUTTON} disabled={!data || query.isFetching || page.offset === 0}
        onClick={() => data && setPage({ offset: Math.max(0, page.offset - PAGE_SIZE), revision: data.source_revision })}>Previous passage</button>
      <button type="button" className={BUTTON} disabled={!data || query.isFetching || data.next_offset === null}
        onClick={() => data?.next_offset != null && setPage({ offset: data.next_offset, revision: data.source_revision })}>Next passage</button>
      <button type="button" className={BUTTON} onClick={onClose}>Close evidence</button>
    </>}>
    {query.isPending && <p role="status">Loading retained evidence…</p>}
    {query.isError && <div role="alert" className="space-y-2">
      <p>{resolveApiErrorMessage(query.error, 'Retained evidence could not be loaded.')}</p>
      {conflict
        ? <button type="button" className={BUTTON} onClick={() => { onClose(); onRefresh() }}>Refresh report</button>
        : <button type="button" className={BUTTON} onClick={() => { void query.refetch() }}>Retry evidence</button>}
    </div>}
    {data && <>
      <p role="status" className="text-xs">{data.total_characters === 0 ? 'No passage was retained for this source. The current publisher page is not a substitute for a reviewed snapshot.'
        : `Characters ${data.offset + 1}–${data.next_offset ?? data.total_characters} of ${data.total_characters}.`}</p>
      {data.evidence_text && <div role="region" aria-label="Retained source passage" tabIndex={0}
        className="max-h-[55vh] overflow-auto whitespace-pre-wrap break-words rounded border border-slate/20 p-3 focus-visible:outline focus-visible:outline-2">{data.evidence_text}</div>}
    </>}
  </DialogSurface>
}
