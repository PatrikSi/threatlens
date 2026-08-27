import { useEffect, useRef, useState } from 'react'

import { resolveApiErrorMessage } from '../api/errors'
import { ConfirmDialog, DialogSurface } from '../components/ConfirmDialog'
import type { MFARecoveryCodesResponse } from '../types/identity'
import { formatDateTime } from '../utils/datetime'
import { disableWhen } from './accountSecurityUtils'

export type SensitiveAction = 'regenerate' | 'disable'

export type SensitiveDraft = {
  currentPassword: string
  code: string
}

export function SecretValue({
  label,
  value,
}: {
  label: string
  value: string
}) {
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'failed'>(
    'idle',
  )
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value)
      setCopyState('copied')
    } catch {
      setCopyState('failed')
    }
  }
  return (
    <div>
      <p className="text-xs font-semibold text-slate dark:text-slate-300">
        {label}
      </p>
      <div className="mt-1 flex min-w-0 flex-col gap-2 sm:flex-row">
        <code className="min-w-0 flex-1 break-all rounded border border-slate/20 bg-slate/5 px-2 py-2 text-xs dark:border-cyan-900/40 dark:bg-white/[0.03]">
          {value}
        </code>
        <button
          type="button"
          className="min-h-11 shrink-0 rounded border border-slate/20 px-3 py-2 text-sm font-semibold dark:border-cyan-900/40"
          onClick={() => void copy()}
          aria-label={`Copy ${label.toLowerCase()}`}
        >
          {copyState === 'copied' ? 'Copied' : 'Copy'}
        </button>
      </div>
      {copyState === 'failed' && (
        <p role="alert" className="mt-1 text-xs text-red-600 dark:text-red-300">
          Clipboard access was denied. Select and copy the value manually.
        </p>
      )}
    </div>
  )
}

