import { resolveApiErrorMessage } from '../api/errors'
import { ConfirmDialog, DialogSurface } from '../components/ConfirmDialog'
import { formatDateTime } from '../utils/datetime'
import { ALERT_CLOSURE_DISPOSITIONS } from './alertOccurrenceModel'
import type { AlertOccurrencesController } from './useAlertOccurrencesController'

export function AlertOccurrenceDialogs({ controller }: { controller: AlertOccurrencesController }) {
  return (
    <>
      <CloseOccurrenceDialog controller={controller} />
      <SnoozeOccurrenceDialog controller={controller} />
      <BackfillOccurrencesDialog controller={controller} />
    </>
  )
}

function CloseOccurrenceDialog({ controller }: { controller: AlertOccurrencesController }) {
  const target = controller.closeTarget
  const count = target?.occurrences.length ?? 0
  const dispositionOnly = target?.mode === 'disposition'
  return (
    <ConfirmDialog
      open={Boolean(target)}
      title={dispositionOnly
        ? 'Change closure disposition?'
        : count === 1 ? 'Close alert occurrence?' : `Close ${count} alert occurrences?`}
      description={dispositionOnly
        ? 'The correction is appended to occurrence activity history.'
        : 'Closing is final in the current alert lifecycle. Activity history and source evidence remain available.'}
      confirmLabel={dispositionOnly
        ? 'Update disposition'
        : count === 1 ? 'Close occurrence' : `Close ${count} occurrences`}
      confirmTone="danger"
      isConfirming={controller.lifecycleMutation.isPending || controller.bulkLifecycleMutation.isPending}
      confirmingLabel={dispositionOnly
        ? 'Updating disposition...'
        : count === 1 ? 'Closing occurrence...' : `Closing ${count} occurrences...`}
      confirmDisabled={controller.closeConfirmationDisabled}
      onCancel={() => {
        controller.setCloseTarget(null)
        controller.setActionError(null)
      }}
      onConfirm={controller.confirmClose}
    >
      <div>
        <label htmlFor="alert-close-disposition" className="text-sm font-semibold text-ink dark:text-white">
          Closure disposition
        </label>
        <select
          id="alert-close-disposition"
          className="mt-1 min-h-11 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
          value={controller.closeDisposition}
          disabled={controller.lifecycleMutation.isPending || controller.bulkLifecycleMutation.isPending}
          onChange={(event) => controller.setCloseDisposition(event.target.value as typeof controller.closeDisposition)}
        >
          {ALERT_CLOSURE_DISPOSITIONS.map((disposition) => (
            <option key={disposition.value} value={disposition.value}>{disposition.label}</option>
          ))}
        </select>
      </div>
      {controller.actionError && <p role="alert" className="text-sm text-red-600 dark:text-red-300">{controller.actionError}</p>}
    </ConfirmDialog>
  )
}

function SnoozeOccurrenceDialog({ controller }: { controller: AlertOccurrencesController }) {
  return (
    <ConfirmDialog
      open={Boolean(controller.snoozeTarget)}
      title="Snooze alert occurrence?"
      description="The occurrence remains visible and keeps its lifecycle state while snoozed."
      confirmLabel="Snooze occurrence"
      confirmTone="primary"
      isConfirming={controller.snoozeMutation.isPending}
      confirmingLabel="Snoozing occurrence..."
      confirmDisabled={!controller.snoozeTarget || Boolean(controller.snoozeValidationError)}
      onCancel={() => {
        controller.setSnoozeTarget(null)
        controller.setActionError(null)
      }}
      onConfirm={controller.confirmSnooze}
    >
      <div>
        <label htmlFor="alert-snooze-until" className="text-sm font-semibold text-ink dark:text-white">
          Snoozed until
        </label>
        <input
          id="alert-snooze-until"
          type="datetime-local"
          className="mt-1 min-h-11 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
          value={controller.snoozeUntil}
          disabled={controller.snoozeMutation.isPending}
          onChange={(event) => controller.setSnoozeUntil(event.target.value)}
        />
      </div>
      <div>
        <label htmlFor="alert-snooze-reason" className="text-sm font-semibold text-ink dark:text-white">
          Reason
        </label>
        <textarea
          id="alert-snooze-reason"
          rows={3}
          maxLength={500}
          className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
          value={controller.snoozeReason}
          disabled={controller.snoozeMutation.isPending}
          onChange={(event) => controller.setSnoozeReason(event.target.value)}
        />
        <p className="mt-1 text-xs text-slate dark:text-slate-400">{controller.snoozeReason.length}/500</p>
      </div>
      {controller.snoozeValidationError && <p role="status" className="text-sm text-amber-800 dark:text-amber-200">{controller.snoozeValidationError}</p>}
      {controller.actionError && <p role="alert" className="text-sm text-red-600 dark:text-red-300">{controller.actionError}</p>}
    </ConfirmDialog>
  )
}

