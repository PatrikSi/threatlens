import { ReactNode, useEffect, useId } from 'react'

type ConfirmDialogProps = {
  open: boolean
  title: string
  description?: ReactNode
  children?: ReactNode
  confirmLabel: string
  cancelLabel?: string
  isConfirming?: boolean
  confirmDisabled?: boolean
  onConfirm: () => void
  onCancel: () => void
}

export function ConfirmDialog({
  open,
  title,
  description,
  children,
  confirmLabel,
  cancelLabel = 'Cancel',
  isConfirming = false,
  confirmDisabled = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const titleId = useId()
  const descriptionId = useId()

  useEffect(() => {
    if (!open) {
      return
    }

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        onCancel()
      }
    }

    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [open, onCancel])

  if (!open) {
    return null
  }

  const hasBody = Boolean(description || children)
  const showHeaderDescription = Boolean(description) && !children

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/60 px-4 py-6">
      <div
        className="w-full max-w-xl rounded-2xl border border-slate/20 bg-white p-5 shadow-2xl dark:border-cyan-900/40 dark:bg-[#041612]"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={hasBody ? descriptionId : undefined}
      >
        <div className="flex items-start justify-between gap-4">
          <div className="space-y-1">
            <h3 id={titleId} className="font-display text-xl text-ink dark:text-white">
              {title}
            </h3>
            {showHeaderDescription && (
              <p id={descriptionId} className="text-sm text-slate dark:text-white/75">
                {description}
              </p>
            )}
          </div>
          <button
            type="button"
            className="rounded border border-slate/20 px-2 py-1 text-xs dark:border-cyan-900/40"
            onClick={onCancel}
          >
            Close
          </button>
        </div>

        {hasBody && (
          <div id={showHeaderDescription ? undefined : descriptionId} className="mt-4 space-y-3 text-sm text-slate dark:text-white/75">
            {!showHeaderDescription && description && <p>{description}</p>}
            {children}
          </div>
        )}

        <div className="mt-5 flex flex-wrap items-center justify-end gap-2">
          <button
            type="button"
            className="rounded border border-slate/20 px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-slate/5 dark:border-cyan-900/40 dark:text-slate-100 dark:hover:bg-white/[0.04]"
            onClick={onCancel}
            autoFocus
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            className="rounded bg-red-600 px-3 py-2 text-sm font-semibold text-white hover:bg-red-500 disabled:cursor-not-allowed disabled:opacity-60"
            onClick={onConfirm}
            disabled={confirmDisabled || isConfirming}
          >
            {isConfirming ? 'Working...' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
