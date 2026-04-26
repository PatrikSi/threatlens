import { FormEvent, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, apiFetch } from '../api/client'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { useTokenCreateFormState } from '../hooks/useTokenCreateFormState'
import { ApiToken, ApiTokenCreateResponse } from '../types/api'
import { formatDateTime } from '../utils/datetime'

export function TokensPage() {
  const queryClient = useQueryClient()
  const meQuery = useCurrentUser()
  const [tokenFormState, dispatchTokenForm] = useTokenCreateFormState()
  const [adminUserFilter, setAdminUserFilter] = useState('')
  const [pendingRevocation, setPendingRevocation] = useState<ApiToken | null>(null)
  const [revocationNotice, setRevocationNotice] = useState<{ tone: 'success' | 'error'; message: string } | null>(null)
  const isAdmin = meQuery.data?.role === 'admin'

  const tokenQueryKey = ['tokens', adminUserFilter]
  const tokensQuery = useQuery({
    queryKey: tokenQueryKey,
    queryFn: () => {
      const params = new URLSearchParams()
      if (isAdmin && adminUserFilter.trim()) {
        params.set('user_id', adminUserFilter.trim())
      }
      const suffix = params.toString() ? `?${params.toString()}` : ''
      return apiFetch<ApiToken[]>(`/tokens${suffix}`)
    },
  })

  const createToken = useMutation({
    mutationKey: ['tokens', 'create'],
    mutationFn: () => {
      const scopes = tokenFormState.scopesText
        .split(',')
        .map((scope) => scope.trim())
        .filter(Boolean)

      const body: Record<string, unknown> = {
        name: tokenFormState.name,
        expires_in_days: tokenFormState.expiresInDays,
      }
      if (scopes.length > 0) {
        body.scopes = scopes
      }
      if (tokenFormState.currentPassword.trim()) {
        body.current_password = tokenFormState.currentPassword
      }

      return apiFetch<ApiTokenCreateResponse>('/tokens', {
        method: 'POST',
        body: JSON.stringify(body),
      })
    },
    onSuccess: (data) => {
      dispatchTokenForm({ type: 'createSucceeded', value: data })
      void queryClient.invalidateQueries({ queryKey: ['tokens'] })
    },
    onError: () => {
      dispatchTokenForm({ type: 'createFailed' })
    },
  })

  const revokeToken = useMutation({
    mutationKey: ['tokens', 'revoke'],
    mutationFn: (tokenId: string) => apiFetch(`/tokens/${tokenId}`, { method: 'DELETE' }),
    onMutate: () => {
      setRevocationNotice(null)
    },
    onSuccess: () => {
      setPendingRevocation(null)
      setRevocationNotice({ tone: 'success', message: 'API token revoked.' })
      void queryClient.invalidateQueries({ queryKey: ['tokens'] })
    },
    onError: (error) => {
      setRevocationNotice({
        tone: 'error',
        message: error instanceof ApiError && error.message.trim() ? error.message : 'Failed to revoke token.',
      })
    },
  })

  const onConfirmRevoke = () => {
    if (!pendingRevocation) {
      return
    }

    revokeToken.mutate(pendingRevocation.id)
  }

  const onCreateSubmit = (event: FormEvent) => {
    event.preventDefault()
    dispatchTokenForm({ type: 'createStarted' })
    createToken.mutate()
  }

  const legacyUnscopedTokens = tokensQuery.data?.filter((token) => token.scopes.length === 0) ?? []

  return (
    <div className="grid gap-4 xl:grid-cols-[420px_1fr]">
      <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
        <h2 className="font-display text-xl">Create API Token</h2>
        <p className="mt-1 text-sm text-slate dark:text-slate-300">Token value is only shown once after creation.</p>
        <div className="mt-3 rounded-lg border border-cyan/30 bg-cyan/10 px-3 py-2 text-sm text-slate dark:border-cyan-500/30 dark:bg-cyan-500/10 dark:text-slate-200">
          Leave scopes blank to apply the recommended read-only defaults. Explicit empty scope lists are not allowed.
        </div>
        <div className="mt-3 rounded-lg border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
          Browser sessions must confirm the current account password before creating a durable API token.
        </div>
        <form className="mt-3 space-y-3" onSubmit={onCreateSubmit}>
          <div>
            <label htmlFor="token-name" className="text-sm font-semibold">Name</label>
            <input
              id="token-name"
              className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
              value={tokenFormState.name}
              onChange={(event) => dispatchTokenForm({ type: 'setName', value: event.target.value })}
              required
            />
          </div>
          <div>
            <label htmlFor="token-expiry-days" className="text-sm font-semibold">Expiry (days)</label>
            <input
              id="token-expiry-days"
              className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
              type="number"
              min={1}
              max={3650}
              value={tokenFormState.expiresInDays}
              onChange={(event) => dispatchTokenForm({ type: 'setExpiresInDays', value: Number(event.target.value) })}
              required
            />
          </div>
          <div>
            <label htmlFor="token-scopes" className="text-sm font-semibold">Scopes (comma-separated)</label>
            <input
              id="token-scopes"
              className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
              value={tokenFormState.scopesText}
              onChange={(event) => dispatchTokenForm({ type: 'setScopesText', value: event.target.value })}
              placeholder="read:feeds,write:items"
            />
          </div>
          <div>
            <label htmlFor="token-current-password" className="text-sm font-semibold">Current Password</label>
            <input
              id="token-current-password"
              className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
              type="password"
              autoComplete="current-password"
              value={tokenFormState.currentPassword}
              onChange={(event) => dispatchTokenForm({ type: 'setCurrentPassword', value: event.target.value })}
              required
            />
          </div>
          <button className="rounded bg-ink px-3 py-2 text-white dark:bg-cyan dark:text-[#053c2e]" disabled={createToken.isPending}>
            Generate Token
          </button>
          {createToken.isError && (
            <p role="alert" aria-live="assertive" aria-atomic="true" className="text-sm text-red-600">
              {createToken.error instanceof ApiError ? createToken.error.message : 'Failed to create token.'}
            </p>
          )}
        </form>

        {tokenFormState.createdToken && (
          <div className="mt-4 rounded border border-cyan/40 bg-cyan/10 p-3 text-sm dark:bg-cyan/15">
            <p className="font-semibold">New token</p>
            <p className="mt-1 break-all font-mono text-xs">{tokenFormState.createdToken.token}</p>
            <p className="mt-1 text-xs text-slate dark:text-slate-300">Prefix: {tokenFormState.createdToken.token_prefix}</p>
          </div>
        )}
      </section>

      <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="font-display text-xl">Token Inventory</h2>
          {isAdmin && (
            <div className="space-y-1">
              <label
                htmlFor="token-admin-user-filter"
                className="block text-xs font-semibold uppercase text-slate dark:text-slate-300"
              >
                Filter by User ID
              </label>
              <input
                id="token-admin-user-filter"
                value={adminUserFilter}
                onChange={(event) => setAdminUserFilter(event.target.value)}
                placeholder="Filter by user_id"
                className="w-72 rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
              />
            </div>
          )}
        </div>

        {legacyUnscopedTokens.length > 0 && (
          <div className="mt-3 rounded-lg border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
            {legacyUnscopedTokens.length === 1 ? '1 token has' : `${legacyUnscopedTokens.length} tokens have`} no scopes.
            Scoped API routes now reject unscoped tokens, so rotate these credentials before they break automation.
          </div>
        )}

        <div className="mt-3 space-y-2">
          {revocationNotice && (
            <p
              role={revocationNotice.tone === 'error' ? 'alert' : 'status'}
              aria-live={revocationNotice.tone === 'error' ? 'assertive' : 'polite'}
              aria-atomic="true"
              className={`text-sm ${revocationNotice.tone === 'success' ? 'text-emerald-700 dark:text-emerald-300' : 'text-red-600'}`}
            >
              {revocationNotice.message}
            </p>
          )}
          {tokensQuery.data?.map((token) => (
            <div key={token.id} className="rounded border border-slate/20 p-3 dark:border-cyan-900/40">
              <div className="flex items-center justify-between gap-2">
                <div>
                  <p className="font-semibold">{token.name}</p>
                  <p className="text-xs text-slate dark:text-slate-300">{token.token_prefix}</p>
                </div>
                <button
                  className="rounded border border-slate/30 px-2 py-1 text-xs text-red-600 dark:border-cyan-900/40"
                  onClick={() => setPendingRevocation(token)}
                  disabled={Boolean(token.revoked_at) || revokeToken.isPending || Boolean(pendingRevocation)}
                >
                  {token.revoked_at ? 'Revoked' : 'Revoke'}
                </button>
              </div>
              <p className="mt-1 text-xs text-slate dark:text-slate-300">Scopes: {token.scopes.join(', ') || 'none'}</p>
              {isAdmin && <p className="mt-1 text-xs text-slate dark:text-slate-300">User ID: {token.user_id}</p>}
              <p className="mt-1 text-xs text-slate dark:text-slate-300">
                Expires: {token.expires_at ? formatDateTime(token.expires_at) : 'Never'}
              </p>
              <p className="mt-1 text-xs text-slate dark:text-slate-300">
                Last used: {token.last_used_at ? formatDateTime(token.last_used_at) : 'Never'}
              </p>
            </div>
          ))}

          {tokensQuery.isLoading && <p className="text-sm text-slate dark:text-slate-300">Loading tokens...</p>}
          {tokensQuery.isError && <p className="text-sm text-red-600">Failed to load tokens.</p>}
        </div>
      </section>

      <ConfirmDialog
        open={Boolean(pendingRevocation)}
        title="Revoke API token?"
        description="Revoking a token immediately disables any client that is using it."
        confirmLabel="Revoke token"
        onCancel={() => setPendingRevocation(null)}
        onConfirm={onConfirmRevoke}
        confirmDisabled={revokeToken.isPending}
        isConfirming={revokeToken.isPending}
      >
        {pendingRevocation && (
          <div className="space-y-3">
            <p className="font-semibold text-ink dark:text-white">{pendingRevocation.name}</p>
            <p className="text-xs text-slate dark:text-white/70">Prefix: {pendingRevocation.token_prefix}</p>
            {isAdmin && <p className="text-xs text-slate dark:text-white/70">User ID: {pendingRevocation.user_id}</p>}
            <p className="text-xs text-slate dark:text-white/70">
              Scopes: {pendingRevocation.scopes.join(', ') || 'none'}
            </p>
            <p className="text-xs text-slate dark:text-white/70">
              Expires: {pendingRevocation.expires_at ? formatDateTime(pendingRevocation.expires_at) : 'Never'}
            </p>
            {revocationNotice?.tone === 'error' && (
              <p role="alert" aria-live="assertive" aria-atomic="true" className="text-sm text-red-600">
                {revocationNotice.message}
              </p>
            )}
          </div>
        )}
      </ConfirmDialog>
    </div>
  )
}
