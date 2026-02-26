import { FormEvent, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
import { AlertInterest } from '../types/api'

const ALERT_CATEGORIES = [
  { value: 'software', label: 'Software' },
  { value: 'vendor', label: 'Vendor' },
  { value: 'apt_group', label: 'APT Group' },
  { value: 'vulnerability', label: 'Vulnerability' },
  { value: 'malware', label: 'Malware' },
  { value: 'technique', label: 'Technique' },
  { value: 'campaign', label: 'Campaign' },
  { value: 'infrastructure', label: 'Infrastructure' },
  { value: 'other', label: 'Other' },
]

export function AlertsPage() {
  const queryClient = useQueryClient()

  const [name, setName] = useState('')
  const [category, setCategory] = useState<string>(ALERT_CATEGORIES[0].value)
  const [keywordsText, setKeywordsText] = useState('')
  const [showDisabled, setShowDisabled] = useState(false)

  const alertsQuery = useQuery({
    queryKey: ['alerts', showDisabled],
    queryFn: () => apiFetch<AlertInterest[]>(`/alerts?include_disabled=${showDisabled}`),
  })

  const createAlert = useMutation({
    mutationFn: (payload: { name: string; category: string; keywords: string[] }) =>
      apiFetch<AlertInterest>('/alerts', {
        method: 'POST',
        body: JSON.stringify({
          ...payload,
          enabled: true,
        }),
      }),
    onSuccess: () => {
      setName('')
      setKeywordsText('')
      void queryClient.invalidateQueries({ queryKey: ['alerts'] })
    },
  })

  const updateAlert = useMutation({
    mutationFn: (payload: { id: string; body: Record<string, unknown> }) =>
      apiFetch<AlertInterest>(`/alerts/${payload.id}`, {
        method: 'PATCH',
        body: JSON.stringify(payload.body),
      }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['alerts'] }),
  })

  const deleteAlert = useMutation({
    mutationFn: (id: string) =>
      apiFetch(`/alerts/${id}`, {
        method: 'DELETE',
      }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['alerts'] }),
  })

  const groupedAlerts = useMemo(() => {
    const groups = new Map<string, AlertInterest[]>()
    for (const categoryOption of ALERT_CATEGORIES) {
      groups.set(categoryOption.value, [])
    }
    for (const alert of alertsQuery.data ?? []) {
      const group = groups.get(alert.category)
      if (group) {
        group.push(alert)
        continue
      }
      if (!groups.has('other')) {
        groups.set('other', [])
      }
      groups.get('other')?.push(alert)
    }
    return groups
  }, [alertsQuery.data])

  const onCreate = (event: FormEvent) => {
    event.preventDefault()
    const parsedKeywords = keywordsText
      .split(',')
      .map((keyword) => keyword.trim())
      .filter(Boolean)

    if (!name.trim() || parsedKeywords.length === 0) {
      return
    }

    createAlert.mutate({
      name: name.trim(),
      category,
      keywords: parsedKeywords,
    })
  }

  return (
    <div className="grid gap-4 xl:grid-cols-[440px_1fr]">
      <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
        <h2 className="font-display text-xl">Alert Interests</h2>
        <p className="mt-1 text-sm text-slate dark:text-slate-300">
          Define focused interests by category. Dashboard alert windows match item text against these keywords.
        </p>

        <form className="mt-4 space-y-3" onSubmit={onCreate}>
          <div>
            <label className="text-sm font-semibold">Interest Name</label>
            <input
              className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Microsoft Security Updates"
              required
            />
          </div>

          <div>
            <label className="text-sm font-semibold">Category</label>
            <select
              className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
              value={category}
              onChange={(event) => setCategory(event.target.value)}
            >
              {ALERT_CATEGORIES.map((entry) => (
                <option key={entry.value} value={entry.value}>
                  {entry.label}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="text-sm font-semibold">Keywords (comma-separated)</label>
            <textarea
              className="mt-1 h-24 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
              value={keywordsText}
              onChange={(event) => setKeywordsText(event.target.value)}
              placeholder="microsoft, exchange, entra id"
              required
            />
          </div>

          <button
            className="rounded bg-ink px-3 py-2 text-white disabled:opacity-50 dark:bg-cyan dark:text-[#053c2e]"
            type="submit"
            disabled={createAlert.isPending}
          >
            Add Interest
          </button>
          {createAlert.isError && <p className="text-sm text-red-600">Failed to create alert interest.</p>}
        </form>
      </section>

      <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="font-display text-xl">Configured Alerts</h2>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={showDisabled}
              onChange={(event) => setShowDisabled(event.target.checked)}
              className="accent-cyan"
            />
            Include disabled
          </label>
        </div>

        <div className="mt-4 space-y-4">
          {ALERT_CATEGORIES.map((categoryOption) => {
            const entries = groupedAlerts.get(categoryOption.value) ?? []
            if (!entries.length) {
              return null
            }
            return (
              <div key={categoryOption.value} className="rounded border border-slate/20 p-3 dark:border-cyan-900/40">
                <h3 className="text-sm font-semibold uppercase tracking-wide text-slate dark:text-slate-300">{categoryOption.label}</h3>
                <div className="mt-2 space-y-2">
                  {entries.map((alert) => (
                    <article key={alert.id} className="rounded border border-slate/20 p-2 dark:border-cyan-900/40">
                      <div className="flex items-center justify-between gap-2">
                        <p className="font-semibold">{alert.name}</p>
                        <div className="flex items-center gap-2">
                          <button
                            type="button"
                            className="rounded border border-slate/30 px-2 py-1 text-xs dark:border-cyan-900/40"
                            onClick={() => updateAlert.mutate({ id: alert.id, body: { enabled: !alert.enabled } })}
                            disabled={updateAlert.isPending}
                          >
                            {alert.enabled ? 'Disable' : 'Enable'}
                          </button>
                          <button
                            type="button"
                            className="rounded border border-slate/30 px-2 py-1 text-xs text-red-600 dark:border-cyan-900/40"
                            onClick={() => deleteAlert.mutate(alert.id)}
                            disabled={deleteAlert.isPending}
                          >
                            Delete
                          </button>
                        </div>
                      </div>
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {alert.keywords.map((keyword) => (
                          <span
                            key={`${alert.id}-${keyword}`}
                            className="rounded-full border border-amber-300/60 bg-amber-100/70 px-2 py-0.5 text-[11px] text-amber-800 dark:border-amber-800/40 dark:bg-amber-950/30 dark:text-amber-200"
                          >
                            {keyword}
                          </span>
                        ))}
                      </div>
                    </article>
                  ))}
                </div>
              </div>
            )
          })}

          {alertsQuery.isLoading && <p className="text-sm text-slate dark:text-slate-300">Loading alert interests...</p>}
          {alertsQuery.isError && <p className="text-sm text-red-600">Failed to load alert interests.</p>}
          {!alertsQuery.isLoading && (alertsQuery.data?.length ?? 0) === 0 && (
            <p className="text-sm text-slate dark:text-slate-300">No alert interests configured yet.</p>
          )}
        </div>
      </section>
    </div>
  )
}
