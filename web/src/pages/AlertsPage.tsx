import { FormEvent, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import { AlertInterest, AlertMatchListResponse } from '../types/api'
import { formatDateTime } from '../utils/datetime'

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

const ALERT_PREVIEW_LIMIT = 5

export function AlertsPage() {
  const queryClient = useQueryClient()

  const [editingAlertId, setEditingAlertId] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [category, setCategory] = useState<string>(ALERT_CATEGORIES[0].value)
  const [keywordsText, setKeywordsText] = useState('')
  const [showDisabled, setShowDisabled] = useState(false)
  const [pendingDeleteAlert, setPendingDeleteAlert] = useState<AlertInterest | null>(null)

  const parsedKeywords = useMemo(
    () =>
      keywordsText
        .split(',')
        .map((keyword) => keyword.trim())
        .filter(Boolean),
    [keywordsText],
  )
  const previewEnabled = parsedKeywords.length > 0 && category.trim().length > 0

  const alertsQuery = useQuery({
    queryKey: ['alerts', showDisabled],
    queryFn: () => apiFetch<AlertInterest[]>(`/alerts?include_disabled=${showDisabled}`),
  })

  const previewQuery = useQuery({
    queryKey: ['alerts', 'preview', category, ...parsedKeywords],
    queryFn: () =>
      apiFetch<AlertMatchListResponse>('/alerts/preview', {
        method: 'POST',
        body: JSON.stringify({
          name: name.trim() || 'Preview',
          category,
          keywords: parsedKeywords,
          limit: ALERT_PREVIEW_LIMIT,
        }),
      }),
    enabled: previewEnabled,
    staleTime: 15_000,
  })

  const saveAlert = useMutation({
    mutationKey: ['alerts', 'save'],
    mutationFn: (payload: { id?: string; name: string; category: string; keywords: string[] }) => {
      if (payload.id) {
        return apiFetch<AlertInterest>(`/alerts/${payload.id}`, {
          method: 'PATCH',
          body: JSON.stringify({
            name: payload.name,
            category: payload.category,
            keywords: payload.keywords,
          }),
        })
      }

      return apiFetch<AlertInterest>('/alerts', {
        method: 'POST',
        body: JSON.stringify({
          name: payload.name,
          category: payload.category,
          keywords: payload.keywords,
          enabled: true,
        }),
      })
    },
    onSuccess: () => {
      resetForm(true)
      void queryClient.invalidateQueries({ queryKey: ['alerts'] })
    },
  })

  const updateAlert = useMutation({
    mutationKey: ['alerts', 'update'],
    mutationFn: (payload: { id: string; body: Record<string, unknown> }) =>
      apiFetch<AlertInterest>(`/alerts/${payload.id}`, {
        method: 'PATCH',
        body: JSON.stringify(payload.body),
      }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['alerts'] }),
  })

  const deleteAlert = useMutation({
    mutationKey: ['alerts', 'delete'],
    mutationFn: (id: string) =>
      apiFetch(`/alerts/${id}`, {
        method: 'DELETE',
      }),
    onSuccess: (_, deletedId) => {
      if (editingAlertId === deletedId) {
        resetForm(true)
      }
      void queryClient.invalidateQueries({ queryKey: ['alerts'] })
    },
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

  const editingAlert = useMemo(
    () => (alertsQuery.data ?? []).find((alert) => alert.id === editingAlertId) ?? null,
    [alertsQuery.data, editingAlertId],
  )
  const hasUnsavedAlertDraftChanges =
    name !== (editingAlert?.name ?? '') ||
    category !== (editingAlert?.category ?? ALERT_CATEGORIES[0].value) ||
    keywordsText !== (editingAlert?.keywords.join(', ') ?? '')
  const confirmDiscardUnsavedAlertChanges = useUnsavedChangesWarning(
    hasUnsavedAlertDraftChanges,
    'Discard unsaved alert changes?',
  )

  const onSave = (event: FormEvent) => {
    event.preventDefault()
    if (!name.trim() || parsedKeywords.length === 0) {
      return
    }

    saveAlert.mutate({
      id: editingAlertId ?? undefined,
      name: name.trim(),
      category,
      keywords: parsedKeywords,
    })
  }

  const onEdit = (alert: AlertInterest) => {
    if (alert.id === editingAlertId) {
      return
    }
    confirmDiscardUnsavedAlertChanges(() => {
      setEditingAlertId(alert.id)
      setName(alert.name)
      setCategory(alert.category)
      setKeywordsText(alert.keywords.join(', '))
    })
  }

  function resetForm(force = false) {
    if (force) {
      setEditingAlertId(null)
      setName('')
      setCategory(ALERT_CATEGORIES[0].value)
      setKeywordsText('')
      return
    }
    confirmDiscardUnsavedAlertChanges(() => {
      setEditingAlertId(null)
      setName('')
      setCategory(ALERT_CATEGORIES[0].value)
      setKeywordsText('')
    })
  }

  const onRequestDeleteAlert = (alert: AlertInterest) => {
    confirmDiscardUnsavedAlertChanges(() => {
      setPendingDeleteAlert(alert)
    })
  }

  const confirmDeleteAlert = () => {
    if (!pendingDeleteAlert) {
      return
    }

    const alertId = pendingDeleteAlert.id
    setPendingDeleteAlert(null)
    deleteAlert.mutate(alertId)
  }

  return (
    <>
      <div className="grid gap-4 xl:grid-cols-[480px_1fr]">
        <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 className="font-display text-xl">{editingAlertId ? 'Edit Alert Interest' : 'Alert Interests'}</h2>
              <p className="mt-1 text-sm text-slate dark:text-slate-300">
                Define focused interests by category. Dashboard alert windows match item text against these keywords.
              </p>
            </div>
            {editingAlertId && (
              <button
                type="button"
                className="rounded border border-slate/30 px-3 py-1.5 text-sm font-semibold dark:border-cyan-900/40"
                onClick={() => resetForm()}
              >
                Cancel edit
              </button>
            )}
          </div>

          <form className="mt-4 space-y-3" onSubmit={onSave}>
            <div>
              <label htmlFor="alert-interest-name" className="text-sm font-semibold">
                Interest Name
              </label>
              <input
                id="alert-interest-name"
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="Microsoft Security Updates"
                required
              />
            </div>

            <div>
              <label htmlFor="alert-interest-category" className="text-sm font-semibold">
                Category
              </label>
              <select
                id="alert-interest-category"
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
              <label htmlFor="alert-interest-keywords" className="text-sm font-semibold">
                Keywords (comma-separated)
              </label>
              <textarea
                id="alert-interest-keywords"
                className="mt-1 h-24 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                value={keywordsText}
                onChange={(event) => setKeywordsText(event.target.value)}
                placeholder="microsoft, exchange, entra id"
                required
              />
            </div>

            <div className="flex flex-wrap gap-2">
              <button
                className="rounded bg-ink px-3 py-2 text-white disabled:opacity-50 dark:bg-cyan dark:text-[#053c2e]"
                type="submit"
                disabled={saveAlert.isPending}
              >
                {editingAlertId ? 'Save changes' : 'Add Interest'}
              </button>
              {editingAlertId && (
                <button
                  className="rounded border border-slate/30 px-3 py-2 text-sm font-semibold dark:border-cyan-900/40"
                  type="button"
                  onClick={() => resetForm()}
                >
                  Reset
                </button>
              )}
            </div>
            {saveAlert.isError && <p className="text-sm text-red-600">Failed to save alert interest.</p>}
          </form>

        <section className="mt-5 rounded-xl border border-slate/20 bg-white/70 p-4 dark:border-cyan-900/40 dark:bg-white/[0.03]">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h3 className="font-display text-lg">Current Match Preview</h3>
              <p className="mt-1 text-sm text-slate dark:text-white/70">
                See how this alert would behave against the current corpus before you save it.
              </p>
            </div>
            {previewQuery.data && (
              <span className="tl-chip tl-chip-md tl-chip-info">
                {previewQuery.data.total} current match{previewQuery.data.total === 1 ? '' : 'es'}
              </span>
            )}
          </div>

          {!previewEnabled && (
            <p className="mt-3 text-sm text-slate dark:text-white/70">
              Add at least one keyword to preview recent matches.
            </p>
          )}

          {previewEnabled && previewQuery.isLoading && (
            <p className="mt-3 text-sm text-slate dark:text-white/70">Looking up current matches...</p>
          )}

          {previewEnabled && previewQuery.isError && (
            <p className="mt-3 text-sm text-red-600">Failed to load alert preview.</p>
          )}

          {previewEnabled && previewQuery.data && (
            <div className="mt-4 space-y-3">
              {previewQuery.data.items.length > 0 ? (
                previewQuery.data.items.map((item) => {
                  const previewMatch = item.matches[0]
                  return (
                    <article key={item.id} className="rounded-lg border border-slate/20 bg-white/80 p-3 dark:border-cyan-900/40 dark:bg-[#072019]/70">
                      <div className="flex flex-wrap items-start justify-between gap-2">
                        <div>
                          <p className="font-semibold">{item.title}</p>
                          <p className="mt-1 text-xs text-slate dark:text-white/60">
                            {item.feed_name} • {formatTimestamp(item.first_seen_at)}
                          </p>
                        </div>
                        <span className="tl-chip tl-chip-neutral">
                          {describeAlertCategory(previewMatch?.category ?? category)}
                        </span>
                      </div>
                      {item.summary && <p className="mt-2 text-sm text-slate dark:text-white/75">{item.summary}</p>}
                      {previewMatch && (
                        <div className="mt-2 flex flex-wrap gap-1.5">
                          {previewMatch.matched_keywords.map((keyword) => (
                            <span
                              key={`${item.id}-${keyword}`}
                              className="tl-chip tl-chip-neutral"
                            >
                              {keyword}
                            </span>
                          ))}
                        </div>
                      )}
                    </article>
                  )
                })
              ) : (
                <p className="text-sm text-slate dark:text-white/70">This alert would not match any current items yet.</p>
              )}

              {previewQuery.data.total > previewQuery.data.items.length && (
                <p className="text-xs text-slate dark:text-white/60">
                  Showing the {previewQuery.data.items.length} most recent matches out of {previewQuery.data.total}.
                </p>
              )}
            </div>
          )}
        </section>
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
                <div key={categoryOption.value} className="rounded border border-slate/20 bg-white/70 p-3 dark:border-cyan-900/40 dark:bg-white/[0.02]">
                  <h3 className="text-sm font-semibold uppercase text-slate dark:text-slate-300">{categoryOption.label}</h3>
                  <div className="mt-2 space-y-2">
                    {entries.map((alert) => (
                      <article
                        key={alert.id}
                        className={`rounded border p-2 ${
                          editingAlertId === alert.id
                            ? 'border-cyan bg-cyan/10 dark:border-cyan-500/35 dark:bg-cyan-950/30'
                            : 'border-slate/20 bg-white/75 dark:border-cyan-900/40 dark:bg-[#072019]/45'
                        }`}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <div>
                            <p className="font-semibold">{alert.name}</p>
                            {!alert.enabled && <p className="mt-1 text-xs text-slate dark:text-white/60">Disabled</p>}
                          </div>
                          <div className="flex items-center gap-2">
                            <button
                              type="button"
                              className="rounded border border-slate/30 px-2 py-1 text-xs dark:border-cyan-900/40"
                              onClick={() => onEdit(alert)}
                            >
                              Edit
                            </button>
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
                              onClick={() => onRequestDeleteAlert(alert)}
                              disabled={deleteAlert.isPending || Boolean(pendingDeleteAlert)}
                            >
                              Delete
                            </button>
                          </div>
                        </div>
                        <div className="mt-2 flex flex-wrap gap-1.5">
                          {alert.keywords.map((keyword) => (
                            <span
                              key={`${alert.id}-${keyword}`}
                              className="tl-chip tl-chip-neutral"
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

      <ConfirmDialog
        open={Boolean(pendingDeleteAlert)}
        title="Delete alert interest?"
        description="This permanently removes the alert interest and stops future item matching for these keywords."
        confirmLabel="Delete alert"
        onCancel={() => setPendingDeleteAlert(null)}
        onConfirm={confirmDeleteAlert}
        confirmDisabled={!pendingDeleteAlert}
        isConfirming={deleteAlert.isPending}
      >
        {pendingDeleteAlert && (
          <div className="space-y-3">
            <div className="space-y-1">
              <p className="font-semibold text-ink dark:text-white">{pendingDeleteAlert.name}</p>
              <p className="text-xs text-slate dark:text-white/70">
                Category: {describeAlertCategory(pendingDeleteAlert.category)} · {pendingDeleteAlert.keywords.length} keyword
                {pendingDeleteAlert.keywords.length === 1 ? '' : 's'}
              </p>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {pendingDeleteAlert.keywords.map((keyword) => (
                <span
                  key={`${pendingDeleteAlert.id}-${keyword}`}
                  className="tl-chip tl-chip-neutral"
                >
                  {keyword}
                </span>
              ))}
            </div>
            {pendingDeleteAlert.id === editingAlertId && hasUnsavedAlertDraftChanges && (
              <p className="rounded-lg border border-amber-300/70 bg-amber-100/80 px-3 py-2 text-xs text-amber-900 dark:border-amber-800/50 dark:bg-amber-950/30 dark:text-amber-200">
                Your current unsaved edits for this alert will be discarded too.
              </p>
            )}
          </div>
        )}
      </ConfirmDialog>
      {confirmDiscardUnsavedAlertChanges.discardDialog}
    </>
  )
}

function describeAlertCategory(category: string): string {
  return ALERT_CATEGORIES.find((entry) => entry.value === category)?.label ?? category
}

function formatTimestamp(value: string | null): string {
  return value ? formatDateTime(value) : 'Unknown time'
}
