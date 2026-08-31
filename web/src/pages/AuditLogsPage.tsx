import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { SettingsPageHeader } from '../components/SettingsPageHeader'
import { AuditLogExportResponse, AuditLogListResponse } from '../types/api'
import { formatDateTime } from '../utils/datetime'

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

export function AuditLogsPage() {
  const [action, setAction] = useState('')
  const [actorUserId, setActorUserId] = useState('')
  const [page, setPage] = useState(1)
  const pageSize = 50
  const [exportError, setExportError] = useState('')
  const [exportMessage, setExportMessage] = useState('')
  const trimmedActorUserId = actorUserId.trim()
  const actorUserIdError =
    trimmedActorUserId && !UUID_PATTERN.test(trimmedActorUserId) ? 'Actor user ID must be a valid UUID.' : ''
  const auditQueryEnabled = !actorUserIdError

  const auditQuery = useQuery({
    queryKey: ['audit-logs', action, actorUserId, page],
    queryFn: () => {
      const params = new URLSearchParams()
      params.set('page', String(page))
      params.set('page_size', String(pageSize))
      if (action.trim()) params.set('action', action.trim())
      if (trimmedActorUserId) params.set('actor_user_id', trimmedActorUserId)
      return apiFetch<AuditLogListResponse>(`/audit-logs?${params.toString()}`)
    },
    enabled: auditQueryEnabled,
  })

  const totalPages = auditQueryEnabled ? Math.max(1, Math.ceil((auditQuery.data?.total ?? 0) / pageSize)) : 1
  const logs = auditQueryEnabled ? (auditQuery.data?.logs ?? []) : []
  const auditQueryError = auditQuery.isError
    ? resolveApiErrorMessage(auditQuery.error, 'Audit logs could not be loaded')
    : ''
  const hasActiveFilters = Boolean(action || actorUserId)

  const exportLogs = useMutation({
    mutationFn: () => {
      const params = new URLSearchParams()
      if (action.trim()) params.set('action', action.trim())
      if (trimmedActorUserId) params.set('actor_user_id', trimmedActorUserId)
      params.set('limit', '10000')
      return apiFetch<AuditLogExportResponse>(`/audit-logs/export?${params.toString()}`)
    },
    onSuccess: (payload) => {
      const body = JSON.stringify(payload, null, 2)
      const blob = new Blob([body], { type: 'application/json' })
      const objectUrl = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = objectUrl
      anchor.download = `threatlens-audit-logs-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.json`
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      URL.revokeObjectURL(objectUrl)

      setExportError('')
      if (payload.truncated) {
        setExportMessage(`Exported first 10000 logs out of ${payload.total}. Refine filters for a complete export.`)
        return
      }
      setExportMessage(`Exported ${payload.logs.length} logs.`)
    },
    onError: (error) => {
      setExportMessage('')
      setExportError(resolveApiErrorMessage(error, 'Audit logs could not be exported'))
    },
  })

  return (
    <div className="space-y-4">
      <SettingsPageHeader
        scope="Organization"
        title="Audit log"
        description="Review security and administrative events across this deployment."
        actions={(
          <button
            type="button"
            className="w-full rounded border border-slate/30 px-3 py-2 text-sm font-semibold sm:w-auto dark:border-cyan-900/40"
            onClick={() => exportLogs.mutate()}
            disabled={exportLogs.isPending || Boolean(actorUserIdError)}
          >
            {exportLogs.isPending ? 'Exporting...' : 'Export JSON'}
          </button>
        )}
      >
        {(exportError || exportMessage) && (
          <div className="py-3">
            {exportError && (
              <p role="alert" aria-live="assertive" aria-atomic="true" className="text-sm text-red-600 dark:text-red-300">
                {exportError}
              </p>
            )}
            {exportMessage && (
              <p role="status" aria-live="polite" aria-atomic="true" className="text-sm text-slate dark:text-slate-300">
                {exportMessage}
              </p>
            )}
          </div>
        )}
      </SettingsPageHeader>

      <section className="min-w-0 overflow-hidden rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="grid w-full gap-3 sm:grid-cols-2 lg:w-auto">
            <label htmlFor="audit-log-action-filter" className="text-xs font-semibold text-slate dark:text-slate-300">
              Event
              <input
                id="audit-log-action-filter"
                value={action}
                onChange={(event) => {
                  setPage(1)
                  setAction(event.target.value)
                }}
                placeholder="For example, item.updated"
                className="mt-1 block w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm font-normal text-ink dark:border-cyan-900/40 dark:bg-[#072019] dark:text-slate-100"
              />
            </label>
            <label htmlFor="audit-log-actor-filter" className="text-xs font-semibold text-slate dark:text-slate-300">
              Actor user ID
              <input
                id="audit-log-actor-filter"
                value={actorUserId}
                onChange={(event) => {
                  setPage(1)
                  setActorUserId(event.target.value)
                }}
                placeholder="UUID"
                aria-invalid={Boolean(actorUserIdError)}
                aria-describedby={actorUserIdError ? 'audit-log-actor-filter-error' : undefined}
                className="mt-1 block w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm font-normal text-ink sm:w-64 dark:border-cyan-900/40 dark:bg-[#072019] dark:text-slate-100"
              />
            </label>
          </div>
          <button
            type="button"
            className="rounded border border-slate/30 px-3 py-2 text-sm font-semibold disabled:opacity-50 dark:border-cyan-900/40"
            disabled={!hasActiveFilters}
            onClick={() => {
              setAction('')
              setActorUserId('')
              setPage(1)
            }}
          >
            Clear filters
          </button>
        </div>
        {actorUserIdError && (
          <p
            id="audit-log-actor-filter-error"
            role="alert"
            aria-live="assertive"
            aria-atomic="true"
            className="mt-2 text-sm text-red-600 dark:text-red-300"
          >
            {actorUserIdError}
          </p>
        )}

      <div className="mt-3 space-y-2 sm:hidden" aria-label="Audit events">
        {auditQueryEnabled && auditQuery.isLoading && (
          <p className="py-4 text-center text-sm text-slate dark:text-slate-300">Loading audit logs...</p>
        )}
        {auditQueryEnabled && auditQuery.isError && (
          <div role="alert" className="rounded-lg border border-red-300/60 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-950/25 dark:text-red-200">
            {auditQueryError}
          </div>
        )}
        {auditQueryEnabled && !auditQuery.isLoading && !auditQuery.isError && logs.length === 0 && (
          <div className="rounded-lg border border-dashed border-slate/25 px-3 py-4 text-center text-sm text-slate dark:border-cyan-900/40 dark:text-slate-300">
            No events match the current filters.
          </div>
        )}
        {logs.map((log) => (
          <details key={log.id} className="rounded border border-slate/20 bg-white/70 dark:border-cyan-900/40 dark:bg-white/[0.03]">
            <summary className="cursor-pointer p-2.5 marker:text-slate dark:marker:text-slate-400">
            <div className="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-2">
              <div className="min-w-0">
                <p className="break-words font-mono text-xs font-semibold leading-5 text-ink dark:text-slate-100">{log.action}</p>
                <p className="mt-0.5 text-[11px] text-slate dark:text-slate-400">{formatDateTime(log.created_at)}</p>
              </div>
              <span className={log.success ? 'tl-chip tl-chip-neutral' : 'tl-chip tl-chip-danger'}>
                {log.success ? 'Success' : 'Failed'}
              </span>
            </div>
            </summary>
            <dl className="grid gap-2 border-t border-slate/15 px-2.5 py-2 text-xs dark:border-cyan-900/30">
              <div>
                <dt className="font-semibold uppercase text-slate dark:text-slate-400">Resource</dt>
                <dd className="mt-0.5 break-all text-ink dark:text-slate-200">
                  {log.resource_type}{log.resource_id ? `:${log.resource_id}` : ''}
                </dd>
              </div>
              <div>
                <dt className="font-semibold uppercase text-slate dark:text-slate-400">Actor</dt>
                <dd className="mt-0.5 break-all font-mono text-ink dark:text-slate-200">{log.actor_user_id || 'system'}</dd>
              </div>
            </dl>
          </details>
        ))}
      </div>

      <div className="mt-3 hidden max-w-full overflow-x-auto sm:block">
        <table className="min-w-[720px] text-left text-sm">
          <thead>
            <tr className="border-b border-slate/20 dark:border-cyan-900/40">
              <th scope="col" className="px-2 py-2">Time</th>
              <th scope="col" className="px-2 py-2">Event</th>
              <th scope="col" className="px-2 py-2">Resource</th>
              <th scope="col" className="px-2 py-2">Actor</th>
              <th scope="col" className="px-2 py-2">Status</th>
            </tr>
          </thead>
          <tbody>
            {auditQueryEnabled && auditQuery.isLoading && (
              <tr>
                <td colSpan={5} className="px-2 py-6 text-center text-slate dark:text-slate-300">
                  Loading audit logs...
                </td>
              </tr>
            )}
            {auditQueryEnabled && auditQuery.isError && (
              <tr>
                <td colSpan={5} className="px-2 py-6">
                  <div role="alert" className="rounded-lg border border-red-300/60 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-950/25 dark:text-red-200">
                    {auditQueryError}
                  </div>
                </td>
              </tr>
            )}
            {auditQueryEnabled && !auditQuery.isLoading && !auditQuery.isError && logs.length === 0 && (
              <tr>
                <td colSpan={5} className="px-2 py-6">
                  <div className="rounded-lg border border-dashed border-slate/25 px-3 py-4 text-center text-sm text-slate dark:border-cyan-900/40 dark:text-slate-300">
                    No events match the current filters.
                  </div>
                </td>
              </tr>
            )}
            {logs.map((log) => (
              <tr key={log.id} className="border-b border-slate/10 dark:border-cyan-950/40">
                <td className="px-2 py-2 whitespace-nowrap">{formatDateTime(log.created_at)}</td>
                <td className="px-2 py-2 font-mono text-xs">{log.action}</td>
                <td className="px-2 py-2">
                  {log.resource_type}
                  {log.resource_id ? `:${log.resource_id}` : ''}
                </td>
                <td className="px-2 py-2 font-mono text-xs">{log.actor_user_id || 'system'}</td>
                <td className="px-2 py-2">
                  <span
                    className={
                      log.success
                        ? 'tl-chip tl-chip-neutral'
                        : 'tl-chip tl-chip-danger'
                    }
                  >
                    {log.success ? 'Success' : 'Failed'}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="mt-4 grid grid-cols-[auto_1fr_auto] items-center gap-2 text-sm sm:flex sm:flex-wrap sm:justify-between">
        <button className="rounded border border-slate/30 px-2 py-1 disabled:cursor-not-allowed disabled:opacity-60 dark:border-cyan-900/40" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
          Previous
        </button>
        <span className="text-center sm:w-auto">
          Page {page} of {totalPages}
        </span>
        <button
          className="rounded border border-slate/30 px-2 py-1 disabled:cursor-not-allowed disabled:opacity-60 dark:border-cyan-900/40"
          disabled={page >= totalPages}
          onClick={() => setPage((p) => p + 1)}
        >
          Next
        </button>
      </div>
      </section>
    </div>
  )
}
