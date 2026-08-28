import type { AdminUser, User, UserDirectoryResponse } from '../types/api'
import {
  resolveUsersError,
  type UserProvisioningFilter,
  type UserRoleFilter,
} from './userDirectoryModel'

const ROLE_DEFINITIONS: Array<{
  role: User['role']
  summary: string
  capabilities: string[]
}> = [
  {
    role: 'admin',
    summary:
      'Full administrative access across user management, global settings, and operational oversight.',
    capabilities: [
      'Manage users, approvals, and role changes',
      'Access audit logs and global administration surfaces',
      'Manage feeds, triage actions, tagging, and AI settings',
    ],
  },
  {
    role: 'analyst',
    summary:
      'Operational user for daily feed management, investigation, and triage workflows.',
    capabilities: [
      'Manage feeds and perform triage actions',
      'Configure personal notifications and API tokens',
      'No access to user administration, audit logs, or global AI/tagging controls',
    ],
  },
  {
    role: 'viewer',
    summary:
      'Read-oriented access for monitoring without operational or administrative mutation rights.',
    capabilities: [
      'View dashboard, feeds, and other read-only surfaces',
      'Access personal account settings, API tokens, and notifications',
      'Cannot change feeds, tags, or triage state',
    ],
  },
]

export function UserDirectoryHeader({
  data,
  filteredCount,
  isLoading,
  isError,
  isSuccess,
  createUserFormVisible,
  hasCreateUserDraft,
  search,
  roleFilter,
  accountFilter,
  onToggleCreate,
  onSearchChange,
  onRoleFilterChange,
  onAccountFilterChange,
}: {
  data?: UserDirectoryResponse
  filteredCount: number
  isLoading: boolean
  isError: boolean
  isSuccess: boolean
  createUserFormVisible: boolean
  hasCreateUserDraft: boolean
  search: string
  roleFilter: UserRoleFilter
  accountFilter: UserProvisioningFilter
  onToggleCreate: () => void
  onSearchChange: (value: string) => void
  onRoleFilterChange: (value: UserRoleFilter) => void
  onAccountFilterChange: (value: UserProvisioningFilter) => void
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h2 className="font-display text-xl">User Directory</h2>
        {isLoading && (
          <p role="status" className="text-xs text-slate dark:text-slate-300">
            Loading account inventory...
          </p>
        )}
        {isError && (
          <p className="text-xs text-red-600 dark:text-red-300">
            Account inventory unavailable
          </p>
        )}
        {isSuccess && data && (
          <>
            <p
              role="status"
              aria-live="polite"
              className="text-xs text-slate dark:text-slate-300"
            >
              {data.total === 0
                ? 'No matching accounts'
                : `Showing ${data.offset + 1}-${data.offset + filteredCount} of ${data.total} matching accounts`}
            </p>
            <p className="mt-1 text-xs text-slate dark:text-slate-300">
              Session counts include tracked opaque browser sessions; legacy JWT
              sessions are not included.
            </p>
          </>
        )}
      </div>
      {isSuccess && (
        <div className="flex w-full flex-col gap-2 sm:w-auto sm:flex-row sm:flex-wrap sm:justify-end">
          <button
            type="button"
            className="min-h-11 rounded bg-ink px-3 py-2 text-sm font-semibold text-white sm:min-h-0 dark:bg-cyan dark:text-[#053c2e]"
            aria-expanded={createUserFormVisible}
            aria-controls="create-user-form"
            onClick={onToggleCreate}
          >
            {createUserFormVisible
              ? 'Close form'
              : hasCreateUserDraft
                ? 'Resume local user draft'
                : 'New local user'}
          </button>
          <label htmlFor="user-account-filter" className="sr-only">
            Filter by provisioning source
          </label>
          <select
            id="user-account-filter"
            value={accountFilter}
            onChange={(event) =>
              onAccountFilterChange(
                event.target.value as UserProvisioningFilter,
              )
            }
            className="rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
          >
            <option value="all">All provisioning sources</option>
            <option value="local">Locally provisioned</option>
            <option value="oidc">SSO-provisioned</option>
          </select>
          <label htmlFor="user-role-filter" className="sr-only">
            Filter by role
          </label>
          <select
            id="user-role-filter"
            value={roleFilter}
            onChange={(event) =>
              onRoleFilterChange(event.target.value as UserRoleFilter)
            }
            className="rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
          >
            <option value="all">All roles</option>
            <option value="admin">Admin</option>
            <option value="analyst">Analyst</option>
            <option value="viewer">Viewer</option>
          </select>
          <label htmlFor="user-directory-search" className="sr-only">
            Search users by email, role, status, account type, or provider
          </label>
          <input
            id="user-directory-search"
            value={search}
            onChange={(event) => onSearchChange(event.target.value)}
            placeholder="Search users..."
            className="w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm sm:w-64 dark:border-cyan-900/40 dark:bg-[#072019]"
          />
        </div>
      )}
    </div>
  )
}

