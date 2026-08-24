// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'


beforeEach(() => {
  window.sessionStorage.clear()
  vi.resetModules()
})

afterEach(() => {
  window.sessionStorage.clear()
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
})


function legacyScopeDigest(scope: string): string {
  let hash = 0xcbf29ce484222325n
  for (let index = 0; index < scope.length; index += 1) {
    hash ^= BigInt(scope.charCodeAt(index))
    hash = BigInt.asUintN(64, hash * 0x100000001b3n)
  }
  return `${scope.length.toString(16)}-${hash.toString(16).padStart(16, '0')}`
}
