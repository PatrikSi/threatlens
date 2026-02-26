import { FormEvent, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { ApiToken, ApiTokenCreateResponse } from '../types/api'

export function TokensPage() {
  const queryClient = useQueryClient()
  const meQuery = useCurrentUser()
  const [name, setName] = useState('')
  const [expiresInDays, setExpiresInDays] = useState(90)
  const [scopesText, setScopesText] = useState('')
  const [adminUserFilter, setAdminUserFilter] = useState('')
  const [createdToken, setCreatedToken] = useState<ApiTokenCreateResponse | null>(null)

  const tokenQueryKey = ['tokens', adminUserFilter]
  const tokensQuery = useQuery({
    queryKey: tokenQueryKey,
    queryFn: () => {
      const params = new URLSearchParams()
      if (meQuery.data?.role === 'admin' && adminUserFilter.trim()) {
        params.set('user_id', adminUserFilter.trim())
      }
      const suffix = params.toString() ? `?${params.toString()}` : ''
      return apiFetch<ApiToken[]>(`/tokens${suffix}`)
    },
  })

  const createToken = useMutation({
    mutationFn: () => {
      const scopes = scopesText
        .split(',')
        .map((scope) => scope.trim())
        .filter(Boolean)

      return apiFetch<ApiTokenCreateResponse>('/tokens', {
        method: 'POST',
        body: JSON.stringify({
          name,
          expires_in_days: expiresInDays,
          scopes,
        }),
      })
    },
    onSuccess: (data) => {
      setCreatedToken(data)
      setName('')
      setScopesText('')
      setExpiresInDays(90)
      void queryClient.invalidateQueries({ queryKey: ['tokens'] })
    },
  })

  const revokeToken = useMutation({
    mutationFn: (tokenId: string) => apiFetch(`/tokens/${tokenId}`, { method: 'DELETE' }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['tokens'] }),
  })

  const onCreateSubmit = (event: FormEvent) => {
    event.preventDefault()
    createToken.mutate()
  }

  return (
    <div className="grid gap-4 xl:grid-cols-[420px_1fr]">
      <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-slate-700 dark:bg-slate-900/70">
        <h2 className="font-display text-xl">Create API Token</h2>
        <p className="mt-1 text-sm text-slate dark:text-slate-300">Token value is only shown once after creation.</p>
        <form className="mt-3 space-y-3" onSubmit={onCreateSubmit}>
          <div>
            <label className="text-sm font-semibold">Name</label>
            <input
              className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-slate-600 dark:bg-slate-800"
              value={name}
              onChange={(event) => setName(event.target.value)}
              required
            />
          </div>
          <div>
            <label className="text-sm font-semibold">Expiry (days)</label>
            <input
              className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-slate-600 dark:bg-slate-800"
              type="number"
              min={1}
              max={3650}
              value={expiresInDays}
              onChange={(event) => setExpiresInDays(Number(event.target.value))}
              required
            />
          </div>
          <div>
            <label className="text-sm font-semibold">Scopes (comma-separated)</label>
            <input
              className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-slate-600 dark:bg-slate-800"
              value={scopesText}
              onChange={(event) => setScopesText(event.target.value)}
              placeholder="read:feeds,write:items"
            />
          </div>
          <button className="rounded bg-ink px-3 py-2 text-white dark:bg-cyan dark:text-ink" disabled={createToken.isPending}>
            Generate Token
          </button>
          {createToken.isError && <p className="text-sm text-red-600">Failed to create token.</p>}
        </form>

        {createdToken && (
          <div className="mt-4 rounded border border-cyan/40 bg-cyan/10 p-3 text-sm dark:bg-cyan/15">
            <p className="font-semibold">New token</p>
            <p className="mt-1 break-all font-mono text-xs">{createdToken.token}</p>
            <p className="mt-1 text-xs text-slate dark:text-slate-300">Prefix: {createdToken.token_prefix}</p>
          </div>
        )}
      </section>

      <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-slate-700 dark:bg-slate-900/70">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="font-display text-xl">Token Inventory</h2>
          {meQuery.data?.role === 'admin' && (
            <input
              value={adminUserFilter}
              onChange={(event) => setAdminUserFilter(event.target.value)}
              placeholder="Filter by user_id"
              className="w-72 rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-800"
            />
          )}
        </div>

        <div className="mt-3 space-y-2">
          {tokensQuery.data?.map((token) => (
            <div key={token.id} className="rounded border border-slate/20 p-3 dark:border-slate-700">
              <div className="flex items-center justify-between gap-2">
                <div>
                  <p className="font-semibold">{token.name}</p>
                  <p className="text-xs text-slate dark:text-slate-300">{token.token_prefix}</p>
                </div>
                <button
                  className="rounded border border-slate/30 px-2 py-1 text-xs text-red-600 dark:border-slate-600"
                  onClick={() => revokeToken.mutate(token.id)}
                  disabled={Boolean(token.revoked_at) || revokeToken.isPending}
                >
                  {token.revoked_at ? 'Revoked' : 'Revoke'}
                </button>
              </div>
              <p className="mt-1 text-xs text-slate dark:text-slate-300">Scopes: {token.scopes.join(', ') || 'none'}</p>
              <p className="mt-1 text-xs text-slate dark:text-slate-300">
                Expires: {token.expires_at ? new Date(token.expires_at).toLocaleString() : 'Never'}
              </p>
              <p className="mt-1 text-xs text-slate dark:text-slate-300">
                Last used: {token.last_used_at ? new Date(token.last_used_at).toLocaleString() : 'Never'}
              </p>
            </div>
          ))}

          {tokensQuery.isLoading && <p className="text-sm text-slate">Loading tokens...</p>}
          {tokensQuery.isError && <p className="text-sm text-red-600">Failed to load tokens.</p>}
        </div>
      </section>
    </div>
  )
}
