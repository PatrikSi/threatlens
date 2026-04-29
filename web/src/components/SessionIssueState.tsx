import { Link } from 'react-router-dom'

interface SessionIssueStateProps {
  title: string
  description: string
  errorMessage?: string
  actionLabel: string
  onAction: () => void
  secondaryLinkLabel: string
  secondaryLinkTo: string
  fullscreen?: boolean
}

export function SessionIssueState({
  title,
  description,
  errorMessage,
  actionLabel,
  onAction,
  secondaryLinkLabel,
  secondaryLinkTo,
  fullscreen = false,
}: SessionIssueStateProps) {
  const panel = (
    <div className="tl-surface w-full max-w-lg rounded-2xl p-6 shadow-sm">
      <h2 className="font-display text-3xl text-ink dark:text-white">{title}</h2>
      <p className="mt-2 text-sm text-slate dark:text-slate-300">{description}</p>
      {errorMessage ? <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950/35 dark:text-red-200">{errorMessage}</p> : null}
      <div className="mt-4 flex flex-wrap gap-3">
        <button
          type="button"
          className="rounded border border-cyan/30 bg-cyan/10 px-3 py-1.5 text-sm font-semibold text-cyan transition hover:bg-cyan/15 dark:border-cyan-500/35 dark:text-cyan-100"
          onClick={onAction}
        >
          {actionLabel}
        </button>
        <Link
          to={secondaryLinkTo}
          className="rounded border border-slate/20 px-3 py-1.5 text-sm font-semibold text-slate-700 transition hover:border-slate/30 hover:bg-slate/5 dark:border-white/10 dark:text-slate-100 dark:hover:bg-white/[0.06]"
        >
          {secondaryLinkLabel}
        </Link>
      </div>
    </div>
  )

  if (fullscreen) {
    return <div className="flex min-h-screen items-center justify-center px-4">{panel}</div>
  }

  return panel
}
