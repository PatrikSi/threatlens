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
  panelClassName = 'max-w-xl',
  bodyClassName = 'mt-4 space-y-3 text-sm text-slate dark:text-white/75',
  footerClassName = 'mt-5 flex flex-wrap items-center justify-end gap-2',
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

  const describedBy = [description ? descriptionId : null, children ? bodyId : null].filter(Boolean).join(' ') || undefined

  const dialog = (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/60 px-4 py-6">
      <div
        ref={dialogRef}
        data-dialog-root="true"
        className={`w-full rounded-2xl border border-slate/20 bg-white p-5 shadow-2xl dark:border-cyan-900/40 dark:bg-[#041612] ${panelClassName}`}
        role={role}
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={describedBy}
        aria-busy={ariaBusy}
        tabIndex={-1}
      >
        <div className="flex items-start justify-between gap-4">
          <div className="space-y-1">
            {eyebrow && <p className="text-xs font-semibold text-slate dark:text-white/55">{eyebrow}</p>}
            <h3 id={titleId} className="font-display text-xl text-ink dark:text-white">
              {title}
            </h3>
            {description && (
              <div id={descriptionId} className="text-sm text-slate dark:text-white/75">
                {description}
              </div>
            )}
          </div>
          <button
            ref={closeButtonRef}
            type="button"
            className="rounded border border-slate/20 px-2 py-1 text-xs dark:border-cyan-900/40"
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
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const dismissDisabled = cancelDisabled || isConfirming
  const cancelButtonRef = useRef<HTMLButtonElement | null>(null)
  const initialFocusRef = dismissDisabled ? undefined : cancelButtonRef
  const showHeaderDescription = Boolean(description) && !children
  const confirmButtonClassName =
    confirmTone === 'primary'
      ? 'rounded bg-ink px-3 py-2 text-sm font-semibold text-white hover:bg-slate-900 disabled:cursor-not-allowed disabled:opacity-60 dark:bg-cyan dark:text-[#053c2e] dark:hover:bg-cyan/90'
      : 'rounded bg-red-600 px-3 py-2 text-sm font-semibold text-white hover:bg-red-500 disabled:cursor-not-allowed disabled:opacity-60'

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
