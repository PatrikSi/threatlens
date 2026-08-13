import { resolveApiErrorMessage } from '../api/errors'
import { ExportFilterPanel } from './ExportFilterPanel'
import { ExportFormatPanel } from './ExportFormatPanel'
import { ExportPreviewPanel } from './ExportPreviewPanel'
import { useExportPageController } from './useExportPageController'

export function ExportPage() {
  const controller = useExportPageController()
  const { capabilitiesQuery, previewQuery, exportMutation, format, canExport, blockingReason, exportError, notice } =
    controller
  const capabilities = capabilitiesQuery.data
  const selectedFormat = capabilities?.formats.find((entry) => entry.id === format)

  return (
    <div className="space-y-3 sm:space-y-4">
      <section className="rounded-lg border border-slate/20 bg-white/80 px-3 py-3 dark:border-cyan-900/40 dark:bg-[#041612]/90 sm:px-4 sm:py-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="font-display text-2xl">Export intelligence</h1>
            <p className="mt-0.5 text-sm text-slate dark:text-slate-300">Build filtered article datasets and portable intelligence packages.</p>
          </div>
          {capabilities && (
            <span className="rounded border border-slate/20 bg-slate/5 px-2.5 py-1.5 text-xs text-slate dark:border-white/10 dark:bg-white/[0.03] dark:text-slate-300">
              {capabilities.formats.length} formats available
            </span>
          )}
        </div>
      </section>

      {capabilitiesQuery.isLoading && <PageLoading />}
      {capabilitiesQuery.isError && (
        <div role="alert" className="rounded-lg border border-red-300/70 bg-red-50 p-4 text-sm text-red-800 dark:border-red-800/60 dark:bg-red-950/30 dark:text-red-200">
          <p>{resolveApiErrorMessage(capabilitiesQuery.error, 'Export options could not be loaded')}</p>
          <button
            type="button"
            className="mt-3 rounded border border-red-400 px-3 py-1.5 text-xs font-semibold dark:border-red-700"
            onClick={() => void capabilitiesQuery.refetch()}
          >
            Retry
          </button>
        </div>
      )}

      {capabilities && (
        <>
          <ExportSummary controller={controller} />

          <div className="grid items-start gap-3 sm:gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(390px,0.72fr)]">
            <div className="order-2 xl:order-1">
              <ExportFilterPanel capabilities={capabilities} controller={controller} />
            </div>
            <div className="order-1 xl:order-2">
              <ExportFormatPanel capabilities={capabilities} controller={controller} />
            </div>
          </div>

          <ExportPreviewPanel controller={controller} />

          <section className="sticky bottom-2 z-10 rounded-lg border border-slate/25 bg-white/95 px-3 py-3 shadow-sm backdrop-blur dark:border-cyan-900/50 dark:bg-[#041612]/95 sm:static sm:px-4 sm:py-4 sm:backdrop-blur-none">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div aria-live="polite" className="min-w-0 text-xs text-slate dark:text-slate-300">
                {exportError && <p role="alert" className="text-sm text-red-700 dark:text-red-300">{exportError}</p>}
                {!exportError && notice && <p className="text-sm font-semibold text-emerald-800 dark:text-emerald-200">{notice}</p>}
                {!exportError && !notice && blockingReason && <p id="export-blocking-reason">{blockingReason}</p>}
                {!exportError && !notice && !blockingReason && (
                  <p>
                    {previewQuery.data?.total_matches.toLocaleString()} articles ready for {selectedFormat?.label ?? 'export'}.
                  </p>
                )}
              </div>
              <button
                type="button"
                className="min-h-10 w-full shrink-0 rounded bg-ink px-4 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50 dark:bg-cyan dark:text-[#053c2e] sm:w-auto"
                disabled={!canExport}
                aria-describedby={blockingReason ? 'export-blocking-reason' : undefined}
                onClick={controller.generateExport}
              >
                {exportMutation.isPending ? 'Generating export...' : `Generate ${selectedFormat?.label ?? 'export'}`}
              </button>
            </div>
          </section>
        </>
      )}
    </div>
  )
}

function ExportSummary({ controller }: { controller: ReturnType<typeof useExportPageController> }) {
  const preview = controller.previewQuery.data
  const stats = [
    { label: 'Matching', value: preview?.total_matches },
    { label: 'With article text', value: preview?.articles_with_text },
    { label: 'With IOCs', value: preview?.items_with_iocs },
    { label: 'Preview rows', value: preview?.items.length },
  ]

  return (
    <section aria-label="Export match summary" className="grid grid-cols-2 gap-2 lg:grid-cols-4">
      {stats.map((stat) => (
        <div key={stat.label} className="rounded-lg border border-slate/20 bg-white/80 px-3 py-2.5 dark:border-cyan-900/40 dark:bg-[#041612]/90 sm:px-4 sm:py-3">
          <p className="text-[11px] font-bold uppercase text-slate dark:text-slate-400">{stat.label}</p>
          <p className="mt-0.5 text-xl font-semibold tabular-nums text-ink dark:text-slate-100">
            {stat.value === undefined ? '-' : stat.value.toLocaleString()}
          </p>
        </div>
      ))}
    </section>
  )
}

function PageLoading() {
  return (
    <div role="status" className="rounded-lg border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
      <p className="text-sm text-slate dark:text-slate-300">Loading export options...</p>
      <div className="mt-3 grid grid-cols-2 gap-2 lg:grid-cols-4">
        {[0, 1, 2, 3].map((index) => (
          <div key={index} className="h-14 animate-pulse rounded bg-slate/10 dark:bg-white/[0.05]" />
        ))}
      </div>
    </div>
  )
}
