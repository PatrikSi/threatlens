import { useId, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import type { NotificationTemplateVariable } from '../types/notifications'
import type { NotificationWebhookDraft } from './notificationWebhookDraft'
import { payloadFieldConflict, payloadVariableGroup, variableAppliesToEvent } from './webhookPayloadFields'

const INPUT = 'mt-1 block w-full min-w-0 rounded border border-slate/30 bg-white p-2 text-sm dark:bg-[#072019]'
const BUTTON = 'rounded border border-slate/30 px-3 py-1.5 text-sm disabled:opacity-50'

export function WebhookPayloadFieldPicker({ draft, onChange, disabled }: {
  draft: NotificationWebhookDraft
  onChange: (update: (current: NotificationWebhookDraft) => NotificationWebhookDraft) => void
  disabled: boolean
}) {
  const id = useId()
  const [search, setSearch] = useState('')
  const [selectedKey, setSelectedKey] = useState('')
  const [outputKey, setOutputKey] = useState('')
  const [showAll, setShowAll] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const query = useQuery({
    queryKey: ['notifications', 'template-variables'],
    queryFn: async ({ signal }) => {
      const result = await apiFetch<NotificationTemplateVariable[]>('/notifications/template-variables', { signal })
      if (!Array.isArray(result)) throw new Error('The payload field catalog returned an invalid response. Retry to reload available fields.')
      return result
    },
    staleTime: 60_000,
  })
  const variables = query.isError ? [] : query.data ?? []
  const selected = variables.find((variable) => variable.key === selectedKey)
  const relevant = selected ? variableAppliesToEvent(selected, draft.event_type) : false
  const choices = variables.filter((variable) =>
    (showAll || variableAppliesToEvent(variable, draft.event_type)) &&
    `${variable.key} ${variable.description} ${payloadVariableGroup(variable.key)}`.toLowerCase().includes(search.toLowerCase().trim()),
  )
  const groups = [...new Set(choices.map((variable) => payloadVariableGroup(variable.key)))]
  const fieldMode = draft.body_mode === 'json' || draft.body_mode === 'form'
  const conflict = fieldMode && selected ? payloadFieldConflict(draft.body_fields, outputKey.trim(), draft.body_mode === 'json') : null
  if (draft.payload_mode === 'automation_v1' || draft.body_mode === 'none') return null
  return (
    <fieldset disabled={disabled} className="mt-3 min-w-0 space-y-2 rounded border border-slate/25 p-3">
      <legend className="font-semibold">Include article and event data</legend>
      <p className="text-xs">Search available fields and add them to the payload. Existing fields and custom templates are preserved.</p>
      <div className="grid gap-3 md:grid-cols-2">
        <label htmlFor={`${id}-search`} className="text-sm">Search payload fields
          <input id={`${id}-search`} className={INPUT} type="search" placeholder="Full text, AI relevance, title…" value={search} onChange={(event) => setSearch(event.target.value)} />
        </label>
        <label htmlFor={`${id}-variable`} className="min-w-0 text-sm">Payload field
          <select id={`${id}-variable`} className={INPUT} value={selectedKey} onChange={(event) => {
            setSelectedKey(event.target.value)
            setOutputKey(event.target.value)
            setNotice(null)
          }}>
            <option value="">Select a field</option>
            {selectedKey && !choices.some((variable) => variable.key === selectedKey) && <option value={selectedKey}>{selectedKey} (outside current results)</option>}
            {groups.map((group) => <optgroup key={group} label={group}>{choices.filter((variable) => payloadVariableGroup(variable.key) === group).map((variable) =>
              <option key={variable.key} value={variable.key}>{variable.key} — {variable.description}</option>,
            )}</optgroup>)}
          </select>
        </label>
      </div>
      <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={showAll} onChange={(event) => setShowAll(event.target.checked)} />Show fields for other event types</label>
      {query.isLoading && <p role="status" className="text-sm">Loading payload fields…</p>}
      {query.isError && <p role="alert" className="text-sm">
        {resolveApiErrorMessage(query.error, 'Payload fields could not be loaded. Your current template is preserved.')}{' '}
        <button type="button" className={BUTTON} onClick={() => void query.refetch()}>Retry payload fields</button>
      </p>}
      {!query.isLoading && !query.isError && choices.length === 0 && <p className="text-sm">No matching fields. Change the search or include other event types.</p>}
      {selected && <>
        <p className="text-xs">{selected.description} <code>{`{{ ${selected.key} }}`}</code></p>
        {!relevant && <p className="text-xs text-amber-700 dark:text-amber-300">This field usually has no value for the selected event type.</p>}
        {selected.key.startsWith('item.full_text') && <p className="text-xs">
          Article text uses the retained extracted plain text, up to 128 KiB. Include item.full_text_status to distinguish complete, truncated, unavailable, or changed sources.
          {' '}New RSS events can arrive before article retrieval; prefer AI article analysis ready or an extraction event.
        </p>}
        {fieldMode ? <label htmlFor={`${id}-key`} className="block text-sm">Output key
          <input id={`${id}-key`} className={INPUT} value={outputKey} onChange={(event) => { setOutputKey(event.target.value); setNotice(null) }} />
        </label> : <p className="text-xs">Appends a placeholder to the raw body. Use JSON body fields when values need JSON escaping, especially article text.</p>}
        {conflict && <p role="status" className="text-xs">{conflict}</p>}
      </>}
      <button type="button" className={BUTTON} disabled={!selected || Boolean(conflict) || (fieldMode && !outputKey.trim())} onClick={() => {
        if (!selected || conflict) return
        const token = `{{ ${selected.key} }}`
        onChange((current) => {
          if (current.body_mode === 'raw') return { ...current, body_template: `${current.body_template}${current.body_template ? '\n' : ''}${token}` }
          if (current.body_mode !== 'json' && current.body_mode !== 'form') return current
          if (payloadFieldConflict(current.body_fields, outputKey.trim(), current.body_mode === 'json')) return current
          return { ...current, body_fields: [...current.body_fields, { key: outputKey.trim(), value: token }] }
        })
        setNotice(`Added ${selected.key} to the payload draft.`)
      }}>{fieldMode ? 'Add payload field' : 'Append to raw body'}</button>
      {notice && <p role="status" className="text-sm">{notice}</p>}
    </fieldset>
  )
}
