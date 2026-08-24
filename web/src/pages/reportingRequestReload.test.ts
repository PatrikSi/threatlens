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
    const firstKey = firstModule.beginPendingReportingRequest(scope)
    const storageKey = window.sessionStorage.key(0)

    expect(storageKey).not.toBeNull()
    expect(storageKey).not.toContain('confidential')
    expect(window.sessionStorage.getItem(storageKey ?? '')).not.toContain('confidential')

    vi.resetModules()
    const reloadedModule = await import('./reportingRequestCoordinator')
    const recoveredKey = reloadedModule.beginPendingReportingRequest(scope)

    expect(recoveredKey).toBe(firstKey)
    reloadedModule.settlePendingReportingRequest(scope, recoveredKey, 'confirmed')

    vi.resetModules()
    const settledModule = await import('./reportingRequestCoordinator')
    expect(settledModule.beginPendingReportingRequest(scope)).not.toBe(firstKey)
  })

  it('clears persisted keys that were created by a previous module instance', async () => {
    const firstModule = await import('./reportingRequestCoordinator')
    firstModule.beginPendingReportingRequest('report:create:before-reload')
    expect(window.sessionStorage.length).toBe(1)

    vi.resetModules()
    const reloadedModule = await import('./reportingRequestCoordinator')
    reloadedModule.resetPendingReportingKeys()

    expect(window.sessionStorage.length).toBe(0)
  })
})
