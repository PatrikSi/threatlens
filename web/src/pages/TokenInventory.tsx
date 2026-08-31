import { FormEvent, useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { apiFetchWithResponse } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { ConfirmDialog } from '../components/ConfirmDialog'
import type { ApiToken, ApiTokenListResponse } from '../types/api'
import { formatDateTime } from '../utils/datetime'
import {
  formatTokenRevocationImpact,
  parseTokenRevocationImpact,
} from './tokenRevocationModel'
import {
  loadTokenInventory,
  normalizeTokenInventory,
} from './tokenInventoryApi'

const TOKEN_PAGE_SIZE = 25

type RevocationNotice = {
  tone: 'success' | 'error'
  message: string
}

export function TokenInventory({
  isAdmin,
  secretNotice,
}: {
  isAdmin: boolean
  secretNotice: string
}) {
  const queryClient = useQueryClient()
  const [adminUserFilterDraft, setAdminUserFilterDraft] = useState('')
  const [adminUserFilter, setAdminUserFilter] = useState('')
  const [adminUserFilterError, setAdminUserFilterError] = useState('')
  const [page, setPage] = useState(1)
  const [pendingRevocation, setPendingRevocation] = useState<ApiToken | null>(
    null,
  )
  const [revocationNotice, setRevocationNotice] = useState<RevocationNotice | null>(null)
  const [lineageRefreshRequired, setLineageRefreshRequired] = useState(false)
  const tokensQuery = useQuery({
    queryKey: ['tokens', 'inventory', adminUserFilter, page],
    queryFn: () => {
      const params = new URLSearchParams({
        page: String(page),
        page_size: String(TOKEN_PAGE_SIZE),
      })
      if (isAdmin && adminUserFilter.trim())
        params.set('user_id', adminUserFilter.trim())
      return loadTokenInventory(
        params,
        page,
        TOKEN_PAGE_SIZE,
        isAdmin ? adminUserFilter.trim() : '',
      )
    },
    placeholderData: (previousData) => previousData,
  })
  const revokeToken = useMutation({
    mutationKey: ['tokens', 'revoke'],
    mutationFn: async (tokenId: string) => {
      const response = await apiFetchWithResponse<void>(`/tokens/${tokenId}`, {
        method: 'DELETE',
      })
      return parseTokenRevocationImpact(response.headers)
    },
    onMutate: () => setRevocationNotice(null),
    onSuccess: (impact, tokenId) => {
      const revokedAt = new Date().toISOString()
      queryClient.setQueriesData<ApiTokenListResponse>(
        { queryKey: ['tokens', 'inventory'] },
        (current) =>
          current
            ? {
                ...current,
                tokens: current.tokens.map((token) =>
                  token.id === tokenId
                    ? { ...token, revoked_at: revokedAt }
                    : token,
                ),
              }
            : current,
      )
      setPendingRevocation(null)
      const refreshRequired = (impact.revokedDescendantCount ?? 0) > 0
      if (refreshRequired) setLineageRefreshRequired(true)
      setRevocationNotice({
        tone: 'success',
        message: formatTokenRevocationImpact(impact),
      })
      const refresh = queryClient.invalidateQueries(
        { queryKey: ['tokens'] },
        { throwOnError: refreshRequired },
      )
      if (refreshRequired) {
        void Promise.resolve(refresh)
          .then(() => setLineageRefreshRequired(false))
          .catch(() => undefined)
      }
    },
    onError: (error) => {
      setRevocationNotice({
        tone: 'error',
        message: resolveApiErrorMessage(
          error,
          'API token could not be revoked',
        ),
      })
    },
  })
  const inventory =
    tokensQuery.data === undefined
      ? undefined
      : normalizeTokenInventory(tokensQuery.data, page, TOKEN_PAGE_SIZE)
  const tokens = inventory?.tokens ?? []
  const legacyUnscopedTokens = tokens.filter(
    (token) => token.scopes.length === 0,
  )
  const unscopedTotal =
    inventory?.unscoped_total ?? legacyUnscopedTokens.length
  const pageCount = Math.max(
    1,
    Math.ceil((inventory?.total ?? 0) / TOKEN_PAGE_SIZE),
  )
  const inventoryActionsUnavailable =
    tokensQuery.isFetching ||
    tokensQuery.isPlaceholderData ||
    tokensQuery.isError ||
    lineageRefreshRequired

  useEffect(() => {
    if (tokensQuery.data && page > pageCount) setPage(pageCount)
  }, [page, pageCount, tokensQuery.data])

  useEffect(() => {
    setPendingRevocation(null)
  }, [adminUserFilter, page])

  const retryInventory = () => {
    const refresh = tokensQuery.refetch()
    void Promise.resolve(refresh).then((result) => {
      if (!result?.error) setLineageRefreshRequired(false)
    })
  }

  const applyAdminUserFilter = (event: FormEvent) => {
    event.preventDefault()
    const nextFilter = adminUserFilterDraft.trim()
    if (
      nextFilter &&
      !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
        nextFilter,
      )
    ) {
      setAdminUserFilterError(
        'Enter a complete user ID, such as the ID shown in the user directory.',
      )
      return
    }
    setAdminUserFilterError('')
    setPage(1)
    setAdminUserFilter(nextFilter)
  }

  return (
    <>
      <section
        aria-labelledby="token-inventory-heading"
        className="min-w-0 rounded-xl border border-slate/20 bg-white/80 p-3 dark:border-cyan-900/40 dark:bg-[#041612]/90"
      >
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="flex min-h-7 items-center gap-2">
            <h2 id="token-inventory-heading" className="font-display text-lg">
              Issued tokens
            </h2>
            <TokenInventoryCount inventory={inventory} />
          </div>
          {isAdmin && (
            <AdminTokenFilter
              draft={adminUserFilterDraft}
              applied={adminUserFilter}
              error={adminUserFilterError}
              onDraftChange={(value) => {
                setAdminUserFilterDraft(value)
                setAdminUserFilterError('')
              }}
              onApply={applyAdminUserFilter}
              onClear={() => {
                setAdminUserFilterDraft('')
                setAdminUserFilter('')
                setAdminUserFilterError('')
                setPage(1)
              }}
            />
          )}
        </div>

        {adminUserFilter && (
          <div
            role="status"
            aria-live="polite"
            aria-atomic="true"
            aria-label="Organization token administration scope"
            className="mt-2 rounded-lg border border-amber-300/60 bg-amber-50 px-2.5 py-2 text-amber-950 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100"
          >
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span className="tl-chip tl-chip-warning">
                Organization administration
              </span>
              <span className="text-sm font-semibold">
                Owner-scoped token inventory
              </span>
            </div>
            <p className="mt-1 break-words text-xs">
              Viewing tokens for owner{' '}
              <span className="break-all font-mono text-xs">
                {adminUserFilter}
              </span>
              . You can inspect and revoke this user's tokens; revocation
              immediately disables affected clients.
            </p>
          </div>
        )}

        <TokenScopeMigrationWarning unscopedTotal={unscopedTotal} />

        <div className="mt-2 space-y-2">
          {secretNotice && (
            <p
              role="status"
              aria-live="polite"
              className="text-sm text-green-700 dark:text-green-300"
            >
              {secretNotice}
            </p>
          )}
          {revocationNotice && (
            <p
              role={revocationNotice.tone === 'error' ? 'alert' : 'status'}
              aria-live={
                revocationNotice.tone === 'error' ? 'assertive' : 'polite'
              }
              aria-atomic="true"
              className={`text-sm ${
                revocationNotice.tone === 'success'
                  ? 'text-emerald-700 dark:text-emerald-300'
                  : 'text-red-600 dark:text-red-300'
              }`}
            >
              {revocationNotice.message}
            </p>
          )}
          {lineageRefreshRequired && (
            <p
              role="status"
              aria-live="polite"
              className="text-sm text-amber-800 dark:text-amber-200"
            >
              Delegated token status is refreshing. Revocation actions remain
              disabled until the inventory is current.
            </p>
          )}
          {(tokensQuery.isFetching || tokensQuery.isPlaceholderData) &&
            !tokensQuery.isLoading && (
              <p
                role="status"
                aria-live="polite"
                className="text-sm text-slate dark:text-slate-300"
              >
                Refreshing token inventory. Previous results are read-only until
                the requested inventory is current.
              </p>
            )}
          {inventory && tokens.length > 0 && (
            <ul
              className="divide-y divide-slate/20 overflow-hidden rounded-lg border border-slate/20 dark:divide-cyan-900/40 dark:border-cyan-900/40"
              aria-label="API tokens"
            >
              {tokens.map((token) => (
                <TokenInventoryRow
                  key={token.id}
                  token={token}
                  isAdmin={isAdmin}
                  disabled={
                    Boolean(token.revoked_at) ||
                    revokeToken.isPending ||
                    Boolean(pendingRevocation) ||
                    inventoryActionsUnavailable
                  }
                  onRevoke={() => setPendingRevocation(token)}
                />
              ))}
            </ul>
          )}
          {inventory && tokens.length > 0 && (
            <TokenInventoryPagination
              page={page}
              pageCount={pageCount}
              total={inventory.total}
              itemCount={tokens.length}
              disabled={tokensQuery.isFetching || revokeToken.isPending}
              onPageChange={setPage}
            />
          )}
          {tokensQuery.isLoading && (
            <p role="status" className="text-sm text-slate dark:text-slate-300">
              Loading tokens...
            </p>
          )}
          {tokensQuery.isError && (
            <div
              role="alert"
              className="rounded border border-red-300/60 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-200"
            >
              <p>
                {resolveApiErrorMessage(
                  tokensQuery.error,
                  'API tokens could not be loaded',
                )}
              </p>
              <button
                type="button"
                className="mt-2 min-h-11 rounded border border-current px-3 py-2 font-semibold"
                onClick={retryInventory}
                disabled={tokensQuery.isFetching}
              >
                {tokensQuery.isFetching
                  ? 'Retrying...'
                  : 'Retry token inventory'}
              </button>
            </div>
          )}
          {!tokensQuery.isLoading &&
            !tokensQuery.isError &&
            inventory?.tokens.length === 0 && (
              <div className="rounded-lg border border-dashed border-slate/25 px-3 py-3 text-center text-sm text-slate dark:border-cyan-900/40 dark:text-slate-300">
                {adminUserFilter
                  ? `No API tokens were found for user ${adminUserFilter}.`
                  : 'No API tokens were found for this account.'}
              </div>
            )}
        </div>
      </section>

      <TokenRevocationDialog
        token={pendingRevocation}
        isAdmin={isAdmin}
        actionsUnavailable={inventoryActionsUnavailable}
        isConfirming={revokeToken.isPending}
        notice={revocationNotice}
        onCancel={() => setPendingRevocation(null)}
        onConfirm={(tokenId) => revokeToken.mutate(tokenId)}
      />
    </>
  )
}

