// @vitest-environment jsdom

import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it } from 'vitest'

import {
  beginPendingReportingRequest,
  reportingRequestScope,
  resetPendingReportingKeys,
  settlePendingReportingRequest,
} from '../pages/reportingRequestCoordinator'
import { AuthProvider, useAuth } from './AuthContext'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

let root: Root | null = null
let container: HTMLDivElement | null = null

afterEach(() => {
  act(() => root?.unmount())
  root = null
  container?.remove()
  container = null
  resetPendingReportingKeys()
  window.localStorage.clear()
  window.sessionStorage.clear()
})

describe('AuthProvider session cleanup', () => {
  it('clears unresolved reporting identities when authentication changes', async () => {
    const scope = reportingRequestScope(
      'analyst-1',
      'report:retry',
      '11111111-1111-4111-8111-111111111111',
    )
    const firstKey = await beginPendingReportingRequest(scope)
    settlePendingReportingRequest(scope, firstKey, 'ambiguous')
    renderAuthControl()

    act(() => {
      container?.querySelector('button')?.click()
    })
    const nextKey = await beginPendingReportingRequest(scope)

    expect(nextKey).not.toBe(firstKey)
  })
})


function renderAuthControl(): void {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => {
    root?.render(
      <AuthProvider>
        <LogoutControl />
      </AuthProvider>,
    )
  })
}


function LogoutControl() {
  const { markLoggedOut } = useAuth()
  return <button type="button" onClick={markLoggedOut}>Log out</button>
}
