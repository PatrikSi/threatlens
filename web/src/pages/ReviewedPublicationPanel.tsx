import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, apiDownload, apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { accessibleQueryData } from '../api/queryData'
import { useCurrentUser } from '../hooks/useCurrentUser'
import type { ArticleExportFilters, ArticleExportTLPMarking } from '../types/exports'
import type { IndicatorPublication, IndicatorPublicationPage, ReviewedPublicationPreview, ReviewedIndicator } from '../types/indicatorPublications'
import { hasRequiredPermissions } from '../workspace/workspaceModel'
import { createSecureRequestId } from '../utils/secureRandomId'
import { AssessmentTeamPicker } from './AssessmentTeamPicker'
import { triggerBrowserDownload } from './exportPageModel'
import { PublicationConsumers } from './PublicationConsumers'
import { ReviewedIndicatorEvidenceDialog } from './ReviewedIndicatorEvidenceDialog'
import { TEAM_BUTTON } from './teamPresentation'

const INPUT = 'rounded border border-slate/30 bg-white p-2 dark:bg-[#072019]'

export function ReviewedPublicationPanel({ filters }: { filters: ArticleExportFilters | null }) {
  const [open, setOpen] = useState(false)
  return <section className="space-y-3 rounded-lg border border-slate/25 p-4">
    <button type="button" className="text-left font-semibold" aria-expanded={open} onClick={() => setOpen(!open)}>
      Reviewed team publications {open ? '▾' : '▸'}
    </button>
    <p className="text-sm">Publish current, unsuppressed indicators explicitly assessed as malicious by your team. Raw research exports remain available above.</p>
    {open && <PublicationTeamSelection filters={filters} />}
  </section>
}

function PublicationTeamSelection({ filters }: { filters: ArticleExportFilters | null }) {
  const user = useCurrentUser()
  const identity = accessibleQueryData(user)
  const [team, setTeam] = useState('')
  const permissions = identity?.access?.permissions ?? []
  if (!hasRequiredPermissions(permissions, ['read:items', 'read:teams'])) {
    return <p role="status">Current article and team access is required to view reviewed publications.</p>
  }
  return <div className="space-y-3">
    <AssessmentTeamPicker value={team} onChange={setTeam} label="Publication team" />
    {team && <PublicationWorkspace key={team} team={team} filters={filters}
      writable={!user.isError && hasRequiredPermissions(permissions, ['write:teams'])} />}
  </div>
}

