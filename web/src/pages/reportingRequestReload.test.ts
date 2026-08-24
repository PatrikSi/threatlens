// @vitest-environment jsdom

import { sha256 } from '@noble/hashes/sha2.js'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'


const requestPrefix = 'threatlens.reporting-request.'
const saltKey = 'threatlens.reporting-scope-salt'

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
  it('recovers an unresolved key without storing the request body', async () => {
    const firstModule = await import('./reportingRequestCoordinator')
    const sensitiveIdentity = JSON.stringify({
      prompt: 'confidential internal reporting instructions',
    })
    const scope = firstModule.reportingRequestScope(
      'analyst-1',
      'report:create',
      sensitiveIdentity,
    )
    const firstKey = await firstModule.beginPendingReportingRequest(scope)
    const storageKey = currentRequestStorageKey()

    expect(storageKey).toBeDefined()
    expect(storageKey).not.toContain('confidential')
    expect(window.localStorage.getItem(storageKey ?? '')).not.toContain('confidential')
    expect(storageKey).toMatch(/^threatlens\.reporting-request\.v3-[0-9a-f]{64}$/)

    vi.resetModules()
    const reloadedModule = await import('./reportingRequestCoordinator')
    const recoveredKey = await reloadedModule.beginPendingReportingRequest(scope)

    expect(recoveredKey).toBe(firstKey)
    reloadedModule.settlePendingReportingRequest(scope, recoveredKey, 'confirmed')

    vi.resetModules()
    const settledModule = await import('./reportingRequestCoordinator')
    expect(await settledModule.beginPendingReportingRequest(scope)).not.toBe(firstKey)
  })

  it('clears persisted keys that were created by a previous module instance', async () => {
    const firstModule = await import('./reportingRequestCoordinator')
    await firstModule.beginPendingReportingRequest('report:create:before-reload')
    expect(window.localStorage.length).toBe(2)

    vi.resetModules()
    const reloadedModule = await import('./reportingRequestCoordinator')
    reloadedModule.resetPendingReportingKeys()

    expect(window.localStorage.length).toBe(0)
    expect(window.sessionStorage.length).toBe(0)
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
    expect(storageKey).toBeDefined()
    window.localStorage.setItem(
      storageKey ?? '',
      JSON.stringify({ key: 'invalid\nheader', createdAt: Date.now() }),
    )

    vi.resetModules()
    const reloadedModule = await import('./reportingRequestCoordinator')
    const replacementKey = await reloadedModule.beginPendingReportingRequest(scope)

    expect(replacementKey).not.toBe(firstKey)
    expect(replacementKey).toMatch(/^[A-Za-z0-9._~:-]+$/)
  })

  it('migrates an unresolved key created by the original storage format', async () => {
    const module = await import('./reportingRequestCoordinator')
    const scope = module.reportingRequestScope(
      'analyst-1',
      'report:retry',
      '11111111-1111-4111-8111-111111111111',
    )
    const legacyStorageKey = `${requestPrefix}${legacyScopeDigest(scope)}`
    const legacyRequestKey = '22222222-2222-4222-8222-222222222222'
    window.sessionStorage.setItem(
      legacyStorageKey,
      JSON.stringify({ key: legacyRequestKey, createdAt: Date.now() }),
    )

    const recoveredKey = await module.beginPendingReportingRequest(scope)

    expect(recoveredKey).toBe(legacyRequestKey)
    expect(window.sessionStorage.getItem(legacyStorageKey)).toBeNull()
    expect(currentRequestStorageKey()).toBeDefined()
    expect(migrationMarkerStorageKey()).toBeDefined()
  })

  it('migrates a v2 SHA-256 key without changing its idempotency value', async () => {
    const module = await import('./reportingRequestCoordinator')
    const scope = module.reportingRequestScope('analyst-1', 'report:create', 'sha-v2')
    const salt = '11'.repeat(32)
    const predecessorKey = `${requestPrefix}v2-${shaDigest(`${salt}\0${scope}`)}`
    const requestKey = '33333333-3333-4333-8333-333333333333'
    window.sessionStorage.setItem(saltKey, salt)
    window.sessionStorage.setItem(
      predecessorKey,
      JSON.stringify({ key: requestKey, createdAt: Date.now() }),
    )

    expect(await module.beginPendingReportingRequest(scope)).toBe(requestKey)
    expect(window.sessionStorage.getItem(predecessorKey)).toBeNull()
    expect(currentRequestStorageKey()).toBeDefined()
  })

  it('migrates a v2 restricted-browser fallback key', async () => {
    const module = await import('./reportingRequestCoordinator')
    const scope = module.reportingRequestScope('analyst-1', 'report:create', 'fallback-v2')
    const salt = '22'.repeat(32)
    const bytes = new TextEncoder().encode(`${salt}\0${scope}`)
    const predecessorKey = `${requestPrefix}v2-${legacyFallbackScopeDigest(bytes)}`
    const requestKey = '44444444-4444-4444-8444-444444444444'
    window.sessionStorage.setItem(saltKey, salt)
    window.sessionStorage.setItem(
      predecessorKey,
      JSON.stringify({ key: requestKey, createdAt: Date.now() }),
    )

    expect(await module.beginPendingReportingRequest(scope)).toBe(requestKey)
    expect(window.sessionStorage.getItem(predecessorKey)).toBeNull()
    expect(currentRequestStorageKey()).toBeDefined()
  })

  it('keeps the predecessor when the replacement record cannot be shared', async () => {
    const module = await import('./reportingRequestCoordinator')
    const scope = module.reportingRequestScope(
      'analyst-1',
      'report:retry',
      '11111111-1111-4111-8111-111111111111',
    )
    const legacyStorageKey = `${requestPrefix}${legacyScopeDigest(scope)}`
    const legacyRequestKey = '22222222-2222-4222-8222-222222222222'
    window.sessionStorage.setItem(
      legacyStorageKey,
      JSON.stringify({ key: legacyRequestKey, createdAt: Date.now() }),
    )
    const originalSetItem = Storage.prototype.setItem
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(function (
      this: Storage,
      key: string,
      value: string,
    ) {
      if (this === window.localStorage && key.includes('.v3-')) {
        throw new DOMException('Storage quota exceeded')
      }
      originalSetItem.call(this, key, value)
    })

    expect(await module.beginPendingReportingRequest(scope)).toBe(legacyRequestKey)
    expect(window.sessionStorage.getItem(legacyStorageKey)).not.toBeNull()
    expect(currentRequestStorageKey()).toBeUndefined()

    setItem.mockRestore()
    vi.resetModules()
    const reloadedModule = await import('./reportingRequestCoordinator')
    expect(await reloadedModule.beginPendingReportingRequest(scope)).toBe(legacyRequestKey)
    expect(window.sessionStorage.getItem(legacyStorageKey)).toBeNull()
  })

  it('uses a migration marker when predecessor deletion is interrupted', async () => {
    const module = await import('./reportingRequestCoordinator')
    const scope = module.reportingRequestScope('analyst-1', 'report:retry', 'interrupted')
    const legacyStorageKey = `${requestPrefix}${legacyScopeDigest(scope)}`
    const legacyRequestKey = '55555555-5555-4555-8555-555555555555'
    window.localStorage.setItem(
      legacyStorageKey,
      JSON.stringify({ key: legacyRequestKey, createdAt: Date.now() }),
    )
    const originalRemoveItem = Storage.prototype.removeItem
    const removeItem = vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(function (
      this: Storage,
      key: string,
    ) {
      if (key === legacyStorageKey) throw new DOMException('Deletion interrupted')
      originalRemoveItem.call(this, key)
    })

    const migratedKey = await module.beginPendingReportingRequest(scope)
    expect(migratedKey).toBe(legacyRequestKey)
    expect(window.localStorage.getItem(legacyStorageKey)).not.toBeNull()
    expect(migrationMarkerStorageKey()).toBeDefined()
    module.settlePendingReportingRequest(scope, migratedKey, 'confirmed')

    removeItem.mockRestore()
    vi.resetModules()
    const reloadedModule = await import('./reportingRequestCoordinator')
    expect(await reloadedModule.beginPendingReportingRequest(scope)).not.toBe(legacyRequestKey)
  })

  it('uses shared local storage when session storage is denied', async () => {
    const sessionStorage = window.sessionStorage
    const originalSetItem = Storage.prototype.setItem
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(function (
      this: Storage,
      key: string,
      value: string,
    ) {
      if (this === sessionStorage) throw new DOMException('Session storage denied')
      originalSetItem.call(this, key, value)
    })
    const firstModule = await import('./reportingRequestCoordinator')
    const scope = firstModule.reportingRequestScope(
      'analyst-1',
      'report:create',
      '{"filters":[]}',
    )
    const firstKey = await firstModule.beginPendingReportingRequest(scope)

    expect(window.sessionStorage.length).toBe(0)
    expect(window.localStorage.length).toBe(2)
    vi.resetModules()
    const reloadedModule = await import('./reportingRequestCoordinator')

    expect(await reloadedModule.beginPendingReportingRequest(scope)).toBe(firstKey)
    reloadedModule.resetPendingReportingKeys()
    expect(window.localStorage.length).toBe(0)
  })

  it('preserves a blocked session key until local storage is restored', async () => {
    const localStorage = window.localStorage
    const originalSetItem = Storage.prototype.setItem
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(function (
      this: Storage,
      key: string,
      value: string,
    ) {
      if (this === localStorage) throw new DOMException('Local storage denied')
      originalSetItem.call(this, key, value)
    })
    const firstModule = await import('./reportingRequestCoordinator')
    const scope = firstModule.reportingRequestScope(
      'analyst-1',
      'report:create',
      'storage-restored',
    )
    const firstLease = await firstModule.beginPendingReportingRequestLease(scope)
    expect(firstLease.shared).toBe(false)
    firstModule.settlePendingReportingRequest(scope, firstLease.key, 'blocked')
    expect(storageKeys(window.sessionStorage)).toContainEqual(expect.stringContaining('.v3-'))

    setItem.mockRestore()
    vi.resetModules()
    const reloadedModule = await import('./reportingRequestCoordinator')
    const recoveredLease = await reloadedModule.beginPendingReportingRequestLease(scope)

    expect(recoveredLease.key).toBe(firstLease.key)
    expect(recoveredLease).toMatchObject({ durable: true, shared: true })
  })

  it('does not expire an unresolved key after a forward clock adjustment', async () => {
    const firstModule = await import('./reportingRequestCoordinator')
    const scope = firstModule.reportingRequestScope(
      'analyst-1',
      'report:create',
      '{"filters":[]}',
    )
    const firstKey = await firstModule.beginPendingReportingRequest(scope)
    const originalNow = Date.now()
    vi.spyOn(Date, 'now').mockReturnValue(originalNow + 30 * 24 * 60 * 60 * 1000)

    vi.resetModules()
    const reloadedModule = await import('./reportingRequestCoordinator')
    expect(await reloadedModule.beginPendingReportingRequest(scope)).toBe(firstKey)
  })

  it('uses SHA-256 storage keys without Web Crypto digest support', async () => {
    vi.stubGlobal('crypto', {
      getRandomValues: (bytes: Uint8Array) => bytes.fill(7),
      randomUUID: () => '11111111-1111-4111-8111-111111111111',
    })
    const module = await import('./reportingRequestCoordinator')

    await module.beginPendingReportingRequest('fallback-digest-scope')

    expect(storageKeys(window.localStorage)).toContainEqual(expect.stringMatching(
      /^threatlens\.reporting-request\.v3-[0-9a-f]{64}$/,
    ))
  })

  it('gives independent tabs the same key for simultaneous requests', async () => {
    const firstModule = await import('./reportingRequestCoordinator')
    vi.resetModules()
    const secondModule = await import('./reportingRequestCoordinator')
    const scope = firstModule.reportingRequestScope(
      'analyst-1',
      'report:create',
      'simultaneous',
    )

    const [firstKey, secondKey] = await Promise.all([
      firstModule.beginPendingReportingRequest(scope),
      secondModule.beginPendingReportingRequest(scope),
    ])

    expect(secondKey).toBe(firstKey)
    expect(storageKeys(window.localStorage)).not.toContainEqual(
      expect.stringContaining('reporting-storage-lock'),
    )
  })
})


