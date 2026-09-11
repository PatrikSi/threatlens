import { describe, expect, it, vi } from 'vitest'
import {
  createProviderDraft,
  createProviderRequest,
  createProviderRequestId,
  validateProviderDraft,
} from './aiProviderDraft'

describe('provider write boundaries', () => {
  const valid = { ...createProviderDraft(), name: 'Local', base_url: 'http://localhost:11434/v1', model: 'local-model' }
  it('omits an unchanged credential and requires explicit removal', () => {
    expect(createProviderRequest(valid)).not.toHaveProperty('api_key')
    expect(createProviderRequest(valid)).not.toHaveProperty('clear_api_key')
    expect(createProviderRequest({ ...valid, clear_api_key: true })).toHaveProperty('clear_api_key', true)
  })
  it.each([
    'file:///etc/passwd',
    'https://secret:password@example.test/v1',
    'https://example.test/v1?key=secret',
    'https://example.test/v1#fragment',
  ])('rejects unsafe endpoint input %s', (base_url) => {
    expect(validateProviderDraft({ ...valid, base_url }).base_url).toBeTruthy()
  })
  it('validates required fields, limits and contradictory credential changes', () => {
    expect(validateProviderDraft(valid)).toEqual({})
    expect(
      validateProviderDraft({
        ...valid,
        name: '',
        model: '',
        request_timeout_seconds: '999',
        api_key: 'replacement',
        clear_api_key: true,
      }),
    ).toMatchObject({
      name: expect.any(String),
      model: expect.any(String),
      request_timeout_seconds: expect.any(String),
      api_key: expect.any(String),
    })
  })
  it('creates a valid UUID request key on HTTP origins without randomUUID', () => {
    vi.stubGlobal('crypto', { getRandomValues: crypto.getRandomValues.bind(crypto) })
    try {
      const id = createProviderRequestId()
      expect(id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
      expect(createProviderRequestId()).not.toBe(id)
    } finally {
      vi.unstubAllGlobals()
    }
  })
})
