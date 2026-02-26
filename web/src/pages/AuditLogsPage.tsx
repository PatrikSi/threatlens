import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
import { AuditLogListResponse } from '../types/api'

export function AuditLogsPage() {
  const [action, setAction] = useState('')
  const [actorUserId, setActorUserId] = useState('')
  const [page, setPage] = useState(1)
  const pageSize = 50

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

  return (
    <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#040913]/90">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="font-display text-xl">Audit Logs</h2>
        <div className="flex flex-wrap gap-2">
          <input
            value={action}
            onChange={(event) => {
              setPage(1)
              setAction(event.target.value)
            }}
            placeholder="Action filter"
            className="rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#060d19]"
          />
          <input
            value={actorUserId}
            onChange={(event) => {
              setPage(1)
              setActorUserId(event.target.value)
            }}
            placeholder="Actor user ID"
            className="w-64 rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#060d19]"
          />
        </div>
      </div>

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
                <td className="px-2 py-2 whitespace-nowrap">{new Date(log.created_at).toLocaleString()}</td>
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

      <div className="mt-4 flex items-center justify-between text-sm">
        <button className="rounded border border-slate/30 px-2 py-1" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
          Prev
        </button>
        <span>
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
