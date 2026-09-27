import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { accessibleQueryData } from '../api/queryData'
import { createSecureRequestId } from '../utils/secureRandomId'
import { TEAM_BUTTON } from './teamPresentation'

type Feature = 'extraction' | 'report' | 'hunt'
interface Qualification {
  run_id: string; status: string; provider_version: number; token_budget: number; reserved_tokens: number
  features: Feature[]; error: string | null
  results: { feature: Feature | 'report_section'; state: string; contract_passed?: boolean; latency_ms?: number; total_tokens?: number; error?: string }[]
}

export function AiProviderQualification({ providerId, version }: { providerId: string; version: number }) {
  const [open, setOpen] = useState(false)
  return <section className="space-y-2 rounded border border-slate/25 p-3">
    <button type="button" className="font-semibold" aria-expanded={open} onClick={() => setOpen(!open)}>Feature qualification {open ? '▾' : '▸'}</button>
    <p className="text-sm">Test extraction, cited report findings and hunt contracts with synthetic evidence. These calls can incur provider charges. Passing contracts does not establish semantic quality or analyst approval.</p>
    {open && <QualificationControls key={`${providerId}:${version}`} providerId={providerId} version={version} />}
  </section>
}

function QualificationControls({ providerId, version }: { providerId: string; version: number }) {
  const client = useQueryClient()
  const [features, setFeatures] = useState<Feature[]>(['extraction', 'report', 'hunt'])
  const [authorized, setAuthorized] = useState(false)
  const [budget, setBudget] = useState(24000)
  const identity = useRef<{ body: string; id: string } | null>(null)
  const endpoint = `/ai/providers/${providerId}/qualifications`
  const key = ['ai-provider-qualifications', providerId]
  const history = useQuery({ queryKey: key, queryFn: ({ signal }) => apiFetch<Qualification[]>(endpoint, { signal }),
    refetchInterval: (query) => query.state.data?.some((row) => ['queued', 'running'].includes(row.status)) ? 5000 : false })
  const queue = useMutation({ mutationFn: async (body: object) => {
    const signature = JSON.stringify(body)
    if (identity.current?.body !== signature) identity.current = { body: signature, id: createSecureRequestId() }
    return apiFetch<Qualification>(endpoint, { method: 'POST', body: JSON.stringify({ ...body, request_id: identity.current.id }) })
  }, onSuccess: () => { void client.invalidateQueries({ queryKey: key }) } })
  const rows = accessibleQueryData(history)
  return <div className="space-y-3 text-sm">
    <fieldset disabled={queue.isPending || queue.isSuccess} className="space-y-2">
      <legend className="font-semibold">Authorize saved provider version {version}</legend>
      {(['extraction', 'report', 'hunt'] as const).map((feature) => <label key={feature} className="mr-4 inline-flex items-center gap-2">
        <input type="checkbox" checked={features.includes(feature)} onChange={(event) => setFeatures((current) => event.target.checked ? [...current, feature] : current.filter((value) => value !== feature))} />{feature}
      </label>)}
      <label className="block">Maximum estimated input/output tokens <input type="number" min={1024} max={32000} step={1024} value={budget}
        className="ml-2 rounded border border-slate/30 p-1 dark:bg-[#072019]" onChange={(event) => setBudget(Number(event.target.value))} /></label>
      <label className="flex items-start gap-2"><input type="checkbox" checked={authorized} onChange={(event) => setAuthorized(event.target.checked)} />I authorize the selected synthetic provider calls within this total token budget.</label>
      <button type="button" className={TEAM_BUTTON} disabled={!authorized || features.length === 0 || !Number.isInteger(budget) || budget < 1024 || budget > 32000 || history.isError}
        onClick={() => queue.mutate({ provider_version: version, features, token_budget: budget, authorize_provider_calls: true })}>
        {queue.isPending ? 'Queuing qualification…' : 'Queue feature qualification'}
      </button>
    </fieldset>
    {queue.isSuccess && <p role="status">Qualification queued. Cancel or inspect delivery receipts in AI Operations. Close and reopen this panel to authorize another run.</p>}
    {(queue.error || history.error) && <p role="alert">{resolveApiErrorMessage(queue.error ?? history.error, 'Qualification could not be loaded or queued. Refresh and retry.')}</p>}
    <button type="button" className={TEAM_BUTTON} onClick={() => void history.refetch()}>Refresh qualifications</button>
    <p>Showing the newest 20 qualifications. A timeout or unknown delivery does not authorize a repeated provider call.</p>
    {rows?.map((row) => <article className="rounded border border-slate/20 p-2" key={row.run_id}>
      <p className="font-semibold">Version {row.provider_version} · {row.status} · {row.reserved_tokens.toLocaleString()} / {row.token_budget.toLocaleString()} tokens reserved</p>
      {row.error && <p role="alert">{row.error}</p>}
      <ul>{row.results.map((result) => <li key={result.feature}>
        {result.feature === 'report_section' ? 'Report section' : result.feature}: {result.state === 'completed'
          ? result.contract_passed ? 'Contract passed; analyst quality review still required' : 'Contract failed'
          : 'Delivery pending or requires reconciliation'}
        {result.latency_ms != null ? ` · ${result.latency_ms} ms` : ''}
        {result.total_tokens != null ? ` · ${result.total_tokens} tokens` : ''}
        {result.error ? ` · ${result.error}` : ''}
      </li>)}</ul>
    </article>)}
  </div>
}
