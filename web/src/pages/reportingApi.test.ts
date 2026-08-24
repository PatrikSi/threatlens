import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/client')>()),
  apiFetch: vi.fn(),
}))

import { ApiError, ApiTransportError, apiFetch } from '../api/client'
import { idempotentReportingFetch } from './reportingApi'
import { resetPendingReportingKeys } from './reportingRequestCoordinator'


afterEach(() => {
  vi.mocked(apiFetch).mockReset()
  resetPendingReportingKeys()
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

    await expect(idempotentReportingFetch('/reports', 'scope-1', { method: 'POST' }, passthrough)).rejects.toThrow(
      'network down',
    )
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
})

function passthrough<T>(value: T): T {
  return value
}
