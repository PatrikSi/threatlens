import { describe, expect, it } from 'vitest'
import { createProviderDraft, createProviderRequest, validateProviderDraft } from './aiProviderDraft'
import { createRequestFromDraft, DEFAULT_DRAFT, validateAISettingsDraft } from './aiSettingsDraft'

const limited = { max_concurrent_requests: '3', hourly_token_budget: '1000000000000' }

describe('provider admission settings', () => {
  it('keeps unlimited defaults and serializes the full supported token budget', () => {
    expect(createRequestFromDraft(DEFAULT_DRAFT)).toMatchObject({ max_concurrent_requests: 0, hourly_token_budget: 0 })
    expect(createRequestFromDraft({ ...DEFAULT_DRAFT, ...limited })).toMatchObject({ max_concurrent_requests: 3, hourly_token_budget: 1000000000000 })
    expect(createProviderRequest({ ...createProviderDraft(), ...limited })).toMatchObject({ max_concurrent_requests: 3, hourly_token_budget: 1000000000000 })
  })
  it.each(['', '-1', '1.5', 'Infinity', '1000000000001'])('rejects invalid admission limits %s in both editors', (value) => {
    const invalid = { max_concurrent_requests: value, hourly_token_budget: value }
    expect(validateAISettingsDraft({ ...DEFAULT_DRAFT, ...invalid })).toMatchObject({
      max_concurrent_requests: expect.any(String), hourly_token_budget: expect.any(String),
    })
    expect(validateProviderDraft({ ...createProviderDraft(), ...invalid })).toMatchObject({
      max_concurrent_requests: expect.any(String), hourly_token_budget: expect.any(String),
    })
  })
})
