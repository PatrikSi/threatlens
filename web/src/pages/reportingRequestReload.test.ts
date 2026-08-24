// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'


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
    const storageKey = Array.from(
      { length: window.sessionStorage.length },
      (_, index) => window.sessionStorage.key(index),
    ).find((key) => key?.startsWith('threatlens.reporting-request.'))

    expect(storageKey).toBeDefined()
    expect(storageKey).not.toContain('confidential')
    expect(window.sessionStorage.getItem(storageKey ?? '')).not.toContain('confidential')
    expect(storageKey).toMatch(/^threatlens\.reporting-request\.v2-[0-9a-f]{64}$/)

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
    expect(window.sessionStorage.length).toBe(2)

    vi.resetModules()
    const reloadedModule = await import('./reportingRequestCoordinator')
    reloadedModule.resetPendingReportingKeys()

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
    const storageKey = Array.from(
      { length: window.sessionStorage.length },
      (_, index) => window.sessionStorage.key(index),
    ).find((key) => key?.startsWith('threatlens.reporting-request.'))
    expect(storageKey).toBeDefined()
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

  it('migrates an unresolved key created by the previous storage format', async () => {
    const module = await import('./reportingRequestCoordinator')
    const scope = module.reportingRequestScope(
      'analyst-1',
      'report:retry',
      '11111111-1111-4111-8111-111111111111',
    )
    const legacyStorageKey = `threatlens.reporting-request.${legacyScopeDigest(scope)}`
    const legacyRequestKey = '22222222-2222-4222-8222-222222222222'
    window.sessionStorage.setItem(
      legacyStorageKey,
      JSON.stringify({ key: legacyRequestKey, createdAt: Date.now() }),
    )

    const recoveredKey = await module.beginPendingReportingRequest(scope)

    expect(recoveredKey).toBe(legacyRequestKey)
    expect(window.sessionStorage.getItem(legacyStorageKey)).toBeNull()
    expect(Array.from(
      { length: window.sessionStorage.length },
      (_, index) => window.sessionStorage.key(index),
    )).toContainEqual(expect.stringMatching(
      /^threatlens\.reporting-request\.v2-[0-9a-f]{64}$/,
    ))
  })

  it('keeps the legacy key when the replacement record cannot be stored', async () => {
    const module = await import('./reportingRequestCoordinator')
    const scope = module.reportingRequestScope(
      'analyst-1',
      'report:retry',
      '11111111-1111-4111-8111-111111111111',
    )
    const legacyStorageKey = `threatlens.reporting-request.${legacyScopeDigest(scope)}`
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
      if (key.includes('.v2-')) throw new DOMException('Storage quota exceeded')
      originalSetItem.call(this, key, value)
    })

    expect(await module.beginPendingReportingRequest(scope)).toBe(legacyRequestKey)
    expect(window.sessionStorage.getItem(legacyStorageKey)).not.toBeNull()
    expect(storageKeys(window.sessionStorage)).not.toContainEqual(
      expect.stringContaining('.v2-'),
    )

    setItem.mockRestore()
    vi.resetModules()
    const reloadedModule = await import('./reportingRequestCoordinator')
    expect(await reloadedModule.beginPendingReportingRequest(scope)).toBe(legacyRequestKey)
    expect(window.sessionStorage.getItem(legacyStorageKey)).toBeNull()
  })

  it('falls back to local storage when session storage is denied', async () => {
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

  it('retains a valid key after the browser clock moves backward', async () => {
    const firstModule = await import('./reportingRequestCoordinator')
    const scope = firstModule.reportingRequestScope(
      'analyst-1',
      'report:create',
      '{"filters":[]}',
    )
    const firstKey = await firstModule.beginPendingReportingRequest(scope)
    const storageKey = storageKeys(window.sessionStorage).find(
      (key) => key.includes('.v2-'),
    )
    expect(storageKey).toBeDefined()
    window.sessionStorage.setItem(
      storageKey ?? '',
      JSON.stringify({ key: firstKey, createdAt: Date.now() + 60 * 60 * 1000 }),
    )

    vi.resetModules()
    const reloadedModule = await import('./reportingRequestCoordinator')
    expect(await reloadedModule.beginPendingReportingRequest(scope)).toBe(firstKey)
    const recovered = JSON.parse(
      window.sessionStorage.getItem(storageKey ?? '') ?? '{}',
    ) as { key?: string; createdAt?: number }
    expect(recovered).toMatchObject({
      key: firstKey,
      createdAt: expect.any(Number),
    })
    expect(recovered.createdAt).toBeLessThanOrEqual(Date.now())
  })

  it('uses the SHA-256 fallback when Web Crypto digest is unavailable', async () => {
    vi.stubGlobal('crypto', {
      getRandomValues: (bytes: Uint8Array) => bytes.fill(7),
      randomUUID: () => '11111111-1111-4111-8111-111111111111',
    })
    const module = await import('./reportingRequestCoordinator')

    await module.beginPendingReportingRequest('fallback-digest-scope')

    expect(storageKeys(window.sessionStorage)).toContainEqual(expect.stringMatching(
      /^threatlens\.reporting-request\.v2-[0-9a-f]{64}$/,
    ))
  })
})


function legacyScopeDigest(scope: string): string {
  let hash = 0xcbf29ce484222325n
  for (let index = 0; index < scope.length; index += 1) {
    hash ^= BigInt(scope.charCodeAt(index))
    hash = BigInt.asUintN(64, hash * 0x100000001b3n)
  }
  return `${scope.length.toString(16)}-${hash.toString(16).padStart(16, '0')}`
}


function storageKeys(storage: Storage): string[] {
  return Array.from(
    { length: storage.length },
    (_, index) => storage.key(index),
  ).filter((key): key is string => key !== null)
}
