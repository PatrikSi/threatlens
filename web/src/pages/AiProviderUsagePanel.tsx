import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { AIProviderUsageResponse } from '../types/aiProviderUsage'
import { Panel } from './aiSettingsSupport'
import { formatTimestamp } from './aiSettingsUtils'

const PAGE_SIZE = 25
const BUTTON_CLASS = 'rounded border border-slate/30 px-3 py-2 text-sm font-semibold disabled:opacity-50 dark:border-cyan-900/40'

function latency(value: number | null): string {
  return value == null ? 'No measurement' : `${value.toLocaleString(undefined, { maximumFractionDigits: 2 })} ms`
}

export function AiProviderUsagePanel({ days }: { days: number }) {
  const [page, setPage] = useState(0)
  const query = useQuery({
    queryKey: ['ai', 'ops', 'providers', days, page],
    queryFn: ({ signal }) => apiFetch<AIProviderUsageResponse>(
      `/ai/ops/providers?days=${days}&limit=${PAGE_SIZE}&offset=${page * PAGE_SIZE}`, { signal },
    ),
    staleTime: 15_000,
  })
  const data = query.data
  const start = data?.items.length ? data.offset + 1 : 0
  const end = data?.items.length ? data.offset + data.items.length : 0
  const hasNext = Boolean(data && data.offset + data.items.length < data.total)

  return (
    <Panel title="Provider usage" subtitle="Recorded calls by provider profile, version and model. Historical attribution remains after profile changes or deletion.">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-slate dark:text-white/60">
          Tokens reflect recorded usage; missing usage is counted separately. Latency covers successful calls only.
        </p>
        <button type="button" className={BUTTON_CLASS} disabled={query.isFetching} onClick={() => { void query.refetch() }}>
          Refresh provider usage
        </button>
      </div>
      {query.isError && (
        <p role="alert" className="mb-3 text-sm text-red-700 dark:text-red-300">
          {resolveApiErrorMessage(query.error, 'Provider usage could not be loaded.')}
          {data ? ' Showing previously loaded records for this page.' : ' Use Refresh provider usage to retry.'}
        </p>
      )}
      <div aria-busy={query.isFetching}>
        {query.isLoading && !data && <p role="status" className="py-4 text-sm">Loading provider usage…</p>}
        {data && !data.items.length && (
          <p className="py-4 text-sm">
            {data.total ? 'There are no records on this page. Return to the first page or refresh.' : 'No provider usage recorded in this time window.'}
          </p>
        )}
        {Boolean(data?.items.length) && (
          <div role="region" aria-label="Provider usage records" tabIndex={0} className="overflow-x-auto rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-700">
            <table className="min-w-[1050px] w-full text-left text-sm">
              <caption className="sr-only">Provider usage for the last {days} days</caption>
              <thead className="text-xs uppercase text-slate dark:text-white/55">
                <tr>
                  {['Provider and version', 'Model', 'Call outcomes', 'Recorded tokens', 'Successful latency', 'Failure categories', 'Last call'].map((title) => (
                    <th key={title} scope="col" className="px-2 py-2">{title}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data?.items.map((row) => (
                  <tr key={JSON.stringify([row.provider_id, row.provider_version, row.provider_name, row.model])} className="border-t border-slate/15 align-top dark:border-cyan-900/30">
                    <td className="max-w-56 px-2 py-3">
                      <p className="break-words font-semibold">{row.provider_name}</p>
                      <p>{row.provider_version == null ? 'Version not recorded' : `Version ${row.provider_version}`}</p>
                      {row.provider_id && <code className="break-all text-xs text-slate dark:text-white/60">{row.provider_id}</code>}
                    </td>
                    <td className="max-w-48 break-all px-2 py-3">{row.model}</td>
                    <td className="px-2 py-3">
                      <p>{row.total_requests.toLocaleString()} total</p>
                      <p>{row.successful_requests.toLocaleString()} successful · {row.failed_requests.toLocaleString()} failed</p>
                      <p className="text-xs">{row.not_sent_requests.toLocaleString()} not sent · {row.ambiguous_requests.toLocaleString()} uncertain outcomes</p>
                    </td>
                    <td className="px-2 py-3">
                      <p>{row.total_tokens.toLocaleString()} total</p>
                      <p className="text-xs">{row.prompt_tokens.toLocaleString()} input · {row.completion_tokens.toLocaleString()} output</p>
                      {row.unknown_token_requests > 0 && <p className="text-xs">Usage missing for {row.unknown_token_requests.toLocaleString()} {row.unknown_token_requests === 1 ? 'call' : 'calls'}</p>}
                    </td>
                    <td className="px-2 py-3">
                      <p>P95: {latency(row.p95_latency_ms)}</p>
                      <p className="text-xs">Average: {latency(row.average_latency_ms)}</p>
                    </td>
                    <td className="px-2 py-3">
                      {Object.keys(row.failure_categories).length ? (
                        <ul className="space-y-1">
                          {Object.entries(row.failure_categories).map(([category, count]) => (
                            <li key={category}>{category.replaceAll('_', ' ')}: {count.toLocaleString()}</li>
                          ))}
                        </ul>
                      ) : 'No failures'}
                    </td>
                    <td className="px-2 py-3 text-xs">{formatTimestamp(row.last_request_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
        <p role="status" aria-live="polite" className="text-xs text-slate dark:text-white/60">
          {data ? `${start}–${Math.max(start, end)} of ${data.total} provider/model records` : `Page ${page + 1}`}
        </p>
        <nav className="flex flex-wrap gap-2" aria-label="Provider usage pagination">
          <button type="button" className={BUTTON_CLASS} disabled={page === 0 || query.isFetching} onClick={() => setPage(0)}>First provider page</button>
          <button type="button" className={BUTTON_CLASS} disabled={page === 0 || query.isFetching} onClick={() => setPage((value) => Math.max(0, value - 1))}>
            Previous provider page
          </button>
          <button type="button" className={BUTTON_CLASS} disabled={!hasNext || query.isFetching} onClick={() => setPage((value) => value + 1)}>Next provider page</button>
        </nav>
      </div>
    </Panel>
  )
}
