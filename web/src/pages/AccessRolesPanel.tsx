import { useCallback, useEffect, useMemo, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, LockKeyhole, Plus, Save, Search, Trash2 } from 'lucide-react'

import { resolveApiErrorMessage } from '../api/errors'
import { isAmbiguousMutationError } from '../api/mutationResilience'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import type { IAMPermission, IAMRole } from '../types/api'
import type { IAMCatalog } from './accessGovernanceApi'
import {
  createIAMRole,
  deleteIAMRole,
  updateIAMRole,
} from './accessGovernanceApi'

interface RoleDraft {
  key: string
  name: string
  description: string
  permissions: string[]
}

interface RoleDeleteRequest {
  id: string
  name: string
  revision: number
}

const EMPTY_ROLE: RoleDraft = {
  key: '',
  name: '',
  description: '',
  permissions: [],
}

export function AccessRolesPanel({
  catalog,
  canWrite,
  durablePermissions,
  onDirtyChange,
}: {
  catalog: IAMCatalog
  canWrite: boolean
  durablePermissions: string[]
  onDirtyChange?: (dirty: boolean) => void
}) {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const {
    selectedRoleId,
    setSelectedRoleId,
    selectedRole,
    draft,
    setDraft,
    draftRevision,
    creating,
    dirty,
    loadRoleDraft,
  } = useRoleDraftState(catalog.roles)
  const [deleteRequest, setDeleteRequest] = useState<RoleDeleteRequest | null>(null)

  const filteredRoles = useMemo(() => {
    const needle = search.trim().toLowerCase()
    if (!needle) return catalog.roles
    return catalog.roles.filter((role) =>
      [role.name, role.key, role.description].some((value) =>
        value.toLowerCase().includes(needle),
      ),
    )
  }, [catalog.roles, search])
  const permissionGroups = useMemo(
    () => groupPermissions(catalog.permissions),
    [catalog.permissions],
  )
  const editable =
    canWrite &&
    (creating || Boolean(selectedRole && !selectedRole.is_system))
  const draftDelegable = draft.permissions.every((permission) =>
    durablePermissions.includes(permission),
  )
  const validation = roleValidation(draft, creating)
  const confirmDiscard = useUnsavedChangesWarning(
    dirty,
    'Discard the unsaved access-role changes?',
  )

  useEffect(() => {
    onDirtyChange?.(dirty)
  }, [dirty, onDirtyChange])
  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange])

  const saveRole = useMutation({
    mutationFn: () =>
      creating
        ? createIAMRole(normalizeDraft(draft))
        : updateIAMRole(selectedRoleId as string, {
            expected_revision: draftRevision!,
            name: draft.name.trim(),
            description: draft.description.trim(),
            permissions: [...new Set(draft.permissions)].sort(),
          }),
    onSuccess: async (saved) => {
      await queryClient.invalidateQueries({ queryKey: ['governance', 'iam-catalog'] })
      loadRoleDraft(saved)
    },
    onError: () =>
      queryClient.invalidateQueries({ queryKey: ['governance', 'iam-catalog'] }),
  })
  const removeRole = useMutation({
    mutationFn: () =>
      deleteIAMRole(deleteRequest!.id, deleteRequest!.revision),
    onSuccess: async () => {
      setDeleteRequest(null)
      setSelectedRoleId(null)
      await queryClient.invalidateQueries({ queryKey: ['governance', 'iam-catalog'] })
    },
    onError: () =>
      queryClient.invalidateQueries({ queryKey: ['governance', 'iam-catalog'] }),
  })
  const mutationError = saveRole.error ?? removeRole.error
  const saveOutcomeUnknown = isAmbiguousMutationError(saveRole.error)

  const chooseRole = (role: IAMRole) => {
    if (saveRole.isPending || removeRole.isPending) return
    confirmDiscard(() => {
      loadRoleDraft(role)
      saveRole.reset()
      removeRole.reset()
    })
  }

  const chooseNewRole = () => {
    if (saveRole.isPending || removeRole.isPending) return
    confirmDiscard(() => {
      loadRoleDraft('new')
      saveRole.reset()
      removeRole.reset()
    })
  }

  return (
    <section className="tl-surface overflow-hidden rounded-xl" aria-labelledby="roles-heading">
      <header className="border-b border-slate/15 px-4 py-4 dark:border-white/10 sm:px-5">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h2 id="roles-heading" className="font-display text-xl">Access roles</h2>
            <p className="mt-1 text-sm text-slate dark:text-slate-300">
              A base role (Administrator, Analyst, or Viewer) sets built-in access for each user.
              Access roles add permission bundles through governance assignments; system
              roles are sealed.
            </p>
          </div>
          {canWrite && (
            <button
              type="button"
              className="inline-flex min-h-11 items-center justify-center gap-2 rounded bg-ink px-3 py-2 text-sm font-semibold text-white sm:min-h-0 dark:bg-cyan dark:text-[#053c2e]"
              onClick={chooseNewRole}
            >
              <Plus className="h-4 w-4" aria-hidden="true" />
              New access role
            </button>
          )}
        </div>
      </header>

      <div className="grid min-h-[620px] lg:grid-cols-[320px_minmax(0,1fr)]">
        <aside className="border-b border-slate/15 p-3 dark:border-white/10 lg:border-b-0 lg:border-r">
          <label className="relative block">
            <span className="sr-only">Search access roles</span>
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate" aria-hidden="true" />
            <input
              type="search"
              className="min-h-11 w-full rounded border border-slate/25 bg-white py-2 pl-9 pr-3 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
              placeholder="Search access roles"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>
          <ul className="mt-3 max-h-[560px] space-y-1 overflow-y-auto" aria-label="Access roles">
            {filteredRoles.map((role) => {
              const selected = role.id === selectedRoleId
              return (
                <li key={role.id}>
                  <button
                    type="button"
                    className={`w-full rounded-lg border px-3 py-2.5 text-left ${selected ? 'border-cyan/50 bg-cyan/10' : 'border-transparent hover:border-slate/20 hover:bg-slate/5 dark:hover:border-white/10 dark:hover:bg-white/[0.04]'}`}
                    aria-current={selected ? 'true' : undefined}
                    onClick={() => chooseRole(role)}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <span className="font-semibold text-ink dark:text-white">{role.name}</span>
                      {role.is_system && <LockKeyhole className="mt-0.5 h-4 w-4 shrink-0 text-slate" aria-label="System access role" />}
                    </div>
                    <p className="mt-0.5 font-mono text-xs text-slate dark:text-slate-400">{role.key}</p>
                    <p className="mt-1 text-xs text-slate dark:text-slate-400">
                      {role.permissions.length} grants · {role.assignment_count} principal assignments · {role.group_count} groups
                    </p>
                  </button>
                </li>
              )
            })}
            {filteredRoles.length === 0 && (
              <li className="px-3 py-6 text-center text-sm text-slate dark:text-slate-400">No access roles match this search.</li>
            )}
          </ul>
        </aside>

        <div className="min-w-0 p-4 sm:p-5">
          {selectedRoleId === null ? (
            <p className="text-sm text-slate dark:text-slate-300">Select an access role to inspect its effective permission bundle.</p>
          ) : (
            <form
              onSubmit={(event) => {
                event.preventDefault()
                if (editable && dirty && !validation && draftDelegable) {
                  saveRole.mutate()
                }
              }}
            >
              <fieldset disabled={!editable || saveRole.isPending || removeRole.isPending}>
                <div className="grid gap-4 md:grid-cols-2">
                  <label className="text-sm font-semibold">
                    Access role name
                    <input
                      className="mt-1 min-h-11 w-full rounded border border-slate/25 bg-white px-3 py-2 font-normal dark:border-cyan-900/40 dark:bg-[#072019]"
                      value={draft.name}
                      onChange={(event) => setDraft((current) => ({ ...current, name: event.target.value }))}
                    />
                  </label>
                  <label className="text-sm font-semibold">
                    Stable key
                    <input
                      className="mt-1 min-h-11 w-full rounded border border-slate/25 bg-white px-3 py-2 font-mono font-normal dark:border-cyan-900/40 dark:bg-[#072019]"
                      value={draft.key}
                      readOnly={!creating}
                      onChange={(event) => setDraft((current) => ({ ...current, key: event.target.value }))}
                    />
                  </label>
                  <label className="text-sm font-semibold md:col-span-2">
                    Description
                    <textarea
                      className="mt-1 min-h-24 w-full rounded border border-slate/25 bg-white px-3 py-2 font-normal dark:border-cyan-900/40 dark:bg-[#072019]"
                      value={draft.description}
                      onChange={(event) => setDraft((current) => ({ ...current, description: event.target.value }))}
                    />
                  </label>
                </div>

                {selectedRole?.is_system && (
                  <div className="mt-4 flex items-start gap-2 rounded border border-slate/20 bg-slate/5 px-3 py-2 text-sm dark:border-white/10 dark:bg-white/[0.04]">
                    <LockKeyhole className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                    System access-role definitions are sealed. Create a custom access role for a narrower permission bundle.
                  </div>
                )}

                <div className="mt-5">
                  <div className="flex items-end justify-between gap-3">
                    <div>
                      <h3 className="font-semibold">Permission bundle</h3>
                      <p className="mt-1 text-xs text-slate dark:text-slate-400">
                        Critical permissions should be combined with approvals, reviews, or temporary elevation.
                      </p>
                    </div>
                    <span className="text-xs font-semibold text-slate dark:text-slate-300">{draft.permissions.length} selected</span>
                  </div>
                  <div className="mt-3 space-y-3">
                    <UnmatchedPermissionGrants
                      grants={draft.permissions.filter(
                        (grant) =>
                          !catalog.permissions.some(
                            (permission) => permission.id === grant,
                          ),
                      )}
                    />
                    {permissionGroups.map(([group, permissions]) => (
                      <PermissionGroup
                        key={group}
                        group={group}
                        permissions={permissions}
                        selected={new Set(draft.permissions)}
                        durablePermissions={durablePermissions}
                        onToggle={(permissionId, checked) =>
                          setDraft((current) => ({
                            ...current,
                            permissions: checked
                              ? [...new Set([...current.permissions, permissionId])].sort()
                              : current.permissions.filter((value) => value !== permissionId),
                          }))
                        }
                      />
                    ))}
                  </div>
                </div>
              </fieldset>

              {validation && editable && <p role="alert" className="mt-4 rounded border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100">{validation}</p>}
              {editable && !draftDelegable && (
                <p className="mt-4 rounded border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100">
                  Remove permissions outside your durable authority before saving this persistent access role. You cannot add them again.
                </p>
              )}
              {!creating && !selectedRole && (
                <p role="alert" className="mt-4 rounded border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100">
                  This access role changed or was removed while you were editing. Copy any draft details you need, then discard this draft and reload the catalog.
                </p>
              )}
              {mutationError && (
                <div role="alert" className="mt-4 rounded border border-red-300/60 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-100">
                  <p className="flex items-start gap-2">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                    {resolveApiErrorMessage(mutationError, 'Access-role change failed')}
                  </p>
                  {saveOutcomeUnknown && (
                    <>
                      <p className="mt-2">The outcome is unknown. The catalog was refreshed; review the matching server access role before attempting another write.</p>
                      <button
                        type="button"
                        className="mt-2 font-semibold underline"
                        onClick={() => {
                          const matched = creating
                            ? catalog.roles.find((role) => role.key === normalizeDraft(draft).key)
                            : selectedRole
                          if (matched) loadRoleDraft(matched)
                          saveRole.reset()
                        }}
                      >
                        Use refreshed state
                      </button>
                    </>
                  )}
                </div>
              )}

              <div className="mt-5 flex flex-col-reverse gap-2 border-t border-slate/15 pt-4 sm:flex-row sm:justify-between dark:border-white/10">
                <div>
                  {selectedRole && !selectedRole.is_system && canWrite && (
                    <button type="button" className="tl-button-danger inline-flex min-h-11 items-center justify-center gap-2 rounded px-3 py-2 text-sm font-semibold disabled:opacity-60 sm:min-h-0" onClick={() => {
                      removeRole.reset()
                      setDeleteRequest({ id: selectedRole.id, name: selectedRole.name, revision: draftRevision! })
                    }} disabled={dirty || saveRole.isPending || removeRole.isPending}>
                      <Trash2 className="h-4 w-4" aria-hidden="true" />
                      Delete access role
                    </button>
                  )}
                </div>
                <button
                  type="submit"
                  className="inline-flex min-h-11 items-center justify-center gap-2 rounded bg-ink px-3 py-2 text-sm font-semibold text-white disabled:opacity-60 sm:min-h-0 dark:bg-cyan dark:text-[#053c2e]"
                  disabled={!editable || !dirty || Boolean(validation) || !draftDelegable || saveRole.isPending || saveOutcomeUnknown}
                >
                  <Save className="h-4 w-4" aria-hidden="true" />
                  {saveRole.isPending ? 'Saving…' : creating ? 'Create access role' : 'Save revision'}
                </button>
              </div>
            </form>
          )}
        </div>
      </div>

      <ConfirmDialog
        open={deleteRequest !== null}
        title={`Delete ${deleteRequest?.name ?? 'access role'}?`}
        description="Deletion is rejected while any user, group, service account, OIDC mapping, handling policy, or live elevation still references this access role. This action cannot be undone."
        confirmLabel="Delete access role"
        isConfirming={removeRole.isPending}
        confirmDisabled={removeRole.error != null}
        onConfirm={() => removeRole.mutate()}
        onCancel={() => {
          setDeleteRequest(null)
          removeRole.reset()
        }}
      >
        {removeRole.error && (
          <p role="alert" className="rounded border border-red-300/60 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-100">
            {resolveApiErrorMessage(removeRole.error, 'Access-role deletion failed')}
          </p>
        )}
      </ConfirmDialog>
      {confirmDiscard.discardDialog}
    </section>
  )
}

