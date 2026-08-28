import { ReactNode, useRef } from 'react'

import { resolveApiErrorMessage } from '../api/errors'
import { DialogSurface } from '../components/ConfirmDialog'
import type { InvestigationSeverity, InvestigationStatus } from '../types/investigations'
import { formatInvestigationSeverity, formatInvestigationStatus } from './investigationPageModel'

export function InvestigationStatusChip({ status }: { status: InvestigationStatus }) {
  const tones: Record<InvestigationStatus, string> = {
    open: 'tl-chip-info',
    monitoring: 'tl-chip-warning',
    closed: 'tl-chip-success',
    archived: 'tl-chip-neutral',
  }
  return <span className={`tl-chip tl-chip-md ${tones[status]}`}>{formatInvestigationStatus(status)}</span>
}

export function InvestigationSeverityChip({ severity }: { severity: InvestigationSeverity }) {
  const tones: Record<InvestigationSeverity, string> = {
    critical: 'border-red-300 bg-red-100 text-red-800 dark:border-red-700/60 dark:bg-red-950/50 dark:text-red-200',
    high: 'border-orange-300 bg-orange-100 text-orange-800 dark:border-orange-700/60 dark:bg-orange-950/40 dark:text-orange-200',
    medium: 'border-amber-300 bg-amber-100 text-amber-800 dark:border-amber-700/60 dark:bg-amber-950/40 dark:text-amber-200',
    low: 'border-slate/20 bg-slate/5 text-slate-700 dark:border-white/10 dark:bg-white/[0.05] dark:text-slate-200',
  }
  return (
    <span className={`inline-flex rounded border px-2 py-0.5 text-xs font-semibold ${tones[severity]}`}>
      {formatInvestigationSeverity(severity)}
    </span>
  )
}

export function InvestigationPageError({
  error,
  fallback,
  onRetry,
}: {
  error: unknown
  fallback: string
  onRetry: () => void
}) {
  return (
    <div role="alert" className="rounded-lg border border-red-300/70 bg-red-50 p-4 text-sm text-red-800 dark:border-red-800/60 dark:bg-red-950/30 dark:text-red-200">
      <p>{resolveApiErrorMessage(error, fallback)}</p>
      <button
        type="button"
        className="mt-3 min-h-11 rounded border border-red-400 px-3 py-2 text-sm font-semibold md:min-h-0 md:py-1.5 dark:border-red-700"
        onClick={onRetry}
      >
        Retry
      </button>
    </div>
  )
}

export function InvestigationRefreshWarning({
  children,
  onRetry,
}: {
  children: ReactNode
  onRetry: () => void
}) {
  return (
    <div role="alert" className="flex flex-wrap items-center justify-between gap-2 rounded border border-amber-300/70 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-700/50 dark:bg-amber-950/30 dark:text-amber-100">
      <span>{children}</span>
      <button
        type="button"
        className="min-h-11 rounded border border-amber-400 px-3 py-2 font-semibold md:min-h-0 md:py-1 dark:border-amber-700"
        onClick={onRetry}
      >
        Retry refresh
      </button>
    </div>
  )
}

export function InvestigationInlineMessage({
  tone,
  children,
}: {
  tone: 'error' | 'warning' | 'success' | 'info'
  children: ReactNode
}) {
  const tones = {
    error: 'border-red-300/70 bg-red-50 text-red-800 dark:border-red-800/60 dark:bg-red-950/30 dark:text-red-200',
    warning: 'border-amber-300/70 bg-amber-50 text-amber-900 dark:border-amber-700/50 dark:bg-amber-950/30 dark:text-amber-100',
    success: 'border-green-300/70 bg-green-50 text-green-800 dark:border-green-700/50 dark:bg-green-950/30 dark:text-green-200',
    info: 'border-cyan/30 bg-cyan/10 text-slate-800 dark:border-cyan-700/40 dark:bg-cyan-950/30 dark:text-cyan-100',
  }
  return (
    <div role={tone === 'error' || tone === 'warning' ? 'alert' : 'status'} className={`rounded border px-3 py-2 text-sm ${tones[tone]}`}>
      {children}
    </div>
  )
}

export function InvestigationLoading({ message }: { message: string }) {
  return (
    <div role="status" className="tl-surface rounded-lg px-4 py-8 text-center text-sm text-slate dark:text-slate-300">
      {message}
    </div>
  )
}

export function InvestigationConfirmDialog({
  open,
  title,
  description,
  children,
  error,
  confirmLabel,
  confirmTone = 'danger',
  isConfirming,
  onCancel,
  onConfirm,
}: {
  open: boolean
  title: string
  description?: ReactNode
  children?: ReactNode
  error?: ReactNode
  confirmLabel: string
  confirmTone?: 'danger' | 'primary'
  isConfirming: boolean
  onCancel: () => void
  onConfirm: () => void
}) {
  const cancelRef = useRef<HTMLButtonElement | null>(null)
  const confirmClass = confirmTone === 'primary'
    ? 'bg-ink text-white dark:bg-cyan dark:text-[#053c2e]'
    : 'tl-button-danger'
  return (
    <DialogSurface
      open={open}
      title={title}
      description={children ? undefined : description}
      role="alertdialog"
      dismissDisabled={isConfirming}
      ariaBusy={isConfirming}
      initialFocusRef={cancelRef}
      panelClassName="max-w-xl [&_button]:min-h-11 md:[&_button]:min-h-0"
      onClose={onCancel}
      footer={
        <>
          <button ref={cancelRef} type="button" className="min-h-11 rounded border border-slate/20 px-3 py-2 text-sm font-semibold dark:border-white/10" disabled={isConfirming} onClick={onCancel}>Cancel</button>
          <button type="button" className={`min-h-11 rounded px-3 py-2 text-sm font-semibold disabled:opacity-60 ${confirmClass}`} disabled={isConfirming} onClick={onConfirm}>{isConfirming ? 'Working...' : confirmLabel}</button>
        </>
      }
    >
      {(children || error) ? (
        <>
          {children && description && <p>{description}</p>}
          {children}
          {error && <div className="mt-3"><InvestigationInlineMessage tone="error">{error}</InvestigationInlineMessage></div>}
        </>
      ) : undefined}
    </DialogSurface>
  )
}