function TokenRevocationDialog({
  token,
  isAdmin,
  actionsUnavailable,
  isConfirming,
  notice,
  onCancel,
  onConfirm,
}: {
  token: ApiToken | null
  isAdmin: boolean
  actionsUnavailable: boolean
  isConfirming: boolean
  notice: RevocationNotice | null
  onCancel: () => void
  onConfirm: (tokenId: string) => void
}) {
  const confirmRevocation = () => {
    if (!token || actionsUnavailable) return
    onConfirm(token.id)
  }
  return (
    <ConfirmDialog
      open={Boolean(token)}
      title="Revoke API token?"
      description="Revoking a token immediately disables any client using it and recursively revokes every delegated child token in its lineage."
      confirmLabel="Revoke token"
      onCancel={onCancel}
      onConfirm={confirmRevocation}
      confirmDisabled={isConfirming || actionsUnavailable}
      isConfirming={isConfirming}
    >
      {token && (
        <div className="space-y-3">
          <p className="break-words font-semibold text-ink [overflow-wrap:anywhere] dark:text-white">
            {token.name}
          </p>
          <p className="break-all text-xs text-slate dark:text-white/70">
            Prefix: {token.token_prefix}
          </p>
          {isAdmin && (
            <p className="break-all text-xs text-slate dark:text-white/70">
              Owner user ID: {token.user_id}
            </p>
          )}
          <p className="break-words text-xs text-slate dark:text-white/70">
            Permissions: {token.scopes.join(', ') || 'none'}
          </p>
          <p className="text-xs text-slate dark:text-white/70">
            Expires: {token.expires_at ? formatDateTime(token.expires_at) : 'Never'}
          </p>
          {notice?.tone === 'error' && (
            <p
              role="alert"
              aria-live="assertive"
              aria-atomic="true"
              className="text-sm text-red-600"
            >
              {notice.message}
            </p>
          )}
        </div>
      )}
    </ConfirmDialog>
  )
}

