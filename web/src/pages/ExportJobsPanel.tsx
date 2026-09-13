import { resolveApiErrorMessage } from '../api/errors'
import type { ExportJobsController } from './useExportJobs'

export function ExportJobsPanel({ controller }: { controller: ExportJobsController }) {
  const { jobsQuery, page, setPage, cancelMutation, downloadMutation } = controller
  const jobs = jobsQuery.data?.items ?? []
  return (
    <section aria-labelledby="background-export-heading" className="rounded-lg border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 id="background-export-heading" className="font-display text-lg">Background exports</h2>
          <p className="mt-1 text-sm text-slate dark:text-slate-300">Jobs survive navigation and worker restarts. Downloads expire at the time shown and require current access plus the accepting credential.</p>
        </div>
        <button type="button" className="rounded border px-3 py-1.5 text-sm" disabled={jobsQuery.isFetching} onClick={() => void jobsQuery.refetch()}>Refresh jobs</button>
      </div>
      {controller.notice && <p role="status" className="mt-3 text-sm text-emerald-800 dark:text-emerald-200">{controller.notice}</p>}
      {controller.error && <p role="alert" className="mt-3 text-sm text-red-700 dark:text-red-300">{controller.error}</p>}
      {controller.createMutation.isError && <p className="mt-1 text-sm">Check the job list before starting another export. Retrying the same background request reuses its operation key.</p>}
      {jobsQuery.isError && <p role="alert" className="mt-3 text-sm text-red-700 dark:text-red-300">{resolveApiErrorMessage(jobsQuery.error, 'Background export status could not be loaded. Refresh to retry.')}</p>}
      {jobsQuery.isLoading && <p role="status" className="mt-3 text-sm">Loading background exports...</p>}
      {jobsQuery.isSuccess && jobs.length === 0 && <p className="mt-3 text-sm">No background exports on this page.</p>}
      <ul className="mt-3 space-y-3">
        {jobs.map((job) => (
          <li key={job.id} className="rounded border border-slate/20 p-3 dark:border-white/10">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <h3 className="break-words text-sm font-semibold">{job.filename ?? `${job.format.toUpperCase()} export`}</h3>
                <p className="mt-1 text-xs">Requested {new Date(job.created_at).toLocaleString()} · {job.status}</p>
                <p className="mt-1 text-xs text-slate dark:text-slate-300">Expires {new Date(job.expires_at).toLocaleString()}</p>
                {job.status === 'running' && <p role="status" className="mt-1 text-sm">Processed {job.completed_items.toLocaleString()}{job.item_count === null ? '' : ` of ${job.item_count.toLocaleString()}`} articles</p>}
                {job.status === 'queued' && <p className="mt-1 text-sm">Waiting for a worker. Attempt {job.attempts + 1}.</p>}
                {job.message && <p className="mt-2 text-sm text-amber-800 dark:text-amber-200">{job.message}</p>}
                {job.file_size !== null && <p className="mt-1 text-xs">{(job.file_size / 1024 / 1024).toFixed(2)} MB</p>}
              </div>
              <div className="flex gap-2">
                {job.download_available && (
                  <button
                    type="button"
                    className="rounded bg-ink px-3 py-2 text-sm font-semibold text-white disabled:opacity-50 dark:bg-cyan dark:text-[#053c2e]"
                    disabled={downloadMutation.isPending}
                    onClick={() => downloadMutation.mutate(job)}
                  >
                    {downloadMutation.isPending && downloadMutation.variables?.id === job.id ? 'Downloading...' : 'Download export'}
                  </button>
                )}
                {['queued', 'running', 'ready'].includes(job.status) && (
                  <button
                    type="button"
                    className="rounded border px-3 py-2 text-sm disabled:opacity-50"
                    disabled={cancelMutation.isPending}
                    onClick={() => cancelMutation.mutate(job.id)}
                  >
                    {cancelMutation.isPending && cancelMutation.variables === job.id ? 'Cancelling...' : job.status === 'ready' ? 'Delete export' : 'Cancel export'}
                  </button>
                )}
              </div>
            </div>
          </li>
        ))}
      </ul>
      <nav aria-label="Background export pages" className="mt-3 flex items-center justify-between gap-3 text-sm">
        <button type="button" className="rounded border px-3 py-1.5 disabled:opacity-50" disabled={page === 0 || jobsQuery.isFetching} onClick={() => setPage(page - 1)}>Previous jobs</button>
        <span>Page {page + 1}</span>
        <button type="button" className="rounded border px-3 py-1.5 disabled:opacity-50" disabled={!jobsQuery.data?.has_more || jobsQuery.isFetching} onClick={() => setPage(page + 1)}>Next jobs</button>
      </nav>
    </section>
  )
}
