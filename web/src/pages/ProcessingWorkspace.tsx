import { ConfirmDialog } from '../components/ConfirmDialog'
import { resolveApiErrorMessage } from '../api/errors'
import { PROCESSING_STAGES, PROCESSING_STATES, processingAccessError, processingConflict, processingStageLabel } from './processingModel'
import { ProcessingRecoveryRuns } from './ProcessingRecoveryRuns'
import { ProcessingWorkTable } from './ProcessingWorkTable'
import { useProcessingWorkspace, type ProcessingWorkspaceController } from './useProcessingWorkspace'

export function ProcessingWorkspace() {
  const controller = useProcessingWorkspace()
  const { scope, rows, selected, staleSelection, workQuery, busy, canRead, canWrite } = controller
  if (controller.user.isLoading) return <p role="status" className="p-4">Checking processing access...</p>
  if (!canRead) return <p role="alert" className="p-4">Processing work requires permission to read operations and articles.</p>
  const available = !processingAccessError(workQuery.error)
  return (
    <div>
      <section className="space-y-3 p-3" aria-labelledby="processing-heading">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 id="processing-heading" className="font-display text-lg">Incomplete processing</h2>
            <p className="mt-1 text-sm text-slate dark:text-slate-300">Find articles awaiting retrieval, classification, indicator extraction, or tagging. Retry only the selected stages; completed results are preserved.</p>
          </div>
          <button type="button" className="min-h-11 rounded border px-3 py-2 text-sm font-semibold disabled:opacity-50" disabled={busy || workQuery.isFetching} onClick={controller.refresh}>Refresh processing</button>
        </div>
        {!canWrite && <p role="status" className="text-sm">Recovery actions require verified access and permission to write operations. You can still inspect processing status.</p>}
        <ProcessingFilters controller={controller} />
        <p className="text-xs text-slate dark:text-slate-300">Showing up to 50 accessible work records per page, one per article and stage. Select a source name to filter. Browser Back restores earlier pages and filters.</p>
        {controller.notice && <p role="status" className="text-sm">{controller.notice}</p>}
        {controller.error && <p role="alert" className="text-sm">{controller.error}</p>}
        {workQuery.isError && <p role="alert" className="text-sm">{resolveApiErrorMessage(workQuery.error, 'Processing work could not be loaded. Refresh to retry.')}{available && workQuery.data ? ' Showing last known results; recovery is paused until refresh succeeds.' : ''}</p>}
        {workQuery.isLoading && <p role="status">Loading processing work...</p>}
        {available && workQuery.isSuccess && rows.length === 0 && <p className="py-4 text-sm">No incomplete processing matches this scope.</p>}
        {available && rows.length > 0 && <>
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <p role="status">{selected.length} selected on this page</p>
            <button type="button" className="min-h-11 rounded border px-3 py-2 disabled:opacity-50" disabled={!canWrite || busy || workQuery.isError || !rows.some((row) => row.can_retry)} onClick={controller.selectPage}>Select eligible on this page</button>
            <button type="button" className="min-h-11 rounded border px-3 py-2 disabled:opacity-50" disabled={busy || !selected.length} onClick={controller.clearSelection}>Clear selection</button>
            <button type="button" className="min-h-11 rounded bg-ink px-3 py-2 font-semibold text-white disabled:opacity-50 dark:bg-cyan dark:text-[#053c2e]"
              disabled={!canWrite || busy || !selected.length || staleSelection || workQuery.isError} onClick={controller.openReview}>Review selected recovery</button>
          </div>
          {staleSelection && <p role="status" className="text-sm">Selected work changed or is no longer eligible. Clear the selection and review the latest records before retrying.</p>}
        </>}
      </section>
      {available && rows.length > 0 && <ProcessingWorkTable controller={controller} />}
      {available && workQuery.data && <nav aria-label="Processing work pages" className="flex items-center justify-between gap-3 p-3 text-sm">
        <button type="button" className="min-h-11 rounded border px-3 py-2 disabled:opacity-50" disabled={busy || workQuery.isFetching || !scope.cursor} onClick={() => controller.changeScope('work_cursor', '')}>First work page</button>
        <span>{rows.length} records shown{workQuery.data.has_more ? ' · More work available' : ' · End of results'}</span>
        <button type="button" className="min-h-11 rounded border px-3 py-2 disabled:opacity-50" disabled={busy || workQuery.isFetching || !workQuery.data.has_more || !workQuery.data.next_cursor} onClick={() => controller.changeScope('work_cursor', workQuery.data?.next_cursor ?? '')}>Next work page</button>
      </nav>}
      <ProcessingRecoveryRuns controller={controller} />
      <ProcessingRecoveryDialogs controller={controller} />
    </div>
  )
}