function TokenScopeMigrationWarning({
  unscopedTotal,
}: {
  unscopedTotal: number
}) {
  if (unscopedTotal < 1) return null
  return (
    <div className="mt-2 rounded-lg border border-amber-300/60 bg-amber-50 px-2.5 py-2 text-xs text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
      {unscopedTotal === 1 ? '1 token has' : `${unscopedTotal} tokens have`} no
      scopes. Scoped API routes now reject unscoped tokens, so rotate these
      credentials before they break automation.
    </div>
  )
}

function TokenInventoryCount({
  inventory,
}: {
  inventory?: ApiTokenListResponse
}) {
  if (!inventory) return null
  return <span className="tl-chip tl-chip-neutral">{inventory.total} total</span>
}

function TokenInventoryPagination({
  page,
  pageCount,
  total,
  itemCount,
  disabled,
  onPageChange,
}: {
  page: number
  pageCount: number
  total: number
  itemCount: number
  disabled: boolean
  onPageChange: (page: number) => void
}) {
  const first = total === 0 ? 0 : (page - 1) * TOKEN_PAGE_SIZE + 1
  const last = first === 0 ? 0 : first + itemCount - 1
  if (pageCount <= 1) return null
  return (
    <nav
      aria-label="Token inventory pages"
      className="grid grid-cols-[auto_1fr_auto] items-center gap-2 pt-1 text-xs text-slate sm:flex sm:justify-between dark:text-slate-300"
    >
      <button
        type="button"
        className="min-h-11 rounded border border-slate/30 px-2.5 py-1.5 font-semibold disabled:opacity-50 sm:min-h-0 dark:border-cyan-900/40"
        disabled={disabled || page <= 1}
        onClick={() => onPageChange(page - 1)}
      >
        Previous
      </button>
      <span className="text-center">
        {first}-{last} of {total} · Page {page} of {pageCount}
      </span>
      <button
        type="button"
        className="min-h-11 rounded border border-slate/30 px-2.5 py-1.5 font-semibold disabled:opacity-50 sm:min-h-0 dark:border-cyan-900/40"
        disabled={disabled || page >= pageCount}
        onClick={() => onPageChange(page + 1)}
      >
        Next
      </button>
    </nav>
  )
}

