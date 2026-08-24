// @vitest-environment jsdom

import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

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
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  FakeBroadcastChannel.channels.clear()
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

  it('does not replay a retained auth event when a tab mounts', async () => {
    window.localStorage.setItem(
      'threatlens.auth.sync',
      JSON.stringify({ id: 'already-observed-event', at: Date.now() }),
    )
    const scope = reportingRequestScope(
      'analyst-1',
      'report:retry',
      '11111111-1111-4111-8111-111111111111',
    )
    const firstKey = await beginPendingReportingRequest(scope)
    settlePendingReportingRequest(scope, firstKey, 'ambiguous')

    renderAuthControl()

    expect(await beginPendingReportingRequest(scope)).toBe(firstKey)
    expect(container?.querySelector('button')?.dataset.sessionVersion).toBe('0')
  })

  it('applies an auth event that arrived while an existing tab was unmounted', async () => {
    window.sessionStorage.setItem('threatlens.auth.last-handled', 'older-event')
    window.localStorage.setItem(
      'threatlens.auth.sync',
      JSON.stringify({ id: 'newer-event', at: Date.now() }),
    )
    const scope = reportingRequestScope(
      'analyst-1',
      'report:retry',
      '11111111-1111-4111-8111-111111111111',
    )
    const firstKey = await beginPendingReportingRequest(scope)
    settlePendingReportingRequest(scope, firstKey, 'ambiguous')

    renderAuthControl()

    expect(await beginPendingReportingRequest(scope)).not.toBe(firstKey)
    expect(container?.querySelector('button')?.dataset.sessionVersion).toBe('1')
  })

  it('applies an auth event that arrives between render and listener setup', () => {
    const originalGetItem = Storage.prototype.getItem
    const renderedEvent = JSON.stringify({ id: 'rendered-event', at: Date.now() })
    const latestEvent = JSON.stringify({ id: 'latest-event', at: Date.now() + 1 })
    let syncReads = 0
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(function (
      this: Storage,
      key: string,
    ) {
      if (this === window.localStorage && key === 'threatlens.auth.sync') {
        syncReads += 1
        return syncReads === 1 ? renderedEvent : latestEvent
      }
      return originalGetItem.call(this, key)
    })

    renderAuthControl()

    expect(container?.querySelector('button')?.dataset.sessionVersion).toBe('1')
    expect(window.sessionStorage.getItem('threatlens.auth.last-handled')).toBe(
      'latest-event',
    )
  })

  it('receives auth cleanup over BroadcastChannel when storage is denied', async () => {
    vi.stubGlobal('BroadcastChannel', FakeBroadcastChannel)
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('Storage denied')
    })
    const scope = reportingRequestScope(
      'analyst-1',
      'report:retry',
      '11111111-1111-4111-8111-111111111111',
    )
    const firstKey = await beginPendingReportingRequest(scope)
    settlePendingReportingRequest(scope, firstKey, 'ambiguous')
    renderAuthControl()
    const remoteTab = new FakeBroadcastChannel('threatlens.auth')
    const authEvent = { id: 'remote-auth-event', at: Date.now() }

    await act(async () => {
      remoteTab.postMessage(authEvent)
      window.dispatchEvent(new StorageEvent('storage', {
        key: 'threatlens.auth.sync',
        newValue: JSON.stringify(authEvent),
      }))
      await Promise.resolve()
    })
    const nextKey = await beginPendingReportingRequest(scope)

    expect(nextKey).not.toBe(firstKey)
    expect(container?.querySelector('button')?.dataset.sessionVersion).toBe('1')
    remoteTab.close()
  })

  it('clears reporting state when the observed authenticated identity changes', async () => {
    window.localStorage.setItem('threatlens.auth.identity', 'analyst-1')
    const scope = reportingRequestScope(
      'analyst-1',
      'report:retry',
      '11111111-1111-4111-8111-111111111111',
    )
    const firstKey = await beginPendingReportingRequest(scope)
    settlePendingReportingRequest(scope, firstKey, 'ambiguous')
    renderIdentityControl()

    act(() => {
      container?.querySelector('button')?.click()
    })
    const nextKey = await beginPendingReportingRequest(scope)

    expect(nextKey).not.toBe(firstKey)
    expect(window.localStorage.getItem('threatlens.auth.identity')).toBe('analyst-2')
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

function renderIdentityControl(): void {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => {
    root?.render(
      <AuthProvider>
        <IdentityControl />
      </AuthProvider>,
    )
  })
}


function LogoutControl() {
  const { markLoggedOut, sessionVersion } = useAuth()
  return (
    <button
      type="button"
      data-session-version={sessionVersion}
      onClick={markLoggedOut}
    >
      Log out
    </button>
  )
}

function IdentityControl() {
  const { observeAuthenticatedIdentity } = useAuth()
  return (
    <button
      type="button"
      onClick={() => observeAuthenticatedIdentity('analyst-2')}
    >
      Observe identity
    </button>
  )
}


class FakeBroadcastChannel {
  static channels = new Set<FakeBroadcastChannel>()

  readonly name: string
  private listeners = new Set<(event: { data: unknown }) => void>()

  constructor(name: string) {
    this.name = name
    FakeBroadcastChannel.channels.add(this)
  }

  addEventListener(
    _type: string,
    listener: (event: { data: unknown }) => void,
  ): void {
    this.listeners.add(listener)
  }

  removeEventListener(
    _type: string,
    listener: (event: { data: unknown }) => void,
  ): void {
    this.listeners.delete(listener)
  }

  postMessage(data: unknown = { id: 'remote-auth-event', at: Date.now() }): void {
    for (const channel of FakeBroadcastChannel.channels) {
      if (channel !== this && channel.name === this.name) {
        for (const listener of channel.listeners) listener({ data })
      }
    }
  }

  close(): void {
    this.listeners.clear()
    FakeBroadcastChannel.channels.delete(this)
  }
}
