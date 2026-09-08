// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { useQueryClient } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'
import { AuthProvider } from './AuthContext'
import { SessionQueryProvider } from './SessionQueryProvider'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { apiFetch } from '../api/client'
import type { CurrentUser } from '../types/api'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...(await original<typeof import('../api/client')>()), apiFetch: vi.fn() }))
afterEach(() => { window.localStorage.clear(); window.sessionStorage.clear(); vi.clearAllMocks() })
it('rotates the session cache before publishing a silently changed server identity', async () => {
  window.localStorage.clear(); window.sessionStorage.clear()
  let serverIdentity = 'analyst-a'
  vi.mocked(apiFetch).mockImplementation(() => Promise.resolve({ id: serverIdentity } as CurrentUser) as never)
  const observed: Array<{ identity: string | undefined; privateData: unknown }> = []
  let query: ReturnType<typeof useCurrentUser>
  let client: ReturnType<typeof useQueryClient>
  function Probe() {
    client = useQueryClient()
    query = useCurrentUser()
    observed.push({ identity: query.data?.id, privateData: client.getQueryData(['private-work']) })
    return null
  }
  const root = createRoot(document.createElement('div'))
  act(() => root.render(<AuthProvider><SessionQueryProvider><Probe /></SessionQueryProvider></AuthProvider>))
  try {
    await act(async () => { await vi.waitFor(() => expect(query!.data?.id).toBe('analyst-a')) })
    act(() => client!.setQueryData(['private-work'], 'Private analyst A work'))
    serverIdentity = 'analyst-b'
    await act(async () => { await query!.refetch() })
    await act(async () => { await vi.waitFor(() => expect(query!.data?.id).toBe('analyst-b')) })
    expect(observed.some((state) => state.identity === 'analyst-b' && state.privateData === 'Private analyst A work')).toBe(false)
    expect(client!.getQueryData(['private-work'])).toBeUndefined()
  } finally { act(() => root.unmount()); client!.clear() }
})
