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
      }).createdToken,
    ).toBeNull()
  })
})
