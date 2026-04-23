import { describe, expect, it } from 'vitest'

import { createDraftFromSettings, createRequestFromDraft } from './aiSettingsDraft'
import { resolveAiReprocessQueueState } from './aiReprocessQueueState'
import { resolveVisibleRunSelection } from './aiRunSelection'

describe('resolveVisibleRunSelection', () => {
  it('keeps the current selection when it is still visible', () => {
    expect(
      resolveVisibleRunSelection(
        [
          { id: 'run-1' },
          { id: 'run-2' },
        ],
        'run-2',
      ),
    ).toBe('run-2')
  })

  it('falls back to the first visible run when filters remove the current selection', () => {
    expect(
      resolveVisibleRunSelection(
        [
          { id: 'run-3' },
          { id: 'run-4' },
        ],
        'run-2',
      ),
    ).toBe('run-3')
  })

  it('auto-selects the first visible run when nothing is selected yet', () => {
    expect(
      resolveVisibleRunSelection(
        [
          { id: 'run-3' },
          { id: 'run-4' },
        ],
        null,
      ),
    ).toBe('run-3')
  })

  it('clears the selection when no runs match the current filters', () => {
    expect(resolveVisibleRunSelection([], 'run-2')).toBeNull()
    expect(resolveVisibleRunSelection(undefined, 'run-2')).toBeNull()
  })
})

describe('createDraftFromSettings', () => {
  it('maps persisted AI settings into editable form fields', () => {
    const draft = createDraftFromSettings({
      id: 'settings-1',
      ai_enabled: true,
      ai_configured: true,
      api_key_configured: true,
      provider_type: 'openai_compatible',
      base_url: 'http://localhost:11434/v1',
      model: 'local-threat-model',
      temperature: 0.2,
      max_completion_tokens: 6000,
      request_timeout_seconds: 120,
      request_max_retries: 2,
      summary_enabled: true,
      relevance_enabled: true,
      daily_brief_enabled: true,
      auto_enrich_new_items: false,
      daily_brief_window_hours: 48,
      daily_brief_max_items: 30,
      daily_brief_history_limit: 10,
      daily_brief_schedule_hour_utc: 6,
      daily_brief_schedule_minute_utc: 45,
      relevance_medium_threshold: 0.55,
      relevance_high_threshold: 0.8,
      company_name: 'Example Corp',
      company_industry: 'technology',
      company_regions: ['US', 'EU'],
      company_stack: ['Fortinet', 'Okta'],
      company_priority_topics: ['edge', 'identity'],
      company_keywords: ['vpn'],
      company_exclusions: ['consumer'],
      company_profile_text: 'Protects enterprise edge systems.',
      item_enrichment_system_prompt: null,
      daily_brief_system_prompt: null,
      global_instructions: 'Keep it concise.',
      item_summary_instructions: null,
      relevance_instructions: null,
      daily_brief_instructions: null,
      created_at: '2026-04-18T00:00:00Z',
      updated_at: '2026-04-18T01:00:00Z',
      prompt_previews: {
        item_enrichment: {
          label: 'Item enrichment',
          system_prompt: 'Prompt',
          notes: [],
        },
        daily_brief: {
          label: 'Daily brief',
          system_prompt: 'Prompt',
          notes: [],
        },
      },
    })

    expect(draft.daily_brief_run_time_utc).toBe('06:45')
    expect(draft.company_regions).toBe('US\nEU')
    expect(draft.company_stack).toBe('Fortinet\nOkta')
    expect(draft.auto_enrich_new_items).toBe(false)
  })
})

describe('createRequestFromDraft', () => {
  it('normalizes optional text, deduplicates lists, and falls back to a safe UTC schedule', () => {
    const request = createRequestFromDraft({
      base_url: ' https://api.example.com/v1 ',
      model: ' threat-model ',
      temperature: '0.4',
      max_completion_tokens: '7000',
      request_timeout_seconds: '180',
      request_max_retries: '4',
      summary_enabled: true,
      relevance_enabled: true,
      daily_brief_enabled: true,
      auto_enrich_new_items: true,
      daily_brief_run_time_utc: '99:99',
      daily_brief_window_hours: '72',
      daily_brief_max_items: '40',
      daily_brief_history_limit: '12',
      relevance_medium_threshold: '0.6',
      relevance_high_threshold: '0.9',
      company_name: ' Example Corp ',
      company_industry: ' Technology ',
      company_regions: 'US\nEU\nUS',
      company_stack: 'Fortinet, Okta, Fortinet',
      company_priority_topics: 'identity',
      company_keywords: 'vpn',
      company_exclusions: 'consumer',
      company_profile_text: ' Profile ',
      item_enrichment_system_prompt: ' ',
      daily_brief_system_prompt: '',
      global_instructions: ' Keep it concise. ',
      item_summary_instructions: '',
      relevance_instructions: '',
      daily_brief_instructions: '',
    })

    expect(request.base_url).toBe('https://api.example.com/v1')
    expect(request.model).toBe('threat-model')
    expect(request.daily_brief_schedule_hour_utc).toBe(9)
    expect(request.daily_brief_schedule_minute_utc).toBe(0)
    expect(request.company_regions).toEqual(['US', 'EU'])
    expect(request.company_stack).toEqual(['Fortinet', 'Okta'])
    expect(request.item_enrichment_system_prompt).toBeNull()
    expect(request.global_instructions).toBe('Keep it concise.')
  })
})

describe('resolveAiReprocessQueueState', () => {
  it('blocks blank lookback input when no explicit scope is selected', () => {
    expect(
      resolveAiReprocessQueueState({
        days: ' ',
        limit: '100',
        startTime: '',
        endTime: '',
        feedIds: [],
        selectedItems: [],
      }),
    ).toEqual({
      payload: null,
      validation: {
        days: 'Lookback Days must be a whole number greater than 0 when no explicit time or article scope is selected.',
        limit: null,
        timeRange: null,
      },
    })
  })

  it('blocks zero article limits instead of silently falling back to the default batch size', () => {
    expect(
      resolveAiReprocessQueueState({
        days: '7',
        limit: '0',
        startTime: '',
        endTime: '',
        feedIds: [],
        selectedItems: [],
      }),
    ).toEqual({
      payload: null,
      validation: {
        days: null,
        limit: 'Last X Articles must be a whole number greater than 0.',
        timeRange: null,
      },
    })
  })

  it('allows explicit item scope to omit lookback days while still returning a bounded payload', () => {
    expect(
      resolveAiReprocessQueueState({
        days: '',
        limit: '25',
        startTime: '',
        endTime: '',
        feedIds: ['feed-1'],
        selectedItems: [{ id: 'item-7' }],
      }),
    ).toEqual({
      payload: {
        days: null,
        limit: 25,
        start_time: null,
        end_time: null,
        feed_ids: ['feed-1'],
        item_ids: ['item-7'],
      },
      validation: {
        days: null,
        limit: null,
        timeRange: null,
      },
    })
  })
})
