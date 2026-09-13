// @vitest-environment jsdom
import { MutationObserver, QueryClient, useQueryClient } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'

import { apiDownload, apiFetch } from '../api/client'
import { captureSessionLease, invalidateSession, SessionChangedError } from '../api/sessionLifecycle'
import { AuthProvider, useAuth } from './AuthContext'
import { SessionQueryProvider } from './SessionQueryProvider'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
let root: Root | undefined
afterEach(() => {
  act(() => root?.unmount())
  root = undefined
  invalidateSession()
  vi.unstubAllGlobals()
  localStorage.clear()
  sessionStorage.clear()
  document.body.innerHTML = ''
})

it('gives the next identity a fresh cache and fences old mutation callbacks after unmount', async () => {
  let current!: QueryClient
  let changeSession!: () => void
  function Probe() { current = useQueryClient(); changeSession = useAuth().markAuthenticated; return null }
  const container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  act(() => root!.render(<AuthProvider><SessionQueryProvider><Probe /></SessionQueryProvider></AuthProvider>))
  const previous = current
  let resolve!: (value: string) => void
  const result = new Promise<string>((done) => { resolve = done })
  const mutation = new MutationObserver(previous, {
    mutationFn: () => result,
    onSuccess: (value) => { previous.setQueryData(['private-occurrence'], value) },
  })
  const pending = mutation.mutate()
  const rejected = expect(pending).rejects.toBeInstanceOf(SessionChangedError)
  await act(async () => { await Promise.resolve(); changeSession() })
  expect(current).not.toBe(previous)
  await act(async () => { resolve('old account evidence'); await rejected })
  expect(previous.getQueryData(['private-occurrence'])).toBeUndefined()
  expect(current.getQueryData(['private-occurrence'])).toBeUndefined()
})

it.each(['json', 'download'])('rejects late %s results even when transport ignores session abort', async (kind) => {
  let resolve!: (response: Response) => void
  const fetch = vi.fn(() => new Promise<Response>((done) => { resolve = done }))
  vi.stubGlobal('fetch', fetch)
  const pending = kind === 'json' ? apiFetch('/private') : apiDownload('/private')
  const rejected = expect(pending).rejects.toBeInstanceOf(SessionChangedError)
  invalidateSession()
  expect((fetch.mock.calls[0] as unknown as [string, RequestInit])[1].signal?.aborted).toBe(true)
  resolve(new Response(JSON.stringify({ secret: 'old account' }), { status: 200 }))
  await rejected
})

it('fences continuation of a multi-request operation across account changes', () => {
  const lease = captureSessionLease()
  invalidateSession()
  expect(() => lease.assertCurrent()).toThrow(SessionChangedError)
  expect(() => captureSessionLease().assertCurrent()).not.toThrow()
})