function PermissionGroup({
  group,
  permissions,
  selected,
  durablePermissions,
  onToggle,
}: {
  group: string
  permissions: IAMPermission[]
  selected: Set<string>
  durablePermissions: string[]
  onToggle: (permissionId: string, checked: boolean) => void
}) {
  return (
    <fieldset className="rounded-lg border border-slate/20 p-3 dark:border-white/10">
      <legend className="px-1 text-sm font-semibold">{group}</legend>
      <div className="grid gap-2 md:grid-cols-2">
        {permissions.map((permission) => (
          <label key={permission.id} className="flex min-h-11 items-start gap-3 rounded px-2 py-2 hover:bg-slate/5 dark:hover:bg-white/[0.04]">
            <input
              type="checkbox"
              className="mt-0.5 h-5 w-5 shrink-0"
              checked={selected.has(permission.id)}
              disabled={
                !permission.delegable ||
                (!selected.has(permission.id) &&
                  !durablePermissions.includes(permission.id))
              }
              onChange={(event) => onToggle(permission.id, event.target.checked)}
            />
            <span className="min-w-0">
              <span className="flex flex-wrap items-center gap-2">
                <span className="font-semibold text-ink dark:text-white">{permission.label}</span>
                <RiskBadge risk={permission.risk} />
              </span>
              <span className="mt-0.5 block font-mono text-[11px] text-slate dark:text-slate-400">{permission.id}</span>
              <span className="mt-1 block text-xs text-slate dark:text-slate-300">{permission.description}</span>
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  )
}

function UnmatchedPermissionGrants({ grants }: { grants: string[] }) {
  if (grants.length === 0) return null
  return (
    <div className="rounded-lg border border-amber-300/60 bg-amber-50 p-3 text-sm text-amber-950 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100">
      <p className="font-semibold">Wildcard and reserved grants</p>
      <ul className="mt-2 space-y-2">
        {grants.map((grant) => (
          <li key={grant}>
            <span className="font-semibold">
              {grant === '*:*'
                ? 'All current and future permissions (wildcard)'
                : grant}
            </span>
            <span className="mt-0.5 block font-mono text-xs">{grant}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

function RiskBadge({ risk }: { risk: IAMPermission['risk'] }) {
  const className =
    risk === 'critical'
      ? 'border-red-300 bg-red-50 text-red-800 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-100'
      : risk === 'elevated'
        ? 'border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100'
        : 'border-slate/20 bg-slate/5 text-slate dark:border-white/10 dark:bg-white/[0.04] dark:text-slate-300'
  return <span className={`rounded border px-1.5 py-0.5 text-[10px] font-semibold uppercase ${className}`}>{risk}</span>
}

function groupPermissions(permissions: IAMPermission[]): Array<[string, IAMPermission[]]> {
  const groups = new Map<string, IAMPermission[]>()
  for (const permission of permissions) {
    const entries = groups.get(permission.group) ?? []
    entries.push(permission)
    groups.set(permission.group, entries)
  }
  return [...groups.entries()]
    .map(([group, entries]) => [group, entries.sort((left, right) => left.label.localeCompare(right.label))] as [string, IAMPermission[]])
    .sort(([left], [right]) => left.localeCompare(right))
}

function useRoleDraftState(roles: IAMRole[]) {
  const initialRole = roles[0] ?? null
  const [selectedRoleId, setSelectedRoleId] = useState<string | 'new' | null>(
    initialRole?.id ?? null,
  )
  const selectedRole =
    selectedRoleId === 'new'
      ? null
      : roles.find((role) => role.id === selectedRoleId) ?? null
  const [draft, setDraft] = useState<RoleDraft>(() =>
    initialRole ? roleDraft(initialRole) : EMPTY_ROLE,
  )
  const [baselineDraft, setBaselineDraft] = useState<RoleDraft>(() =>
    initialRole ? roleDraft(initialRole) : EMPTY_ROLE,
  )
  const [draftRevision, setDraftRevision] = useState<number | null>(
    initialRole?.revision ?? null,
  )
  const creating = selectedRoleId === 'new'
  const dirty =
    JSON.stringify(normalizeDraft(draft)) !==
    JSON.stringify(normalizeDraft(baselineDraft))

  const loadRoleDraft = useCallback((role: IAMRole | 'new') => {
    const nextRole = role === 'new' ? null : role
    const nextDraft = nextRole ? roleDraft(nextRole) : EMPTY_ROLE
    setSelectedRoleId(nextRole?.id ?? 'new')
    setDraft(nextDraft)
    setBaselineDraft(nextDraft)
    setDraftRevision(nextRole?.revision ?? null)
  }, [])

  useEffect(() => {
    if (selectedRoleId === 'new') {
      if (!dirty) loadRoleDraft('new')
      return
    }
    const current = roles.find((role) => role.id === selectedRoleId)
    if (current) {
      if (!dirty) loadRoleDraft(current)
      return
    }
    if (dirty) return
    const fallback = roles[0] ?? null
    if (fallback) {
      loadRoleDraft(fallback)
      return
    }
    setSelectedRoleId(null)
    setDraft(EMPTY_ROLE)
    setBaselineDraft(EMPTY_ROLE)
    setDraftRevision(null)
  }, [dirty, loadRoleDraft, roles, selectedRoleId])

  return {
    selectedRoleId,
    setSelectedRoleId,
    selectedRole,
    draft,
    setDraft,
    draftRevision,
    creating,
    dirty,
    loadRoleDraft,
  }
}

function roleDraft(role: IAMRole): RoleDraft {
  return {
    key: role.key,
    name: role.name,
    description: role.description,
    permissions: [...role.permissions].sort(),
  }
}

function normalizeDraft(draft: RoleDraft): RoleDraft {
  return {
    key: draft.key.trim().toLowerCase(),
    name: draft.name.trim(),
    description: draft.description.trim(),
    permissions: [...new Set(draft.permissions)].sort(),
  }
}

function roleValidation(draft: RoleDraft, creating: boolean): string | null {
  if (!draft.name.trim()) return 'Access role name is required.'
  if (creating && !/^[a-z][a-z0-9-]{1,62}[a-z0-9]$/.test(draft.key.trim().toLowerCase())) {
    return 'Access-role key must be 3–64 lowercase letters, numbers, or hyphens and start with a letter.'
  }
  return null
}
