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
    const compatibilityRecord = JSON.parse(window.sessionStorage.getItem(
      `${requestPrefix}${legacyScopeDigest(scope)}`,
    ) ?? '{}') as Record<string, unknown>
    expect(compatibilityRecord.key).toBe(firstKey)
    expect(compatibilityRecord.createdAt).toEqual(expect.any(Number))
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

  it('clears keys created by a previous module instance', async () => {
    const firstModule = await import('./reportingRequestCoordinator')
    await firstModule.beginPendingReportingRequest('report:create:before-reload')
    expect(window.sessionStorage.length).toBe(2)

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
    window.sessionStorage.setItem(
      `${requestPrefix}${legacyScopeDigest(scope)}`,
      JSON.stringify({ key: 'invalid\nheader' }),
    )

    vi.resetModules()
    const reloadedModule = await import('./reportingRequestCoordinator')
    const replacementKey = await reloadedModule.beginPendingReportingRequest(scope)

    expect(replacementKey).not.toBe(firstKey)
    expect(replacementKey).toMatch(/^[A-Za-z0-9._~:-]+$/)
  })

  it('adopts a v1 key and retains its alias until definitive settlement', async () => {
    const module = await import('./reportingRequestCoordinator')
    const scope = module.reportingRequestScope('analyst-1', 'report:retry', 'report-1')
    const predecessorKey = `${requestPrefix}${legacyScopeDigest(scope)}`
    const requestKey = '22222222-2222-4222-8222-222222222222'
    window.sessionStorage.setItem(
      predecessorKey,
      JSON.stringify({ key: requestKey, createdAt: Date.now() }),
    )

    expect(await module.beginPendingReportingRequest(scope)).toBe(requestKey)
    expect(window.sessionStorage.getItem(predecessorKey)).not.toBeNull()
    expect(currentRequestStorageKey()).toBeDefined()

    module.settlePendingReportingRequest(scope, requestKey, 'confirmed')
    expect(window.sessionStorage.getItem(predecessorKey)).toBeNull()
    expect(currentRequestStorageKey()).toBeUndefined()
  })

  it('writes the previous v3 key even when its migration marker exists', async () => {
    const module = await import('./reportingRequestCoordinator')
    const salt = '10'.repeat(32)
    const scope = module.reportingRequestScope('analyst-1', 'report:create', 'rollback')
    const digest = shaDigest(`${salt}\0${scope}`)
    const v3Key = `${requestPrefix}v3-${digest}`
    window.localStorage.setItem(saltKey, salt)
    window.localStorage.setItem(`${requestPrefix}migration-${digest}`, '1')

    const requestKey = await module.beginPendingReportingRequest(scope)
    const rollbackRecord = JSON.parse(
      window.sessionStorage.getItem(v3Key) ?? '{}',
    ) as Record<string, unknown>

    expect(rollbackRecord.key).toBe(requestKey)
    expect(rollbackRecord.createdAt).toEqual(expect.any(Number))
    expect(rollbackRecord.supersedes).toEqual(expect.arrayContaining([
      `${requestPrefix}v4-${shaDigest(scope)}`,
      `${requestPrefix}${legacyScopeDigest(scope)}`,
    ]))

    window.sessionStorage.removeItem(v3Key)
    for (const supersededKey of rollbackRecord.supersedes as string[]) {
      window.sessionStorage.removeItem(supersededKey)
      window.localStorage.removeItem(supersededKey)
    }
    vi.resetModules()
    const reupgradedModule = await import('./reportingRequestCoordinator')
    expect(await reupgradedModule.beginPendingReportingRequest(scope)).not.toBe(
      requestKey,
    )
  })

  it('adopts both released v2 digest formats without changing the request key', async () => {
    const module = await import('./reportingRequestCoordinator')
    const salt = '11'.repeat(32)
    window.sessionStorage.setItem(saltKey, salt)

    for (const [identity, digest] of [
      ['sha-v2', (bytes: Uint8Array) => bytesToHex(sha256(bytes))],
      ['fallback-v2', legacyFallbackScopeDigest],
    ] as const) {
      const scope = module.reportingRequestScope('analyst-1', 'report:create', identity)
      const bytes = new TextEncoder().encode(`${salt}\0${scope}`)
      const predecessorKey = `${requestPrefix}v2-${digest(bytes)}`
      const requestKey = `${identity}-request-key`
      window.sessionStorage.setItem(
        predecessorKey,
        JSON.stringify({ key: requestKey, createdAt: Date.now() }),
      )

      expect(await module.beginPendingReportingRequest(scope)).toBe(requestKey)
    }
  })

  it('fails closed when predecessor formats contain conflicting unresolved keys', async () => {
    const module = await import('./reportingRequestCoordinator')
    const scope = module.reportingRequestScope('analyst-1', 'report:create', 'conflict')
    const salt = '22'.repeat(32)
    const v2Key = `${requestPrefix}v2-${shaDigest(`${salt}\0${scope}`)}`
    const v1Key = `${requestPrefix}${legacyScopeDigest(scope)}`
    window.sessionStorage.setItem(saltKey, salt)
    window.sessionStorage.setItem(
      v2Key,
      JSON.stringify({ key: 'first-request-key', createdAt: Date.now() }),
    )
    window.localStorage.setItem(
      v1Key,
      JSON.stringify({ key: 'second-request-key', createdAt: Date.now() }),
    )

    await expect(module.beginPendingReportingRequest(scope)).rejects.toThrow(
      'conflicting unresolved report request keys',
    )
  })

  it('does not let a settled v4 key suppress a different unresolved alias', async () => {
    const module = await import('./reportingRequestCoordinator')
    const scope = module.reportingRequestScope('analyst-1', 'report:create', 'settled-conflict')
    window.sessionStorage.setItem(
      `${requestPrefix}v4-${shaDigest(scope)}`,
      JSON.stringify({ key: 'settled-request-key', createdAt: Date.now(), settled: true }),
    )
    window.localStorage.setItem(
      `${requestPrefix}${legacyScopeDigest(scope)}`,
      JSON.stringify({ key: 'different-unresolved-key', createdAt: Date.now() }),
    )

    await expect(module.beginPendingReportingRequest(scope)).rejects.toThrow(
      'conflicting unresolved report request keys',
    )
  })

  it('fails before dispatch preparation when legacy storage cannot be inspected', async () => {
    const originalGetItem = Storage.prototype.getItem
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(function (
      this: Storage,
      key: string,
    ) {
      if (this === window.localStorage) throw new DOMException('Storage unreadable')
      return originalGetItem.call(this, key)
    })
    const module = await import('./reportingRequestCoordinator')

    await expect(module.beginPendingReportingRequest('unreadable-storage')).rejects.toThrow(
      'no request was sent',
    )
  })

  it('fails when storage becomes unreadable after the initial compatibility read', async () => {
    const originalGetItem = Storage.prototype.getItem
    let localReads = 0
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(function (
      this: Storage,
      key: string,
    ) {
      if (this === window.localStorage && localReads++ > 0) {
        throw new DOMException('Storage became unreadable')
      }
      return originalGetItem.call(this, key)
    })
    const module = await import('./reportingRequestCoordinator')

    await expect(module.beginPendingReportingRequest('read-race')).rejects.toThrow(
      'no request was sent',
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

  it('attempts every rollback alias and reports partial persistence as unsafe', async () => {
    const salt = '33'.repeat(32)
    const scope = 'partial-rollback-write'
    const blockedKey = `${requestPrefix}v3-${shaDigest(`${salt}\0${scope}`)}`
    window.sessionStorage.setItem(saltKey, salt)
    const originalSetItem = Storage.prototype.setItem
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(function (
      this: Storage,
      key: string,
      value: string,
    ) {
      if (this === window.sessionStorage && key === blockedKey) {
        throw new DOMException('v3 write failed')
      }
      originalSetItem.call(this, key, value)
    })
    const module = await import('./reportingRequestCoordinator')

    const lease = await module.beginPendingReportingRequestLease(scope)
    const settlement = module.settlePendingReportingRequest(
      scope,
      lease.key,
      'ambiguous',
    )

    expect(lease.durable).toBe(false)
    expect(settlement.durable).toBe(false)
    const v1Key = `${requestPrefix}${legacyScopeDigest(scope)}`
    const v1Record = JSON.parse(
      window.sessionStorage.getItem(v1Key) ?? '{}',
    ) as Record<string, unknown>
    expect(v1Record.supersedes).not.toContain(blockedKey)

    vi.restoreAllMocks()
    const migratedSupersedes = [
      ...(v1Record.supersedes as string[]),
      v1Key,
    ]
    window.sessionStorage.setItem(blockedKey, JSON.stringify({
      ...v1Record,
      supersedes: migratedSupersedes,
    }))
    window.sessionStorage.removeItem(v1Key)
    for (const supersededKey of migratedSupersedes) {
      window.sessionStorage.removeItem(supersededKey)
      window.localStorage.removeItem(supersededKey)
    }
    expect(window.sessionStorage.getItem(blockedKey)).not.toBeNull()
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

  it('does not discard the terminal fence while a rollback alias remains', async () => {
    const module = await import('./reportingRequestCoordinator')
    const scope = module.reportingRequestScope('analyst-1', 'report:create', 'alias-fence')
    const firstKey = await module.beginPendingReportingRequest(scope)
    const blockedAlias = `${requestPrefix}${legacyScopeDigest(scope)}`
    const originalRemoveItem = Storage.prototype.removeItem
    const removeItem = vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(function (
      this: Storage,
      key: string,
    ) {
      if (this === window.sessionStorage && key === blockedAlias) {
        throw new DOMException('Alias removal failed')
      }
      originalRemoveItem.call(this, key)
    })

    expect(module.settlePendingReportingRequest(
      scope,
      firstKey,
      'confirmed',
    ).durable).toBe(false)
    expect(window.sessionStorage.getItem(blockedAlias)).not.toBeNull()
    await expect(module.beginPendingReportingRequest(scope)).rejects.toThrow(
      'could not finish settling',
    )

    removeItem.mockRestore()
    expect(await module.beginPendingReportingRequest(scope)).not.toBe(firstKey)
  })

})


function currentRequestStorageKey(): string | undefined {
  return storageKeys(window.sessionStorage).find((key) => key.includes('.v4-'))
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
