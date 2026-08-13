import { describe, expect, it } from 'vitest'

import { changePendingEntityCount } from './usePendingEntityActions'

describe('changePendingEntityCount', () => {
  it('tracks concurrent entities independently', () => {
    const firstPending = changePendingEntityCount({}, 'update', 'user-1', 1)
    const bothPending = changePendingEntityCount(firstPending, 'update', 'user-2', 1)
    const secondOnly = changePendingEntityCount(bothPending, 'update', 'user-1', -1)

    expect(secondOnly).toEqual({ 'update\u0000user-2': 1 })
  })

  it('counts overlapping operations for the same entity', () => {
    const firstPending = changePendingEntityCount({}, 'note', 'item-1', 1)
    const twicePending = changePendingEntityCount(firstPending, 'note', 'item-1', 1)
    const oncePending = changePendingEntityCount(twicePending, 'note', 'item-1', -1)
    const complete = changePendingEntityCount(oncePending, 'note', 'item-1', -1)

    expect(oncePending).toEqual({ 'note\u0000item-1': 1 })
    expect(complete).toEqual({})
  })
})
