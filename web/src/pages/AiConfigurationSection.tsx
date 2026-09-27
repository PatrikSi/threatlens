import type { ReactNode } from 'react'

export function AiConfigurationSection({ id, title, description, children, shared = true, initiallyOpen = false }: {
  id: string
  title: string
  description: string
  children: ReactNode
  shared?: boolean
  initiallyOpen?: boolean
}) {
  return (
    <details id={id} open={initiallyOpen} data-ai-shared-settings={shared || undefined}
      className="min-w-0 scroll-mt-40 rounded-xl border border-slate/20 bg-white/80 dark:border-cyan-900/40 dark:bg-[#041612]/90">
      <summary className="cursor-pointer rounded-xl px-4 py-3 font-semibold focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2">
        {title}<span className="mt-1 block text-sm font-normal text-slate dark:text-slate-300">{description}</span>
      </summary>
      <div className="min-w-0 space-y-3 px-3 pb-3">{children}</div>
    </details>
  )
}
