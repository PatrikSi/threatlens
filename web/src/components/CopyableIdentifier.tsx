import { useState } from 'react'

export function CopyableIdentifier({
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
    <div className="min-w-0">
      <div className="flex min-w-0 items-center gap-1.5">
        <code className="min-w-0 break-all text-[11px] text-slate-700 dark:text-slate-200">
          {value}
        </code>
        <button
          type="button"
          className="shrink-0 rounded border border-slate/20 px-1.5 py-0.5 text-[11px] font-semibold text-slate-700 dark:border-cyan-900/40 dark:text-slate-200"
          onClick={() => void copy()}
          aria-label={copyState === 'copied'
            ? `${label} copied`
            : `Copy ${label.toLowerCase()}`}
          title={`Copy ${label.toLowerCase()}`}
        >
          {copyState === 'copied' ? 'Copied' : 'Copy'}
        </button>
      </div>
      <span className="sr-only" aria-live="polite">
        {copyState === 'copied' ? `${label} copied to clipboard.` : ''}
      </span>
      {copyState === 'failed' && (
        <p role="alert" className="mt-1 text-xs text-red-600 dark:text-red-300">
          Clipboard access was denied. Select and copy the value manually.
        </p>
      )}
    </div>
  )
}
