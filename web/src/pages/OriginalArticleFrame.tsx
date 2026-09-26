import { useEffect, useState } from 'react'

export function OriginalArticleFrame({
  url,
  title,
  isResizing,
}: {
  url: string
  title: string
  isResizing: boolean
}) {
  const [state, setState] = useState<'loading' | 'loaded' | 'possibly_blocked'>(
    'loading',
  )
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setState((current) =>
        current === 'loaded' ? current : 'possibly_blocked',
      )
    }, 5000)
    return () => window.clearTimeout(timer)
  }, [])
  return (
    <>
      {state !== 'loaded' && (
        <div
          role="status"
          className={`border-b px-4 py-2 text-xs ${
            state === 'possibly_blocked'
              ? 'border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900/40 dark:bg-amber-950/20 dark:text-amber-200'
              : 'border-slate/20 bg-slate-50 text-slate dark:border-cyan-900/40 dark:bg-white/[0.03]'
          }`}
        >
          {state === 'possibly_blocked'
            ? 'Preview is still loading. Open the original source if it does not render here.'
            : 'Loading original site...'}
        </div>
      )}
      <div
        className={`min-h-0 flex-1 bg-white dark:bg-[#020b09] ${isResizing ? 'cursor-ew-resize select-none' : ''}`}
      >
        <iframe
          title={`Original article preview: ${title}`}
          src={url}
          className={`h-full w-full border-0 bg-white ${isResizing ? 'pointer-events-none' : ''}`}
          sandbox="allow-popups allow-popups-to-escape-sandbox"
          referrerPolicy="no-referrer"
          onLoad={() => setState('loaded')}
        />
      </div>
    </>
  )
}
