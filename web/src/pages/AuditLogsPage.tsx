import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
import { AuditLogExportResponse, AuditLogListResponse } from '../types/api'
import { formatDateTime } from '../utils/datetime'

export function AuditLogsPage() {
  const [action, setAction] = useState('')
  const [actorUserId, setActorUserId] = useState('')
  const [page, setPage] = useState(1)
  const pageSize = 50
  const [exportError, setExportError] = useState('')
  const [exportMessage, setExportMessage] = useState('')

  const auditQuery = useQuery({
    queryKey: ['audit-logs', action, actorUserId, page],
    queryFn: () => {
      const params = new URLSearchParams()
      params.set('page', String(page))
      params.set('page_size', String(pageSize))
      if (action.trim()) params.set('action', action.trim())
      if (actorUserId.trim()) params.set('actor_user_id', actorUserId.trim())
      return apiFetch<AuditLogListResponse>(`/audit-logs?${params.toString()}`)
    },
  })

  const totalPages = Math.max(1, Math.ceil((auditQuery.data?.total ?? 0) / pageSize))

  const exportLogs = useMutation({
    mutationFn: () => {
      const params = new URLSearchParams()
      if (action.trim()) params.set('action', action.trim())
      if (actorUserId.trim()) params.set('actor_user_id', actorUserId.trim())
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
      setExportError((error as Error).message || 'Failed to export audit logs')
    },
  })

  return (
    <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="font-display text-xl">Audit Logs</h2>
        <div className="grid w-full gap-2 sm:flex sm:w-auto sm:flex-wrap">
          <input
            value={action}
            onChange={(event) => {
              setPage(1)
              setAction(event.target.value)
            }}
            placeholder="Action filter"
            className="w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
          />
          <input
            value={actorUserId}
            onChange={(event) => {
              setPage(1)
              setActorUserId(event.target.value)
            }}
            placeholder="Actor user ID"
            className="w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm sm:w-64 dark:border-cyan-900/40 dark:bg-[#072019]"
          />
          <button
            type="button"
            className="w-full rounded border border-slate/30 px-3 py-2 text-sm sm:w-auto dark:border-cyan-900/40"
            onClick={() => exportLogs.mutate()}
            disabled={exportLogs.isPending}
          >
            {exportLogs.isPending ? 'Exporting...' : 'Export JSON'}
          </button>
        </div>
      </div>
      {exportError && <p className="mt-2 text-sm text-red-600">{exportError}</p>}
      {exportMessage && <p className="mt-2 text-sm text-slate dark:text-slate-300">{exportMessage}</p>}

      <div className="mt-3 overflow-x-auto">
        <table className="min-w-full text-left text-sm">
          <thead>
            <tr className="border-b border-slate/20 dark:border-cyan-900/40">
              <th className="px-2 py-2">Time</th>
              <th className="px-2 py-2">Action</th>
              <th className="px-2 py-2">Resource</th>
              <th className="px-2 py-2">Actor</th>
              <th className="px-2 py-2">Status</th>
            </tr>
          </thead>
          <tbody>
            {auditQuery.data?.logs.map((log) => (
              <tr key={log.id} className="border-b border-slate/10 dark:border-cyan-950/40">
                <td className="px-2 py-2 whitespace-nowrap">{formatDateTime(log.created_at)}</td>
                <td className="px-2 py-2 font-mono text-xs">{log.action}</td>
                <td className="px-2 py-2">
                  {log.resource_type}
                  {log.resource_id ? `:${log.resource_id}` : ''}
                </td>
                <td className="px-2 py-2 font-mono text-xs">{log.actor_user_id || 'system'}</td>
                <td className="px-2 py-2">
                  <span className={log.success ? 'text-green-600' : 'text-red-600'}>{log.success ? 'success' : 'failed'}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="mt-4 flex flex-wrap items-center justify-between gap-2 text-sm">
        <button className="rounded border border-slate/30 px-2 py-1" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
          Prev
        </button>
        <span className="w-full text-center sm:w-auto">
          Page {page} / {totalPages}
        </span>
        <button
          className="rounded border border-slate/30 px-2 py-1"
          disabled={page >= totalPages}
          onClick={() => setPage((p) => p + 1)}
        >
          Next
        </button>
      </div>
    </section>
  )
}
