import type { ReportingFeedback } from './useReportingController'


export function ReportingActionFeedback({
  feedback,
}: {
  feedback: ReportingFeedback
}) {
  if (!feedback) return null

  if (feedback.kind === 'error') {
    return (
      <div
        role="alert"
        aria-live="assertive"
        aria-atomic="true"
        className="rounded-lg border border-red-300/70 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-800/60 dark:bg-red-950/30 dark:text-red-200"
      >
        {feedback.message}
      </div>
    )
  }

  return (
    <div
      role="status"
      aria-live="polite"
      aria-atomic="true"
      className="rounded-lg border border-emerald-300/60 bg-emerald-50 px-3 py-2 text-sm text-emerald-800 dark:border-emerald-800/50 dark:bg-emerald-950/20 dark:text-emerald-200"
    >
      {feedback.message}
    </div>
  )
}
