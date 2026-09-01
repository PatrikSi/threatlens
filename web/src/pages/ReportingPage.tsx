import { resolveApiErrorMessage } from '../api/errors'
import { ReportBuilder } from './ReportBuilder'
import { ReportDetailView } from './ReportDetailView'
import { ReportLibrary } from './ReportLibrary'
import { ReportSchedulesPanel } from './ReportSchedulesPanel'
import { ReportTemplatesPanel } from './ReportTemplatesPanel'
import { ReportingActionFeedback } from './ReportingActionFeedback'
import { type ReportingTab, useReportingController } from './useReportingController'

const TABS: Array<{ id: ReportingTab; label: string }> = [
  { id: 'reports', label: 'Reports' },
  { id: 'templates', label: 'Templates' },
  { id: 'schedules', label: 'Schedules' },
]

export function ReportingPage() {
  const controller = useReportingController()
  const visibleTabs = controller.isAdmin ? TABS : TABS.filter((tab) => tab.id !== 'schedules')

  if (controller.reportDetailQuery.isLoading) {
    return <PageStatus message="Loading intelligence report..." />
  }
  if (controller.reportDetailQuery.data) {
    return (
      <div className="space-y-3 sm:space-y-4">
        <ReportingActionFeedback feedback={controller.feedback} />
        {controller.reportDetailQuery.isError && (
          <RefreshWarning
            message={resolveApiErrorMessage(
              controller.reportDetailQuery.error,
              'The latest report status could not be refreshed',
            )}
            onRetry={() => void controller.reportDetailQuery.refetch()}
          />
        )}
        <ReportDetailView
          controller={controller}
          report={controller.reportDetailQuery.data}
        />
      </div>
    )
  }
  if (controller.reportDetailQuery.isError) {
    return (
      <PageError
        message={resolveApiErrorMessage(
          controller.reportDetailQuery.error,
          'The intelligence report could not be loaded',
        )}
        onRetry={() => void controller.reportDetailQuery.refetch()}
      />
    )
  }

  return (
    <div className="space-y-3 sm:space-y-4">
      <section className="rounded-lg border border-slate/20 bg-white/80 px-3 py-3 dark:border-cyan-900/40 dark:bg-[#041612]/90 sm:px-4 sm:py-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="font-display text-2xl">Intelligence reporting</h1>
            <p className="mt-0.5 text-sm text-slate dark:text-slate-300">
              Build sourced reports from the intelligence already collected in ThreatLens.
            </p>
          </div>
          {controller.capabilitiesQuery.data && (
            <div className="text-right text-xs text-slate dark:text-slate-300">
              <p>{controller.capabilitiesQuery.data.context_window_tokens.toLocaleString()} token context</p>
              <p>{controller.capabilitiesQuery.data.max_model_calls} calls per report</p>
            </div>
          )}
        </div>
        <nav aria-label="Reporting views" className="mt-3 flex gap-1 overflow-x-auto border-t border-slate/15 pt-3 dark:border-white/10">
          {visibleTabs.map((tab) => (
            <button
              key={tab.id}
              type="button"
              className={`min-h-9 shrink-0 rounded px-3 py-1.5 text-sm font-semibold ${
                controller.activeTab === tab.id
                  ? 'bg-ink text-white dark:bg-cyan dark:text-[#053c2e]'
                  : 'border border-slate/20 text-slate-700 dark:border-white/10 dark:text-slate-200'
              }`}
              aria-pressed={controller.activeTab === tab.id}
              onClick={() => controller.setActiveTab(tab.id)}
            >
              {tab.label}
            </button>
          ))}
        </nav>
      </section>

      {!controller.canAuthor && (
        <p className="rounded-lg border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-700/40 dark:bg-amber-950/20 dark:text-amber-200">
          Reporting is read-only. Generating reports or changing templates requires write access to reports.
        </p>
      )}

      <ReportingActionFeedback feedback={controller.feedback} />

      {controller.capabilitiesQuery.isLoading && <PageStatus message="Loading reporting capabilities..." />}
      {controller.capabilitiesQuery.isError && (
        <PageError
          message={resolveApiErrorMessage(controller.capabilitiesQuery.error, 'Reporting capabilities could not be loaded')}
          onRetry={() => void controller.capabilitiesQuery.refetch()}
        />
      )}

      {controller.capabilitiesQuery.data && controller.activeTab === 'reports' && (
        <>
          {controller.canAuthor && <ReportBuilder controller={controller} />}
          <ReportLibrary controller={controller} />
        </>
      )}
      {controller.capabilitiesQuery.data && controller.activeTab === 'templates' && (
        <ReportTemplatesPanel controller={controller} />
      )}
      {controller.capabilitiesQuery.data && controller.activeTab === 'schedules' && controller.isAdmin && (
        <ReportSchedulesPanel controller={controller} />
      )}
    </div>
  )
}

function PageStatus({ message }: { message: string }) {
  return <div role="status" className="rounded-lg border border-slate/20 bg-white/80 p-4 text-sm text-slate dark:border-cyan-900/40 dark:bg-[#041612]/90 dark:text-slate-300">{message}</div>
}

function PageError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div role="alert" className="rounded-lg border border-red-300/70 bg-red-50 p-4 text-sm text-red-800 dark:border-red-800/60 dark:bg-red-950/30 dark:text-red-200">
      <p>{message}</p>
      <button type="button" className="mt-3 rounded border border-red-400 px-3 py-1.5 text-xs font-semibold dark:border-red-700" onClick={onRetry}>Retry</button>
    </div>
  )
}

function RefreshWarning({
  message,
  onRetry,
}: {
  message: string
  onRetry: () => void
}) {
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-700/40 dark:bg-amber-950/20 dark:text-amber-200"
    >
      <span>{message} The last loaded report remains visible.</span>
      <button
        type="button"
        className="rounded border border-amber-400 px-2.5 py-1 text-xs font-semibold dark:border-amber-700"
        onClick={onRetry}
      >
        Retry refresh
      </button>
    </div>
  )
}
