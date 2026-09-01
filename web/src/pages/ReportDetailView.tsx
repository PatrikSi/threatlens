import type { ReportDetail } from '../types/api'
import { Status } from './ReportLibrary'
import { formatReportDate } from './reportingPageModel'
import type { ReportingController } from './useReportingController'


type ReportDownloadFormat = 'markdown' | 'html' | 'pdf'


export function ReportDetailView({
  controller,
  report,
}: {
  controller: ReportingController
  report: ReportDetail
}) {
  const running = report.status === 'queued' || report.status === 'running'
  const canManage = controller.canAuthor && (
    controller.isAdmin || report.owner_user_id === controller.currentUser.data?.id
  )
  const warnings = Array.isArray(report.coverage.warnings)
    ? report.coverage.warnings.map(String)
    : []

  return (
    <div className="space-y-3 sm:space-y-4">
      {!controller.canAuthor && (
        <p className="rounded-lg border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-700/40 dark:bg-amber-950/20 dark:text-amber-200">
          This report is read-only. Retrying or deleting reports requires write access to reports.
        </p>
      )}
      <ReportHeader
        controller={controller}
        report={report}
        running={running}
        canManage={canManage}
      />

      {running && (
        <GenerationStatus
          status={report.status}
          stage={report.generation_stage}
        />
      )}
      {report.error && <GenerationError report={report} />}
      {warnings.map((warning) => (
        <p
          key={warning}
          className="rounded-lg border border-amber-300/60 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-700/40 dark:bg-amber-950/20 dark:text-amber-200"
        >
          {warning}
        </p>
      ))}

      <ReportStats report={report} />
      <ReportContent report={report} />
    </div>
  )
}


function ReportHeader({
  controller,
  report,
  running,
  canManage,
}: {
  controller: ReportingController
  report: ReportDetail
  running: boolean
  canManage: boolean
}) {
  return (
    <section className="rounded-lg border border-slate/20 bg-white/85 px-3 py-3 dark:border-cyan-900/40 dark:bg-[#041612]/90 sm:px-4 sm:py-4">
      <button
        type="button"
        className="mb-3 rounded border border-slate/20 px-2.5 py-1 text-xs font-semibold dark:border-white/10"
        onClick={controller.closeReport}
      >
        ← Report library
      </button>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="break-words font-display text-2xl">{report.title}</h1>
            <Status value={report.status} />
          </div>
          <p className="mt-1 text-sm text-slate dark:text-slate-300">
            {new Date(report.period_start).toLocaleDateString()} through{' '}
            {new Date(report.period_end).toLocaleDateString()} ·{' '}
            {report.included_source_count} of {report.source_count} matching sources
          </p>
        </div>
        <ReportActions
          controller={controller}
          report={report}
          running={running}
          canManage={canManage}
        />
      </div>
    </section>
  )
}


function ReportActions({
  controller,
  report,
  running,
  canManage,
}: {
  controller: ReportingController
  report: ReportDetail
  running: boolean
  canManage: boolean
}) {
  const disabled = controller.detailActionPending

  return (
    <div className="flex flex-wrap gap-1.5" aria-busy={disabled}>
      {report.status === 'ready' && (
        <>
          <DownloadButton
            label="Markdown"
            format="markdown"
            reportId={report.id}
            controller={controller}
            disabled={disabled}
          />
          <DownloadButton
            label="HTML"
            format="html"
            reportId={report.id}
            controller={controller}
            disabled={disabled}
          />
          <DownloadButton
            label="PDF"
            format="pdf"
            reportId={report.id}
            controller={controller}
            disabled={disabled}
          />
        </>
      )}
      {(report.status === 'error' || report.status === 'skipped') && canManage && (
        <button
          type="button"
          className="rounded bg-ink px-3 py-1.5 text-xs font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50 dark:bg-cyan dark:text-[#053c2e]"
          disabled={disabled}
          onClick={() => controller.retryMutation.mutate(report.id)}
        >
          Retry
        </button>
      )}
      {!running && canManage && (
        <button
          type="button"
          className="rounded border border-red-300 px-3 py-1.5 text-xs font-semibold text-red-700 disabled:cursor-not-allowed disabled:opacity-50 dark:border-red-800 dark:text-red-300"
          disabled={disabled}
          onClick={() => {
            if (window.confirm('Delete this report and its immutable source snapshot?')) {
              controller.deleteMutation.mutate(report.id)
            }
          }}
        >
          Delete
        </button>
      )}
    </div>
  )
}


function DownloadButton({
  label,
  format,
  reportId,
  controller,
  disabled,
}: {
  label: string
  format: ReportDownloadFormat
  reportId: string
  controller: ReportingController
  disabled: boolean
}) {
  return (
    <button
      type="button"
      className="rounded border border-slate/20 px-3 py-1.5 text-xs font-semibold disabled:cursor-not-allowed disabled:opacity-50 dark:border-white/10"
      disabled={disabled}
      onClick={() => controller.downloadMutation.mutate({ reportId, format })}
    >
      {label}
    </button>
  )
}


