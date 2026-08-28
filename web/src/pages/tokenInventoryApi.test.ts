import { describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api/client'
import type { ApiToken } from '../types/api'
import {
  loadTokenInventory,
  normalizeTokenInventory,
} from './tokenInventoryApi'

const TOKENS: ApiToken[] = [
  {
    id: 'token-1',
    user_id: 'user-1',
    name: 'First token',
    token_prefix: 'tl_first',
    scopes: [],
    last_used_at: null,
    expires_at: null,
    revoked_at: null,
    created_at: '2026-08-01T00:00:00Z',
  },
  {
    id: 'token-2',
    user_id: 'user-1',
    name: 'Second token',
    token_prefix: 'tl_second',
    scopes: ['read:feeds'],
    last_used_at: null,
    expires_at: null,
    revoked_at: null,
    created_at: '2026-08-02T00:00:00Z',
  },
]

describe('token inventory compatibility', () => {
  it('normalizes and locally pages a legacy token list', () => {
    expect(normalizeTokenInventory(TOKENS, 2, 1)).toEqual({
      tokens: [TOKENS[1]],
      total: 2,
      unscoped_total: 1,
      page: 2,
      page_size: 1,
    })
  })

  it('falls back to the legacy endpoint only when inventory is unavailable', async () => {
    const fetcher = vi
      .fn()
      .mockRejectedValueOnce(
        new ApiError('Not found', 404, '/tokens/inventory'),
      )
      .mockResolvedValueOnce(TOKENS)
    const params = new URLSearchParams({ page: '1', page_size: '25' })

    const result = await loadTokenInventory(
      params,
      1,
      25,
      'user-1',
      fetcher,
    )

    expect(fetcher).toHaveBeenNthCalledWith(
      1,
      '/tokens/inventory?page=1&page_size=25',
    )
    expect(fetcher).toHaveBeenNthCalledWith(2, '/tokens?user_id=user-1')
    expect(result.tokens).toEqual(TOKENS)
  })

  it('does not hide authorization failures behind a legacy fallback', async () => {
    const forbidden = new ApiError('Forbidden', 403, '/tokens/inventory')
    const fetcher = vi.fn().mockRejectedValue(forbidden)

    await expect(
      loadTokenInventory(new URLSearchParams(), 1, 25, '', fetcher),
    ).rejects.toBe(forbidden)
    expect(fetcher).toHaveBeenCalledTimes(1)
  })

  it('rejects malformed paginated responses with a useful protocol error', () => {
    expect(() =>
      normalizeTokenInventory(
        {
          tokens: TOKENS,
          total: -1,
          unscoped_total: 1,
          page: 1,
          page_size: 25,
        },
        1,
        25,
      ),
    ).toThrow('invalid token inventory response')
  })
})
