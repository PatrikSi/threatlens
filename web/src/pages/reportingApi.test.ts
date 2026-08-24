// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/client')>()),
  apiFetch: vi.fn(),
}))

import { ApiError, ApiTransportError, apiFetch } from '../api/client'
import { idempotentReportingFetch } from './reportingApi'
import {
  beginPendingReportingRequest,
  resetPendingReportingKeys,
  settlePendingReportingRequest,
} from './reportingRequestCoordinator'


afterEach(() => {
  vi.mocked(apiFetch).mockReset()
  resetPendingReportingKeys()
  window.localStorage.clear()
  window.sessionStorage.clear()
  vi.restoreAllMocks()
})

describe('idempotentReportingFetch', () => {
  it('reuses a key after an ambiguous transport failure', async () => {
    const keys: string[] = []
    vi.mocked(apiFetch)
      .mockImplementationOnce((_path, options) => {
        keys.push(new Headers(options?.headers).get('Idempotency-Key') ?? '')
        return Promise.reject(new ApiTransportError('network down', '/reports', 'network'))
      })
      .mockImplementationOnce((_path, options) => {
        keys.push(new Headers(options?.headers).get('Idempotency-Key') ?? '')
        return Promise.resolve({ id: 'report-1' })
      })

    await expect(
      idempotentReportingFetch('/reports', 'scope-1', { method: 'POST' }, passthrough),
    ).rejects.toThrow('network down')
    await expect(idempotentReportingFetch('/reports', 'scope-1', { method: 'POST' }, passthrough)).resolves.toEqual({
      id: 'report-1',
    })

    expect(keys[0]).not.toBe('')
    expect(keys[1]).toBe(keys[0])
  })

  it('reuses a key when a successful response cannot be validated', async () => {
    const keys: string[] = []
    vi.mocked(apiFetch).mockImplementation((_path, options) => {
      keys.push(new Headers(options?.headers).get('Idempotency-Key') ?? '')
      return Promise.resolve(undefined)
    })
    const rejectInvalid = () => {
      throw new ApiError('invalid confirmation', 202, '/reports', null, {
        code: 'invalid_response',
        retryable: true,
      })
    }

    await expect(idempotentReportingFetch('/reports', 'scope-2', { method: 'POST' }, rejectInvalid)).rejects.toThrow(
      'invalid confirmation',
    )
    await expect(idempotentReportingFetch('/reports', 'scope-2', { method: 'POST' }, rejectInvalid)).rejects.toThrow(
      'invalid confirmation',
    )

    expect(keys[1]).toBe(keys[0])
  })

  it('clears a key after a definitive rejection', async () => {
    const keys: string[] = []
    vi.mocked(apiFetch).mockImplementation((_path, options) => {
      keys.push(new Headers(options?.headers).get('Idempotency-Key') ?? '')
      return keys.length === 1
        ? Promise.reject(new ApiError('invalid request', 422, '/reports'))
        : Promise.resolve({ id: 'report-1' })
    })

    await expect(idempotentReportingFetch('/reports', 'scope-3', { method: 'POST' }, passthrough)).rejects.toThrow(
      'invalid request',
    )
    await expect(idempotentReportingFetch('/reports', 'scope-3', { method: 'POST' }, passthrough)).resolves.toEqual({
      id: 'report-1',
    })

    expect(keys[1]).not.toBe(keys[0])
  })

  it('does not lose a key when overlapping responses settle success then ambiguity', async () => {
    const keys: string[] = []
    let resolveFirst: ((value: unknown) => void) | undefined
    let rejectSecond: ((error: Error) => void) | undefined
    vi.mocked(apiFetch)
      .mockImplementationOnce((_path, options) => {
        keys.push(new Headers(options?.headers).get('Idempotency-Key') ?? '')
        return new Promise((resolve) => { resolveFirst = resolve })
      })
      .mockImplementationOnce((_path, options) => {
        keys.push(new Headers(options?.headers).get('Idempotency-Key') ?? '')
        return new Promise((_resolve, reject) => { rejectSecond = reject })
      })
      .mockImplementationOnce((_path, options) => {
        keys.push(new Headers(options?.headers).get('Idempotency-Key') ?? '')
        return Promise.resolve({ id: 'report-1' })
      })

    const first = idempotentReportingFetch('/reports', 'overlap-scope', { method: 'POST' }, passthrough)
    const overlapping = idempotentReportingFetch('/reports', 'overlap-scope', { method: 'POST' }, passthrough)
    await vi.waitFor(() => {
      expect(resolveFirst).toBeTypeOf('function')
      expect(rejectSecond).toBeTypeOf('function')
    })
    resolveFirst?.({ id: 'report-1' })
    await expect(first).resolves.toEqual({ id: 'report-1' })
    rejectSecond?.(new ApiTransportError('network down', '/reports', 'network'))
    await expect(overlapping).rejects.toThrow('network down')
    await expect(
      idempotentReportingFetch('/reports', 'overlap-scope', { method: 'POST' }, passthrough),
    ).resolves.toEqual({ id: 'report-1' })

    expect(keys[0]).not.toBe('')
    expect(keys[1]).toBe(keys[0])
    expect(keys[2]).toBe(keys[0])
  })

  it('reports storage loss that occurs after an ambiguous dispatch', async () => {
    let storageIsDenied = false
    const originalSetItem = Storage.prototype.setItem
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(function (
      this: Storage,
      key: string,
      value: string,
    ) {
      if (storageIsDenied) throw new DOMException('Storage revoked')
      originalSetItem.call(this, key, value)
    })
    vi.mocked(apiFetch).mockImplementation(() => {
      storageIsDenied = true
      return Promise.reject(new ApiTransportError('network down', '/reports', 'network'))
    })

    await expect(idempotentReportingFetch(
      '/reports',
      'storage-revoked-scope',
      { method: 'POST' },
      passthrough,
    )).rejects.toThrow('could not retain the shared request key')
  })

  it('does not dispatch a prepared request after authentication changes', async () => {
    const scope = 'auth-change-scope'
    const key = await beginPendingReportingRequest(scope)
    settlePendingReportingRequest(scope, key, 'ambiguous')

    const request = idempotentReportingFetch(
      '/reports',
      scope,
      { method: 'POST' },
      passthrough,
    )
    resetPendingReportingKeys()

    await expect(request).rejects.toThrow('Authentication changed')
    expect(apiFetch).not.toHaveBeenCalled()
  })

  it('does not dispatch when a shared request key cannot be persisted', async () => {
    const localStorage = window.localStorage
    const originalSetItem = Storage.prototype.setItem
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(function (
      this: Storage,
      key: string,
      value: string,
    ) {
      if (this === localStorage) throw new DOMException('Local storage denied')
      originalSetItem.call(this, key, value)
    })

    await expect(idempotentReportingFetch(
      '/reports',
      'storage-denied-scope',
      { method: 'POST' },
      passthrough,
    )).rejects.toThrow('no request was sent')
    expect(apiFetch).not.toHaveBeenCalled()
  })

  it('does not return an old-session response after authentication changes', async () => {
    let resolveRequest: ((value: unknown) => void) | undefined
    const validate = vi.fn(passthrough)
    vi.mocked(apiFetch).mockImplementation(() => (
      new Promise((resolve) => { resolveRequest = resolve })
    ))
    const request = idempotentReportingFetch(
      '/reports',
      'active-auth-change-scope',
      { method: 'POST' },
      validate,
    )
    await vi.waitFor(() => expect(resolveRequest).toBeTypeOf('function'))

    resetPendingReportingKeys()
    resolveRequest?.({ id: 'old-user-report' })

    await expect(request).rejects.toThrow('Authentication changed')
    expect(validate).not.toHaveBeenCalled()
  })
})

function passthrough<T>(value: T): T {
  return value
}
