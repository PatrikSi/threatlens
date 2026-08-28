// @vitest-environment jsdom

import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const currentUserMocks = vi.hoisted(() => ({
  options: null as Record<string, unknown> | null,
  observeIdentity: vi.fn(),
}))

vi.mock('@tanstack/react-query', () => ({
  useQuery: (options: Record<string, unknown>) => {
    currentUserMocks.options = options
    return {
      data: undefined,
      error: null,
      isError: false,
      isLoading: false,
    }
  },
}))

vi.mock('../components/AuthContext', () => ({
  useAuth: () => ({
    observeAuthenticatedIdentity: currentUserMocks.observeIdentity,
    sessionVersion: 3,
  }),
}))

import { ApiError } from '../api/client'
import { useCurrentUser } from './useCurrentUser'

let root: Root | null = null
let container: HTMLDivElement | null = null

function Probe() {
  useCurrentUser()
  return null
}

afterEach(() => {
  act(() => root?.unmount())
  root = null
  container?.remove()
  container = null
  currentUserMocks.options = null
  currentUserMocks.observeIdentity.mockReset()
})

describe('useCurrentUser', () => {
  it('revalidates role surfaces and does not retry terminal authorization failures', () => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    act(() => root?.render(<Probe />))

    expect(currentUserMocks.options).toMatchObject({
      queryKey: ['auth', 'me', 3],
      refetchInterval: 30_000,
      refetchIntervalInBackground: false,
      refetchOnMount: 'always',
      refetchOnWindowFocus: true,
    })
    const retry = currentUserMocks.options?.retry as (
      count: number,
      error: unknown,
    ) => boolean
    expect(retry(0, new ApiError('Forbidden', 403, '/auth/me'))).toBe(false)
    expect(retry(0, new Error('Temporary failure'))).toBe(true)
    expect(retry(1, new Error('Temporary failure'))).toBe(false)
  })
})