export function SensitiveActionDialog({
  action,
  draft,
  error,
  isPending,
  actionsDisabled,
  onDraftChange,
  onConfirm,
  onCancel,
}: {
  action: SensitiveAction | null
  draft: SensitiveDraft
  error: unknown
  isPending: boolean
  actionsDisabled: boolean
  onDraftChange: (draft: SensitiveDraft) => void
  onConfirm: () => void
  onCancel: () => void
}) {
  const regenerating = action === 'regenerate'
  const passwordInputRef = useRef<HTMLInputElement | null>(null)
  return (
    <ConfirmDialog
      open={Boolean(action)}
      title={
        regenerating
          ? 'Generate new recovery codes?'
          : 'Disable multi-factor authentication?'
      }
      description={
        regenerating
          ? 'All existing recovery codes will stop working immediately. Confirm with a current code from your authenticator.'
          : 'Local password sign-in will no longer require a second factor. Other browser sessions will be revoked.'
      }
      confirmLabel={regenerating ? 'Generate new codes' : 'Disable MFA'}
      confirmTone={regenerating ? 'primary' : 'danger'}
      confirmDisabled={disableWhen(
        actionsDisabled,
        !draft.currentPassword,
        !draft.code.trim(),
      )}
      isConfirming={isPending}
      initialFocusRef={passwordInputRef}
      onConfirm={onConfirm}
      onCancel={onCancel}
    >
      <label htmlFor="mfa-sensitive-password" className="block font-semibold">
        Current password
      </label>
      <input
        ref={passwordInputRef}
        id="mfa-sensitive-password"
        type="password"
        autoComplete="current-password"
        value={draft.currentPassword}
        onChange={(event) =>
          onDraftChange({ ...draft, currentPassword: event.target.value })
        }
        className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
        disabled={actionsDisabled}
      />
      <label htmlFor="mfa-sensitive-code" className="mt-3 block font-semibold">
        {regenerating
          ? '6-digit authenticator code'
          : 'Authenticator or recovery code'}
      </label>
      <input
        id="mfa-sensitive-code"
        type="text"
        inputMode={regenerating ? 'numeric' : 'text'}
        autoComplete="one-time-code"
        value={draft.code}
        onChange={(event) =>
          onDraftChange({ ...draft, code: event.target.value })
        }
        className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 font-mono dark:border-cyan-900/40 dark:bg-[#072019]"
        disabled={actionsDisabled}
      />
      {actionsDisabled && (
        <p role="alert" className="mt-3 text-sm text-amber-800 dark:text-amber-200">
          The current MFA status is unavailable. Close this dialog, refresh
          security status, and retry.
        </p>
      )}
      {Boolean(error) && (
        <DialogError
          error={error}
          fallback={
            regenerating
              ? 'Recovery codes could not be generated'
              : 'MFA could not be disabled'
          }
        />
      )}
    </ConfirmDialog>
  )
}

export function RecoveryCodesDialog({
  data,
  onDownload,
  onDone,
}: {
  data: MFARecoveryCodesResponse | null
  onDownload: (codes: string[], generatedAt: string) => void
  onDone: () => void
}) {
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'failed'>(
    'idle',
  )

  useEffect(() => {
    setCopyState('idle')
  }, [data?.generated_at])

  const copyAll = async () => {
    if (!data) return
    try {
      await navigator.clipboard.writeText(data.recovery_codes.join('\n'))
      setCopyState('copied')
    } catch {
      setCopyState('failed')
    }
  }

  return (
    <DialogSurface
      open={Boolean(data)}
      title="Store your recovery codes"
      description="These codes are shown once. Store them somewhere secure before closing this dialog."
      dismissDisabled
      describeBody={false}
      closeLabel="Recovery codes must be acknowledged"
      onClose={() => undefined}
      footer={
        <>
          <button
            type="button"
            className="min-h-11 rounded border border-slate/20 px-3 py-2 text-sm font-semibold dark:border-cyan-900/40"
            onClick={() => void copyAll()}
          >
            Copy all codes
          </button>
          <button
            type="button"
            className="min-h-11 rounded border border-slate/20 px-3 py-2 text-sm font-semibold dark:border-cyan-900/40"
            onClick={() =>
              data && onDownload(data.recovery_codes, data.generated_at)
            }
          >
            Download codes
          </button>
          <button
            type="button"
            className="min-h-11 rounded bg-ink px-3 py-2 text-sm font-semibold text-white dark:bg-cyan dark:text-[#053c2e]"
            onClick={onDone}
          >
            I stored these codes
          </button>
        </>
      }
    >
      <ol
        className="grid grid-cols-1 gap-2 sm:grid-cols-2"
        aria-label="Recovery codes"
      >
        {data?.recovery_codes.map((code) => (
          <li key={code}>
            <code className="block rounded border border-slate/20 bg-slate/5 px-3 py-2 text-center text-sm dark:border-cyan-900/40 dark:bg-white/[0.03]">
              {code}
            </code>
          </li>
        ))}
      </ol>
      <div aria-live="polite" aria-atomic="true">
        {copyState === 'copied' && (
          <p
            role="status"
            className="text-sm text-green-700 dark:text-green-300"
          >
            All recovery codes copied.
          </p>
        )}
        {copyState === 'failed' && (
          <p role="alert" className="text-sm text-red-600 dark:text-red-300">
            Clipboard access was denied. Select the codes manually or download
            them.
          </p>
        )}
      </div>
      <p className="text-xs text-slate dark:text-slate-300">
        Generated {formatDateTime(data?.generated_at)}
      </p>
    </DialogSurface>
  )
}

export function DialogError({
  error,
  fallback,
}: {
  error: unknown
  fallback: string
}) {
  return (
    <p
      role="alert"
      aria-live="assertive"
      className="mt-3 rounded border border-red-300/60 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-200"
    >
      {resolveApiErrorMessage(error, fallback)}
    </p>
  )
}
