import type { AITaskEventResponse, AITaskRunResponse } from '../types/api'
import { DialogSurface } from '../components/ConfirmDialog'
import { EmptyInline, MiniStat, Panel } from './aiSettingsSupport'
import { formatDebugPayload, formatRunTaskLabel, formatTimestamp } from './aiSettingsUtils'

export function ProviderExchangeModal({
  run,
  event,
  isLoading,
  errorMessage,
  onClose,
}: {
  run: AITaskRunResponse | null
  event: AITaskEventResponse | null
  isLoading: boolean
  errorMessage: string
  onClose: () => void
}) {
  if (!run && !isLoading && !errorMessage) {
    return null
  }

  const payload = event?.payload ?? {}
  const requestPayload = payload.request_payload
  const requestUrl = typeof payload.request_url === 'string' ? payload.request_url : null
  const responseBody = typeof payload.response_body === 'string' ? payload.response_body : null
  const responseJson = payload.response_json
  const responseJsonSummary = payload.response_json_summary
  const statusCode = typeof payload.status_code === 'number' ? payload.status_code : null
  const requestSummary = buildProviderRequestSummary(payload)
  const responseSummary = buildProviderResponseSummary(payload)

  return (
    <DialogSurface
      open
      title="Provider Exchange"
      description={run ? `${formatRunTaskLabel(run)}${run.item_title ? ` · ${run.item_title}` : ''}` : 'Loading run detail'}
      onClose={onClose}
      panelClassName="max-h-[85vh] max-w-5xl overflow-y-auto"
      bodyClassName="mt-4 space-y-4 text-sm text-slate dark:text-white/75"
    >
      {isLoading && <p>Loading request/response details...</p>}
      {!isLoading && errorMessage && <p className="text-red-600">{errorMessage}</p>}
      {!isLoading && !errorMessage && !event && <p>No provider request/response was captured for this run.</p>}

      {!isLoading && !errorMessage && event && (
        <div className="space-y-4">
          <div className="grid gap-3 md:grid-cols-3">
            <MiniStat label="Event" value={event.event_type} />
            <MiniStat label="Captured" value={formatTimestamp(event.created_at)} />
            <MiniStat label="HTTP Status" value={statusCode ?? 'n/a'} />
          </div>

          {event.message && (
            <div className="rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-sm text-red-700 dark:text-red-300">
              {event.message}
            </div>
          )}

          <div className="grid gap-4 xl:grid-cols-2">
            <Panel title="Request">
              {requestUrl && <p className="mb-3 break-all text-xs text-slate dark:text-white/60">{requestUrl}</p>}
              {requestPayload != null ? (
                <pre className="overflow-x-auto rounded-lg border border-slate/15 bg-slate/5 p-3 text-xs dark:border-cyan-900/30 dark:bg-white/[0.03]">
                  {formatDebugPayload(requestPayload)}
                </pre>
              ) : (
                <>
                  <p className="mb-3 text-xs text-slate dark:text-white/60">
                    Raw prompt payload is redacted; the persisted exchange keeps the operational request summary below.
                  </p>
                  <ExchangeSummaryList entries={requestSummary} emptyMessage="No request summary was recorded." />
                </>
              )}
            </Panel>

            <Panel title="Response">
              {responseBody ? (
                <pre className="overflow-x-auto rounded-lg border border-slate/15 bg-slate/5 p-3 text-xs dark:border-cyan-900/30 dark:bg-white/[0.03]">
                  {responseBody}
                </pre>
              ) : (
                <>
                  <p className="mb-3 text-xs text-slate dark:text-white/60">
                    Raw provider response is redacted; the persisted exchange keeps response size, status, and parsed-shape summary.
                  </p>
                  <ExchangeSummaryList entries={responseSummary} emptyMessage="No response summary was recorded." />
                </>
              )}
              {responseJson != null && (
                <>
                  <p className="mt-3 text-xs font-semibold uppercase text-slate dark:text-white/55">Parsed Response JSON</p>
                  <pre className="mt-2 overflow-x-auto rounded-lg border border-slate/15 bg-slate/5 p-3 text-xs dark:border-cyan-900/30 dark:bg-white/[0.03]">
                    {formatDebugPayload(responseJson)}
                  </pre>
                </>
              )}
              {responseJson == null && responseJsonSummary != null && (
                <>
                  <p className="mt-3 text-xs font-semibold uppercase text-slate dark:text-white/55">
                    Parsed Response Summary
                  </p>
                  <pre className="mt-2 overflow-x-auto rounded-lg border border-slate/15 bg-slate/5 p-3 text-xs dark:border-cyan-900/30 dark:bg-white/[0.03]">
                    {formatDebugPayload(responseJsonSummary)}
                  </pre>
                </>
              )}
            </Panel>
          </div>
        </div>
      )}
    </DialogSurface>
  )
}