function BackfillOccurrencesDialog({ controller }: { controller: AlertOccurrencesController }) {
  const { backfill } = controller
  const busy = backfill.preview.isPending || backfill.apply.isPending
  return (
    <DialogSurface
      open={backfill.open}
      title="Backfill occurrence history"
      description="Evaluate previously collected articles against the current alert rules."
      closeLabel="Close alert occurrence backfill"
      dismissDisabled={busy}
      ariaBusy={busy}
      panelClassName="max-w-2xl"
      onClose={backfill.closeDialog}
      footer={
        <>
          <button
            type="button"
            className="min-h-11 rounded border border-slate/25 px-3 py-1.5 text-sm font-semibold disabled:opacity-50 sm:min-h-10 dark:border-white/15"
            disabled={busy}
            onClick={backfill.closeDialog}
          >
            Close
          </button>
          <button
            type="button"
            className="min-h-11 rounded border border-slate/30 px-3 py-1.5 text-sm font-semibold disabled:opacity-50 sm:min-h-10 dark:border-white/15"
            disabled={busy || Boolean(backfill.validationError)}
            onClick={backfill.previewBackfill}
          >
            {backfill.preview.isPending ? 'Calculating...' : 'Preview backfill'}
          </button>
          <button
            type="button"
            className="min-h-11 rounded bg-ink px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-50 sm:min-h-10 dark:bg-cyan dark:text-[#053c2e]"
            disabled={!backfill.canApply}
            onClick={backfill.applyBackfill}
          >
            {backfill.apply.isPending ? 'Applying reviewed page...' : 'Apply reviewed page'}
          </button>
          {backfill.canContinue && (
            <button
              type="button"
              className="min-h-11 rounded bg-ink px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-50 sm:min-h-10 dark:bg-cyan dark:text-[#053c2e]"
              disabled={busy}
              onClick={backfill.continueBackfill}
            >
              Preview next page
            </button>
          )}
        </>
      }
    >
      <div
        role="note"
        className="border border-amber-300/70 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-700/50 dark:bg-amber-950/25 dark:text-amber-200"
      >
        Backfill never sends SMTP or webhook notifications. It only creates durable occurrence history for matching articles.
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label htmlFor="alert-backfill-since" className="text-sm font-semibold text-ink dark:text-white">Start time</label>
          <input
            id="alert-backfill-since"
            type="datetime-local"
            className="mt-1 min-h-11 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
            disabled={busy}
            value={backfill.draft.since}
            onChange={(event) => backfill.updateDraft({ since: event.target.value })}
          />
        </div>
        <div>
          <label htmlFor="alert-backfill-until" className="text-sm font-semibold text-ink dark:text-white">End time</label>
          <input
            id="alert-backfill-until"
            type="datetime-local"
            className="mt-1 min-h-11 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
            disabled={busy}
            value={backfill.draft.until}
            onChange={(event) => backfill.updateDraft({ until: event.target.value })}
          />
        </div>
      </div>
      <div>
        <label htmlFor="alert-backfill-limit" className="text-sm font-semibold text-ink dark:text-white">Item limit</label>
        <input
          id="alert-backfill-limit"
          type="number"
          min={1}
          max={500}
          step={1}
          className="mt-1 min-h-11 w-full rounded border border-slate/30 bg-white px-3 py-2 sm:max-w-40 dark:border-cyan-900/40 dark:bg-[#072019]"
          disabled={busy}
          value={backfill.draft.limit}
          onChange={(event) => backfill.updateDraft({ limit: event.target.value })}
        />
      </div>
      {backfill.validationError && <p role="status" className="text-sm text-amber-800 dark:text-amber-200">{backfill.validationError}</p>}
      {backfill.preview.isError && (
        <p role="alert" className="text-sm text-red-600 dark:text-red-300">
          {resolveApiErrorMessage(backfill.preview.error, 'The backfill preview could not be calculated')}
        </p>
      )}
      {backfill.preview.data && (
        <section className="border-y border-slate/15 py-3 dark:border-white/10" aria-labelledby="alert-backfill-preview-heading">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h4 id="alert-backfill-preview-heading" className="font-semibold text-ink dark:text-white">Preview</h4>
            <span className="tl-chip tl-chip-info">{backfill.preview.data.matched_count} articles in window</span>
          </div>
          {backfill.preview.data.truncated && (
            <p className="mt-2 text-xs text-amber-800 dark:text-amber-200">
              More articles remain after this reviewed page. Apply this exact page, then preview the continuation.
            </p>
          )}
          {backfill.preview.data.candidates.length === 0 ? (
            <p className="mt-2 text-sm text-slate dark:text-slate-300">No articles fall within this window.</p>
          ) : (
            <ul className="mt-2 max-h-48 divide-y divide-slate/15 overflow-y-auto border-y border-slate/15 text-sm dark:divide-white/10 dark:border-white/10">
              {backfill.preview.data.candidates.slice(0, 25).map((candidate) => (
                <li key={candidate.item_id} className="flex items-start justify-between gap-3 py-2">
                  <span className="min-w-0 break-words font-medium text-ink dark:text-slate-200">{candidate.title}</span>
                  <time className="shrink-0 text-xs text-slate dark:text-slate-400" dateTime={candidate.first_seen_at}>
                    {formatDateTime(candidate.first_seen_at)}
                  </time>
                </li>
              ))}
            </ul>
          )}
          {backfill.preview.data.candidates.length > 25 && (
            <p className="mt-2 text-xs text-slate dark:text-slate-400">
              Showing 25 of {backfill.preview.data.returned_count} preview candidates.
            </p>
          )}
        </section>
      )}
      {backfill.apply.isError && (
        <p role="alert" className="text-sm text-red-600 dark:text-red-300">
          {resolveApiErrorMessage(backfill.apply.error, 'The occurrence backfill could not be started')}
        </p>
      )}
      {backfill.apply.data && (
        <div
          role="status"
          className={backfill.apply.data.enqueue_failed
            ? 'border border-amber-300/70 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-700/50 dark:bg-amber-950/25 dark:text-amber-200'
            : 'border border-green-300/70 bg-green-50 px-3 py-2 text-sm text-green-900 dark:border-green-700/50 dark:bg-green-950/25 dark:text-green-200'}
        >
          {backfill.apply.data.accepted} evaluation request{backfill.apply.data.accepted === 1 ? '' : 's'} accepted;{' '}
          {backfill.apply.data.existing} already existed and {backfill.apply.data.skipped} changed or disappeared. No notifications will be sent.
          {backfill.apply.data.enqueue_failed
            ? ' Immediate worker enqueue failed; the durable requests remain available for background reconciliation. Check worker health.'
            : ''}
          {backfill.apply.data.has_more
            ? ' More articles remain. Preview the next page to continue safely.'
            : ' The selected backfill window is complete.'}
        </div>
      )}
    </DialogSurface>
  )
}
