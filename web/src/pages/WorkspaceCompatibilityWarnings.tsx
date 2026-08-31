import { workspaceWarningMessage } from '../workspace/workspaceModel'

export function WorkspaceCompatibilityWarnings({ warnings }: { warnings: readonly string[] }) {
  const uniqueWarnings = [...new Set(warnings)]
  if (uniqueWarnings.length === 0) return null

  return (
    <details className="mt-3 rounded border border-amber-300/60 bg-amber-50/70 px-3 py-2 text-sm text-amber-950 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100">
      <summary className="cursor-pointer font-semibold">
        Version compatibility warnings ({uniqueWarnings.length})
      </summary>
      <ul className="mt-2 space-y-1">
        {uniqueWarnings.map((warning) => (
          <li key={warning} className="break-words [overflow-wrap:anywhere]">
            {workspaceWarningMessage(warning)}
          </li>
        ))}
      </ul>
    </details>
  )
}
