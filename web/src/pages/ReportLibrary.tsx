import { resolveApiErrorMessage } from '../api/errors'
import type { ReportListItem } from '../types/api'
import { formatReportDate } from './reportingPageModel'
import type { ReportingController } from './useReportingController'

export function ReportLibrary({ controller }: { controller: ReportingController }) {
  const { reportLibrary: library } = controller
  const reports = library.reports
  return (
    <section className="rounded-lg border border-slate/20 bg-white/80 dark:border-cyan-900/40 dark:bg-[#041612]/90">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-slate/15 px-3 py-3 dark:border-white/10 sm:px-4">
        <div><h2 className="font-display text-lg">Report library</h2><p className="mt-0.5 text-xs text-slate dark:text-slate-400">Reports available to your account, newest first. Created dates use UTC.</p></div>
        <button type="button" className="rounded border border-slate/20 px-3 py-1.5 text-xs font-semibold dark:border-white/10" onClick={() => void controller.reportsQuery.refetch()}>Refresh</button>
      </header>
      <div className="flex flex-wrap items-end gap-3 border-b border-slate/15 p-3 dark:border-white/10">
        <label className="text-xs font-semibold">Status
          <select aria-label="Report status" className="ml-2 rounded border border-slate/30 bg-white p-2 dark:bg-[#072019]" value={library.filters.status} onChange={(event) => library.updateFilters({ status: event.target.value as typeof library.filters.status })}>
            <option value="">All statuses</option>
            {['queued', 'running', 'ready', 'error', 'skipped'].map((status) => <option key={status} value={status}>{status}</option>)}
          </select>
        </label>
        <label className="text-xs font-semibold">Created from
          <input type="date" aria-label="Reports created from" className="ml-2 rounded border border-slate/30 bg-white p-2 dark:bg-[#072019]" value={library.filters.createdFrom} onChange={(event) => library.updateFilters({ createdFrom: event.target.value })} />
        </label>
        <label className="text-xs font-semibold">Created through
          <input type="date" aria-label="Reports created through" className="ml-2 rounded border border-slate/30 bg-white p-2 dark:bg-[#072019]" value={library.filters.createdThrough} onChange={(event) => library.updateFilters({ createdThrough: event.target.value })} />
        </label>
        <button type="button" className="rounded border border-slate/30 px-3 py-2 text-xs" onClick={library.clearFilters}>Clear report filters</button>
      </div>
      {library.filterError && <p role="alert" className="p-3 text-sm text-red-700 dark:text-red-300">{library.filterError}</p>}
      {controller.reportsQuery.isLoading && <p role="status" className="p-4 text-sm text-slate dark:text-slate-300">Loading reports...</p>}
      {controller.reportsQuery.isError && <p role="alert" className="p-4 text-sm text-red-700 dark:text-red-300">{resolveApiErrorMessage(controller.reportsQuery.error, 'Reports could not be loaded')}</p>}
      {!controller.reportsQuery.isLoading && !controller.reportsQuery.isError && !library.filterError && !reports.length && <p className="p-4 text-sm text-slate dark:text-slate-300">No reports match this scope. Clear the filters to check other reports available to your account.</p>}
      {reports.length > 0 && (
        <>
          <div className="hidden overflow-x-auto md:block">
            <table className="w-full min-w-[820px] text-left text-sm">
              <thead className="bg-slate/5 text-xs uppercase text-slate dark:bg-white/[0.03] dark:text-slate-400">
                <tr>
                  <th className="px-3 py-2.5">Report</th>
                  <th className="px-3 py-2.5">Status</th>
                  <th className="px-3 py-2.5">Period</th>
                  <th className="px-3 py-2.5">Sources</th>
                  <th className="px-3 py-2.5">Generated</th>
                  <th className="px-3 py-2.5"><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate/15 dark:divide-white/10">
                {reports.map((report) => (
                  <ReportRow key={report.id} report={report} onOpen={controller.openReport} />
                ))}
              </tbody>
            </table>
          </div>
          <div className="divide-y divide-slate/15 dark:divide-white/10 md:hidden">
            {reports.map((report) => (
              <ReportMobileRow key={report.id} report={report} onOpen={controller.openReport} />
            ))}
          </div>
        </>
      )}
      <nav aria-label="Report library pages" className="flex flex-wrap items-center justify-between gap-2 border-t border-slate/15 p-3 text-xs dark:border-white/10">
        <p role="status">{reports.length ? `Showing ${library.first}–${library.last}` : 'No reports shown'} · Page {library.page}{controller.reportsQuery.isFetching ? ' · Refreshing…' : ''}</p>
        <div className="flex gap-2">
          <button type="button" className="rounded border border-slate/30 px-3 py-2 disabled:opacity-50" disabled={library.page === 1 || controller.reportsQuery.isFetching} onClick={() => library.setPage(library.page - 1)}>Previous reports</button>
          <button type="button" className="rounded border border-slate/30 px-3 py-2 disabled:opacity-50" disabled={!library.hasNextPage || controller.reportsQuery.isFetching || Boolean(library.filterError)} onClick={() => library.setPage(library.page + 1)}>Next reports</button>
        </div>
      </nav>
    </section>
  )
}

