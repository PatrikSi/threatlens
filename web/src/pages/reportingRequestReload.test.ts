// @vitest-environment jsdom

import { sha256 } from '@noble/hashes/sha2.js'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'


const requestPrefix = 'threatlens.reporting-request.'

beforeEach(() => {
  window.sessionStorage.clear()
  window.localStorage.clear()
  vi.resetModules()
})

afterEach(() => {
  window.sessionStorage.clear()
  window.localStorage.clear()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  vi.resetModules()
})

describe('reporting request reload persistence', () => {
  it('recovers an unresolved key in the same tab without storing the request body', async () => {
    const firstModule = await import('./reportingRequestCoordinator')
    const scope = firstModule.reportingRequestScope(
      'analyst-1',
      'report:create',
      JSON.stringify({ prompt: 'confidential internal instructions' }),
    )
    const firstKey = await firstModule.beginPendingReportingRequest(scope)
    const storageKey = currentRequestStorageKey()

    expect(storageKey).toMatch(/^threatlens\.reporting-request\.v4-[0-9a-f]{64}$/)
    expect(storageKey).not.toContain('confidential')
    expect(window.sessionStorage.getItem(storageKey ?? '')).not.toContain('confidential')
    expect(window.sessionStorage.length).toBe(1)
    expect(window.localStorage.length).toBe(0)

    vi.resetModules()
    const reloadedModule = await import('./reportingRequestCoordinator')
    const recoveredKey = await reloadedModule.beginPendingReportingRequest(scope)
    expect(recoveredKey).toBe(firstKey)
    reloadedModule.settlePendingReportingRequest(scope, recoveredKey, 'confirmed')

    vi.resetModules()
    const settledModule = await import('./reportingRequestCoordinator')
    expect(await settledModule.beginPendingReportingRequest(scope)).not.toBe(firstKey)
  })

  it('clears persisted request records created by previous module instances', async () => {
    const firstModule = await import('./reportingRequestCoordinator')
    await firstModule.beginPendingReportingRequest('report:create:before-reload')
    window.localStorage.setItem(`${requestPrefix}obsolete-draft`, '{}')

    vi.resetModules()
    const reloadedModule = await import('./reportingRequestCoordinator')
    reloadedModule.resetPendingReportingKeys()

    expect(window.sessionStorage.length).toBe(0)
    expect(window.localStorage.length).toBe(0)
  })

  it('replaces a malformed recovered idempotency key', async () => {
    const firstModule = await import('./reportingRequestCoordinator')
    const scope = firstModule.reportingRequestScope(
      'analyst-1',
      'report:create',
      '{"filters":[]}',
    )
    const firstKey = await firstModule.beginPendingReportingRequest(scope)
    const storageKey = currentRequestStorageKey()
    window.sessionStorage.setItem(
      storageKey ?? '',
      JSON.stringify({ key: 'invalid\nheader', createdAt: Date.now() }),
    )

    vi.resetModules()
    const reloadedModule = await import('./reportingRequestCoordinator')
    const replacementKey = await reloadedModule.beginPendingReportingRequest(scope)

    expect(replacementKey).not.toBe(firstKey)
    expect(replacementKey).toMatch(/^[A-Za-z0-9._~:-]+$/)
  })

  it('replaces malformed persisted JSON values', async () => {
    const scope = 'malformed-json-value'
    const storageKey = `${requestPrefix}v4-${shaDigest(scope)}`
    window.sessionStorage.setItem(storageKey, 'null')
    const module = await import('./reportingRequestCoordinator')

    await expect(module.beginPendingReportingRequest(scope)).resolves.toMatch(
      /^[A-Za-z0-9._~:-]+$/,
    )
  })

  it('fails closed when malformed request state cannot be removed', async () => {
    const scope = 'malformed-unremovable'
    const storageKey = `${requestPrefix}v4-${shaDigest(scope)}`
    window.sessionStorage.setItem(storageKey, '{not-json')
    const originalRemoveItem = Storage.prototype.removeItem
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(function (
      this: Storage,
      key: string,
    ) {
      if (this === window.sessionStorage && key === storageKey) {
        throw new DOMException('Storage removal denied')
      }
      originalRemoveItem.call(this, key)
    })
    const module = await import('./reportingRequestCoordinator')

    await expect(module.beginPendingReportingRequest(scope)).rejects.toThrow(
      'invalid report request state',
    )
  })

  it('fails before dispatch preparation when session storage cannot be inspected', async () => {
    const originalGetItem = Storage.prototype.getItem
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(function (
      this: Storage,
      key: string,
    ) {
      if (this === window.sessionStorage) throw new DOMException('Storage unreadable')
      return originalGetItem.call(this, key)
    })
    const module = await import('./reportingRequestCoordinator')

    await expect(module.beginPendingReportingRequest('unreadable-storage')).rejects.toThrow(
      'no request was sent',
    )
  })

  it('does not throw from authentication cleanup when a storage getter is denied', async () => {
    const module = await import('./reportingRequestCoordinator')
    const storageGetter = vi.spyOn(window, 'sessionStorage', 'get').mockImplementation(() => {
      throw new DOMException('Storage getter denied')
    })

    expect(() => module.resetPendingReportingKeys()).not.toThrow()
    storageGetter.mockRestore()
    await expect(module.beginPendingReportingRequest('after-storage-reset')).resolves.toMatch(
      /^[A-Za-z0-9._~:-]+$/,
    )
  })

  it('keeps a volatile key usable when browser storage is unavailable', async () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('Storage denied')
    })
    const module = await import('./reportingRequestCoordinator')
    const scope = module.reportingRequestScope('analyst-1', 'report:create', 'volatile')
    const firstLease = await module.beginPendingReportingRequestLease(scope)

    expect(firstLease.durable).toBe(false)
    expect(module.settlePendingReportingRequest(
      scope,
      firstLease.key,
      'ambiguous',
    ).durable).toBe(false)
    expect(await module.beginPendingReportingRequest(scope)).toBe(firstLease.key)
  })

  it('writes a settlement tombstone when deletion is interrupted', async () => {
    const firstModule = await import('./reportingRequestCoordinator')
    const scope = firstModule.reportingRequestScope(
      'analyst-1',
      'report:create',
      'interrupted-delete',
    )
    const firstKey = await firstModule.beginPendingReportingRequest(scope)
    const storageKey = currentRequestStorageKey()
    const originalRemoveItem = Storage.prototype.removeItem
    const removeItem = vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(function (
      this: Storage,
      key: string,
    ) {
      if (this === window.sessionStorage && key === storageKey) {
        throw new DOMException('Deletion interrupted')
      }
      originalRemoveItem.call(this, key)
    })

    expect(firstModule.settlePendingReportingRequest(
      scope,
      firstKey,
      'confirmed',
    ).durable).toBe(true)
    expect(JSON.parse(window.sessionStorage.getItem(storageKey ?? '') ?? '{}')).toMatchObject({
      key: firstKey,
      settled: true,
    })

    removeItem.mockRestore()
    vi.resetModules()
    const reloadedModule = await import('./reportingRequestCoordinator')
    expect(await reloadedModule.beginPendingReportingRequest(scope)).not.toBe(firstKey)
  })

  it('persists a definitive overlap before every caller settles', async () => {
    const firstModule = await import('./reportingRequestCoordinator')
    const scope = firstModule.reportingRequestScope(
      'analyst-1',
      'report:retry',
      'overlapping-confirmation',
    )
    const firstKey = await firstModule.beginPendingReportingRequest(scope)
    await firstModule.beginPendingReportingRequest(scope)

    firstModule.settlePendingReportingRequest(scope, firstKey, 'confirmed')
    expect(JSON.parse(
      window.sessionStorage.getItem(currentRequestStorageKey() ?? '') ?? '{}',
    )).toMatchObject({ key: firstKey, settled: true })

    vi.resetModules()
    const reloadedModule = await import('./reportingRequestCoordinator')
    expect(await reloadedModule.beginPendingReportingRequest(scope)).not.toBe(firstKey)
  })

  it('keeps an in-memory terminal fence until failed cleanup can be retried', async () => {
    const module = await import('./reportingRequestCoordinator')
    const scope = module.reportingRequestScope('analyst-1', 'report:create', 'terminal-fence')
    const firstKey = await module.beginPendingReportingRequest(scope)
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('Storage write failed')
    })
    const removeItem = vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
      throw new DOMException('Storage removal failed')
    })

    expect(module.settlePendingReportingRequest(scope, firstKey, 'confirmed').durable).toBe(false)
    await expect(module.beginPendingReportingRequest(scope)).rejects.toThrow(
      'could not finish settling',
    )

    setItem.mockRestore()
    removeItem.mockRestore()
    expect(await module.beginPendingReportingRequest(scope)).not.toBe(firstKey)
  })
})


function currentRequestStorageKey(): string | undefined {
  return storageKeys(window.sessionStorage).find((key) => key.includes('.v4-'))
}


function shaDigest(value: string): string {
  return Array.from(
    sha256(new TextEncoder().encode(value)),
    (byte) => byte.toString(16).padStart(2, '0'),
  ).join('')
}


function storageKeys(storage: Storage): string[] {
  return Array.from(
    { length: storage.length },
    (_, index) => storage.key(index),
  ).filter((key): key is string => key !== null)
}
