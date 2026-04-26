import { describe, expect, it } from 'vitest'

import { createDraftFromSettings, createRequestFromDraft, validateAISettingsDraft } from './aiSettingsDraft'
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

  it('preserves valid zero-valued numeric settings', () => {
    const request = createRequestFromDraft({
      base_url: 'http://localhost:11434/v1',
      model: 'local-threat-model',
      temperature: '0',
      max_completion_tokens: '5000',
      request_timeout_seconds: '120',
      request_max_retries: '0',
      summary_enabled: true,
      relevance_enabled: true,
      daily_brief_enabled: true,
      auto_enrich_new_items: true,
      daily_brief_run_time_utc: '09:00',
      daily_brief_window_hours: '24',
      daily_brief_max_items: '20',
      daily_brief_history_limit: '7',
      relevance_medium_threshold: '0',
      relevance_high_threshold: '0',
      company_name: '',
      company_industry: '',
      company_regions: '',
      company_stack: '',
      company_priority_topics: '',
      company_keywords: '',
      company_exclusions: '',
      company_profile_text: '',
      item_enrichment_system_prompt: '',
      daily_brief_system_prompt: '',
      global_instructions: '',
      item_summary_instructions: '',
      relevance_instructions: '',
      daily_brief_instructions: '',
    })

    expect(request.temperature).toBe(0)
    expect(request.request_max_retries).toBe(0)
    expect(request.relevance_medium_threshold).toBe(0)
    expect(request.relevance_high_threshold).toBe(0)
  })
})

describe('validateAISettingsDraft', () => {
  it('matches backend numeric bounds before saving settings', () => {
    const validation = validateAISettingsDraft({
      base_url: 'http://localhost:11434/v1',
      model: 'local-threat-model',
      temperature: '2.1',
      max_completion_tokens: '127',
      request_timeout_seconds: '4',
      request_max_retries: '6',
      summary_enabled: true,
      relevance_enabled: true,
      daily_brief_enabled: true,
      auto_enrich_new_items: true,
      daily_brief_run_time_utc: '09:00',
      daily_brief_window_hours: '5',
      daily_brief_max_items: '101',
      daily_brief_history_limit: '0',
      relevance_medium_threshold: '0.8',
      relevance_high_threshold: '0.7',
      company_name: '',
      company_industry: '',
      company_regions: '',
      company_stack: '',
      company_priority_topics: '',
      company_keywords: '',
      company_exclusions: '',
      company_profile_text: '',
      item_enrichment_system_prompt: '',
      daily_brief_system_prompt: '',
      global_instructions: '',
      item_summary_instructions: '',
      relevance_instructions: '',
      daily_brief_instructions: '',
    })

    expect(validation.temperature).toBe('Temperature must be between 0 and 2.')
    expect(validation.max_completion_tokens).toBe('Max Completion Tokens must be between 128 and 8192.')
    expect(validation.request_timeout_seconds).toBe('Request Timeout Seconds must be between 5 and 300.')
    expect(validation.request_max_retries).toBe('Max Retry Attempts must be between 0 and 5.')
    expect(validation.daily_brief_window_hours).toBe('Daily Brief Window Hours must be between 6 and 168.')
    expect(validation.daily_brief_max_items).toBe('Daily Brief Max Articles must be between 5 and 100.')
    expect(validation.daily_brief_history_limit).toBe('Retained Daily Briefings must be between 1 and 90.')
    expect(validation.relevance_high_threshold).toBe(
      'High Relevance Threshold must be greater than Medium Relevance Threshold.',
    )
  })

  it('matches backend text limits and rejects invalid scheduled brief times', () => {
    const validation = validateAISettingsDraft({
      base_url: 'x'.repeat(4001),
      model: 'm'.repeat(256),
      temperature: '0.2',
      max_completion_tokens: '5000',
      request_timeout_seconds: '300',
      request_max_retries: '3',
      summary_enabled: true,
      relevance_enabled: true,
      daily_brief_enabled: true,
      auto_enrich_new_items: true,
      daily_brief_run_time_utc: '24:00',
      daily_brief_window_hours: '24',
      daily_brief_max_items: '20',
      daily_brief_history_limit: '7',
      relevance_medium_threshold: '0.55',
      relevance_high_threshold: '0.8',
      company_name: 'c'.repeat(256),
      company_industry: '',
      company_regions: '',
      company_stack: '',
      company_priority_topics: '',
      company_keywords: '',
      company_exclusions: '',
      company_profile_text: 'p'.repeat(4001),
      item_enrichment_system_prompt: '',
      daily_brief_system_prompt: '',
      global_instructions: '',
      item_summary_instructions: '',
      relevance_instructions: '',
      daily_brief_instructions: '',
    })

    expect(validation.base_url).toBe('Base URL cannot exceed 4000 characters.')
    expect(validation.model).toBe('Model cannot exceed 255 characters.')
    expect(validation.company_name).toBe('Company Name cannot exceed 255 characters.')
    expect(validation.company_profile_text).toBe('Additional Company Context cannot exceed 4000 characters.')
    expect(validation.daily_brief_run_time_utc).toBe('Daily Brief Run Time must be a valid UTC time.')
  })

  it('rejects equal relevance thresholds before the backend does', () => {
    const validation = validateAISettingsDraft({
      base_url: 'http://localhost:11434/v1',
      model: 'local-threat-model',
      temperature: '0.2',
      max_completion_tokens: '5000',
      request_timeout_seconds: '300',
      request_max_retries: '3',
      summary_enabled: true,
      relevance_enabled: true,
      daily_brief_enabled: true,
      auto_enrich_new_items: true,
      daily_brief_run_time_utc: '09:00',
      daily_brief_window_hours: '24',
      daily_brief_max_items: '20',
      daily_brief_history_limit: '7',
      relevance_medium_threshold: '0.8',
      relevance_high_threshold: '0.8',
      company_name: '',
      company_industry: '',
      company_regions: '',
      company_stack: '',
      company_priority_topics: '',
      company_keywords: '',
      company_exclusions: '',
      company_profile_text: '',
      item_enrichment_system_prompt: '',
      daily_brief_system_prompt: '',
      global_instructions: '',
      item_summary_instructions: '',
      relevance_instructions: '',
      daily_brief_instructions: '',
    })

    expect(validation.relevance_high_threshold).toBe('High Relevance Threshold must be greater than Medium Relevance Threshold.')
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
        itemSelection: null,
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
        itemSelection: null,
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
        itemSelection: null,
      },
    })
  })

  it('blocks search-only reprocess queues because search is only a picker filter', () => {
    expect(
      resolveAiReprocessQueueState({
        days: '7',
        limit: '25',
        startTime: '',
        endTime: '',
        feedIds: [],
        selectedItems: [],
        itemSearch: 'fortinet',
      }),
    ).toEqual({
      payload: null,
      validation: {
        days: null,
        limit: null,
        timeRange: null,
        itemSelection: 'Add a matching article or clear the search before queueing. Search text is only used for picking articles.',
      },
    })
  })

  it('blocks explicit item selections that exceed the requested article limit', () => {
    expect(
      resolveAiReprocessQueueState({
        days: '',
        limit: '1',
        startTime: '',
        endTime: '',
        feedIds: [],
        selectedItems: [{ id: 'item-1' }, { id: 'item-2' }],
      }),
    ).toEqual({
      payload: null,
      validation: {
        days: null,
        limit: null,
        timeRange: null,
        itemSelection: 'Selected articles cannot exceed Last X Articles. Increase the limit or remove selected articles.',
      },
    })
  })
})
