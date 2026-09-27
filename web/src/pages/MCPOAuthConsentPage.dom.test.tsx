// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import MCPOAuthConsentPage from './MCPOAuthConsentPage'
import { apiFetch } from '../api/client'
import { deferred, intelButton, mountIntel, settle } from './articleIntelligenceTestSupport'
vi.mock('../api/client', async (original) => ({ ...await original<typeof import('../api/client')>(), apiFetch: vi.fn() }))
;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
afterEach(() => { view?.close(); view = undefined; vi.resetAllMocks() })
const query = '?client_id=client-a&redirect_uri=https%3A%2F%2Fclient.example%2Fcallback&state=state-1234567890123'
const preview = { client_name: 'Client A', scopes: ['read:mcp', 'read:items'], resource: 'https://threatlens.example/api/v1/mcp', redirect_uri: 'https://client.example/callback', expires_in: 900 }

it('does not reuse a cached preview when a duplicate-parameter URL is visited', async () => {
  vi.mocked(apiFetch).mockResolvedValue(preview)
  view = await mountIntel(<MCPOAuthConsentPage />, '/' + query)
  expect(view.host.textContent).toContain('Allow read access')
  await act(async () => { await view!.router.navigate('/' + query + '&client_id=client-a') })
  await settle()
  expect(view.host.textContent).toContain('duplicate parameters')
  expect(view.host.querySelector('form')).toBeNull()
})

it('a changed consent request clears password and cannot inherit a late completion', async () => {
  const accepted = deferred<unknown>()
  vi.mocked(apiFetch).mockImplementation((path) => path.endsWith('/authorize') ? accepted.promise : Promise.resolve(preview))
  view = await mountIntel(<MCPOAuthConsentPage />, '/' + query)
  const password = view.host.querySelector('input[type=password]') as HTMLInputElement
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(password, 'temporary-password')
    password.dispatchEvent(new Event('input', { bubbles: true }))
  })
  act(() => intelButton(view!.host, 'Allow read access').click())
  await settle()
  await act(async () => { await view!.router.navigate('/' + query.replace('state-1234567890123', 'state-NEW123456789')) })
  await settle()
  expect((view.host.querySelector('input[type=password]') as HTMLInputElement).value).toBe('')
  // A malformed late callback would display the verification error if the old
  // workspace were allowed to settle into the replacement request.
  await act(async () => accepted.resolve({ redirect_uri: 'not-a-url' }))
  await settle()
  expect(view.host.textContent).not.toContain('callback could not be verified')
  expect(intelButton(view.host, 'Allow read access').disabled).toBe(false)
})
