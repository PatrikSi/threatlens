import { describe, expect, it } from 'vitest'
import { createProviderDraft, createProviderRequest, validateProviderDraft } from './aiProviderDraft'
import { createDraftFromSettings, createRequestFromDraft, DEFAULT_DRAFT, validateAISettingsDraft } from './aiSettingsDraft'
import type { AISettings } from '../types/ai'

const capabilities = {
  request_dialect: 'chat_completions_modern' as const, temperature: null,
  reasoning_effort: 'low' as const, structured_output_mode: 'json_object' as const,
  model_context_window_tokens: 32768, model_max_output_tokens: 8192,
}

describe('provider capability draft boundaries', () => {
  it('keeps legacy defaults and explicitly omits unverified limits', () => {
    expect(createRequestFromDraft(DEFAULT_DRAFT)).toMatchObject({
      request_dialect: 'chat_completions', temperature: 0.2, reasoning_effort: null,
      structured_output_mode: 'off', model_context_window_tokens: null, model_max_output_tokens: null,
    })
  })
  it('round-trips configured capabilities and omitted temperature through both editors', () => {
    const saved = { ...createRequestFromDraft(DEFAULT_DRAFT), ...capabilities } as AISettings
    expect(createRequestFromDraft(createDraftFromSettings(saved))).toMatchObject(capabilities)
    const provider = {
      ...createProviderDraft(), request_dialect: capabilities.request_dialect, reasoning_effort: capabilities.reasoning_effort,
      structured_output_mode: capabilities.structured_output_mode, temperature: '',
      model_context_window_tokens: '32768', model_max_output_tokens: '8192',
      name: 'Reasoning model', base_url: 'https://model.example.test/v1', model: 'supported-model',
    }
    expect(validateProviderDraft(provider)).toEqual({})
    expect(createProviderRequest(provider)).toMatchObject(capabilities)
  })
  it.each(['NaN', 'Infinity', '-1', '1.5', '999999999'])('rejects invalid model limits %s in both editors', (value) => {
    const draft = { ...DEFAULT_DRAFT, model_max_output_tokens: value, model_context_window_tokens: value }
    expect(validateAISettingsDraft(draft)).toMatchObject({
      model_max_output_tokens: expect.any(String), model_context_window_tokens: expect.any(String),
    })
    expect(validateProviderDraft({ ...createProviderDraft(), ...draft })).toMatchObject({
      model_max_output_tokens: expect.any(String), model_context_window_tokens: expect.any(String),
    })
  })
  it('distinguishes omitted temperature from zero and omitted reasoning from none', () => {
    expect(createRequestFromDraft({ ...DEFAULT_DRAFT, temperature: '', reasoning_effort: '' })).toMatchObject({ temperature: null, reasoning_effort: null })
    expect(createRequestFromDraft({ ...DEFAULT_DRAFT, temperature: '0', reasoning_effort: 'none' })).toMatchObject({ temperature: 0, reasoning_effort: 'none' })
  })
})