function currentRequestStorageKey(): string | undefined {
  return storageKeys(window.localStorage).find((key) => key.includes('.v3-'))
}


function migrationMarkerStorageKey(): string | undefined {
  return storageKeys(window.localStorage).find((key) => key.includes('.migration-'))
}


function shaDigest(value: string): string {
  return bytesToHex(sha256(new TextEncoder().encode(value)))
}


function legacyScopeDigest(scope: string): string {
  let hash = 0xcbf29ce484222325n
  for (let index = 0; index < scope.length; index += 1) {
    hash ^= BigInt(scope.charCodeAt(index))
    hash = BigInt.asUintN(64, hash * 0x100000001b3n)
  }
  return `${scope.length.toString(16)}-${hash.toString(16).padStart(16, '0')}`
}


function legacyFallbackScopeDigest(bytes: Uint8Array): string {
  const seeds = [
    0xcbf29ce484222325n,
    0x84222325cbf29cen,
    0x9e3779b97f4a7c15n,
    0x6a09e667f3bcc909n,
  ]
  return seeds.map((seed, lane) => {
    let hash = seed
    for (let index = 0; index < bytes.length; index += 1) {
      hash ^= BigInt(bytes[index] ^ ((index + lane * 67) & 0xff))
      hash = BigInt.asUintN(64, hash * 0x100000001b3n)
      hash ^= hash >> 32n
    }
    return BigInt.asUintN(64, hash).toString(16).padStart(16, '0')
  }).join('')
}


function bytesToHex(bytes: Uint8Array): string {
  return Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('')
}


function storageKeys(storage: Storage): string[] {
  return Array.from(
    { length: storage.length },
    (_, index) => storage.key(index),
  ).filter((key): key is string => key !== null)
}
