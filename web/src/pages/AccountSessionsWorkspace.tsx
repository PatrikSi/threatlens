import { resolveApiErrorMessage } from '../api/errors'
import type { AuthSession, AuthSessionListResponse } from '../types/identity'
import { formatDateTime } from '../utils/datetime'
import {
  describeSessionClient,
  effectiveSessionExpiry,
  formatAuthMethod,
  sessionStatus,
} from './accountSecurityModel'

export function AccountSessionsWorkspace({
  data,
  isLoading,
  loadError,
  actionsDisabled,
  onRevoke,
  onRevokeOthers,
}: {
  data?: AuthSessionListResponse
  isLoading: boolean
  loadError: unknown
  actionsDisabled: boolean
  onRevoke: (session: AuthSession) => void
  onRevokeOthers: () => void
}) {
  const sessions = Array.isArray(data?.sessions) ? data.sessions : []
  const activeSessions = sessions.filter((session) =>
    ['active', 'current'].includes(sessionStatus(session)),
  )
  const historicalSessions = sessions.filter((session) =>
    ['expired', 'revoked'].includes(sessionStatus(session)),
  )
  return (
    <div className="tl-surface rounded-xl p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="font-semibold">Browser sessions</h3>
          <p className="mt-1 text-sm text-slate dark:text-slate-300">
            Review and revoke signed-in browsers.
          </p>
        </div>
        {data && (
          <span className="rounded bg-slate/10 px-2 py-1 text-xs font-semibold dark:bg-white/10">
            {data.active_count} active
          </span>
        )}
      </div>
      {isLoading && (
        <p className="mt-3 text-sm text-slate dark:text-slate-300">
          Loading browser sessions...
        </p>
      )}
      {Boolean(loadError) && (
        <p
          role="alert"
          className="mt-3 rounded border border-red-300/60 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-200"
        >
          {resolveApiErrorMessage(loadError, 'Browser sessions could not be loaded')}
        </p>
      )}
      {data && actionsDisabled && (
        <p
          id="session-actions-stale"
          role="status"
          className="mt-2 text-xs font-semibold text-amber-800 dark:text-amber-200"
        >
          Session actions are disabled until the current session list can be
          loaded.
        </p>
      )}
      {data && (
        <>
          {activeSessions.length === 0 ? (
            <p className="mt-4 text-sm text-slate dark:text-slate-300">
              No active opaque browser sessions are recorded. This can occur for
              a legacy session created before the security upgrade.
            </p>
          ) : (
            <ul
              className="mt-4 divide-y divide-slate/15 dark:divide-cyan-900/30"
              aria-label="Active browser sessions"
            >
              {activeSessions.map((session) => (
                <SessionRow
                  key={session.id}
                  session={session}
                  actionsDisabled={actionsDisabled}
                  onRevoke={onRevoke}
                />
              ))}
            </ul>
          )}
          {data.active_count > 1 && (
            <button
              type="button"
              className="mt-3 min-h-11 rounded border border-red-300/70 px-3 py-2 text-sm font-semibold text-red-700 dark:border-red-500/40 dark:text-red-200"
              onClick={onRevokeOthers}
              disabled={actionsDisabled}
              aria-describedby={
                actionsDisabled ? 'session-actions-stale' : undefined
              }
            >
              Revoke all other sessions
            </button>
          )}
          {historicalSessions.length > 0 && (
            <details className="mt-4 border-t border-slate/15 pt-3 dark:border-cyan-900/30">
              <summary className="cursor-pointer text-sm font-semibold">
                Recent inactive sessions ({historicalSessions.length})
              </summary>
              <ul
                className="mt-2 divide-y divide-slate/15 dark:divide-cyan-900/30"
                aria-label="Inactive browser sessions"
              >
                {historicalSessions.map((session) => (
                  <SessionRow
                    key={session.id}
                    session={session}
                    actionsDisabled={actionsDisabled}
                    onRevoke={onRevoke}
                  />
                ))}
              </ul>
            </details>
          )}
          {data.history_truncated && (
            <p className="mt-3 text-xs text-slate dark:text-slate-300">
              The API returned only the 200 most recent session records. Audit
              logs retain older activity.
            </p>
          )}
          {data.active_truncated && (
            <p
              role="alert"
              className="mt-3 rounded border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200"
            >
              Some active sessions are omitted by the API limit. Use “Revoke all
              other sessions” to invalidate sessions that are not listed.
            </p>
          )}
        </>
      )}
    </div>
  )
}

function SessionRow({
  session,
  actionsDisabled,
  onRevoke,
}: {
  session: AuthSession
  actionsDisabled: boolean
  onRevoke: (session: AuthSession) => void
}) {
  const status = sessionStatus(session)
  return (
    <li className="py-3 first:pt-0 last:pb-0">
      <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <p className="break-words text-sm font-semibold">
            {describeSessionClient(session)}{' '}
            {status === 'current' && (
              <span className="ml-1 text-green-700 dark:text-green-300">Current</span>
            )}
            {status === 'expired' && (
              <span className="ml-1 text-slate dark:text-slate-300">Expired</span>
            )}
          </p>
          <p className="mt-1 text-xs text-slate dark:text-slate-300">
            {formatAuthMethod(session)} · IP {session.client_ip || 'not recorded'}
          </p>
          <p className="mt-1 text-xs text-slate dark:text-slate-300">
            Last active {formatDateTime(session.last_seen_at)} · Effective expiry{' '}
            {formatDateTime(effectiveSessionExpiry(session))}
          </p>
          <p className="mt-1 text-xs text-slate dark:text-slate-300">
            Idle expiry {formatDateTime(session.idle_expires_at)} · Maximum expiry{' '}
            {formatDateTime(session.absolute_expires_at)}
          </p>
          {session.revoked_at && (
            <p className="mt-1 text-xs text-slate dark:text-slate-300">
              Revoked {formatDateTime(session.revoked_at)}
              {session.revoked_reason
                ? ` · ${session.revoked_reason.replaceAll('_', ' ')}`
                : ''}
            </p>
          )}
        </div>
        {(status === 'active' || status === 'current') && (
          <button
            type="button"
            className="min-h-11 shrink-0 rounded border border-red-300/70 px-3 py-2 text-sm font-semibold text-red-700 dark:border-red-500/40 dark:text-red-200"
            onClick={() => onRevoke(session)}
            disabled={actionsDisabled}
            aria-describedby={
              actionsDisabled ? 'session-actions-stale' : undefined
            }
            aria-label={`Revoke ${describeSessionClient(session)}${session.current ? ', current session' : ''}`}
          >
            Revoke
          </button>
        )}
      </div>
    </li>
  )
}
