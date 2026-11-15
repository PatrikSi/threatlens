import { describe, expect, it } from 'vitest'

import {
  createInitialTokenCreateFormState,
  reduceTokenCreateFormState,
} from './useTokenCreateFormState'

describe('reduceTokenCreateFormState', () => {
  it('clears a previously created token when creation starts again', () => {
    const stateWithToken = {
      ...createInitialTokenCreateFormState(),
      createdToken: {
        token: 'tl_secret',
        token_prefix: 'tl_secret',
        expires_at: null,
      },
    }

    expect(
      reduceTokenCreateFormState(stateWithToken, { type: 'createStarted' })
        .createdToken,
    ).toBeNull()
  })

  it('clears a previously created token after a failed retry', () => {
    const stateWithToken = {
      ...createInitialTokenCreateFormState(),
      createdToken: {
        token: 'tl_secret',
        token_prefix: 'tl_secret',
        expires_at: null,
      },
    }

    expect(
      reduceTokenCreateFormState(stateWithToken, { type: 'createFailed' })
        .createdToken,
    ).toBeNull()
  })

  it('removes an acknowledged one-time token from state', () => {
    const stateWithToken = {
      ...createInitialTokenCreateFormState(),
      createdToken: {
        token: 'tl_secret',
        token_prefix: 'tl_secret',
        expires_at: null,
      },
    }

    expect(
      reduceTokenCreateFormState(stateWithToken, {
        type: 'dismissCreatedToken',
        value: stateWithToken.createdToken,
      }).createdToken,
    ).toBeNull()
  })

  it('does not dismiss a newer token when an older copy completes in the same update batch', () => {
    const first = { token: 'first-secret', token_prefix: 'first', expires_at: null }
    const second = { token: 'second-secret', token_prefix: 'second', expires_at: null }
    const initial = { ...createInitialTokenCreateFormState(), createdToken: first }
    const updated = reduceTokenCreateFormState(initial, { type: 'createSucceeded', value: second })
    expect(reduceTokenCreateFormState(updated, { type: 'dismissCreatedToken', value: first })).toBe(updated)
    expect(updated.createdToken).toBe(second)
  })
})
