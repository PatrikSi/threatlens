import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../api/client'
import { accessibleQueryData } from '../api/queryData'
import { resolveApiErrorMessage } from '../api/errors'
import type { DataPolicyOverview } from '../types/api'
import { TEAM_BUTTON } from './teamPresentation'

export function TeamAIHandlingRestrictions({ value, onChange, approved, destinations }: {
  value: Record<string, string[]>
  onChange: (value: Record<string, string[]>) => void
  approved: string[]
  destinations: { key: string; name: string }[]
}) {
  const query = useQuery({
    queryKey: ['iam', 'team-ai-handling-labels'],
    queryFn: ({ signal }) => apiFetch<DataPolicyOverview>('/iam/data-policies', { signal }),
  })
  const labels = accessibleQueryData(query)?.labels ?? []
  const name = (key: string) => destinations.find((entry) => entry.key === key)?.name ?? (key === 'legacy' ? 'Legacy provider settings' : key)
  return <fieldset className="space-y-3">
    <legend className="font-semibold">Handling destination restrictions</legend>
    <p className="text-xs">Each label uses the approved destinations unless restricted here. An empty destination selection blocks AI for that label. Both captured and current article labels apply.</p>
    {query.isError && <p role="alert">
      {resolveApiErrorMessage(query.error, 'Handling labels could not be loaded. Existing restrictions are preserved; label selection requires read:iam.')}
      {' '}<button type="button" className={TEAM_BUTTON} onClick={() => void query.refetch()}>Retry handling labels</button>
    </p>}
    <label className="block text-sm">Restrict a handling label
      <select className="mt-1 block w-full rounded border p-2 dark:bg-[#072019]" value="" disabled={query.isError || query.isPending}
        onChange={(event) => { if (event.target.value) onChange({ ...value, [event.target.value]: [...approved] }) }}>
        <option value="">Choose a handling label</option>
        {labels.filter((label) => !(label.id in value)).map((label) => <option key={label.id} value={label.id}>{label.name}{label.is_active ? '' : ' (inactive)'}</option>)}
      </select>
    </label>
    {Object.entries(value).map(([id, selected]) => <fieldset key={id} className="space-y-2 rounded border p-3">
      <legend>{labels.find((label) => label.id === id)?.name ?? `Unavailable handling label (${id})`}</legend>
      {selected.length === 0 && <p className="text-sm">AI requests are blocked for this label.</p>}
      {[...new Set([...approved, ...selected])].map((key) => <label key={key} className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={selected.includes(key)} onChange={(event) => onChange({ ...value, [id]: event.target.checked ? [...selected, key] : selected.filter((entry) => entry !== key) })} />
        {name(key)}{!approved.includes(key) && ' (no longer approved; remove to save)'}
      </label>)}
      <button type="button" className={TEAM_BUTTON} onClick={() => { const next = { ...value }; delete next[id]; onChange(next) }}>Use all approved destinations</button>
    </fieldset>)}
  </fieldset>
}
