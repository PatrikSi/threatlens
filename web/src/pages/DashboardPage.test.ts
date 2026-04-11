import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { getDashboardStorageKeys, migrateLegacyDashboardStorage } from './DashboardPage'

function createLocalStorageMock() {
  const store = new Map<string, string>()

  return {
    clear() {
      store.clear()
    },
    getItem(key: string) {
      return store.has(key) ? store.get(key)! : null
    },
    setItem(key: string, value: string) {
      store.set(key, value)
    },
    removeItem(key: string) {
      store.delete(key)
    },
  }
}

let localStorageMock = createLocalStorageMock()

beforeEach(() => {
  localStorageMock = createLocalStorageMock()
  vi.stubGlobal('window', { localStorage: localStorageMock } as Window)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('getDashboardStorageKeys', () => {
  it('scopes persisted dashboard state by user id', () => {
    const aliceKeys = getDashboardStorageKeys('alice')
    const bobKeys = getDashboardStorageKeys('bob')

    expect(aliceKeys.windows).toBe('threatlens.dashboard.windows.v2:alice')
    expect(aliceKeys.windowSeenAt).toBe('threatlens.dashboard.window-seen.v1:alice')
    expect(aliceKeys.lastOpenedAt).toBe('threatlens.user-last-open.v1:alice')

    expect(bobKeys.windows).toBe('threatlens.dashboard.windows.v2:bob')
    expect(bobKeys.windowSeenAt).toBe('threatlens.dashboard.window-seen.v1:bob')
    expect(bobKeys.lastOpenedAt).toBe('threatlens.user-last-open.v1:bob')
  })
})

describe('migrateLegacyDashboardStorage', () => {
  it('copies legacy shared dashboard state into scoped per-user keys', () => {
    const keys = getDashboardStorageKeys('alice')
    localStorageMock.clear()
    localStorageMock.setItem('threatlens.dashboard.windows.v2', JSON.stringify([{ id: 'window-1' }]))
    localStorageMock.setItem('threatlens.dashboard.window-seen.v1', JSON.stringify({ 'window-1': '2026-04-11T00:00:00.000Z' }))
    localStorageMock.setItem('threatlens.user-last-open.v1', '2026-04-11T01:00:00.000Z')

    expect(migrateLegacyDashboardStorage('alice')).toBe(true)
    expect(localStorageMock.getItem(keys.windows)).toBe(JSON.stringify([{ id: 'window-1' }]))
    expect(localStorageMock.getItem(keys.windowSeenAt)).toBe(JSON.stringify({ 'window-1': '2026-04-11T00:00:00.000Z' }))
    expect(localStorageMock.getItem(keys.lastOpenedAt)).toBe('2026-04-11T01:00:00.000Z')
  })

  it('does not overwrite already scoped dashboard state', () => {
    const keys = getDashboardStorageKeys('alice')
    localStorageMock.clear()
    localStorageMock.setItem(keys.windows, JSON.stringify([{ id: 'scoped-window' }]))
    localStorageMock.setItem(keys.windowSeenAt, JSON.stringify({ 'scoped-window': '2026-04-11T02:00:00.000Z' }))
    localStorageMock.setItem(keys.lastOpenedAt, '2026-04-11T03:00:00.000Z')
    localStorageMock.setItem('threatlens.dashboard.windows.v2', JSON.stringify([{ id: 'legacy-window' }]))
    localStorageMock.setItem('threatlens.dashboard.window-seen.v1', JSON.stringify({ 'legacy-window': '2026-04-11T04:00:00.000Z' }))
    localStorageMock.setItem('threatlens.user-last-open.v1', '2026-04-11T05:00:00.000Z')

    expect(migrateLegacyDashboardStorage('alice')).toBe(false)
    expect(localStorageMock.getItem(keys.windows)).toBe(JSON.stringify([{ id: 'scoped-window' }]))
    expect(localStorageMock.getItem(keys.windowSeenAt)).toBe(JSON.stringify({ 'scoped-window': '2026-04-11T02:00:00.000Z' }))
    expect(localStorageMock.getItem(keys.lastOpenedAt)).toBe('2026-04-11T03:00:00.000Z')
  })
})
