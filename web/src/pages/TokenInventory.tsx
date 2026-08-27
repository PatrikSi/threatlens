import { FormEvent, useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { apiFetch, apiFetchWithResponse } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { ConfirmDialog } from '../components/ConfirmDialog'
import type { ApiToken, ApiTokenListResponse } from '../types/api'
import { formatDateTime } from '../utils/datetime'
import {
  formatTokenRevocationImpact,
  parseTokenRevocationImpact,
} from './tokenRevocationModel'

const TOKEN_PAGE_SIZE = 25

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
  const [revocationNotice, setRevocationNotice] = useState<{
    tone: 'success' | 'error'
    message: string
  } | null>(null)
  const tokensQuery = useQuery({
    queryKey: ['tokens', 'inventory', adminUserFilter, page],
    queryFn: () => {
      const params = new URLSearchParams({
        page: String(page),
        page_size: String(TOKEN_PAGE_SIZE),
      })
      if (isAdmin && adminUserFilter.trim())
        params.set('user_id', adminUserFilter.trim())
      return apiFetch<ApiTokenListResponse>(
        `/tokens/inventory?${params.toString()}`,
      )
    },
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
      setRevocationNotice({
        tone: 'success',
        message: formatTokenRevocationImpact(impact),
      })
      void queryClient.invalidateQueries({ queryKey: ['tokens'] })
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
  const legacyUnscopedTokens =
    tokensQuery.data?.tokens.filter((token) => token.scopes.length === 0) ?? []
  const pageCount = Math.max(
    1,
    Math.ceil((tokensQuery.data?.total ?? 0) / TOKEN_PAGE_SIZE),
  )

  useEffect(() => {
    if (tokensQuery.data && page > pageCount) setPage(pageCount)
  }, [page, pageCount, tokensQuery.data])

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
        className="min-w-0 rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90"
      >
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 id="token-inventory-heading" className="font-display text-xl">
            Token Inventory
          </h2>
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

        {legacyUnscopedTokens.length > 0 && (
          <div className="mt-3 rounded-lg border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
            {legacyUnscopedTokens.length === 1
              ? '1 token has'
              : `${legacyUnscopedTokens.length} tokens have`}{' '}
            no scopes. Scoped API routes now reject unscoped tokens, so rotate
            these credentials before they break automation.
          </div>
        )}

        <div className="mt-3 space-y-2">
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
          {tokensQuery.data && tokensQuery.data.tokens.length > 0 && (
            <ul className="space-y-2" aria-label="API tokens">
              {tokensQuery.data.tokens.map((token) => (
                <TokenInventoryRow
                  key={token.id}
                  token={token}
                  isAdmin={isAdmin}
                  disabled={
                    Boolean(token.revoked_at) ||
                    revokeToken.isPending ||
                    Boolean(pendingRevocation)
                  }
                  onRevoke={() => setPendingRevocation(token)}
                />
              ))}
            </ul>
          )}
          {tokensQuery.data && tokensQuery.data.tokens.length > 0 && (
            <TokenInventoryPagination
              page={page}
              pageCount={pageCount}
              total={tokensQuery.data.total}
              itemCount={tokensQuery.data.tokens.length}
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
                onClick={() => void tokensQuery.refetch()}
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
            tokensQuery.data?.tokens.length === 0 && (
              <div className="rounded-lg border border-dashed border-slate/25 px-3 py-4 text-center text-sm text-slate dark:border-cyan-900/40 dark:text-slate-300">
                {adminUserFilter
                  ? `No API tokens were found for user ${adminUserFilter}.`
                  : 'No API tokens were found for this account.'}
              </div>
            )}
        </div>
      </section>

      <ConfirmDialog
        open={Boolean(pendingRevocation)}
        title="Revoke API token?"
        description="Revoking a token immediately disables any client using it and recursively revokes every delegated child token in its lineage."
        confirmLabel="Revoke token"
        onCancel={() => setPendingRevocation(null)}
        onConfirm={() =>
          pendingRevocation && revokeToken.mutate(pendingRevocation.id)
        }
        confirmDisabled={revokeToken.isPending}
        isConfirming={revokeToken.isPending}
      >
        {pendingRevocation && (
          <div className="space-y-3">
            <p className="break-words font-semibold text-ink [overflow-wrap:anywhere] dark:text-white">
              {pendingRevocation.name}
            </p>
            <p className="break-all text-xs text-slate dark:text-white/70">
              Prefix: {pendingRevocation.token_prefix}
            </p>
            {isAdmin && (
              <p className="break-all text-xs text-slate dark:text-white/70">
                User ID: {pendingRevocation.user_id}
              </p>
            )}
            <p className="break-words text-xs text-slate dark:text-white/70">
              Scopes: {pendingRevocation.scopes.join(', ') || 'none'}
            </p>
            <p className="text-xs text-slate dark:text-white/70">
              Expires:{' '}
              {pendingRevocation.expires_at
                ? formatDateTime(pendingRevocation.expires_at)
                : 'Never'}
            </p>
            {revocationNotice?.tone === 'error' && (
              <p
                role="alert"
                aria-live="assertive"
                aria-atomic="true"
                className="text-sm text-red-600"
              >
                {revocationNotice.message}
              </p>
            )}
          </div>
        )}
      </ConfirmDialog>
    </>
  )
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
  return (
    <div className="grid grid-cols-[auto_1fr_auto] items-center gap-2 pt-2 text-sm sm:flex sm:justify-between">
      <button
        type="button"
        className="min-h-11 rounded border border-slate/30 px-3 py-2 disabled:opacity-50 dark:border-cyan-900/40"
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
        className="min-h-11 rounded border border-slate/30 px-3 py-2 disabled:opacity-50 dark:border-cyan-900/40"
        disabled={disabled || page >= pageCount}
        onClick={() => onPageChange(page + 1)}
      >
        Next
      </button>
    </div>
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
  const statusMessage = draftDirty
    ? applied
      ? `Draft not applied. Results still use user ${applied}.`
      : 'Draft not applied. Results still include your own tokens.'
    : `Showing tokens for user ${applied}.`
  return (
    <form
      className="w-full max-w-full space-y-1 sm:w-auto"
      onSubmit={onApply}
    >
      <label
        htmlFor="token-admin-user-filter"
        className="block text-xs font-semibold uppercase text-slate dark:text-slate-300"
      >
        Filter by User ID
      </label>
      <input
        id="token-admin-user-filter"
        value={draft}
        onChange={(event) => onDraftChange(event.target.value)}
        placeholder="Paste a complete user ID"
        aria-describedby={error ? 'token-admin-user-filter-error' : undefined}
        aria-invalid={Boolean(error)}
        className="w-full max-w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm sm:w-72 dark:border-cyan-900/40 dark:bg-[#072019]"
      />
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="submit"
          className="min-h-11 rounded bg-ink px-3 py-2 text-sm font-semibold text-white dark:bg-cyan dark:text-[#053c2e]"
        >
          Apply filter
        </button>
        {(applied || draft) && (
          <button
            type="button"
            className="min-h-11 rounded border border-slate/30 px-3 py-2 text-sm font-semibold dark:border-cyan-900/40"
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
          className="max-w-72 text-xs text-red-600 dark:text-red-300"
        >
          {error}
        </p>
      )}
      {!error && (applied || draftDirty) && (
        <p
          role="status"
          aria-live="polite"
          className="max-w-72 break-all text-xs text-slate dark:text-slate-300"
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
    <li className="min-w-0 rounded border border-slate/20 p-3 dark:border-cyan-900/40">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <p className="break-words font-semibold [overflow-wrap:anywhere]">
            {token.name}
          </p>
          <p className="break-all text-xs text-slate dark:text-slate-300">
            {token.token_prefix}
          </p>
        </div>
        <button
          type="button"
          className="shrink-0 rounded border border-slate/30 px-2 py-1 text-xs text-red-600 dark:border-cyan-900/40"
          onClick={onRevoke}
          disabled={disabled}
        >
          {token.revoked_at ? 'Revoked' : 'Revoke'}
        </button>
      </div>
      <p className="mt-1 break-words text-xs text-slate dark:text-slate-300">
        Scopes: {token.scopes.join(', ') || 'none'}
      </p>
      {isAdmin && (
        <p className="mt-1 break-all text-xs text-slate dark:text-slate-300">
          User ID: {token.user_id}
        </p>
      )}
      <p className="mt-1 text-xs text-slate dark:text-slate-300">
        Expires: {token.expires_at ? formatDateTime(token.expires_at) : 'Never'}
      </p>
      <p className="mt-1 text-xs text-slate dark:text-slate-300">
        Last used:{' '}
        {token.last_used_at ? formatDateTime(token.last_used_at) : 'Never'}
      </p>
    </li>
  )
}
