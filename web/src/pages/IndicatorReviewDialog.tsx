import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '../api/client'
import { accessibleQueryData } from '../api/queryData'
import { resolveApiErrorMessage } from '../api/errors'
import { DialogSurface } from '../components/ConfirmDialog'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import type {
  Indicator,
  IndicatorHistoryPage,
  IndicatorPage,
  IndicatorVerdict,
} from '../types/indicators'
import { IndicatorEvidence } from './IndicatorEvidence'

const BUTTON =
  'rounded border border-slate/30 px-3 py-1.5 text-sm disabled:opacity-50'
const INPUT =
  'mt-1 w-full rounded border border-slate/30 bg-white p-2 dark:bg-[#072019]'
const VERDICTS: IndicatorVerdict[] = [
  'unreviewed',
  'malicious',
  'benign',
  'reference',
  'example',
  'retracted',
]

function initialDraft(indicator: Indicator) {
  return {
    verdict: indicator.assessment?.verdict ?? 'unreviewed',
    reason: indicator.assessment?.reason ?? '',
    expiry: indicator.assessment?.expires_at?.slice(0, 16) ?? '',
  }
}

export function IndicatorReviewDialog({
  itemId,
  teamId,
  indicator,
  baseline,
  writable,
  onClose,
}: {
  itemId: string
  teamId: string
  indicator: Indicator
  baseline: IndicatorPage
  writable: boolean
  onClose: () => void
}) {
  const client = useQueryClient()
  const [draft, setDraft] = useState(() => initialDraft(indicator))
  const [historyOpen, setHistoryOpen] = useState(false)
  const [historyPage, setHistoryPage] = useState(1)
  const path = `/items/${itemId}/indicators/${indicator.id}/assessment`
  const dirty =
    JSON.stringify(draft) !== JSON.stringify(initialDraft(indicator))
  const discard = useUnsavedChangesWarning(
    dirty,
    'Discard this unsaved indicator review?',
  )
  const history = useQuery({
    queryKey: ['indicator-history', itemId, indicator.id, teamId, historyPage],
    queryFn: ({ signal }) =>
      apiFetch<IndicatorHistoryPage>(
        `${path}/history?team_id=${teamId}&page=${historyPage}&page_size=10`,
        { signal },
      ),
    enabled: historyOpen,
    retry: false,
  })
  const historyData = accessibleQueryData(history)
  const save = useMutation({
    mutationFn: () =>
      apiFetch(`${path}?team_id=${teamId}`, {
        method: 'PATCH',
        body: JSON.stringify({
          expected_version: indicator.assessment?.version ?? 0,
          source_revision: baseline.source_revision,
          extraction_revision: baseline.extraction_revision,
          verdict: draft.verdict,
          reason: draft.reason.trim(),
          expires_at: draft.expiry
            ? new Date(`${draft.expiry}:00Z`).toISOString()
            : null,
        }),
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['item-indicators', itemId] })
      void client.invalidateQueries({
        queryKey: ['indicator-history', itemId, indicator.id],
      })
      onClose()
    },
  })
  const close = () => {
    if (!save.isPending) discard(onClose)
  }
  const invalidExpiry = Boolean(
    draft.expiry &&
      (!Number.isFinite(Date.parse(`${draft.expiry}:00Z`)) ||
        Date.parse(`${draft.expiry}:00Z`) <= Date.now()),
  )
  return (
    <>
      <DialogSurface
        open
        title="Review indicator for this team"
        description={`Source revision ${baseline.source_revision}, extraction revision ${baseline.extraction_revision}. This verdict belongs to the selected team.`}
        onClose={close}
        describeBody={false}
        panelClassName="max-w-3xl"
      >
        <p className="mb-3 break-all font-mono text-sm">
          {indicator.type}: {indicator.value}
        </p>
        <IndicatorEvidence indicator={indicator} />
        {indicator.assessment && !indicator.assessment.current && (
          <p role="status" className="mt-2">
            The saved verdict is expired or belongs to older evidence. Saving
            reviews the revision shown here.
          </p>
        )}
        {save.isError && (
          <p role="alert" className="mt-3">
            {resolveApiErrorMessage(
              save.error,
              'Review could not be saved. Your notes are preserved.',
            )}{' '}
            If the evidence changed, preserve your notes, close this review and
            refresh the indicators.
          </p>
        )}
        <fieldset
          disabled={!writable || save.isPending}
          className="mt-3 space-y-3"
        >
          <label className="block text-sm">
            Analyst verdict
            <select
              className={INPUT}
              value={draft.verdict}
              onChange={(event) =>
                setDraft({
                  ...draft,
                  verdict: event.target.value as IndicatorVerdict,
                })
              }
            >
              {VERDICTS.map((verdict) => (
                <option key={verdict} value={verdict}>
                  {verdict}
                </option>
              ))}
            </select>
          </label>
          <label className="block text-sm">
            Review reason
            <textarea
              className={INPUT}
              maxLength={2000}
              rows={3}
              value={draft.reason}
              onChange={(event) =>
                setDraft({ ...draft, reason: event.target.value })
              }
            />
          </label>
          <label className="block text-sm">
            Expires at (UTC, optional)
            <input
              className={INPUT}
              type="datetime-local"
              value={draft.expiry}
              onChange={(event) =>
                setDraft({ ...draft, expiry: event.target.value })
              }
            />
          </label>
          {invalidExpiry && (
            <p role="alert">Choose a future UTC expiry or leave it empty.</p>
          )}
          <button
            type="button"
            className={BUTTON}
            disabled={!draft.reason.trim() || invalidExpiry}
            onClick={() => save.mutate()}
          >
            {save.isPending ? 'Saving review…' : 'Save team verdict'}
          </button>
        </fieldset>
        <details
          className="mt-3"
          open={historyOpen}
          onToggle={(event) => setHistoryOpen(event.currentTarget.open)}
        >
          <summary className="cursor-pointer font-semibold">
            Review history
          </summary>
          {history.isPending && historyOpen && (
            <p role="status">Loading history…</p>
          )}
          {history.isError && (
            <p role="alert">
              {resolveApiErrorMessage(
                history.error,
                'Review history could not be loaded.',
              )}{' '}
              <button className={BUTTON} onClick={() => void history.refetch()}>
                Retry history
              </button>
            </p>
          )}
          {historyData?.items.map((entry) => (
            <article
              key={entry.version}
              className="mt-2 border-t border-slate/20 pt-2 text-sm"
            >
              <p>
                Revision {entry.version} ·{' '}
                {new Date(entry.created_at).toLocaleString()}
              </p>
              <p className="whitespace-pre-wrap break-words">
                {String(entry.snapshot.verdict ?? '')}:{' '}
                {String(entry.snapshot.reason ?? '')}
              </p>
            </article>
          ))}
          {historyData?.total === 0 && (
            <p className="text-sm">No prior reviews.</p>
          )}
          {historyData && historyData.total > 10 && (
            <div className="mt-2 flex gap-2">
              <button
                className={BUTTON}
                disabled={historyPage === 1 || history.isFetching}
                onClick={() => setHistoryPage(historyPage - 1)}
              >
                Previous reviews
              </button>
              <span>
                Page {historyPage} of {Math.ceil(historyData.total / 10)}
              </span>
              <button
                className={BUTTON}
                disabled={
                  historyPage * 10 >= historyData.total || history.isFetching
                }
                onClick={() => setHistoryPage(historyPage + 1)}
              >
                Next reviews
              </button>
            </div>
          )}
        </details>
        <button
          className={`${BUTTON} mt-3`}
          disabled={save.isPending}
          onClick={close}
        >
          Close review
        </button>
      </DialogSurface>
      {discard.discardDialog}
    </>
  )
}
