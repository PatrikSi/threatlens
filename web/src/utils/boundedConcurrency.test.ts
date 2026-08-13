import { describe, expect, it } from 'vitest'

import { mapSettledWithConcurrency } from './boundedConcurrency'

describe('mapSettledWithConcurrency', () => {
  it('preserves result order while limiting active operations', async () => {
    let active = 0
    let maxActive = 0

    const results = await mapSettledWithConcurrency([0, 1, 2, 3, 4, 5, 6], 3, async (value) => {
      active += 1
      maxActive = Math.max(maxActive, active)
      await new Promise((resolve) => setTimeout(resolve, value % 2 === 0 ? 2 : 1))
      active -= 1
      if (value === 4) {
        throw new Error('failed-four')
      }
      return value * 10
    })

    expect(maxActive).toBe(3)
    expect(results).toEqual([
      { status: 'fulfilled', value: 0 },
      { status: 'fulfilled', value: 10 },
      { status: 'fulfilled', value: 20 },
      { status: 'fulfilled', value: 30 },
      { status: 'rejected', reason: expect.objectContaining({ message: 'failed-four' }) },
      { status: 'fulfilled', value: 50 },
      { status: 'fulfilled', value: 60 },
    ])
  })

  it('rejects an invalid concurrency limit', async () => {
    await expect(mapSettledWithConcurrency([1], 0, async (value) => value)).rejects.toThrow(
      'Concurrency must be a positive integer.',
    )
  })
})
