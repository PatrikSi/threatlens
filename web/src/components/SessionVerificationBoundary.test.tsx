// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, useState } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'

import { apiFetch } from '../api/client'
import { invalidateSession, SessionVerificationError } from '../api/sessionLifecycle'
import { AuthProvider } from './AuthContext'
import { PermissionRoute } from './PermissionRoute'
import { ProtectedRoute } from './ProtectedRoute'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
let root: Root | undefined
let client: QueryClient | undefined
afterEach(() => {
  act(() => root?.unmount())
  client?.clear()
  invalidateSession()
  vi.unstubAllGlobals()
  localStorage.clear()
  sessionStorage.clear()
  document.body.innerHTML = ''
})

it('preserves nested route drafts during a real session-query failure, blocks writes, and recovers', async () => {
  const { container, refetch } = mountDraft()
  const original = container.querySelector('textarea')!
  await act(async () => { (container.querySelector('button') as HTMLButtonElement).click() })
  expect(original.value).toBe('unsaved investigation notes')
  vi.stubGlobal('fetch', vi.fn(async () => new Response('Service unavailable', { status: 503 })))
  await refetch()
  expect(container.querySelector('textarea')).toBe(original)
  expect(original.value).toBe('unsaved investigation notes')
  expect(container.hasAttribute('inert')).toBe(true)
  expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(1)
  await expect(apiFetch('/private', { method: 'POST' })).rejects.toBeInstanceOf(SessionVerificationError)
  vi.stubGlobal('fetch', vi.fn(async () => Response.json(user)))
  await refetch()
  expect(container.querySelector('textarea')).toBe(original)
  expect(original.value).toBe('unsaved investigation notes')
  expect(container.hasAttribute('inert')).toBe(false)
  expect(document.querySelector('[role="dialog"]')).toBeNull()
})

it.each([401, 403])('closes draft content after confirmed status %s', async (status) => {
  const { container, refetch } = mountDraft()
  vi.stubGlobal('fetch', vi.fn(async () => new Response('Access denied', { status })))
  await refetch()
  expect(container.querySelector('textarea')).toBeNull()
  expect(container.textContent).toContain(status === 401 ? 'Sign in' : 'Access blocked')
})

const user = { id: 'test-user', role: 'admin', access: { permissions: ['feeds:write'] } }
function Draft() {
  const [value, setValue] = useState('')
  return <><textarea value={value} onChange={(event) => setValue(event.target.value)} /><button onClick={() => setValue('unsaved investigation notes')}>Edit</button></>
}
function mountDraft() {
  client = new QueryClient({ defaultOptions: { queries: { retry: false, retryDelay: 0 } } })
  client.setQueryData(['auth', 'me', 0], user)
  // Mount revalidation remains pending until explicit refetch cancels it.
  vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => new Promise((_resolve, reject) => {
    options.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })
  })))
  const container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  act(() => root!.render(<AuthProvider><QueryClientProvider client={client!}><MemoryRouter><Routes>
    <Route path="/login" element={<p>Sign in</p>} />
    <Route path="*" element={<ProtectedRoute><PermissionRoute permissions={['feeds:write']}><Draft /></PermissionRoute></ProtectedRoute>} />
  </Routes></MemoryRouter></QueryClientProvider></AuthProvider>))
  return {
    container,
    refetch: async () => { await act(async () => { await client!.refetchQueries({ queryKey: ['auth', 'me', 0] }); await new Promise((resolve) => setTimeout(resolve, 20)) }) },
  }
}
