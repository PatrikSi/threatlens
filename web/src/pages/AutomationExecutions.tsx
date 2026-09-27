import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../api/client'
import { accessibleQueryData } from '../api/queryData'
import { resolveApiErrorMessage } from '../api/errors'
import { DialogSurface } from '../components/ConfirmDialog'

type Execution = {
  id: string
  action_id: string
  status: string
  external_job_id: string | null
  sequence: number
  findings: string | null
  investigation_note_id: string | null
  policy_state: string
  policy_revision: number
  policy_acknowledged_revision: number
  updated_at: string
}
type ExecutionPage = { items: Execution[]; next_cursor: string | null }
type Investigation = {
  id: string
  title: string
  version: number
  status: string
}
const BUTTON =
  'rounded border border-slate/30 px-3 py-1.5 text-sm disabled:opacity-50'

function Findings({
  execution,
  writable,
  onSaved,
}: {
  execution: Execution
  writable: boolean
  onSaved: () => void
}) {
  const [selection, setSelection] = useState('')
  const [page, setPage] = useState(1)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const query = useQuery({
    queryKey: ['automation', 'investigations', page],
    queryFn: ({ signal }) =>
      apiFetch<{ investigations: Investigation[]; total: number }>(
        `/investigations?page=${page}&page_size=25`,
        { signal },
      ),
    enabled: writable && !execution.investigation_note_id,
  })
  const data = accessibleQueryData(query)
  const selected = data?.investigations.find((entry) => entry.id === selection)
  async function attach() {
    if (!selected || busy) return
    setBusy(true)
    setError('')
    try {
      await apiFetch(
        `/notifications/automation/executions/${execution.id}/findings`,
        {
          method: 'POST',
          body: JSON.stringify({
            investigation_id: selected.id,
            expected_investigation_version: selected.version,
            expected_sequence: execution.sequence,
          }),
        },
      )
      onSaved()
    } catch (cause) {
      setError(
        resolveApiErrorMessage(
          cause,
          'Findings could not be attached. Reload the investigation before retrying.',
        ),
      )
      void query.refetch()
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="space-y-2">
      <pre className="whitespace-pre-wrap break-words text-sm">
        {execution.findings}
      </pre>
      {execution.investigation_note_id ? (
        <p role="status">Findings attached to an investigation.</p>
      ) : (
        writable && (
          <>
            <label className="block text-sm">
              Attach findings to an existing investigation
              <select
                className="ml-2 max-w-full rounded border p-1 dark:bg-[#072019]"
                value={selection}
                disabled={busy || query.isError}
                onChange={(event) => setSelection(event.target.value)}
              >
                <option value="">Select investigation</option>
                {data?.investigations
                  .filter((entry) => entry.status !== 'archived')
                  .map((entry) => (
                    <option value={entry.id} key={entry.id}>
                      {entry.title}
                    </option>
                  ))}
              </select>
            </label>
            <div className="flex flex-wrap gap-2">
              <button
                className={BUTTON}
                disabled={busy || page === 1}
                onClick={() => {
                  setPage(page - 1)
                  setSelection('')
                }}
              >
                Previous investigations
              </button>
              <button
                className={BUTTON}
                disabled={busy || !data || page * 25 >= data.total}
                onClick={() => {
                  setPage(page + 1)
                  setSelection('')
                }}
              >
                Next investigations
              </button>
              <button
                className={BUTTON}
                disabled={busy || !selected || query.isError}
                onClick={() => void attach()}
              >
                {busy ? 'Attaching…' : 'Attach findings'}
              </button>
            </div>
            {query.isError && (
              <p role="alert">
                {resolveApiErrorMessage(
                  query.error,
                  'Investigations could not be loaded.',
                )}{' '}
                <button className={BUTTON} onClick={() => void query.refetch()}>
                  Retry investigations
                </button>
              </p>
            )}
          </>
        )
      )}
      {error && <p role="alert">{error}</p>}
    </div>
  )
}

export function AutomationExecutions({
  onClose,
  writable,
  webhookId,
}: {
  onClose: () => void
  writable: boolean
  webhookId?: string
}) {
  const [includeArchived, setIncludeArchived] = useState(false)
  const [cursors, setCursors] = useState<(string | null)[]>([null])
  const cursor = cursors[cursors.length - 1]
  const query = useQuery({
    queryKey: [
      'notifications',
      'automation-executions',
      cursor,
      includeArchived,
      webhookId,
    ],
    queryFn: ({ signal }) =>
      apiFetch<ExecutionPage>(
        `/notifications/automation/executions?limit=25${webhookId ? `&webhook_id=${encodeURIComponent(webhookId)}` : ''}${includeArchived ? '&include_archived=true' : ''}${cursor ? `&after=${encodeURIComponent(cursor)}` : ''}`,
        { signal },
      ),
    refetchInterval: 30_000,
  })
  const data = accessibleQueryData(query)
  return (
    <DialogSurface
      open
      title="External automation executions"
      description="HTTP delivery only confirms transport. Receiver callbacks report execution. Unknown means the receiver has not confirmed a result; do not launch the same action again. Policy withdrawals preserve completed hunt history."
      onClose={onClose}
      panelClassName="max-w-4xl"
      describeBody={false}
    >
      <div className="space-y-3">
        <label className="flex gap-2">
          <input
            type="checkbox"
            checked={includeArchived}
            onChange={(event) => {
              setIncludeArchived(event.target.checked)
              setCursors([null])
            }}
          />
          Include archived execution history
        </label>
        {query.isPending && <p role="status">Loading executions…</p>}
        {query.isError && (
          <p role="alert">
            {resolveApiErrorMessage(
              query.error,
              'Executions could not be loaded.',
            )}{' '}
            <button className={BUTTON} onClick={() => void query.refetch()}>
              Retry executions
            </button>
          </p>
        )}
        {data?.items.length === 0 && (
          <p>
            No accessible executions on this page. Typed automation deliveries
            appear here after routing.
          </p>
        )}
        {data?.items.map((entry) => (
          <article
            className="space-y-2 rounded border border-slate/30 p-3"
            key={entry.id}
          >
            <h3 className="font-semibold">
              {entry.external_job_id ?? 'Awaiting receiver acknowledgement'} ·{' '}
              {entry.status}
            </h3>
            <p className="break-all text-xs">Action {entry.action_id}</p>
            <p className="text-sm">
              Policy: {entry.policy_state}
              {entry.policy_revision > entry.policy_acknowledged_revision
                ? ' (receiver acknowledgement pending)'
                : ''}{' '}
              · Receiver revision: {entry.sequence} · Updated{' '}
              {new Date(entry.updated_at).toLocaleString()}
            </p>
            {entry.findings && (
              <Findings
                key={`${entry.id}:${entry.sequence}`}
                execution={entry}
                writable={writable}
                onSaved={() => void query.refetch()}
              />
            )}
          </article>
        ))}
        <div className="flex gap-2">
          <button
            className={BUTTON}
            disabled={cursors.length === 1 || query.isFetching}
            onClick={() => setCursors((value) => value.slice(0, -1))}
          >
            Previous executions
          </button>
          <button
            className={BUTTON}
            disabled={!data?.next_cursor || query.isFetching || query.isError}
            onClick={() =>
              data?.next_cursor &&
              setCursors((value) => [...value, data.next_cursor])
            }
          >
            Next executions
          </button>
          <button
            className={BUTTON}
            disabled={query.isFetching}
            onClick={() => void query.refetch()}
          >
            Refresh executions
          </button>
        </div>
        <p className="text-xs">
          Receivers must poll and acknowledge /notifications/automation/updates
          to apply withdrawals and replacements. A successful original delivery
          is not acknowledgement of later policy changes.
        </p>
      </div>
    </DialogSurface>
  )
}
