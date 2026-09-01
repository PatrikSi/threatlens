import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { CopyableIdentifier } from '../components/CopyableIdentifier'
import { SettingsPageHeader } from '../components/SettingsPageHeader'
import {
  AuditLog,
  AuditLogExportResponse,
  AuditLogListResponse,
} from '../types/api'
import { formatDateTime } from '../utils/datetime'

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

const AUDIT_ACTION_LABELS: Record<string, string> = {
  'audit.export': 'Exported audit log',
  'auth.login': 'Sign-in attempt',
  'auth.login.mfa_challenge': 'MFA challenge issued',
  'auth.login.mfa_verify': 'MFA sign-in check',
  'auth.logout': 'Signed out',
  'auth.oidc.login': 'SSO sign-in attempt',
  'auth.register': 'Registered account',
  'authorization.permission_denied': 'Permission denied',
  'authorization.role_denied': 'Base role denied',
  'users.create': 'Created user',
  'users.mfa.reset': 'Reset user MFA',
  'users.update': 'Updated user',
}

export function AuditLogsPage() {
  const [action, setAction] = useState('')
  const [actorPrincipalId, setActorPrincipalId] = useState('')
  const [expandedLogId, setExpandedLogId] = useState<string | null>(null)
  const [page, setPage] = useState(1)
  const pageSize = 50
  const [exportError, setExportError] = useState('')
  const [exportMessage, setExportMessage] = useState('')
  const trimmedActorPrincipalId = actorPrincipalId.trim()
  const actorPrincipalIdError =
    trimmedActorPrincipalId && !UUID_PATTERN.test(trimmedActorPrincipalId) ? 'Actor/principal ID must be a valid UUID.' : ''
  const auditQueryEnabled = !actorPrincipalIdError

  const auditQuery = useQuery({
    queryKey: ['audit-logs', action, actorPrincipalId, page],
    queryFn: () => {
      const params = new URLSearchParams()
      params.set('page', String(page))
      params.set('page_size', String(pageSize))
      if (action.trim()) params.set('action', action.trim())
      if (trimmedActorPrincipalId) params.set('actor_principal_id', trimmedActorPrincipalId)
      return apiFetch<AuditLogListResponse>(`/audit-logs?${params.toString()}`)
    },
    enabled: auditQueryEnabled,
  })

  const totalPages = auditQueryEnabled ? Math.max(1, Math.ceil((auditQuery.data?.total ?? 0) / pageSize)) : 1
  const logs = auditQueryEnabled ? (auditQuery.data?.logs ?? []) : []
  const auditQueryError = auditQuery.isError
    ? resolveApiErrorMessage(auditQuery.error, 'Audit logs could not be loaded')
    : ''
  const hasActiveFilters = Boolean(action || actorPrincipalId)

  const exportLogs = useMutation({
    mutationFn: () => {
      const params = new URLSearchParams()
      if (action.trim()) params.set('action', action.trim())
      if (trimmedActorPrincipalId) params.set('actor_principal_id', trimmedActorPrincipalId)
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
    <div className="space-y-3">
      <SettingsPageHeader
        scope="Organization"
        title="Audit log"
        description="Trace security, access, and administrative activity across this deployment."
        actions={(
          <button
            type="button"
            className="w-full rounded border border-slate/30 px-3 py-2 text-sm font-semibold sm:w-auto dark:border-cyan-900/40"
            onClick={() => exportLogs.mutate()}
            disabled={exportLogs.isPending || Boolean(actorPrincipalIdError)}
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

      <section className="min-w-0 overflow-hidden rounded-xl border border-slate/20 bg-white/80 p-3 dark:border-cyan-900/40 dark:bg-[#041612]/90">
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
              Actor/principal ID
              <input
                id="audit-log-actor-filter"
                value={actorPrincipalId}
                onChange={(event) => {
                  setPage(1)
                  setActorPrincipalId(event.target.value)
                }}
                placeholder="UUID"
                aria-invalid={Boolean(actorPrincipalIdError)}
                aria-describedby={actorPrincipalIdError ? 'audit-log-actor-filter-error' : undefined}
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
              setActorPrincipalId('')
              setPage(1)
            }}
          >
            Clear filters
          </button>
        </div>
        {actorPrincipalIdError && (
          <p
            id="audit-log-actor-filter-error"
            role="alert"
            aria-live="assertive"
            aria-atomic="true"
            className="mt-2 text-sm text-red-600 dark:text-red-300"
          >
            {actorPrincipalIdError}
          </p>
        )}

        <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-slate/15 pt-2 text-xs text-slate dark:border-cyan-900/30 dark:text-slate-300">
          <p role="status" aria-live="polite">
            {auditQuery.isLoading
              ? 'Loading audit events...'
              : `Showing ${logs.length} of ${auditQuery.data?.total ?? 0} events`}
          </p>
          <p>Newest first · Times shown in your local timezone</p>
        </div>

        <AuditMobileCollection
          enabled={auditQueryEnabled}
          isLoading={auditQuery.isLoading}
          isError={auditQuery.isError}
          errorMessage={auditQueryError}
          logs={logs}
          filtered={hasActiveFilters}
        />
        <AuditDesktopCollection
          enabled={auditQueryEnabled}
          isLoading={auditQuery.isLoading}
          isError={auditQuery.isError}
          errorMessage={auditQueryError}
          logs={logs}
          filtered={hasActiveFilters}
          expandedLogId={expandedLogId}
          onToggleLog={(logId) =>
            setExpandedLogId((current) => current === logId ? null : logId)
          }
        />

      <div className="mt-3 grid grid-cols-[auto_1fr_auto] items-center gap-2 text-sm sm:flex sm:flex-wrap sm:justify-between">
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

type AuditCollectionProps = {
  enabled: boolean
  isLoading: boolean
  isError: boolean
  errorMessage: string
  logs: AuditLog[]
  filtered: boolean
}

function AuditMobileCollection({
  enabled,
  isLoading,
  isError,
  errorMessage,
  logs,
  filtered,
}: AuditCollectionProps) {
  return (
    <div className="mt-2 space-y-2 sm:hidden" aria-label="Audit events">
      {enabled && isLoading && <AuditLoadingState />}
      {enabled && isError && <AuditErrorState message={errorMessage} />}
      {enabled && !isLoading && !isError && logs.length === 0 && (
        <AuditEmptyState filtered={filtered} />
      )}
      {logs.map((log) => (
        <details key={log.id} className="rounded border border-slate/20 bg-white/70 dark:border-cyan-900/40 dark:bg-white/[0.03]">
          <summary className="cursor-pointer p-2.5 marker:text-slate dark:marker:text-slate-400">
            <div className="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-2">
              <div className="min-w-0">
                <p className="break-words text-sm font-semibold leading-5 text-ink dark:text-slate-100">
                  {formatAuditAction(log.action)}
                </p>
                <p className="break-all font-mono text-[11px] text-slate dark:text-slate-400">
                  {log.action}
                </p>
                <p className="mt-1 truncate text-xs text-slate-700 dark:text-slate-300">
                  {resolveActorLabel(log)} · {resolveResourceLabel(log)}
                </p>
                <p className="mt-0.5 text-[11px] text-slate dark:text-slate-400">
                  {formatDateTime(log.created_at)}
                </p>
              </div>
              <AuditResult success={log.success} />
            </div>
          </summary>
          <div className="border-t border-slate/15 px-2.5 py-2 dark:border-cyan-900/30">
            <AuditEventDetails log={log} />
          </div>
        </details>
      ))}
    </div>
  )
}

function AuditDesktopCollection({
  enabled,
  isLoading,
  isError,
  errorMessage,
  logs,
  filtered,
  expandedLogId,
  onToggleLog,
}: AuditCollectionProps & {
  expandedLogId: string | null
  onToggleLog: (logId: string) => void
}) {
  return (
    <div className="mt-2 hidden max-w-full overflow-x-auto sm:block">
      <table className="min-w-[980px] text-left text-sm">
        <thead>
          <tr className="border-b border-slate/20 dark:border-cyan-900/40">
            <th scope="col" className="px-2 py-2">Time</th>
            <th scope="col" className="px-2 py-2">Event</th>
            <th scope="col" className="px-2 py-2">Actor</th>
            <th scope="col" className="px-2 py-2">Resource</th>
            <th scope="col" className="px-2 py-2">Source</th>
            <th scope="col" className="px-2 py-2">Result</th>
          </tr>
        </thead>
        <tbody>
          {enabled && isLoading && (
            <tr><td colSpan={6} className="px-2 py-4"><AuditLoadingState /></td></tr>
          )}
          {enabled && isError && (
            <tr><td colSpan={6} className="px-2 py-4"><AuditErrorState message={errorMessage} /></td></tr>
          )}
          {enabled && !isLoading && !isError && logs.length === 0 && (
            <tr><td colSpan={6} className="px-2 py-4"><AuditEmptyState filtered={filtered} /></td></tr>
          )}
          {logs.map((log) => (
            <AuditDesktopRows
              key={log.id}
              log={log}
              expanded={expandedLogId === log.id}
              onToggle={() => onToggleLog(log.id)}
            />
          ))}
        </tbody>
      </table>
    </div>
  )
}

function AuditDesktopRows({
  log,
  expanded,
  onToggle,
}: {
  log: AuditLog
  expanded: boolean
  onToggle: () => void
}) {
  return (
    <>
      <tr className="border-b border-slate/10 align-top dark:border-cyan-950/40">
        <td className="whitespace-nowrap px-2 py-2 text-xs">
          {formatDateTime(log.created_at)}
        </td>
        <td className="px-2 py-2">
          <p className="font-semibold text-ink dark:text-slate-100">
            {formatAuditAction(log.action)}
          </p>
          <p className="font-mono text-[11px] text-slate dark:text-slate-400">
            {log.action}
          </p>
          <button
            type="button"
            className="mt-1 text-xs font-semibold text-cyan-800 underline-offset-2 hover:underline dark:text-cyan-300"
            aria-expanded={expanded}
            aria-controls={`audit-details-${log.id}`}
            onClick={onToggle}
          >
            {expanded ? 'Close details' : 'View details'}
          </button>
        </td>
        <td className="max-w-56 px-2 py-2">
          <p className="truncate font-semibold" title={resolveActorLabel(log)}>
            {resolveActorLabel(log)}
          </p>
          <p className="text-xs text-slate dark:text-slate-400">
            {formatPrincipalType(log.actor_principal_type, log.actor_user_id)}
          </p>
        </td>
        <td className="max-w-56 px-2 py-2">
          <p className="truncate font-semibold" title={resolveResourceLabel(log)}>
            {resolveResourceLabel(log)}
          </p>
          <p className="font-mono text-[11px] text-slate dark:text-slate-400">
            {log.resource_type}
          </p>
        </td>
        <td className="px-2 py-2">
          <p className="whitespace-nowrap text-xs">{log.source_ip || 'Not recorded'}</p>
          <p className="text-[11px] text-slate dark:text-slate-400">
            {formatCredentialKind(log.credential_kind)}
          </p>
        </td>
        <td className="px-2 py-2"><AuditResult success={log.success} /></td>
      </tr>
      {expanded && (
        <tr id={`audit-details-${log.id}`}>
          <td colSpan={6} className="border-b border-slate/15 bg-slate/[0.025] px-3 py-3 dark:border-cyan-950/50 dark:bg-white/[0.025]">
            <AuditEventDetails log={log} />
          </td>
        </tr>
      )}
    </>
  )
}

function AuditEventDetails({ log }: { log: AuditLog }) {
  const elevationIds = log.authorization_elevation_ids ?? []
  const hasAuthorizationContext = Boolean(
    elevationIds.length || log.authorization_approval_id || log.execution_receipt_id,
  )
  const hasMetadata = Object.keys(log.metadata_json ?? {}).length > 0

  return (
    <div className="space-y-3 text-xs">
      {log.data_access_redacted && (
        <p className="rounded border border-amber-300/60 bg-amber-50 px-2.5 py-2 text-amber-900 dark:border-amber-800/40 dark:bg-amber-950/30 dark:text-amber-100">
          Some event details are hidden by handling-label access policy.
        </p>
      )}
      <div className="grid gap-3 md:grid-cols-3">
        <AuditDetailGroup title="Actor and credential">
          <AuditTextValue label="Actor" value={resolveActorLabel(log)} />
          <AuditTextValue
            label="Principal type"
            value={formatPrincipalType(log.actor_principal_type, log.actor_user_id)}
          />
          {log.actor_principal_id && (
            <AuditIdentifierValue label="Principal ID" value={log.actor_principal_id} />
          )}
          {log.actor_user_id && log.actor_user_id !== log.actor_principal_id && (
            <AuditIdentifierValue label="User ID" value={log.actor_user_id} />
          )}
          <AuditTextValue label="Credential" value={formatCredentialKind(log.credential_kind)} />
          {log.credential_id && (
            <AuditIdentifierValue label="Credential ID" value={log.credential_id} />
          )}
        </AuditDetailGroup>

        <AuditDetailGroup title="Resource">
          <AuditTextValue label="Name" value={resolveResourceLabel(log)} />
          <AuditTextValue label="Type" value={log.resource_type} mono />
          {log.resource_id && (
            <AuditIdentifierValue label="Resource ID" value={log.resource_id} />
          )}
        </AuditDetailGroup>

        <AuditDetailGroup title="Request and source">
          <AuditIdentifierValue label="Event ID" value={log.id} />
          {log.request_id && (
            <AuditIdentifierValue label="Request ID" value={log.request_id} />
          )}
          {!log.request_id && (
            <AuditTextValue label="Request ID" value="Not recorded" />
          )}
          {log.source_ip && (
            <AuditIdentifierValue label="Source IP" value={log.source_ip} />
          )}
          {!log.source_ip && (
            <AuditTextValue label="Source IP" value="Not recorded" />
          )}
          <AuditTextValue label="Recorded" value={formatDateTime(log.created_at)} />
          <AuditTextValue label="Result" value={log.success ? 'Succeeded' : 'Failed'} />
        </AuditDetailGroup>
      </div>

      {hasAuthorizationContext && (
        <AuditDetailGroup title="Authorization context">
          <div className="grid gap-2 md:grid-cols-3">
            {log.authorization_approval_id && (
              <AuditIdentifierValue label="Approval ID" value={log.authorization_approval_id} />
            )}
            {log.execution_receipt_id && (
              <AuditIdentifierValue label="Execution receipt ID" value={log.execution_receipt_id} />
            )}
            {elevationIds.map((elevationId, index) => (
              <AuditIdentifierValue
                key={elevationId}
                label={elevationIds.length === 1 ? 'Elevation ID' : `Elevation ID ${index + 1}`}
                value={elevationId}
              />
            ))}
          </div>
        </AuditDetailGroup>
      )}

      <AuditDetailGroup title="Event metadata">
        {hasMetadata ? (
          <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-all rounded border border-slate/15 bg-slate/5 p-2 font-mono text-[11px] leading-5 text-slate-800 dark:border-cyan-900/30 dark:bg-black/20 dark:text-slate-200">
            {JSON.stringify(log.metadata_json, null, 2)}
          </pre>
        ) : (
          <p className="text-slate dark:text-slate-400">
            No additional metadata was recorded for this event.
          </p>
        )}
      </AuditDetailGroup>
    </div>
  )
}

function AuditDetailGroup({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="min-w-0 rounded border border-slate/15 bg-white/60 p-2.5 dark:border-cyan-900/30 dark:bg-white/[0.025]">
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate dark:text-slate-300">{title}</h3>
      <div className="space-y-2">{children}</div>
    </section>
  )
}

function AuditTextValue({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="min-w-0">
      <p className="font-semibold text-slate dark:text-slate-400">{label}</p>
      <p className={`mt-0.5 break-words text-slate-900 dark:text-slate-100 ${mono ? 'font-mono text-[11px]' : ''}`}>
        {value}
      </p>
    </div>
  )
}

function AuditIdentifierValue({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <p className="font-semibold text-slate dark:text-slate-400">{label}</p>
      <div className="mt-0.5"><CopyableIdentifier label={label} value={value} /></div>
    </div>
  )
}

function AuditResult({ success }: { success: boolean }) {
  return (
    <span className={`tl-chip shrink-0 ${success ? 'tl-chip-success' : 'tl-chip-danger'}`}>
      <span aria-hidden="true">{success ? '✓' : '×'}</span>{' '}
      {success ? 'Succeeded' : 'Failed'}
    </span>
  )
}

function AuditLoadingState() {
  return (
    <div role="status" className="rounded border border-slate/15 px-3 py-3 text-center text-sm text-slate dark:border-cyan-900/30 dark:text-slate-300">
      Loading audit events...
    </div>
  )
}

function AuditErrorState({ message }: { message: string }) {
  return (
    <div role="alert" className="rounded-lg border border-red-300/60 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-950/25 dark:text-red-200">
      {message}
    </div>
  )
}

function AuditEmptyState({ filtered }: { filtered: boolean }) {
  return (
    <div className="rounded-lg border border-dashed border-slate/25 px-3 py-3 text-center text-sm text-slate dark:border-cyan-900/40 dark:text-slate-300">
      {filtered ? 'No events match the current filters.' : 'No audit events have been recorded yet.'}
    </div>
  )
}

function formatAuditAction(action: string) {
  return AUDIT_ACTION_LABELS[action] ?? humanizeAuditValue(action)
}

function resolveActorLabel(log: AuditLog) {
  if (log.actor_label_snapshot?.trim()) return log.actor_label_snapshot.trim()
  if (log.actor_principal_type === 'anonymous') return 'Unauthenticated'
  if (log.actor_principal_type && log.actor_principal_id) {
    return `Unknown ${humanizeAuditValue(log.actor_principal_type).toLowerCase()}`
  }
  if (log.actor_principal_type) return humanizeAuditValue(log.actor_principal_type)
  if (log.actor_user_id) return 'Unknown user'
  return 'Actor not recorded'
}

function resolveResourceLabel(log: AuditLog) {
  if (log.resource_label_snapshot?.trim()) return log.resource_label_snapshot.trim()
  return humanizeAuditValue(log.resource_type)
}

function formatPrincipalType(value: string | null, actorUserId: string | null) {
  if (value === 'anonymous') return 'Unauthenticated'
  if (!value && actorUserId) return 'User'
  return value ? humanizeAuditValue(value) : 'Not recorded'
}

function formatCredentialKind(value: string | null) {
  if (!value) return 'No request credential'
  const labels: Record<string, string> = {
    api_token: 'API token',
    session_cookie: 'Browser session',
    session_bearer: 'Legacy session bearer',
    legacy_session: 'Legacy browser session',
    service_account_token: 'Service account token',
  }
  return labels[value] ?? humanizeAuditValue(value)
}

function humanizeAuditValue(value: string) {
  const words = value.split(/[._-]+/).filter(Boolean)
  if (!words.length) return value
  const acronyms: Record<string, string> = {
    ai: 'AI',
    api: 'API',
    iam: 'IAM',
    mfa: 'MFA',
    oidc: 'OIDC',
    smtp: 'SMTP',
    sso: 'SSO',
  }
  const rendered = words.map((word) => acronyms[word.toLowerCase()] ?? word).join(' ')
  return rendered.charAt(0).toUpperCase() + rendered.slice(1)
}