function AdminTokenFilter({
  draft,
  applied,
  error,
  onDraftChange,
  onApply,
  onClear,
}: {
  draft: string
  applied: string
  error: string
  onDraftChange: (value: string) => void
  onApply: (event: FormEvent) => void
  onClear: () => void
}) {
  const draftDirty = draft.trim() !== applied
  const statusMessage = applied
    ? `Draft not applied. Results still use owner ${applied}.`
    : 'Draft not applied. Results still include your own tokens.'
  return (
    <form
      aria-label="Filter token inventory by owner"
      className="w-full max-w-full sm:w-auto"
      onSubmit={onApply}
    >
      <label
        htmlFor="token-admin-user-filter"
        className="block text-xs font-semibold text-slate dark:text-slate-300"
      >
        Owner user ID
      </label>
      <div className="mt-1 flex min-w-0 items-stretch gap-1.5">
        <input
          id="token-admin-user-filter"
          value={draft}
          onChange={(event) => onDraftChange(event.target.value)}
          placeholder="Paste a complete user ID"
          aria-describedby={error ? 'token-admin-user-filter-error' : undefined}
          aria-invalid={Boolean(error)}
          className="min-h-11 min-w-0 max-w-full flex-1 rounded border border-slate/30 bg-white px-2.5 py-1.5 text-sm sm:min-h-0 sm:w-64 dark:border-cyan-900/40 dark:bg-[#072019]"
        />
        <button
          type="submit"
          className="min-h-11 shrink-0 rounded bg-ink px-2.5 py-1.5 text-sm font-semibold text-white sm:min-h-0 dark:bg-cyan dark:text-[#053c2e]"
        >
          Apply
        </button>
        {(applied || draft) && (
          <button
            type="button"
            className="min-h-11 shrink-0 rounded border border-slate/30 px-2.5 py-1.5 text-sm font-semibold sm:min-h-0 dark:border-cyan-900/40"
            onClick={onClear}
          >
            Clear
          </button>
        )}
      </div>
      {error && (
        <p
          id="token-admin-user-filter-error"
          role="alert"
          className="mt-1 max-w-full text-xs text-red-600 sm:max-w-[26rem] dark:text-red-300"
        >
          {error}
        </p>
      )}
      {!error && draftDirty && (
        <p
          role="status"
          aria-live="polite"
          className="mt-1 max-w-full break-all text-xs text-slate sm:max-w-[26rem] dark:text-slate-300"
        >
          {statusMessage}
        </p>
      )}
    </form>
  )
}