function ReportRow({ report, onOpen }: { report: ReportListItem; onOpen: (id: string) => void }) {
  return (
    <tr className="hover:bg-slate/5 dark:hover:bg-white/[0.03]">
      <td className="max-w-md px-3 py-2.5">
        <button type="button" className="block max-w-full text-left" onClick={() => onOpen(report.id)}>
          <span className="block break-words font-semibold text-cyan-900 dark:text-cyan-100">
            {report.title}
          </span>
          <span className="mt-0.5 block text-xs capitalize text-slate dark:text-slate-400">
            {report.trigger_source} · {report.report_type.replaceAll('_', ' ')}
          </span>
        </button>
      </td>
      <td className="px-3 py-2.5"><Status value={report.status} /></td>
      <td className="whitespace-nowrap px-3 py-2.5 text-xs">
        {new Date(report.period_start).toLocaleDateString()} - {new Date(report.period_end).toLocaleDateString()}
      </td>
      <td className="px-3 py-2.5 tabular-nums">
        {report.included_source_count} / {report.source_count}
      </td>
      <td className="whitespace-nowrap px-3 py-2.5 text-xs">
        {formatReportDate(report.generated_at)}
      </td>
      <td className="px-3 py-2.5 text-right">
        <button
          type="button"
          className="rounded border border-slate/20 px-2.5 py-1 text-xs font-semibold dark:border-white/10"
          onClick={() => onOpen(report.id)}
        >
          Open
        </button>
      </td>
    </tr>
  )
}

function ReportMobileRow({ report, onOpen }: { report: ReportListItem; onOpen: (id: string) => void }) {
  return (
    <button
      type="button"
      className="block w-full px-3 py-3 text-left"
      onClick={() => onOpen(report.id)}
    >
      <span className="flex items-start justify-between gap-2">
        <span className="min-w-0 break-words font-semibold">{report.title}</span>
        <Status value={report.status} />
      </span>
      <span className="mt-1 block text-xs text-slate dark:text-slate-400">
        {new Date(report.period_start).toLocaleDateString()} - {new Date(report.period_end).toLocaleDateString()}
        {' · '}{report.included_source_count} sources · {report.trigger_source}
      </span>
      {report.error && (
        <span className="mt-1 block text-xs text-red-700 dark:text-red-300">
          {report.error}
        </span>
      )}
    </button>
  )
}

export function Status({ value }: { value: ReportListItem['status'] }) {
  const colors = {
    ready: 'border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-800/50 dark:bg-emerald-950/20 dark:text-emerald-200',
    error: 'border-red-300 bg-red-50 text-red-800 dark:border-red-800/50 dark:bg-red-950/20 dark:text-red-200',
    skipped: 'border-slate/30 bg-slate/5 text-slate dark:border-white/10 dark:bg-white/[0.03] dark:text-slate-300',
    queued: 'border-cyan/30 bg-cyan/5 text-cyan-900 dark:border-cyan-800/50 dark:text-cyan-100',
    running: 'border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-800/50 dark:bg-amber-950/20 dark:text-amber-200',
  }
  return (
    <span className={`inline-flex rounded border px-2 py-0.5 text-[11px] font-bold uppercase ${colors[value]}`}>
      {value}
    </span>
  )
}