function PublicationWorkspace({ team, filters, writable }: {
  team: string; filters: ArticleExportFilters | null; writable: boolean
}) {
  const client = useQueryClient()
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  const [format, setFormat] = useState<'stix' | 'misp'>('stix')
  const [marking, setMarking] = useState<ArticleExportTLPMarking>('TLP:AMBER')
  const [distribution, setDistribution] = useState(0)
  const [cursor, setCursor] = useState<string | null>(null)
  const [approved, setApproved] = useState(false)
  const [evidence, setEvidence] = useState<{ preview: ReviewedPublicationPreview; row: ReviewedIndicator } | null>(null)
  const requestIdentity = useRef<{ body: string; id: string } | null>(null)
  const scope = JSON.stringify({ team, filters })
  const endpoint = `/teams/${team}/indicator-publications`
  const preview = useMutation({
    mutationFn: async (input: { scope: string; filters: ArticleExportFilters }) => ({
      scope: input.scope,
      value: await apiFetch<ReviewedPublicationPreview>(`${endpoint}/preview`, {
        method: 'POST', body: JSON.stringify({ filters: input.filters }),
      }),
    }),
  })
  const publish = useMutation({
    mutationFn: async (body: object) => {
      const signature = JSON.stringify(body)
      if (requestIdentity.current?.body !== signature) requestIdentity.current = { body: signature, id: createSecureRequestId() }
      return apiFetch<IndicatorPublication>(endpoint, {
        method: 'POST', body: JSON.stringify({ ...body, idempotency_key: requestIdentity.current.id }),
      })
    },
    onSuccess: () => {
      if (!mounted.current) return
      requestIdentity.current = null
      setApproved(false)
      void client.invalidateQueries({ queryKey: ['reviewed-publications', team] })
    },
  })
  const history = useQuery({
    queryKey: ['reviewed-publications', team, cursor],
    queryFn: ({ signal }) => apiFetch<IndicatorPublicationPage>(
      `${endpoint}?limit=20${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`, { signal },
    ),
  })
  const download = useMutation({
    mutationFn: (id: string) => apiDownload(`${endpoint}/${id}/download`),
    onSuccess: (file) => {
      if (!mounted.current) return
      triggerBrowserDownload(file.blob, file.filename ?? 'reviewed-intelligence.json')
      void client.invalidateQueries({ queryKey: ['reviewed-publications', team] })
    },
  })
  const withdraw = useMutation({
    mutationFn: (row: IndicatorPublication) => apiFetch<IndicatorPublication>(`${endpoint}/${row.id}/withdraw`, {
      method: 'POST', body: JSON.stringify({ expected_revision: row.revision }),
    }),
    onSuccess: () => {
      if (mounted.current) void client.invalidateQueries({ queryKey: ['reviewed-publications', team] })
    },
  })
  const [withdrawal, setWithdrawal] = useState<IndicatorPublication | null>(null)
  const latestMutations = useRef({ preview, publish, download, withdraw })
  latestMutations.current = { preview, publish, download, withdraw }
  const accessLost = [preview.error, publish.error, history.error]
    .some((error) => error instanceof ApiError && [401, 403, 404].includes(error.status))
    || [download.error, withdraw.error].some((error) => error instanceof ApiError && [401, 403].includes(error.status))
  const currentPreview = currentPublicationPreview(preview, scope, accessLost)
  const page = accessibleQueryData(history)
  const error = preview.error ?? publish.error ?? history.error ?? download.error ?? withdraw.error
  useEffect(() => {
    if (!accessLost) return
    setApproved(false)
    if (preview.isSuccess) preview.reset()
  }, [accessLost, preview])
  function clearCompletedErrors() {
    // This closure captures the errors visible when recovery began. A later
    // refresh must never reset a newer request, result, or failure.
    const captured = { preview, publish, download, withdraw }
    for (const name of ['preview', 'publish', 'download', 'withdraw'] as const) {
      const current = latestMutations.current[name]
      if (current.isError && current.error === captured[name].error) current.reset()
    }
  }
  async function refresh() {
    setApproved(false)
    const result = await history.refetch()
    if (!mounted.current || result.isError) return
    clearCompletedErrors()
  }
  function loadPreview() {
    if (!filters) return
    setEvidence(null)
    setApproved(false)
    preview.mutate({ scope, filters }, { onSuccess: clearCompletedErrors })
  }
  function save() {
    if (!currentPreview || !filters) return
    const body = { filters, preview_fingerprint: currentPreview.fingerprint, format, marking, misp_distribution: distribution }
    publish.mutate(body)
  }
  return <div className="space-y-3">
    <fieldset disabled={publish.isPending} className="space-y-3">
      <legend className="font-semibold">Review the current article filters</legend>
      <p className="text-sm">At most 100 articles and 250 approved indicators per publication. Stale reviews, examples and team suppressions are excluded. Evidence stays pinned to the reviewed revisions.</p>
      <button type="button" className={TEAM_BUTTON} disabled={!filters || preview.isPending}
        onClick={loadPreview}>
        {preview.isPending ? 'Loading reviewed indicators…' : 'Preview reviewed indicators'}
      </button>
      {preview.data && preview.data.scope !== scope && <p role="status">The article filters changed. Refresh the reviewed preview before publishing.</p>}
      {currentPreview && <>
        <p role="status">{currentPreview.indicators.length} reviewed indicators in {currentPreview.matched_articles} articles; {currentPreview.excluded_or_unreviewed} observations excluded or unreviewed.</p>
        <ReviewedIndicatorsTable preview={currentPreview}
          onReview={(row) => setEvidence({ preview: currentPreview, row })} />
        <div className="flex flex-wrap gap-3">
          <label>Reviewed format <select className={INPUT} value={format} onChange={(event) => { setApproved(false); setFormat(event.target.value as 'stix' | 'misp') }}>
            <option value="stix">STIX 2.1</option><option value="misp">MISP</option>
          </select></label>
          <label>Handling marking <select className={INPUT} value={marking} onChange={(event) => { setApproved(false); setMarking(event.target.value as ArticleExportTLPMarking) }}>
            {['none', 'TLP:WHITE', 'TLP:GREEN', 'TLP:AMBER', 'TLP:RED'].map((value) => <option key={value}>{value}</option>)}
          </select></label>
          {format === 'misp' && <label>MISP distribution <select className={INPUT} value={distribution} onChange={(event) => { setApproved(false); setDistribution(Number(event.target.value)) }}>
            <option value={0}>Your organization only</option><option value={1}>This community only</option>
            <option value={2}>Connected communities</option><option value={3}>All communities</option>
          </select></label>}
        </div>
        <label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={approved} onChange={(event) => setApproved(event.target.checked)} />
          I reviewed this exact selection and its handling marking. MISP detection attributes will be enabled; consumers must apply later withdrawals and expiry.
        </label>
        <button type="button" className={TEAM_BUTTON} disabled={!writable || !approved || !currentPreview.indicators.length || preview.isPending} onClick={save}>
          {publish.isPending ? 'Saving publication…' : 'Approve reviewed publication'}
        </button>
      </>}
    </fieldset>
    {evidence && currentPreview === evidence.preview && <ReviewedIndicatorEvidenceDialog
      teamId={team} previewFingerprint={currentPreview.fingerprint} reviewed={evidence.row}
      onClose={() => setEvidence(null)} onRefresh={loadPreview} />}
    {error && <p role="alert">{resolveApiErrorMessage(error, 'The publication request could not be completed. Retry or refresh its preview.')}</p>}
    {publish.isSuccess && <p role="status">Publication saved. Download it from the list below.</p>}
    <div className="space-y-2">
      {writable && !accessLost && <PublicationConsumers teamId={team} publications={page?.items ?? []} />}
      <h3 className="font-semibold">Publication history</h3>
      <p className="text-xs">Withdrawals are checked periodically and on download. Reimport updated artifacts to apply revoked/deleted indicators; a downloaded file cannot update itself.</p>
      <button className={TEAM_BUTTON} disabled={history.isFetching} onClick={() => void refresh()}>Refresh publications</button>
      {history.isPending && <p role="status">Loading publications…</p>}
      {page?.items.length === 0 && <p>No accessible reviewed publications on this page.</p>}
      {page?.items.map((row) => <div key={row.id} className="flex flex-wrap items-center justify-between gap-2 rounded border p-2 text-sm">
        <div>
          <p>{row.format.toUpperCase()} · {row.status.replaceAll('_', ' ')} · revision {row.revision} · {row.withdrawn_count}/{row.indicator_count} withdrawn · {new Date(row.created_at).toLocaleString()}</p>
          <p className="break-all text-xs">Publication ID: <code className="select-all">{row.id}</code></p>
        </div>
        <button className={TEAM_BUTTON} disabled={download.isPending} onClick={() => download.mutate(row.id)}>Download publication</button>
        {writable && row.status !== 'withdrawn' && <button className={TEAM_BUTTON}
          disabled={withdraw.isPending} onClick={() => setWithdrawal(row)}>Withdraw publication</button>}
      </div>)}
      {withdrawal && !accessLost && <div className="rounded border border-amber-500 p-3" role="group" aria-label="Confirm publication withdrawal">
        <p>Withdraw all remaining indicators from this publication? This cannot be undone. Download and reimport the updated artifact to notify its consumers.</p>
        <button className={TEAM_BUTTON} disabled={withdraw.isPending} onClick={() => withdraw.mutate(withdrawal, {
          onSuccess: () => { if (mounted.current) setWithdrawal(null) },
        })}>Confirm withdrawal</button>
        <button className={TEAM_BUTTON} disabled={withdraw.isPending} onClick={() => setWithdrawal(null)}>Keep publication</button>
      </div>}
      <div className="flex gap-2">
        <button className={TEAM_BUTTON} disabled={!cursor || history.isFetching} onClick={() => setCursor(null)}>First page</button>
        <button className={TEAM_BUTTON} disabled={!page?.has_more || history.isFetching} onClick={() => setCursor(page?.next_cursor ?? null)}>Next publications</button>
      </div>
    </div>
  </div>
}