function ProcessingRecoveryDialogs({ controller }: { controller: ProcessingWorkspaceController }) {
  const { canWrite, review, cancelReview, createRun, cancelRun } = controller
  return (
    <>
      <ConfirmDialog open={Boolean(review)} title="Recover selected processing?" confirmLabel="Queue selected recovery" confirmTone="primary"
        isConfirming={createRun.isPending} confirmingLabel="Queueing recovery..." confirmDisabled={!canWrite || processingConflict(createRun.error) || processingAccessError(createRun.error)}
        onCancel={controller.closeReview} onConfirm={() => { if (review) createRun.mutate(review) }}>
        <p>Queue {review?.rows.length ?? 0} selected work records. Only selected pipeline stages are retried. Already committed results are preserved. Normal processing may produce alerts and notifications.</p>
        <ul className="max-h-48 list-disc overflow-y-auto pl-5">{review?.rows.map((row) => <li key={`${row.item_id}:${row.stage}`}>{row.title} · {processingStageLabel(row.stage)}</li>)}</ul>
        {createRun.isError && <div role="alert">
          <p>{resolveApiErrorMessage(createRun.error, 'Recovery acceptance could not be confirmed.')}</p>
          <p>{processingConflict(createRun.error) || processingAccessError(createRun.error)
            ? 'Close this review and refresh the worklist before selecting current eligible records again.'
            : 'Retrying here reuses the same request key. If you close this review, check your recovery runs before starting another request.'}</p>
        </div>}
      </ConfirmDialog>
      <ConfirmDialog open={Boolean(cancelReview)} title="Cancel remaining recovery work?" confirmLabel="Cancel remaining work"
        isConfirming={cancelRun.isPending} confirmDisabled={!canWrite} onCancel={controller.closeCancel}
        onConfirm={() => { if (cancelReview) cancelRun.mutate(cancelReview) }}>
        <p>Cancellation stops remaining selected work. Already committed processing results are preserved.</p>
        {cancelRun.isError && <p role="alert">{controller.error}</p>}
      </ConfirmDialog>
    </>
  )
}

function ProcessingFilters({ controller }: { controller: ProcessingWorkspaceController }) {
  const { busy, scope, rows } = controller
  return (
        <fieldset disabled={busy} className="flex min-w-0 flex-wrap gap-3">
          <legend className="sr-only">Processing filters</legend>
          <label className="text-sm font-semibold">Stage
            <select className="ml-2 min-h-11 rounded border bg-white px-2 dark:bg-[#041612]" value={scope.stage} onChange={(event) => controller.changeScope('work_stage', event.target.value)}>
              <option value="">All stages</option>{PROCESSING_STAGES.map((entry) => <option key={entry.value} value={entry.value}>{entry.label}</option>)}
            </select>
          </label>
          <label className="text-sm font-semibold">State
            <select className="ml-2 min-h-11 rounded border bg-white px-2 dark:bg-[#041612]" value={scope.state} onChange={(event) => controller.changeScope('work_state', event.target.value)}>
              <option value="">All incomplete work</option>{PROCESSING_STATES.map((entry) => <option key={entry.value} value={entry.value}>{entry.label}</option>)}
            </select>
          </label>
          {scope.feed && <button type="button" className="min-h-11 rounded border px-3 py-2 text-sm" onClick={() => controller.changeScope('work_feed', '')}>
            Clear source filter: {rows.find((row) => row.feed_id === scope.feed)?.feed_name ?? scope.feed}
          </button>}
        </fieldset>
  )
}