function GenerationStatus({
  status,
  stage,
}: {
  status: ReportDetail['status']
  stage: string
}) {
  if (stage === 'waiting_for_worker') {
    return (
      <div
        role="status"
        className="rounded-lg border border-amber-300/70 bg-amber-50 px-3 py-3 text-sm text-amber-900 dark:border-amber-800/60 dark:bg-amber-950/30 dark:text-amber-100"
      >
        <p className="font-semibold">Waiting for an AI report worker</p>
        <p className="mt-1 text-xs">
          No active worker is subscribed to the report queue. An administrator
          should recreate the worker-ai service with the current deployment
          configuration. The queued report will resume automatically.
        </p>
      </div>
    )
  }

  if (status === 'queued') {
    return (
      <div
        role="status"
        className="rounded-lg border border-cyan/25 bg-cyan/5 px-3 py-3 text-sm text-cyan-900 dark:border-cyan-800/50 dark:text-cyan-100"
      >
        <p className="font-semibold">Waiting for report generation</p>
        <p className="mt-1 text-xs">
          The request is queued for the AI worker. Busy local models can take a
          few minutes to begin. This view refreshes automatically.
        </p>
      </div>
    )
  }

  return (
    <div
      role="status"
      className="rounded-lg border border-cyan/25 bg-cyan/5 px-3 py-3 text-sm text-cyan-900 dark:border-cyan-800/50 dark:text-cyan-100"
    >
      <p className="font-semibold">
        Generation in progress: {stage.replaceAll('_', ' ')}
      </p>
      <p className="mt-1 text-xs">
        The AI worker is processing bounded evidence batches. Local models can
        take several minutes per stage. This view refreshes automatically.
      </p>
    </div>
  )
}


function GenerationError({ report }: { report: ReportDetail }) {
  return (
    <div
      role="alert"
      className="rounded-lg border border-red-300/70 bg-red-50 px-3 py-3 text-sm text-red-800 dark:border-red-800/60 dark:bg-red-950/30 dark:text-red-200"
    >
      <p className="font-semibold">
        {report.error_code?.replaceAll('_', ' ') ?? 'Generation failed'}
      </p>
      <p className="mt-1">{report.error}</p>
    </div>
  )
}


function ReportStats({ report }: { report: ReportDetail }) {
  return (
    <section
      aria-label="Report generation details"
      className="grid grid-cols-2 gap-2 lg:grid-cols-6"
    >
      <Stat label="Sources" value={report.included_source_count.toLocaleString()} />
      <Stat
        label="Coverage"
        value={`${String(report.coverage.coverage_percent ?? 0)}%`}
      />
      <Stat label="Batches" value={report.generation_batches.toLocaleString()} />
      <Stat label="Model calls" value={report.model_calls.toLocaleString()} />
      <Stat
        label="Tokens"
        value={(report.total_tokens ?? report.estimated_input_tokens).toLocaleString()}
      />
      <Stat label="Generated" value={formatReportDate(report.generated_at)} compact />
    </section>
  )
}


function ReportContent({ report }: { report: ReportDetail }) {
  const includedSources = report.sources.filter((source) => source.included)

  return (
    <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
      <article className="rounded-lg border border-slate/20 bg-white/90 px-3 py-4 dark:border-cyan-900/40 dark:bg-[#041612]/90 sm:px-5">
        {report.sections.map((section) => (
          <section
            key={section.key}
            className="border-b border-slate/15 py-4 first:pt-0 last:border-0 last:pb-0 dark:border-white/10"
          >
            <h2 className="font-display text-xl">{section.title}</h2>
            {section.body_markdown ? (
              <ReportMarkdownText value={section.body_markdown} />
            ) : (
              <p className="mt-2 text-sm italic text-slate dark:text-slate-400">
                This section is {section.status}.
              </p>
            )}
          </section>
        ))}
      </article>
      <aside className="rounded-lg border border-slate/20 bg-white/80 dark:border-cyan-900/40 dark:bg-[#041612]/90 xl:sticky xl:top-3">
        <header className="border-b border-slate/15 px-3 py-2.5 dark:border-white/10">
          <h2 className="font-display text-base">Source evidence</h2>
          <p className="text-xs text-slate dark:text-slate-400">
            Immutable snapshot used for this report.
          </p>
        </header>
        <div className="max-h-[70vh] divide-y divide-slate/15 overflow-y-auto dark:divide-white/10">
          {includedSources.map((source) => (
            <a
              key={source.citation_key}
              href={source.url}
              target="_blank"
              rel="noreferrer"
              className="block px-3 py-2.5 text-sm hover:bg-slate/5 dark:hover:bg-white/[0.03]"
            >
              <span className="text-xs font-bold text-cyan-800 dark:text-cyan-200">
                [{source.citation_key}]
              </span>
              <span className="mt-0.5 block break-words font-semibold">
                {source.title}
              </span>
              <span className="mt-0.5 block text-xs text-slate dark:text-slate-400">
                {source.feed_name} · {source.classification ?? 'Unclassified'}
              </span>
            </a>
          ))}
        </div>
      </aside>
    </div>
  )
}


function ReportMarkdownText({ value }: { value: string }) {
  return (
    <div className="mt-2 space-y-2 text-sm leading-6 text-slate-800 dark:text-slate-200">
      {value.split('\n').map((line, index) =>
        line.trim() ? (
          <p
            key={`${index}-${line.slice(0, 20)}`}
            className={line.startsWith('- ') ? 'pl-3 before:mr-2 before:content-["•"]' : ''}
          >
            {line.replace(/^- /, '')}
          </p>
        ) : null,
      )}
    </div>
  )
}


function Stat({
  label,
  value,
  compact = false,
}: {
  label: string
  value: string
  compact?: boolean
}) {
  return (
    <div className="min-w-0 rounded-lg border border-slate/20 bg-white/80 px-3 py-2 dark:border-cyan-900/40 dark:bg-[#041612]/90">
      <p className="text-[10px] font-bold uppercase text-slate dark:text-slate-400">
        {label}
      </p>
      <p
        className={`mt-0.5 break-words font-semibold tabular-nums ${compact ? 'text-xs' : 'text-lg'}`}
      >
        {value}
      </p>
    </div>
  )
}