type ExchangeSummaryEntry = {
  label: string
  value: string | number
}

function ExchangeSummaryList({ entries, emptyMessage }: { entries: ExchangeSummaryEntry[]; emptyMessage: string }) {
  if (!entries.length) {
    return <EmptyInline>{emptyMessage}</EmptyInline>
  }

  return (
    <dl className="space-y-2 rounded-lg border border-slate/15 bg-slate/5 p-3 text-xs dark:border-cyan-900/30 dark:bg-white/[0.03]">
      {entries.map((entry) => (
        <div key={entry.label} className="grid gap-1 sm:grid-cols-[140px_minmax(0,1fr)]">
          <dt className="font-semibold uppercase text-slate dark:text-white/55">{entry.label}</dt>
          <dd className="min-w-0 break-words text-slate-900 dark:text-white/80">{entry.value}</dd>
        </div>
      ))}
    </dl>
  )
}

function buildProviderRequestSummary(payload: Record<string, unknown>): ExchangeSummaryEntry[] {
  return compactExchangeEntries([
    { label: 'Host', value: stringPayloadValue(payload.request_host) },
    { label: 'Path', value: stringPayloadValue(payload.request_path) },
    { label: 'Model', value: stringPayloadValue(payload.request_model) },
    { label: 'Messages', value: numberPayloadValue(payload.request_message_count) },
    { label: 'Roles', value: arrayPayloadValue(payload.request_message_roles) },
    { label: 'Prompt chars', value: numberPayloadValue(payload.request_prompt_chars) },
    { label: 'Temperature', value: numberPayloadValue(payload.request_temperature) },
    { label: 'Max tokens', value: numberPayloadValue(payload.request_max_tokens) },
    { label: 'Attempt', value: formatAttemptSummary(payload) },
  ])
}

function buildProviderResponseSummary(payload: Record<string, unknown>): ExchangeSummaryEntry[] {
  return compactExchangeEntries([
    { label: 'Body chars', value: numberPayloadValue(payload.response_body_chars) },
    { label: 'Body SHA-256', value: stringPayloadValue(payload.response_body_sha256) },
    { label: 'Finish reason', value: stringPayloadValue(payload.finish_reason) },
    { label: 'Attempt', value: formatAttemptSummary(payload) },
  ])
}

function compactExchangeEntries(
  entries: Array<{ label: string; value: string | number | null }>,
): ExchangeSummaryEntry[] {
  return entries.filter((entry): entry is ExchangeSummaryEntry => entry.value !== null)
}

function stringPayloadValue(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null
}

function numberPayloadValue(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function arrayPayloadValue(value: unknown): string | null {
  if (!Array.isArray(value)) {
    return null
  }
  const entries = value.filter((entry): entry is string | number => typeof entry === 'string' || typeof entry === 'number')
  return entries.length ? entries.join(', ') : null
}

function formatAttemptSummary(payload: Record<string, unknown>): string | null {
  const attempt = numberPayloadValue(payload.attempt)
  const maxAttempts = numberPayloadValue(payload.max_attempts)
  if (attempt === null) {
    return null
  }
  return maxAttempts === null ? String(attempt) : `${attempt} / ${maxAttempts}`
}