function ReviewedIndicatorsTable({ preview, onReview }: {
  preview: ReviewedPublicationPreview; onReview: (row: ReviewedIndicator) => void
}) {
  return <div className="max-h-80 overflow-auto" tabIndex={0} aria-label="Reviewed indicators">
    <table className="w-full text-left text-sm">
      <thead><tr><th>Indicator</th><th>Article</th><th>Review</th><th>Expiry</th></tr></thead>
      <tbody>{preview.indicators.map((row) => <tr key={`${row.item_id}:${row.ioc_id}`}>
        <td className="break-all p-2">{row.type}: {row.value}</td><td>{row.title}</td>
        <td>
          <p>Version {row.assessment_version} · {row.evidence_count} passages</p>
          <button type="button" className={TEAM_BUTTON} aria-label={`Review evidence for ${row.value}`}
            onClick={() => onReview(row)}>Review evidence</button>
        </td>
        <td>{row.expires_at ? new Date(row.expires_at).toLocaleString() : 'No expiry'}</td>
      </tr>)}</tbody>
    </table>
  </div>
}

function currentPublicationPreview(
  preview: { isSuccess: boolean; data?: { scope: string; value: ReviewedPublicationPreview } },
  scope: string,
  accessLost: boolean,
): ReviewedPublicationPreview | undefined {
  if (accessLost || !preview.isSuccess || preview.data?.scope !== scope) return undefined
  return preview.data.value
}
