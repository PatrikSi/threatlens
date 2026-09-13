import { afterEach, describe, expect, it, vi } from 'vitest'
import { createSecureRequestId } from './secureRandomId'

afterEach(() => vi.unstubAllGlobals())

describe('cryptographically secure UUID request identifiers', () => {
  it('uses the native UUID API when available', () => {
    const id = crypto.randomUUID()
    vi.stubGlobal('crypto', { randomUUID: () => id })
    expect(createSecureRequestId()).toBe(id)
  })

  it('uses fresh random bytes and valid UUIDv4 version and variant bits on HTTP LAN origins', () => {
    vi.stubGlobal('crypto', { getRandomValues: crypto.getRandomValues.bind(crypto) })
    const ids = Array.from({ length: 100 }, createSecureRequestId)
    expect(new Set(ids).size).toBe(ids.length)
    for (const id of ids) expect(id).toMatch(/^[\da-f]{8}-[\da-f]{4}-4[\da-f]{3}-[89ab][\da-f]{3}-[\da-f]{12}$/)
  })

  it.each([undefined, {}, { getRandomValues: () => { throw new Error('Crypto blocked') } }])(
    'fails with actionable guidance when cryptography is unavailable (%j)', (crypto) => {
      vi.stubGlobal('crypto', crypto)
      expect(createSecureRequestId).toThrow('Secure random generation is unavailable')
      expect(createSecureRequestId).toThrow('use a supported browser')
    },
  )
})
