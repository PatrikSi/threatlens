import { resolveApiErrorMessage } from '../api/errors'
import {
  investigationCollectionPageCount,
  investigationResultRange,
} from './investigationPageModel'

export function InvestigationCollectionQueryState({
  label,
  total,
  truncated,
  loading,
  fetching,
  error,
  hasData,
  onRetry,
}: {
  label: string
  total: number
  truncated: boolean
  loading: boolean
  fetching: boolean
  error: unknown
  hasData: boolean
  onRetry: () => void
}) {
  if (loading && !hasData) {
    return (
      <p
        role="status"
        aria-busy="true"
        className="py-8 text-center text-sm text-slate dark:text-slate-300"
      >
        {truncated
          ? `Loading the first page of ${total.toLocaleString()} ${label}...`
          : `Loading ${label}...`}
      </p>
    )
  }
  if (error) {
    return (
      <div
        role="alert"
        className={`mt-3 rounded border px-3 py-2 text-sm ${
          hasData
            ? 'border-amber-300/70 bg-amber-50 text-amber-900 dark:border-amber-700/50 dark:bg-amber-950/20 dark:text-amber-200'
            : 'border-red-300/70 bg-red-50 text-red-800 dark:border-red-800/50 dark:bg-red-950/20 dark:text-red-200'
        }`}
      >
        <p>
          {resolveApiErrorMessage(error, `${sentenceCase(label)} could not be loaded`)}
        </p>
        {hasData && <p className="mt-1">The last loaded page remains visible.</p>}
        <button
          type="button"
          className="mt-2 min-h-11 rounded border border-current px-3 py-2 font-semibold sm:min-h-0 sm:py-1.5"
          disabled={fetching}
          onClick={onRetry}
        >
          {fetching ? 'Retrying...' : `Retry ${label}`}
        </button>
      </div>
    )
  }
  if (fetching && hasData) {
    return (
      <p role="status" aria-busy="true" className="mt-3 text-xs text-slate dark:text-slate-400">
        Updating {label}...
      </p>
    )
  }
  return null
}

export function InvestigationCollectionPagination({
  label,
  total,
  page,
  pageSize,
  itemCount,
  fetching,
  disabled = false,
  disabledReason,
  onPageChange,
}: {
  label: string
  total: number
  page: number
  pageSize: number
  itemCount: number
  fetching: boolean
  disabled?: boolean
  disabledReason?: string
  onPageChange: (page: number) => void
}) {
  if (total === 0) return null
  const pages = investigationCollectionPageCount(total, pageSize)
  const controlsDisabled = fetching || disabled
  const disabledReasonId = `investigation-${label.replaceAll(/[^a-z0-9]+/gi, '-').toLowerCase()}-pagination-disabled`
  return (
    <nav
      aria-label={`${sentenceCase(label)} pagination`}
      className="mt-3 flex flex-col gap-2 text-sm sm:flex-row sm:flex-wrap sm:items-center sm:justify-between"
    >
      <p className="text-center text-slate sm:text-left dark:text-slate-300" aria-live="polite">
        {investigationResultRange(total, page, pageSize, itemCount)} · Page {page} of {pages}
      </p>
      {pages > 1 && (
        <div className="grid grid-cols-2 gap-2 sm:flex">
          <button
            type="button"
            className="min-h-11 rounded border border-slate/20 px-3 py-2 font-semibold disabled:opacity-50 sm:min-h-0 sm:py-1.5 dark:border-white/10"
            aria-label={`Previous page of ${label}`}
            aria-describedby={disabled && disabledReason ? disabledReasonId : undefined}
            title={disabled ? disabledReason : undefined}
            disabled={controlsDisabled || page <= 1}
            onClick={() => onPageChange(page - 1)}
          >
            Previous
          </button>
          <button
            type="button"
            className="min-h-11 rounded border border-slate/20 px-3 py-2 font-semibold disabled:opacity-50 sm:min-h-0 sm:py-1.5 dark:border-white/10"
            aria-label={`Next page of ${label}`}
            aria-describedby={disabled && disabledReason ? disabledReasonId : undefined}
            title={disabled ? disabledReason : undefined}
            disabled={controlsDisabled || page >= pages}
            onClick={() => onPageChange(page + 1)}
          >
            Next
          </button>
        </div>
      )}
      {disabled && disabledReason && (
        <span
          id={disabledReasonId}
          className="text-center text-xs text-slate sm:order-3 sm:w-full dark:text-slate-400"
        >
          {disabledReason}
        </span>
      )}
    </nav>
  )
}

function sentenceCase(value: string): string {
  return value ? `${value.charAt(0).toUpperCase()}${value.slice(1)}` : 'Collection'
}
