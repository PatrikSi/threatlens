import { useId, useState } from 'react'
import { resolveApiErrorMessage } from '../api/errors'
import type { WebhookConditionField } from '../types/webhookAutomation'
import { useWebhookConditionChoices } from './useWebhookConditionChoices'

const INPUT = 'block w-full min-w-0 rounded border border-slate/30 bg-white p-2 text-sm dark:bg-[#072019]'
const BUTTON = 'rounded border border-slate/30 px-2 py-1 text-xs disabled:opacity-50'

export function WebhookConditionValues({ field, values, onChange }: {
  field: WebhookConditionField
  values: string[]
  onChange: (values: string[]) => void
}) {
  const id = useId()
  const [search, setSearch] = useState('')
  const catalog = useWebhookConditionChoices(field)
  const selected = values.map((value) => value.trim()).filter(Boolean)
  const choices = catalog.choices.filter((choice) => !selected.includes(choice.value) && `${choice.label} ${choice.value}`.toLowerCase().includes(search.toLowerCase().trim()))
  const hasChoices = catalog.remote || catalog.choices.length > 0
  return (
    <div className="min-w-0 space-y-2">
      {hasChoices && <>
        <label htmlFor={`${id}-search`} className="block text-xs">Search values
          <input id={`${id}-search`} className={INPUT} type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search by name or value" />
        </label>
        <label htmlFor={`${id}-choice`} className="block text-xs">Add a value
          <select id={`${id}-choice`} className={INPUT} value="" onChange={(event) => {
            if (event.target.value && !selected.includes(event.target.value)) onChange([...selected, event.target.value])
          }}>
            <option value="">Choose a value</option>
            {choices.map((choice) => <option key={choice.value} value={choice.value}>{choice.label}{catalog.remote && field !== 'tag' ? ` · ${choice.value}` : ''}</option>)}
          </select>
        </label>
        {catalog.isLoading && <p role="status" className="text-xs">Loading available values…</p>}
        {catalog.error != null && <p role="alert" className="text-xs">{resolveApiErrorMessage(catalog.error, 'Available values could not be loaded. Saved values are preserved.')} <button className={BUTTON} type="button" onClick={() => void catalog.refetch()}>Retry values</button></p>}
        {!catalog.isLoading && !catalog.error && choices.length === 0 && <p className="text-xs">No additional matching values in this list. You can enter exact values below.</p>}
        {field === 'alert_rule_id' && <p className="text-xs">Searches up to 1,000 recent accessible alert rules; use an exact ID for older rules.</p>}
        {field === 'team_id' && <div className="flex flex-wrap items-center gap-2 text-xs">
          <span>Team page {catalog.page}{catalog.total != null ? ` of ${Math.max(1, Math.ceil(catalog.total / 100))}` : ''}. Search applies to this page.</span>
          <button type="button" className={BUTTON} disabled={catalog.page === 1 || catalog.isLoading} onClick={() => catalog.setPage(catalog.page - 1)}>Previous teams</button>
          <button type="button" className={BUTTON} disabled={catalog.total == null || catalog.page * 100 >= catalog.total || catalog.isLoading} onClick={() => catalog.setPage(catalog.page + 1)}>Next teams</button>
        </div>}
      </>}
      {selected.length > 0 && <ul aria-label="Selected condition values" className="flex flex-wrap gap-1">
        {selected.map((value, index) => {
          const choice = catalog.choices.find((entry) => entry.value === value)
          return <li key={`${value}-${index}`} className="max-w-full rounded border border-slate/25 px-2 py-1 text-xs">
            <span className="break-all">{choice?.label ?? value}</span>{' '}
            <button type="button" aria-label={`Remove value ${choice?.label ?? value}`} className="font-semibold" onClick={() => onChange(selected.filter((_, position) => position !== index))}>Remove</button>
          </li>
        })}
      </ul>}
      <details open={!hasChoices || undefined}>
        <summary className="cursor-pointer text-xs">Enter exact values</summary>
        <label htmlFor={`${id}-manual`} className="mt-1 block text-xs">Values (comma-separated)
          <input id={`${id}-manual`} className={INPUT} value={values.join(',')} onChange={(event) => onChange(event.target.value.split(','))} />
        </label>
        <p className="text-xs">Existing values remain selected even if no longer listed. Names and identifiers must match exactly.</p>
      </details>
    </div>
  )
}