function TokenInventoryRow({
  token,
  isAdmin,
  disabled,
  onRevoke,
}: {
  token: ApiToken
  isAdmin: boolean
  disabled: boolean
  onRevoke: () => void
}) {
  return (
    <li className="min-w-0 px-3 py-2.5">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <p className="break-words font-semibold [overflow-wrap:anywhere]">
            {token.name}
          </p>
          <p className="break-all text-xs text-slate dark:text-slate-300">
            Prefix: <span className="font-mono">{token.token_prefix}</span>
          </p>
        </div>
        {token.revoked_at ? (
          <span className="tl-chip tl-chip-neutral shrink-0">Revoked</span>
        ) : (
          <button
            type="button"
            className="shrink-0 rounded border border-slate/30 px-2 py-1 text-xs font-semibold text-red-600 disabled:cursor-not-allowed disabled:opacity-50 max-sm:min-h-11 max-sm:min-w-11 dark:border-cyan-900/40"
            onClick={onRevoke}
            disabled={disabled}
          >
            Revoke
          </button>
        )}
      </div>
      <dl className="mt-1.5 grid gap-x-4 gap-y-1 text-xs text-slate sm:grid-cols-2 dark:text-slate-300">
        <div className="min-w-0 break-words sm:col-span-2">
          <dt className="inline font-semibold">Permissions: </dt>
          <dd className="inline">{token.scopes.join(', ') || 'none'}</dd>
        </div>
        {isAdmin && (
          <div className="min-w-0 break-all sm:col-span-2">
            <dt className="block font-semibold sm:inline">Owner user ID:</dt>{' '}
            <dd className="block font-mono sm:inline">{token.user_id}</dd>
          </div>
        )}
        <div>
          <dt className="inline font-semibold">Expires: </dt>
          <dd className="inline">
            {token.expires_at ? formatDateTime(token.expires_at) : 'Never'}
          </dd>
        </div>
        <div>
          <dt className="inline font-semibold">Last used: </dt>
          <dd className="inline">
            {token.last_used_at ? formatDateTime(token.last_used_at) : 'Never'}
          </dd>
        </div>
      </dl>
    </li>
  )
}
