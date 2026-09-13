import { describe, expect, it } from 'vitest'
import { createProviderDraft, createProviderRequest, validateProviderDraft } from './aiProviderDraft'
import { DEFAULT_DRAFT, createRequestFromDraft, validateAISettingsDraft } from './aiSettingsDraft'

const provider = {
  ...createProviderDraft(),
  name: 'Analysis',
  base_url: 'https://ai.example.test/v1',
  model: 'analysis-model',
}

describe('AI completion budget boundaries', () => {
  it.each(['128', '8193', '65536', '131072'])(
    'accepts %s default tokens in legacy and named providers without increasing the report budget',
    (tokens) => {
      const legacy = { ...DEFAULT_DRAFT, max_completion_tokens: tokens }
      const named = { ...provider, max_completion_tokens: tokens }
      expect(validateAISettingsDraft(legacy)).toEqual({})
      expect(validateProviderDraft(named)).toEqual({})
      expect(createRequestFromDraft(legacy).max_completion_tokens).toBe(Number(tokens))
      expect(createProviderRequest(named).max_completion_tokens).toBe(Number(tokens))
      expect(createRequestFromDraft(legacy).report_reserved_output_tokens).toBe(1200)
    },
  )

  it.each(['', ' ', 'NaN', 'Infinity', '127', '131073', '5000.5'])(
    'rejects invalid default completion budget %j in both editors',
    (tokens) => {
      expect(validateAISettingsDraft({ ...DEFAULT_DRAFT, max_completion_tokens: tokens }).max_completion_tokens)
        .toBeTruthy()
      expect(validateProviderDraft({ ...provider, max_completion_tokens: tokens }).max_completion_tokens)
        .toBeTruthy()
    },
  )

  it.each(['256', '8193', '65536', '131072'])(
    'saves %s initial report tokens independently of the provider default',
    (tokens) => {
      const draft = {
        ...DEFAULT_DRAFT,
        report_reserved_output_tokens: tokens,
        report_context_window_tokens: '262144',
      }
      expect(validateAISettingsDraft(draft)).toEqual({})
      expect(createRequestFromDraft(draft).report_reserved_output_tokens).toBe(Number(tokens))
      expect(createRequestFromDraft(draft).max_completion_tokens).toBe(5000)
    },
  )

  it.each(['', ' ', 'NaN', 'Infinity', '255', '131073', '1200.5'])(
    'rejects invalid initial report budget %j',
    (tokens) => {
      const draft = {
        ...DEFAULT_DRAFT,
        report_reserved_output_tokens: tokens,
        report_context_window_tokens: '262144',
      }
      expect(validateAISettingsDraft(draft).report_reserved_output_tokens).toBeTruthy()
    },
  )

  it('requires enough context when raising the report output budget', () => {
    expect(validateAISettingsDraft({ ...DEFAULT_DRAFT, report_reserved_output_tokens: '131072' }))
      .toHaveProperty('report_context_window_tokens', expect.stringContaining('512 input tokens'))
  })

  it('allows exactly 512 usable input tokens after rounded safety and protocol overhead', () => {
    const boundary = {
      ...DEFAULT_DRAFT,
      report_context_window_tokens: '4096',
      report_context_safety_percent: '5',
      report_reserved_output_tokens: '2995',
      report_source_token_cap: '511',
    }
    expect(validateAISettingsDraft(boundary)).toEqual({})
    expect(validateAISettingsDraft({ ...boundary, report_reserved_output_tokens: '2996' }))
      .toHaveProperty('report_context_window_tokens', expect.stringContaining('512 input tokens'))
    expect(validateAISettingsDraft({ ...boundary, report_source_token_cap: '512' }))
      .toHaveProperty('report_source_token_cap', expect.stringContaining('usable report context'))
  })
})
