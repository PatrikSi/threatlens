import { ReactNode, useEffect, useId, useRef } from 'react'

type ConfirmDialogProps = {
  open: boolean
  title: string
  description?: ReactNode
  children?: ReactNode
  confirmLabel: string
  cancelLabel?: string
  closeLabel?: string
  isConfirming?: boolean
  confirmDisabled?: boolean
  cancelDisabled?: boolean
  confirmTone?: 'danger' | 'primary'
  role?: 'dialog' | 'alertdialog'
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
  closeLabel = 'Close dialog',
  isConfirming = false,
  confirmDisabled = false,
  cancelDisabled = false,
  confirmTone = 'danger',
  role = 'alertdialog',
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const titleId = useId()
  const descriptionId = useId()
  const dialogRef = useRef<HTMLDivElement | null>(null)
  const cancelButtonRef = useRef<HTMLButtonElement | null>(null)
  const previousFocusRef = useRef<HTMLElement | null>(null)
  const dismissDisabled = cancelDisabled || isConfirming
  const dismissDisabledRef = useRef(dismissDisabled)

  useEffect(() => {
    dismissDisabledRef.current = dismissDisabled
  }, [dismissDisabled])

  useEffect(() => {
    if (!open) {
      return
    }

    previousFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const focusTarget = dismissDisabled ? dialogRef.current : cancelButtonRef.current ?? dialogRef.current
    window.requestAnimationFrame(() => {
      focusTarget?.focus()
    })

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !dismissDisabledRef.current) {
        event.preventDefault()
        onCancel()
        return
      }

      if (event.key !== 'Tab' || !dialogRef.current) {
        return
      }

      const focusable = Array.from(
        dialogRef.current.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
        ),
      ).filter((element) => !element.hasAttribute('disabled') && element.getAttribute('aria-hidden') !== 'true')
      if (!focusable.length) {
        event.preventDefault()
        dialogRef.current.focus()
        return
      }

      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      const active = document.activeElement

      if (event.shiftKey && active === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && active === last) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      if (previousFocusRef.current?.isConnected) {
        previousFocusRef.current.focus()
      }
      previousFocusRef.current = null
    }
  }, [dismissDisabled, onCancel, open])

  if (!open) {
    return null
  }

  const hasBody = Boolean(description || children)
  const showHeaderDescription = Boolean(description) && !children
  const confirmButtonClassName =
    confirmTone === 'primary'
      ? 'rounded bg-ink px-3 py-2 text-sm font-semibold text-white hover:bg-slate-900 disabled:cursor-not-allowed disabled:opacity-60 dark:bg-cyan dark:text-[#053c2e] dark:hover:bg-cyan/90'
      : 'rounded bg-red-600 px-3 py-2 text-sm font-semibold text-white hover:bg-red-500 disabled:cursor-not-allowed disabled:opacity-60'

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/60 px-4 py-6">
      <div
        ref={dialogRef}
        className="w-full max-w-xl rounded-2xl border border-slate/20 bg-white p-5 shadow-2xl dark:border-cyan-900/40 dark:bg-[#041612]"
        role={role}
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={hasBody ? descriptionId : undefined}
        aria-busy={isConfirming}
        tabIndex={-1}
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
            ref={cancelButtonRef}
            type="button"
            className="rounded border border-slate/20 px-2 py-1 text-xs dark:border-cyan-900/40"
            onClick={onCancel}
            disabled={dismissDisabled}
            aria-label={closeLabel}
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
            disabled={dismissDisabled}
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            className={confirmButtonClassName}
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
