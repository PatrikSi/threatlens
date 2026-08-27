import { ReactNode, RefObject, useId, useRef } from 'react'
import { createPortal } from 'react-dom'

import { useDialogFocusTrap } from '../hooks/useDialogFocusTrap'

type DialogSurfaceProps = {
  open: boolean
  title: ReactNode
  description?: ReactNode
  eyebrow?: ReactNode
  children?: ReactNode
  footer?: ReactNode
  role?: 'dialog' | 'alertdialog'
  closeLabel?: string
  dismissDisabled?: boolean
  ariaBusy?: boolean
  initialFocusRef?: RefObject<HTMLElement | null>
  describeBody?: boolean
  panelClassName?: string
  bodyClassName?: string
  footerClassName?: string
  onClose: () => void
}

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
  initialFocusRef?: RefObject<HTMLElement | null>
  onConfirm: () => void
  onCancel: () => void
}

export function DialogSurface({
  open,
  title,
  description,
  eyebrow,
  children,
  footer,
  role = 'dialog',
  closeLabel = 'Close dialog',
  dismissDisabled = false,
  ariaBusy = false,
  initialFocusRef,
  describeBody = true,
  panelClassName = 'max-w-xl',
  bodyClassName = 'mt-4 space-y-3 text-sm text-slate dark:text-white/75',
  footerClassName = 'mt-5 grid grid-cols-2 gap-2 sm:flex sm:flex-wrap sm:items-center sm:justify-end',
  onClose,
}: DialogSurfaceProps) {
  const titleId = useId()
  const descriptionId = useId()
  const bodyId = useId()
  const dialogRef = useRef<HTMLDivElement | null>(null)
  const closeButtonRef = useRef<HTMLButtonElement | null>(null)
  useDialogFocusTrap({
    open,
    dialogRef,
    closeButtonRef,
    initialFocusRef,
    dismissDisabled,
    onClose,
  })

  if (!open) {
    return null
  }

  const describedBy =
    [
      description ? descriptionId : null,
      children && describeBody ? bodyId : null,
    ]
      .filter(Boolean)
      .join(' ') || undefined

  const dialog = (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/60 px-3 py-3 sm:px-4 sm:py-6">
      <div
        ref={dialogRef}
        data-dialog-root="true"
        className={`max-h-[calc(100dvh-1.5rem)] w-full overflow-y-auto rounded-lg border border-slate/20 bg-white p-4 shadow-2xl sm:max-h-none sm:overflow-visible sm:rounded-2xl sm:p-5 dark:border-cyan-900/40 dark:bg-[#041612] ${panelClassName}`}
        role={role}
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={describedBy}
        aria-busy={ariaBusy}
        tabIndex={-1}
      >
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0 flex-1 space-y-1">
            {eyebrow && (
              <p className="text-xs font-semibold text-slate dark:text-white/55">
                {eyebrow}
              </p>
            )}
            <h3
              id={titleId}
              className="break-words font-display text-xl text-ink [overflow-wrap:anywhere] dark:text-white"
            >
              {title}
            </h3>
            {description && (
              <div
                id={descriptionId}
                className="text-sm text-slate dark:text-white/75"
              >
                {description}
              </div>
            )}
          </div>
          <button
            ref={closeButtonRef}
            type="button"
            className="shrink-0 rounded border border-slate/20 px-2 py-1 text-xs dark:border-cyan-900/40"
            onClick={onClose}
            disabled={dismissDisabled}
            aria-label={closeLabel}
          >
            Close
          </button>
        </div>

        {children && (
          <div id={bodyId} className={bodyClassName}>
            {children}
          </div>
        )}

        {footer && <div className={footerClassName}>{footer}</div>}
      </div>
    </div>
  )

  if (typeof document === 'undefined') {
    return dialog
  }

  return createPortal(dialog, document.body)
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
  initialFocusRef: requestedInitialFocusRef,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const dismissDisabled = cancelDisabled || isConfirming
  const cancelButtonRef = useRef<HTMLButtonElement | null>(null)
  const initialFocusRef = dismissDisabled
    ? undefined
    : (requestedInitialFocusRef ?? cancelButtonRef)
  const showHeaderDescription = Boolean(description) && !children
  const confirmButtonClassName =
    confirmTone === 'primary'
      ? 'rounded bg-ink px-3 py-2 text-sm font-semibold text-white hover:bg-slate-900 disabled:cursor-not-allowed disabled:opacity-60 dark:bg-cyan dark:text-[#053c2e] dark:hover:bg-cyan/90'
      : 'tl-button-danger rounded px-3 py-2 text-sm font-semibold disabled:cursor-not-allowed disabled:opacity-60'

  return (
    <DialogSurface
      open={open}
      title={title}
      description={showHeaderDescription ? description : undefined}
      role={role}
      closeLabel={closeLabel}
      dismissDisabled={dismissDisabled}
      ariaBusy={isConfirming}
      initialFocusRef={initialFocusRef}
      onClose={onCancel}
      footer={
        <>
          <button
            ref={cancelButtonRef}
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
        </>
      }
    >
      {!showHeaderDescription && description && <p>{description}</p>}
      {children}
    </DialogSurface>
  )
}
