import { useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useMutation, useQuery } from '@tanstack/react-query'
import { apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { accessibleQueryData } from '../api/queryData'

interface ConsentPreview { client_name: string; scopes: string[]; resource: string; redirect_uri: string; expires_in: number }

export default function MCPOAuthConsentPage() {
  const [params] = useSearchParams()
  const request = useMemo(() => Object.fromEntries(params), [params])
  const duplicate = [...params.keys()].some((key) => params.getAll(key).length !== 1)
  if (duplicate) return <section className="mx-auto max-w-2xl space-y-4 p-6">
    <h1 className="text-2xl font-semibold">Authorize an MCP client</h1>
    <p role="alert">The authorization request contains duplicate parameters. Restart authorization from your client.</p>
  </section>
  return <ConsentRequest key={JSON.stringify(request)} request={request} />
}

function ConsentRequest({ request }: { request: Record<string, string> }) {
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  const [redirectError, setRedirectError] = useState<string | null>(null)
  const [password, setPassword] = useState('')
  const [mfa, setMfa] = useState('')
  const preview = useQuery({ queryKey: ['mcp-consent', request], retry: false,
    queryFn: ({ signal }) => apiFetch<ConsentPreview>('/mcp/oauth/consent-preview', { method: 'POST', signal, body: JSON.stringify(request) }) })
  const value = accessibleQueryData(preview)
  const consent = useMutation({ mutationFn: (approve: boolean) => apiFetch<{ redirect_uri: string }>('/mcp/oauth/authorize', {
    method: 'POST', body: JSON.stringify({ ...request, approve, current_password: password || null, mfa_code: mfa || null }),
  }), onSuccess: (response) => {
    if (!mounted.current) return
    setPassword(''); setMfa('')
    // The server checks exact registered callback, resource, scopes and PKCE.
    // Independently retain the destination from this exact consent request.
    try {
      const redirect = new URL(response.redirect_uri)
      const expected = new URL(request.redirect_uri)
      if (!value || redirect.origin + redirect.pathname !== expected.origin + expected.pathname) throw new Error('Callback changed')
      window.location.assign(response.redirect_uri)
    } catch {
      setRedirectError('The approved callback could not be verified. Restart authorization from your MCP client.')
    }
  } })
  return <section className="mx-auto max-w-2xl space-y-4 p-6">
    <h1 className="text-2xl font-semibold">Authorize an MCP client</h1>
    <p>A connected client can read ThreatLens evidence on your behalf. Retrieved text may contain untrusted instructions. Only approve a client you recognize.</p>
    {preview.isPending && <p role="status">Checking the client and requested access…</p>}
    {preview.error && <p role="alert">{resolveApiErrorMessage(preview.error, 'This authorization request is unavailable. Restart it from the client.')}</p>}
    {value && <form onSubmit={(event) => { event.preventDefault(); consent.mutate(true) }}>
      <fieldset disabled={consent.isPending || preview.isError} className="space-y-4">
        <legend className="text-lg font-semibold">{value.client_name}</legend>
        <p className="break-all">Resource: {value.resource}</p>
        <p className="break-all">Callback: {value.redirect_uri}</p>
        <ul className="list-inside list-disc">{value.scopes.map((scope) => <li key={scope}>{scope}</li>)}</ul>
        <p>Access expires after 15 minutes and remains limited by your current permissions. You can revoke it from API tokens. The client cannot change records or launch hunts.</p>
        <label className="block">Current password (local accounts)<input type="password" autoComplete="current-password" value={password} maxLength={256} onChange={(e) => setPassword(e.target.value)} className="block w-full rounded border p-2 text-black" /></label>
        <label className="block">MFA code (if enabled)<input autoComplete="one-time-code" value={mfa} maxLength={20} onChange={(e) => setMfa(e.target.value)} className="block w-full rounded border p-2 text-black" /></label>
        <p className="text-sm">SSO accounts require recent identity-provider authentication and any configured MFA assurance.</p>
        <div className="flex gap-3"><button type="submit" className="rounded border px-4 py-2">{consent.isPending ? 'Submitting…' : 'Allow read access'}</button>
          <button type="button" className="rounded border px-4 py-2" onClick={() => consent.mutate(false)}>Deny</button></div>
      </fieldset>
    </form>}
    {redirectError && <p role="alert">{redirectError}</p>}
    {consent.error && <p role="alert">{resolveApiErrorMessage(consent.error, 'Authorization failed. Check your authentication and retry.')}</p>}
  </section>
}