export function UserRoleDefinitions() {
  return (
    <details className="mt-3 rounded-lg border border-slate/20 bg-slate/5 p-2.5 sm:p-3 dark:border-cyan-900/40 dark:bg-white/[0.04]">
      <summary className="cursor-pointer list-none text-sm font-semibold text-slate-900 dark:text-white">
        <span className="inline-flex items-center gap-2">
          <span>Role Definitions</span>
          <span className="text-xs font-normal text-slate dark:text-slate-300">
            Expand for admin, analyst, and viewer access boundaries
          </span>
        </span>
      </summary>
      <div className="mt-3 grid gap-3 lg:grid-cols-3">
        {ROLE_DEFINITIONS.map((entry) => (
          <div
            key={entry.role}
            className="border-l-2 border-slate/20 pl-3 dark:border-cyan-900/50"
          >
            <h3 className="text-sm font-semibold uppercase text-slate-900 dark:text-white">
              {entry.role}
            </h3>
            <p className="mt-1 text-sm text-slate dark:text-slate-300">
              {entry.summary}
            </p>
            <ul className="mt-3 list-disc space-y-1 pl-4 text-sm text-slate-900 dark:text-slate-200">
              {entry.capabilities.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </details>
  )
}

export function UserDirectoryQueryState({
  data,
  filteredUsers,
  directoryIsUnfiltered,
  isLoading,
  isError,
  isFetching,
  error,
  onRetry,
  onOffsetChange,
}: {
  data?: UserDirectoryResponse
  filteredUsers: AdminUser[]
  directoryIsUnfiltered: boolean
  isLoading: boolean
  isError: boolean
  isFetching: boolean
  error: unknown
  onRetry: () => void
  onOffsetChange: (offset: number) => void
}) {
  if (isLoading) {
    return (
      <p role="status" className="text-sm text-slate dark:text-slate-300">
        Loading users...
      </p>
    )
  }
  if (isError) {
    return (
      <div
        role="alert"
        className="rounded border border-red-300/60 bg-red-50 px-3 py-3 text-sm text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-200"
      >
        <p>{resolveUsersError(error)}</p>
        <button
          type="button"
          className="mt-2 min-h-11 rounded border border-current px-3 py-2 font-semibold"
          onClick={onRetry}
          disabled={isFetching}
        >
          {isFetching ? 'Retrying...' : 'Retry user directory'}
        </button>
      </div>
    )
  }
  if (!data) return null

  return (
    <>
      {data.total === 0 && directoryIsUnfiltered && (
        <div className="rounded-lg border border-dashed border-slate/25 px-3 py-4 text-center text-sm text-slate dark:border-cyan-900/40 dark:text-slate-300">
          No accounts are currently returned by the directory. Use the form
          above to create a local account.
        </div>
      )}
      {data.total === 0 && !directoryIsUnfiltered && (
        <div className="rounded-lg border border-dashed border-slate/25 px-3 py-4 text-center text-sm text-slate dark:border-cyan-900/40 dark:text-slate-300">
          No users match the current filters.
        </div>
      )}
      {data.total > 0 && filteredUsers.length === 0 && (
        <div className="rounded-lg border border-dashed border-slate/25 px-3 py-4 text-center text-sm text-slate dark:border-cyan-900/40 dark:text-slate-300">
          This page contains no users matching the immediate client filter. Move
          to another page or adjust the filters.
        </div>
      )}
      {(data.offset > 0 || data.has_more) && (
        <nav
          className="flex flex-col gap-2 border-t border-slate/15 pt-3 sm:flex-row sm:items-center sm:justify-between dark:border-cyan-900/30"
          aria-label="User directory pagination"
        >
          <p className="text-xs text-slate dark:text-slate-300">
            Page {Math.floor(data.offset / data.limit) + 1}
          </p>
          <div className="grid grid-cols-2 gap-2 sm:flex">
            <button
              type="button"
              className="min-h-11 rounded border border-slate/20 px-3 py-2 text-sm font-semibold disabled:opacity-50 dark:border-cyan-900/40"
              disabled={data.offset === 0 || isFetching}
              onClick={() =>
                onOffsetChange(Math.max(0, data.offset - data.limit))
              }
              aria-label="Previous user directory page"
            >
              Previous
            </button>
            <button
              type="button"
              className="min-h-11 rounded border border-slate/20 px-3 py-2 text-sm font-semibold disabled:opacity-50 dark:border-cyan-900/40"
              disabled={!data.has_more || isFetching}
              onClick={() => onOffsetChange(data.offset + data.limit)}
              aria-label="Next user directory page"
            >
              Next
            </button>
          </div>
        </nav>
      )}
    </>
  )
}
