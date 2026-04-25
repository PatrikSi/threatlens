import { afterEach, describe, expect, it, vi } from 'vitest'

import { apiFetch } from './client'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('apiFetch', () => {
  it('returns undefined for empty successful responses', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(new Response('', { status: 200 }))),
    )

    await expect(apiFetch('/empty')).resolves.toBeUndefined()
  })

  it('throws for non-JSON successful API responses', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(new Response('accepted', { status: 202, headers: { 'content-type': 'text/plain' } }))),
    )

    await expect(apiFetch('/accepted')).rejects.toMatchObject({
      status: 202,
      path: '/accepted',
      detail: 'accepted',
    })
  })

  it('preserves JSON null successful responses', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(new Response('null', { status: 200, headers: { 'content-type': 'application/json' } }))),
    )

    await expect(apiFetch<null>('/nullable')).resolves.toBeNull()
  })
})
