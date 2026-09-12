// @vitest-environment jsdom
import { MemoryRouter } from 'react-router-dom'

import { act } from 'react'
import { createRoot, Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const aiSettingsPageDomMocks = vi.hoisted(() => ({
  savePending: false,
  includeNamedProvider: false,
  currentUser: {
    data: {
      id: 'admin-1',
      email: 'admin@example.com',
      role: 'admin',
      is_active: true,
      is_approved: true,
      approved_at: '2026-04-21T10:00:00Z',
      created_at: '2026-04-20T10:00:00Z',
      features: {
        ai_enabled: true,
        ai_configured: true,
        ai_summary_enabled: true,
        ai_relevance_enabled: true,
        ai_daily_brief_enabled: true,
      },
    },
    isLoading: false,
    isError: false,
    error: null,
  },
  queryClient: {
    invalidateQueries: vi.fn(),
    prefetchQuery: vi.fn(() => Promise.resolve()),
  },
  settingsData: {
    id: 'settings-1',
    ai_enabled: true,
    ai_configured: true,
    api_key_configured: true,
    effective_feature_configured: undefined as { item_enrichment: boolean; daily_brief: boolean; report: boolean } | undefined,
    provider_type: 'openai_compatible',
    base_url: 'https://api.example.com/v1',
    model: 'gpt-threat',
    temperature: 0.2,
    max_completion_tokens: 4000,
    request_timeout_seconds: 120,
    request_max_retries: 2,
    report_reserved_output_tokens: 1200,
    report_context_window_tokens: 8192,
    report_context_safety_percent: 15,
    summary_enabled: true,
    relevance_enabled: true,
    daily_brief_enabled: true,
    auto_enrich_new_items: true,
    daily_brief_window_hours: 24,
    daily_brief_max_items: 25,
    daily_brief_history_limit: 10,
    daily_brief_schedule_hour_utc: 9,
    daily_brief_schedule_minute_utc: 0,
    relevance_medium_threshold: 0.5,
    relevance_high_threshold: 0.8,
    company_name: '',
    company_industry: '',
    company_regions: [],
    company_stack: [],
    company_priority_topics: [],
    company_keywords: [],
    company_exclusions: [],
    company_profile_text: '',
    item_enrichment_system_prompt: null,
    daily_brief_system_prompt: null,
    global_instructions: '',
    item_summary_instructions: null,
    relevance_instructions: null,
    daily_brief_instructions: null,
    created_at: '2026-04-20T10:00:00Z',
    updated_at: '2026-04-21T10:00:00Z',
    prompt_previews: {
      item_enrichment: { label: 'Item enrichment', system_prompt: 'Prompt', notes: [] },
      daily_brief: { label: 'Daily brief', system_prompt: 'Prompt', notes: [] },
    },
  },
  settingsError: false,
  overviewData: {
    kpis: {
      total_requests: 0,
      success_rate_pct: 100,
      total_tokens: 0,
      average_latency_ms: 0,
      p95_latency_ms: 0,
      last_successful_run_at: null,
    },
    live: {
      worker_count: 1,
      active_count: 0,
      reserved_count: 0,
      scheduled_count: 0,
      queued_count: 1,
      oldest_queued_age_seconds: null,
      active_tasks: [],
    },
    endpoint_health: {
      last_success_at: null,
      rolling_failure_rate_pct: 0,
      median_latency_ms: 0,
      last_auth_error: null,
      last_provider_error: null,
    },
    feature_health: [],
    failures: [],
    time_series: [],
    per_model: [],
    token_efficiency: {
      average_prompt_tokens: 0,
      average_completion_tokens: 0,
      average_total_tokens: 0,
      prompt_to_completion_ratio: 0,
      top_expensive_feature: null,
      top_expensive_feature_avg_tokens: 0,
    },
    coverage: {
      eligible_items: 0,
      enriched_items: 0,
      pending_items: 0,
      failed_items: 0,
      skipped_no_article_count: 0,
      skipped_ai_disabled_count: 0,
      skipped_not_configured_count: 0,
      skipped_auto_enrich_disabled_count: 0,
      skipped_unchanged_count: 0,
      oldest_pending_at: null,
      last_successful_enrichment_at: null,
      last_successful_daily_brief_at: null,
    },
    relevance_distribution: {
      high_count: 0,
      medium_count: 0,
      low_count: 0,
      average_score: 0,
      by_feed: [],
    },
    cache: {
      reused_count: 0,
      recomputed_count: 0,
      no_op_rate_pct: 0,
    },
    storage: {
      retained_daily_briefs: 0,
      daily_brief_history_limit: 10,
      enrichment_rows: 0,
      usage_event_rows: 0,
      task_history_rows: 0,
      growth_last_7d: 0,
      growth_last_30d: 0,
    },
  },
  liveData: {
    worker_count: 1,
    active_count: 0,
    reserved_count: 0,
    scheduled_count: 0,
    queued_count: 1,
    oldest_queued_age_seconds: null,
  },
  queuedRunsData: {
    items: [
      {
        id: 'run-queued-1',
        task_type: 'daily_brief',
        status: 'queued',
        reason: null,
        trigger_source: 'manual',
        queued_at: '2026-04-21T10:00:00Z',
        started_at: null,
        finished_at: null,
        updated_at: '2026-04-21T10:01:00Z',
        worker_name: null,
        model: 'gpt-threat',
        parent_run_id: null,
        target_count: null,
        processed_count: 0,
        success_count: 0,
        error_count: 0,
        skipped_count: 0,
        daily_brief_id: null,
        metadata: {},
      },
    ],
    total: 1,
    limit: 10,
    offset: 0,
  },
  emptyRunsData: {
    items: [],
    total: 0,
    limit: 20,
    offset: 0,
  },
  activeRunsLoading: false,
  activeRunsRefreshing: false,
  historyRunsDataByPage: {} as Record<number, { items: unknown[]; total: number; limit: number; offset: number }>,
  runDetailById: {} as Record<string, unknown>,
  childRunsDataByParent: {} as Record<string, { items: unknown[]; total: number; limit: number; offset: number }>,
  promptHistoryData: [] as unknown[],
  emptyItemsData: {
    items: [],
    total: 0,
    page: 1,
    page_size: 12,
  },
  cancelMutate: vi.fn(),
  backfillMutate: vi.fn(),
  completeReprocessMutation: true,
  reprocessMutate: vi.fn(),
}))

const routerMocks = vi.hoisted(() => ({
  useBlocker: vi.fn(() => ({
    state: 'unblocked' as 'unblocked' | 'blocked',
    proceed: vi.fn(),
    reset: vi.fn(),
  })),
}))

function aiMutationResult(mutate: ReturnType<typeof vi.fn>) {
  return {
    mutate,
    isPending: false,
    isError: false,
    error: null,
  }
}

function namedProviderQueryData(key: string) {
  if (!aiSettingsPageDomMocks.includeNamedProvider) return undefined
  const provider = {
    ...aiSettingsPageDomMocks.settingsData,
    id: 'provider-1', name: 'Large report model', enabled: true,
    max_completion_tokens: 131072, version: 1, credential_error: null,
  }
  if (key === 'ai:providers::0') return { items: [provider], total: 1, offset: 0, limit: 25 }
  if (key === 'ai:providers:detail:provider-1') return provider
  if (key.startsWith('ai:provider-routing:names:')) return [provider]
  if (key === 'ai:provider-routing') return {
    version: 1, default_provider_id: 'provider-1', item_enrichment_provider_id: null,
    daily_brief_provider_id: null, report_provider_id: null,
  }
}

vi.mock('@tanstack/react-query', () => ({
  keepPreviousData: <T,>(previousData: T) => previousData,
  useQueryClient: () => aiSettingsPageDomMocks.queryClient,
  useQuery: ({ queryKey }: { queryKey: unknown[] }) => {
    const key = queryKey.join(':')
    const baseResult = {
      isLoading: false,
      isFetching: false,
      isError: false,
      error: null,
      data: undefined,
    }

    const providerData = namedProviderQueryData(key)
    if (providerData) return { ...baseResult, data: providerData }

    if (key === 'ai:settings') {
      if (aiSettingsPageDomMocks.settingsError) {
        return {
          ...baseResult,
          isError: true,
          error: new Error('settings unavailable'),
        }
      }
      return {
        ...baseResult,
        data: aiSettingsPageDomMocks.settingsData,
      }
    }

    if (key === 'ai:ops:overview:30') {
      return {
        ...baseResult,
        data: aiSettingsPageDomMocks.overviewData,
      }
    }

    if (key === 'ai:ops:live') {
      return {
        ...baseResult,
        isFetching: aiSettingsPageDomMocks.activeRunsRefreshing,
        data: aiSettingsPageDomMocks.liveData,
      }
    }

    if (key === 'ai:ops:runs:queued-top') {
      if (aiSettingsPageDomMocks.activeRunsLoading) {
        return {
          ...baseResult,
          isLoading: true,
        }
      }
      return {
        ...baseResult,
        isFetching: aiSettingsPageDomMocks.activeRunsRefreshing,
        data: aiSettingsPageDomMocks.queuedRunsData,
      }
    }

    if (key === 'ai:ops:runs:running-top') {
      if (aiSettingsPageDomMocks.activeRunsLoading) {
        return {
          ...baseResult,
          isLoading: true,
        }
      }
      return {
        ...baseResult,
        isFetching: aiSettingsPageDomMocks.activeRunsRefreshing,
        data: aiSettingsPageDomMocks.emptyRunsData,
      }
    }

    if (key === 'feeds:ai-reprocess') {
      return {
        ...baseResult,
        data: [],
      }
    }

    if (String(queryKey[0]) === 'items') {
      return {
        ...baseResult,
        data: aiSettingsPageDomMocks.emptyItemsData,
      }
    }

    if (key === 'ai:ops:prompt-history' || key === 'ai:ops:manual-actions') {
      return {
        ...baseResult,
        data: key === 'ai:ops:prompt-history' ? aiSettingsPageDomMocks.promptHistoryData : [],
      }
    }

    if (String(queryKey[0]) === 'ai' && String(queryKey[1]) === 'ops' && String(queryKey[2]) === 'run') {
      const runId = typeof queryKey[3] === 'string' ? queryKey[3] : ''
      return {
        ...baseResult,
        data: aiSettingsPageDomMocks.runDetailById[runId],
      }
    }

    if (String(queryKey[0]) === 'ai' && String(queryKey[1]) === 'ops' && String(queryKey[2]) === 'inspect-run') {
      const runId = typeof queryKey[3] === 'string' ? queryKey[3] : ''
      return {
        ...baseResult,
        data: aiSettingsPageDomMocks.runDetailById[runId],
      }
    }

    if (String(queryKey[0]) === 'ai' && String(queryKey[1]) === 'ops' && String(queryKey[2]) === 'child-runs') {
      const parentRunId = typeof queryKey[3] === 'string' ? queryKey[3] : ''
      return {
        ...baseResult,
        data: aiSettingsPageDomMocks.childRunsDataByParent[parentRunId] ?? aiSettingsPageDomMocks.emptyRunsData,
      }
    }

    if (String(queryKey[0]) === 'ai' && String(queryKey[1]) === 'ops' && String(queryKey[2]) === 'runs' && key.includes(':30:')) {
      const page = typeof queryKey[5] === 'number' ? queryKey[5] : 0
      return {
        ...baseResult,
        data: aiSettingsPageDomMocks.historyRunsDataByPage[page] ?? aiSettingsPageDomMocks.emptyRunsData,
      }
    }

    return baseResult
  },
  useMutation: (options: {
    mutationKey?: unknown
    onMutate?: (value: unknown) => unknown
    onSuccess?: (result: unknown, value: unknown, context?: unknown) => void
    onSettled?: () => void
  }) => {
    const mutationKey = Array.isArray(options?.mutationKey) ? options.mutationKey.join(':') : String(options?.mutationKey ?? '')
    if (mutationKey === 'ai:settings:save') return { ...aiMutationResult(vi.fn()), isPending: aiSettingsPageDomMocks.savePending }
    if (mutationKey === 'ai:ops:runs:cancel') {
      return aiMutationResult(
        vi.fn((runId: string) => {
          options.onMutate?.(runId)
          aiSettingsPageDomMocks.cancelMutate(runId)
          options.onSuccess?.(
            {
              ...aiSettingsPageDomMocks.queuedRunsData.items[0],
              id: runId,
              status: 'skipped',
              reason: 'canceled',
              finished_at: '2026-04-21T10:02:00Z',
            },
            runId,
          )
          options.onSettled?.()
        }),
      )
    }
    if (mutationKey === 'ai:reprocess') {
      return aiMutationResult(
        vi.fn((payload: unknown) => {
          const submittedScope = options.onMutate?.(payload)
          aiSettingsPageDomMocks.reprocessMutate(payload)
          if (aiSettingsPageDomMocks.completeReprocessMutation) {
            options.onSuccess?.(
              {
                task_id: 'task-reprocess-1',
                queued: true,
                run_id: 'run-reprocess-1',
                celery_task_id: 'task-reprocess-1',
              },
              payload,
              submittedScope,
            )
          }
        }),
      )
    }
    if (mutationKey === 'ai:daily-brief:backfill') {
      return aiMutationResult(
        vi.fn((days: number) => {
          aiSettingsPageDomMocks.backfillMutate(days)
          options.onSuccess?.(
            {
              task_id: 'task-backfill-1',
              queued: true,
              days,
              run_id: 'run-backfill-1',
              celery_task_id: 'task-backfill-1',
            },
            days,
          )
        }),
      )
    }
    return aiMutationResult(vi.fn())
  },
}))

vi.mock('../hooks/useCurrentUser', () => ({
  useCurrentUser: () => aiSettingsPageDomMocks.currentUser,
}))

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return {
    ...actual,
    useBlocker: routerMocks.useBlocker,
  }
})

import { AiSettingsPage } from './AiSettingsPage'

function createAiRun(id: string, overrides: Record<string, unknown> = {}) {
  return {
    id,
    task_type: 'item_enrichment',
    status: 'ready',
    reason: null,
    trigger_source: 'manual',
    queued_at: '2026-04-21T10:00:00Z',
    started_at: '2026-04-21T10:00:01Z',
    finished_at: '2026-04-21T10:00:03Z',
    created_at: '2026-04-21T10:00:00Z',
    updated_at: '2026-04-21T10:00:03Z',
    worker_name: 'worker@host',
    model: 'gpt-threat',
    parent_run_id: null,
    target_count: null,
    processed_count: 0,
    success_count: 0,
    error_count: 0,
    skipped_count: 0,
    skipped_unchanged_count: 0,
    skipped_ineligible_count: 0,
    daily_brief_id: null,
    metadata: {},
    celery_task_id: null,
    actor_user_id: 'admin-1',
    actor_email: 'admin@example.com',
    item_id: null,
    item_title: null,
    item_url: null,
    feed_name: null,
    item_first_seen_at: null,
    item_published_at: null,
    prompt_tokens: 12,
    completion_tokens: 8,
    total_tokens: 20,
    latency_ms: 250,
    duration_ms: 2000,
    prompt_char_count: 1200,
    response_char_count: 320,
    input_text_chars: 800,
    error: null,
    ...overrides,
  }
}

let root: Root | null = null
let container: HTMLDivElement | null = null

function renderPage() {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => {
    root?.render(<MemoryRouter><AiSettingsPage /></MemoryRouter>)
  })
  return container
}

function pageText() {
  return document.body.textContent ?? ''
}

function getButton(text: string) {
  return Array.from(document.querySelectorAll('button')).find((button) => button.textContent?.includes(text)) ?? null
}

function getLabeledInput(labelText: string) {
  return (
    Array.from(document.querySelectorAll('label'))
      .find((label) => label.textContent?.includes(labelText))
      ?.querySelector('input') ?? null
  )
}

function setInputValue(input: HTMLInputElement, value: string) {
  const descriptor = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')
  descriptor?.set?.call(input, value)
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

function clearActiveAiWork() {
  aiSettingsPageDomMocks.liveData = {
    ...aiSettingsPageDomMocks.liveData,
    active_count: 0,
    queued_count: 0,
    oldest_queued_age_seconds: null,
  }
  aiSettingsPageDomMocks.queuedRunsData = {
    items: [],
    total: 0,
    limit: 10,
    offset: 0,
  }
}

afterEach(() => {
  aiSettingsPageDomMocks.savePending = false;
  aiSettingsPageDomMocks.includeNamedProvider = false
  aiSettingsPageDomMocks.settingsData.report_reserved_output_tokens = 1200
  aiSettingsPageDomMocks.settingsData.report_context_window_tokens = 8192
  act(() => {
    root?.unmount()
  })
  root = null
  container?.remove()
  container = null
  document.body.innerHTML = ''
  aiSettingsPageDomMocks.cancelMutate.mockReset()
  aiSettingsPageDomMocks.backfillMutate.mockReset()
  aiSettingsPageDomMocks.reprocessMutate.mockReset()
  aiSettingsPageDomMocks.completeReprocessMutation = true
  aiSettingsPageDomMocks.settingsData.ai_configured = true
  aiSettingsPageDomMocks.settingsData.effective_feature_configured = undefined
  aiSettingsPageDomMocks.settingsError = false
  aiSettingsPageDomMocks.liveData = {
    worker_count: 1,
    active_count: 0,
    reserved_count: 0,
    scheduled_count: 0,
    queued_count: 1,
    oldest_queued_age_seconds: null,
  }
  aiSettingsPageDomMocks.queuedRunsData = {
    items: [
      {
        id: 'run-queued-1',
        task_type: 'daily_brief',
        status: 'queued',
        reason: null,
        trigger_source: 'manual',
        queued_at: '2026-04-21T10:00:00Z',
        started_at: null,
        finished_at: null,
        updated_at: '2026-04-21T10:01:00Z',
        worker_name: null,
        model: 'gpt-threat',
        parent_run_id: null,
        target_count: null,
        processed_count: 0,
        success_count: 0,
        error_count: 0,
        skipped_count: 0,
        daily_brief_id: null,
        metadata: {},
      },
    ],
    total: 1,
    limit: 10,
    offset: 0,
  }
  aiSettingsPageDomMocks.activeRunsLoading = false
  aiSettingsPageDomMocks.activeRunsRefreshing = false
  aiSettingsPageDomMocks.historyRunsDataByPage = {}
  aiSettingsPageDomMocks.runDetailById = {}
  aiSettingsPageDomMocks.childRunsDataByParent = {}
  aiSettingsPageDomMocks.promptHistoryData = []
  routerMocks.useBlocker.mockReset()
  routerMocks.useBlocker.mockImplementation(() => ({
    state: 'unblocked' as const,
    proceed: vi.fn(),
    reset: vi.fn(),
  }))
})

describe('AiSettingsPage DOM workflows', () => {
  it('keeps saved report limits visible beside a named provider with a large completion default', () => {
    aiSettingsPageDomMocks.includeNamedProvider = true
    const view = renderPage()
    act(() => getButton('Configuration')?.click())
    act(() => view.querySelector<HTMLButtonElement>('button[aria-label="Edit provider Large report model"]')!.click())
    expect(view.querySelector<HTMLInputElement>('input[aria-label="Provider default completion tokens"]')?.value).toBe('131072')
    const summary = view.querySelector('section[aria-labelledby="saved-report-budgets-title"]')!
    expect(summary.textContent).toContain('Initial completion1,200 tokens')
    expect(summary.textContent).toContain('Context window8,192 tokens')
    expect(summary.textContent).toContain('Safety margin15%')
    expect(summary.textContent).toContain('Changing a provider default does not change the report budgets')
    expect(summary.querySelector('[role="status"]')).toBeNull()
    expect(summary.previousElementSibling?.textContent).toContain('AI feature assignments')
    act(() => setInputValue(view.querySelector<HTMLInputElement>('input[aria-label="Provider default completion tokens"]')!, '65536'))
    expect(summary.textContent).toContain('Initial completion1,200 tokens')
    expect(summary.querySelector('[role="status"]')).toBeNull()
  })

  it('distinguishes unsaved report edits from saved values and reflects a saved settings refresh', () => {
    const view = renderPage()
    act(() => getButton('Configuration')?.click())
    const summary = view.querySelector('section[aria-labelledby="saved-report-budgets-title"]')!
    act(() => {
      setInputValue(view.querySelector<HTMLInputElement>('input[aria-label="Model Context Window"]')!, '262144')
      setInputValue(view.querySelector<HTMLInputElement>('input[aria-label="Initial report completion tokens"]')!, '32768')
    })
    expect(summary.textContent).toContain('1,200 tokens')
    expect(summary.textContent).toContain('8,192 tokens')
    expect(summary.textContent).not.toContain('32,768')
    expect(summary.querySelector('[role="status"]')?.textContent).toContain('Report budget edits are unsaved')
    // Simulate the server returning the saved values after a configuration refresh.
    aiSettingsPageDomMocks.settingsData = {
      ...aiSettingsPageDomMocks.settingsData,
      report_reserved_output_tokens: 32768,
      report_context_window_tokens: 262144,
    }
    act(() => root!.render(<MemoryRouter><AiSettingsPage /></MemoryRouter>))
    expect(summary.textContent).toContain('32,768 tokens')
    expect(summary.textContent).toContain('262,144 tokens')
    expect(summary.querySelector('[role="status"]')).toBeNull()
  })

  it('focuses the report controls through its named link after configuration unmounts and remounts', () => {
    const view = renderPage()
    for (let visit = 0; visit < 2; visit += 1) {
      act(() => getButton('Configuration')?.click())
      const link = view.querySelector<HTMLAnchorElement>('a[href="#ai-report-budget-controls"]')!
      const controls = view.querySelector<HTMLElement>('#ai-report-budget-controls')!
      expect(link.textContent).toBe('Review report budget controls')
      expect(controls.getAttribute('aria-label')).toBe('Report context guardrails')
      link.focus()
      act(() => link.click())
      expect(document.activeElement).toBe(controls)
      expect(controls.querySelector('input[aria-label="Initial report completion tokens"]')).not.toBeNull()
      act(() => getButton('Jobs')?.click())
      expect(controls.isConnected).toBe(false)
    }
  })

  it('does not substitute draft defaults when saved settings are unavailable', () => {
    aiSettingsPageDomMocks.settingsError = true
    const view = renderPage()
    act(() => getButton('Configuration')?.click())
    const summary = view.querySelector('section[aria-labelledby="saved-report-budgets-title"]')!
    expect(summary.textContent).toContain('Saved report budgets are unavailable until AI settings load')
    expect(summary.querySelector('dl')).toBeNull()
  })

  it('explains independent report and default completion budgets with accessible help', () => {
    const view = renderPage()
    act(() => getButton('Configuration')?.click())
    const initial = view.querySelector<HTMLInputElement>('input[aria-label="Initial report completion tokens"]')!
    const general = view.querySelector<HTMLInputElement>('input[aria-label="Default completion tokens"]')!
    expect(initial.value).toBe('1200')
    expect(general.value).toBe('4000')
    expect(initial.getAttribute('aria-describedby')).toContain('report-help-report_reserved_output_tokens')
    const reportHelp = view.querySelector('#report-help-report_reserved_output_tokens')?.textContent
    expect(reportHelp).toContain('every evidence batch and report section')
    expect(reportHelp).toContain('independent of the provider default')
    expect(reportHelp).toContain('131,072 tokens')
    expect(general.getAttribute('aria-describedby')).toContain('legacy-completion-token-help')
    expect(view.querySelector('#legacy-completion-token-help')?.textContent).toContain('article enrichment and daily briefs')
    expect(view.textContent).toContain('up to the greater of the report budget or provider default')
  })

  it('pauses AI configuration fields while their save is pending', () => {
    aiSettingsPageDomMocks.savePending = true
    const view = renderPage()
    act(() => getButton('Configuration')?.click())
    const fields = Array.from(view.querySelectorAll('fieldset input, fieldset select, fieldset textarea'))
    expect(fields.length).toBeGreaterThan(5)
    expect(fields.every((field) => field.matches(':disabled'))).toBe(true)
    expect(pageText()).toContain('Saving AI settings. Editing resumes')
    aiSettingsPageDomMocks.savePending = false
    act(() => root?.render(<MemoryRouter><AiSettingsPage /></MemoryRouter>))
    expect(view.querySelector('fieldset input')?.matches(':disabled')).toBe(false)
    const endpoint = view.querySelector<HTMLInputElement>('input[aria-label="Base URL"]')!
    expect(endpoint.getAttribute('aria-describedby')).toBe('legacy-provider-endpoint-help')
    expect(view.querySelector('#legacy-provider-endpoint-help')?.textContent).toContain('https://generativelanguage.googleapis.com/v1beta/openai/')
    expect(view.querySelector('#legacy-provider-endpoint-help')?.textContent).toContain(':generateContent')
    expect(view.querySelector('#legacy-provider-endpoint-help')?.textContent).toContain('AI_API_KEY_BASE_URL')
  })

  it('keeps select navigation through large viewports and switches to sidebar tabs at extra large', () => {
    const view = renderPage()
    const mobileSection = view.querySelector<HTMLSelectElement>('#mobile-ai-settings-section')
    const desktopTabs = view.querySelector<HTMLElement>('[aria-label="AI automation sections"]')
    const navigation = mobileSection?.closest('aside')
    const compactNavigation = mobileSection?.closest('label')?.parentElement
    const workspace = navigation?.parentElement

    expect(mobileSection).not.toBeNull()
    expect(Array.from(mobileSection?.options ?? []).map((option) => option.textContent)).toEqual([
      'Statistics',
      'Jobs',
      'Configuration',
    ])
    expect(mobileSection?.closest('label')?.className).toContain('xl:hidden')
    expect(mobileSection?.closest('label')?.className).not.toContain('lg:hidden')
    expect(mobileSection?.previousElementSibling?.className).toContain('sr-only')
    expect(compactNavigation?.className).toContain('md:grid-cols-[minmax(180px,0.75fr)_minmax(0,1.25fr)]')
    expect(compactNavigation?.className).toContain('xl:block')
    expect(desktopTabs?.className).toContain('hidden')
    expect(desktopTabs?.className).toContain('xl:grid')
    expect(desktopTabs?.className).not.toContain('lg:grid')
    expect(view.firstElementChild?.className).toContain('space-y-3')
    expect(navigation?.className).toContain('p-3')
    expect(workspace?.className).toContain('gap-3')
    expect(workspace?.className).toContain('xl:grid-cols-[280px_minmax(0,1fr)]')
    expect(workspace?.className).not.toContain('lg:grid-cols-[280px_minmax(0,1fr)]')
    expect(view.textContent).not.toContain('Review status, work with queued jobs, or change provider and feature settings.')
  })

  it('blocks queued AI work when the saved endpoint is not configured', () => {
    aiSettingsPageDomMocks.settingsData.ai_configured = false
    renderPage()

    act(() => {
      getButton('Jobs')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(getButton('Queue daily brief')?.hasAttribute('disabled')).toBe(true)
    expect(getButton('Queue reprocess')?.hasAttribute('disabled')).toBe(true)
  })

  it('blocks queued AI work when settings readiness cannot be loaded', () => {
    aiSettingsPageDomMocks.settingsError = true
    renderPage()

    act(() => {
      getButton('Jobs')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(pageText()).toContain('AI settings could not be loaded.')
    expect(getButton('Queue daily brief')?.hasAttribute('disabled')).toBe(true)
    expect(getButton('Queue reprocess')?.hasAttribute('disabled')).toBe(true)
  })

  it.each([
    { item_enrichment: false, daily_brief: true },
    { item_enrichment: true, daily_brief: false },
  ])('uses independent provider readiness for article and brief work: %j', (features) => {
    aiSettingsPageDomMocks.settingsData.effective_feature_configured = { ...features, report: true }
    renderPage()
    act(() => getButton('Jobs')?.click())
    expect(getButton('Queue daily brief')?.hasAttribute('disabled')).toBe(!features.daily_brief)
    expect(getButton('Queue reprocess')?.hasAttribute('disabled')).toBe(!features.item_enrichment)
    expect(pageText()).toContain(features.daily_brief ? 'The article enrichment provider is unavailable.' : 'The daily brief provider is unavailable.')
  })

  it('blocks saving AI settings when the saved settings failed to load', () => {
    aiSettingsPageDomMocks.settingsError = true
    renderPage()

    act(() => {
      getButton('Configuration')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(pageText()).toContain('AI settings could not be loaded. Refresh before saving changes.')
    expect(getButton('Save changes')?.hasAttribute('disabled')).toBe(true)
  })

  it('blocks no-op AI settings saves', () => {
    renderPage()

    act(() => {
      getButton('Configuration')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(pageText()).toContain('No AI settings changes to save.')
    expect(getButton('Save changes')?.hasAttribute('disabled')).toBe(true)
  })

  it('omits blank changed-field labels in AI prompt history', () => {
    aiSettingsPageDomMocks.promptHistoryData = [
      {
        id: 'audit-blank',
        action: 'ai.settings.update',
        actor_email: 'admin@example.com',
        created_at: '2026-05-01T22:23:00Z',
        metadata: { changed_fields: [] },
      },
      {
        id: 'audit-fields',
        action: 'ai.settings.update',
        actor_email: 'admin@example.com',
        created_at: '2026-05-01T22:24:00Z',
        metadata: { changed_fields: ['model', 'temperature'] },
      },
    ]
    renderPage()

    act(() => {
      getButton('Configuration')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    const changedFieldRows = Array.from(document.querySelectorAll('p')).filter((paragraph) =>
      paragraph.textContent?.trim().startsWith('Changed:'),
    )
    expect(changedFieldRows).toHaveLength(1)
    expect(changedFieldRows[0]?.textContent).toContain('Changed: model, temperature')
  })

  it('renders accessible tab and selection controls, then wires the queued-task cancellation dialog', () => {
    const view = renderPage()

    expect(view.textContent).toContain('AI statistics moved')
    expect(view.querySelector('a[href="/stats?section=ai"]')?.textContent).toContain('Open AI statistics')
    expect(view.querySelector('#ai-overview-window-days')).toBeNull()
    expect(view.querySelector('table')).toBeNull()

    const jobsTab = Array.from(view.querySelectorAll('button')).find((button) => button.textContent?.includes('Jobs'))
    expect(jobsTab).not.toBeNull()
    expect(view.querySelector('[role="tablist"]')).not.toBeNull()
    expect(jobsTab?.getAttribute('role')).toBe('tab')
    expect(jobsTab?.getAttribute('aria-selected')).toBe('false')

    act(() => {
      jobsTab!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(jobsTab?.getAttribute('aria-selected')).toBe('true')
    expect(view.querySelector(`[aria-labelledby="${jobsTab?.id}"]`)).not.toBeNull()

    const cancelButton = Array.from(view.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Remove From Queue'),
    )
    expect(cancelButton).not.toBeNull()

    act(() => {
      cancelButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(pageText()).toContain('Cancel AI task?')
    expect(pageText()).toContain('Daily Brief')

    const confirmCancelButton = Array.from(document.querySelectorAll('button'))
      .filter((button) => button.textContent?.includes('Remove From Queue'))
      .at(-1)
    expect(confirmCancelButton).not.toBeNull()

    act(() => {
      confirmCancelButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(aiSettingsPageDomMocks.cancelMutate).toHaveBeenCalledWith('run-queued-1')
    const notice = view.querySelector('[role="status"][aria-live="polite"][aria-atomic="true"]')
    expect(notice).not.toBeNull()
    expect(notice?.textContent).toContain('Daily Brief canceled.')
  })

  it('blocks reprocess queueing when the lookback scope becomes blank', () => {
    const view = renderPage()

    const jobsTab = getButton('Jobs')
    expect(jobsTab).not.toBeNull()

    act(() => {
      jobsTab!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    const lookbackInput = Array.from(view.querySelectorAll('label'))
      .find((label) => label.textContent?.includes('Reprocess lookback (days)'))
      ?.querySelector('input')
    const queueButton = Array.from(view.querySelectorAll('button')).find((button) => button.textContent?.includes('Queue reprocess'))

    expect(lookbackInput).not.toBeNull()
    expect(queueButton).not.toBeNull()
    expect(queueButton?.hasAttribute('disabled')).toBe(false)

    act(() => {
      setInputValue(lookbackInput as HTMLInputElement, '')
    })

    expect(pageText()).toContain('Lookback Days must be a whole number greater than 0')
    expect(queueButton?.hasAttribute('disabled')).toBe(true)
  })

  it('resets the reprocess scope after queueing work successfully', () => {
    const view = renderPage()

    act(() => {
      getButton('Jobs')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    const lookbackInput = getLabeledInput('Reprocess lookback (days)') as HTMLInputElement | null
    const startTimeInput = getLabeledInput('Start time') as HTMLInputElement | null
    const queueButton = getButton('Queue reprocess')

    expect(lookbackInput).not.toBeNull()
    expect(startTimeInput).not.toBeNull()
    expect(queueButton).not.toBeNull()

    act(() => {
      setInputValue(lookbackInput!, '14')
      setInputValue(startTimeInput!, '2026-04-21T08:30')
    })

    expect(lookbackInput?.value).toBe('14')
    expect(startTimeInput?.value).toBe('2026-04-21T08:30')

    act(() => {
      queueButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(aiSettingsPageDomMocks.reprocessMutate).toHaveBeenCalledWith(
      expect.objectContaining({
        days: null,
        limit: 100,
        end_time: null,
        feed_ids: [],
        item_ids: [],
      }),
    )
    expect(lookbackInput?.value).toBe('7')
    expect(startTimeInput?.value).toBe('')

    const notice = view.querySelector('[role="status"][aria-live="polite"][aria-atomic="true"]')
    expect(notice).not.toBeNull()
    expect(notice?.textContent).toContain('Queued AI reprocessing run run-reprocess-1.')
  })

  it('does not treat a submitted reprocess scope as unsaved while queueing is still pending', () => {
    aiSettingsPageDomMocks.completeReprocessMutation = false
    const view = renderPage()

    act(() => {
      getButton('Jobs')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    const startTimeInput = getLabeledInput('Start time') as HTMLInputElement | null
    const queueButton = getButton('Queue reprocess')

    expect(startTimeInput).not.toBeNull()
    expect(queueButton).not.toBeNull()

    act(() => {
      setInputValue(startTimeInput!, '2026-04-21T08:30')
    })

    expect(routerMocks.useBlocker).toHaveBeenLastCalledWith(true)

    act(() => {
      queueButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(aiSettingsPageDomMocks.reprocessMutate).toHaveBeenCalledTimes(1)
    expect(routerMocks.useBlocker).toHaveBeenLastCalledWith(false)
    expect(view.textContent).not.toContain('Discard unsaved changes?')
  })

  it('warns on blocked navigation when a reprocess scope is in progress', () => {
    const proceed = vi.fn()
    const reset = vi.fn()
    routerMocks.useBlocker.mockReturnValue({
      state: 'blocked' as const,
      proceed,
      reset,
    })

    renderPage()

    act(() => {
      getButton('Jobs')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    const startTimeInput = getLabeledInput('Start time') as HTMLInputElement | null
    expect(startTimeInput).not.toBeNull()

    act(() => {
      setInputValue(startTimeInput!, '2026-04-21T08:30')
    })

    expect(pageText()).toContain('Discard unsaved changes?')
    expect(pageText()).toContain('You have a reprocess scope in progress. Leave without queueing or clearing it?')

    act(() => {
      getButton('Discard changes')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(proceed).toHaveBeenCalledTimes(1)
    expect(reset).not.toHaveBeenCalled()
  })

  it('confirms before clearing a built reprocess scope', () => {
    renderPage()

    act(() => {
      getButton('Jobs')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    const lookbackInput = getLabeledInput('Reprocess lookback (days)') as HTMLInputElement | null
    expect(lookbackInput).not.toBeNull()

    act(() => {
      setInputValue(lookbackInput!, '14')
    })

    expect(lookbackInput?.value).toBe('14')

    act(() => {
      getButton('Reset scope')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(pageText()).toContain('Clear reprocess scope?')
    expect(lookbackInput?.value).toBe('14')

    act(() => {
      getButton('Clear scope')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(lookbackInput?.value).toBe('7')
  })

  it('keeps active-task loading distinct from a genuinely empty queue', () => {
    aiSettingsPageDomMocks.activeRunsLoading = true
    renderPage()

    act(() => {
      getButton('Jobs')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(pageText()).toContain('Checking queued and running AI tasks...')
    expect(pageText()).not.toContain('No queued or running top-level AI tasks right now.')
  })

  it('keeps active-task refreshes from adding layout-changing status text', () => {
    aiSettingsPageDomMocks.activeRunsRefreshing = true
    renderPage()

    act(() => {
      getButton('Jobs')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(pageText()).not.toContain('Refreshing active task state')
    expect(document.querySelector('[aria-busy="true"]')).not.toBeNull()
  })

  it('pages through AI run history instead of repeating the first page', () => {
    aiSettingsPageDomMocks.historyRunsDataByPage = {
      0: {
        items: Array.from({ length: 20 }, (_entry, index) =>
          createAiRun(`run-${index + 1}`, {
            item_title: `History run ${index + 1}`,
          }),
        ),
        total: 25,
        limit: 20,
        offset: 0,
      },
      1: {
        items: Array.from({ length: 5 }, (_entry, index) =>
          createAiRun(`run-${index + 21}`, {
            item_title: `History run ${index + 21}`,
          }),
        ),
        total: 25,
        limit: 20,
        offset: 20,
      },
    }
    renderPage()

    act(() => {
      getButton('Jobs')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(pageText()).toContain('History run 1')
    expect(pageText()).toContain('Showing 1-20 of 25')

    act(() => {
      getButton('Next')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(pageText()).toContain('History run 21')
    expect(pageText()).not.toContain('History run 1')
    expect(pageText()).toContain('Showing 21-25 of 25')
  })

  it('keeps an explicitly opened active run selected outside the current history window', () => {
    const pinnedRun = createAiRun('run-pinned', {
      status: 'queued',
      started_at: null,
      finished_at: null,
      actor_email: 'pinned-run@example.com',
      item_title: 'Pinned active run',
    })
    const historyRun = createAiRun('run-history', {
      actor_email: 'history-run@example.com',
      item_title: 'Visible history run',
    })
    const pinnedQueueRun = {
      ...aiSettingsPageDomMocks.queuedRunsData.items[0],
      id: 'run-pinned',
      model: 'gpt-threat',
    }
    aiSettingsPageDomMocks.queuedRunsData = {
      items: [pinnedQueueRun],
      total: 1,
      limit: 10,
      offset: 0,
    }
    aiSettingsPageDomMocks.historyRunsDataByPage = {
      0: {
        items: [historyRun],
        total: 1,
        limit: 20,
        offset: 0,
      },
    }
    aiSettingsPageDomMocks.runDetailById = {
      'run-pinned': { run: pinnedRun, events: [] },
      'run-history': { run: historyRun, events: [] },
    }
    renderPage()

    act(() => {
      getButton('Jobs')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    act(() => {
      getButton('Open Run')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(pageText()).toContain('pinned-run@example.com')
    expect(pageText()).not.toContain('history-run@example.com')
  })

  it('shows sanitized request and response summaries in the provider exchange view', () => {
    const run = createAiRun('run-provider-debug', {
      item_title: 'Provider debug article',
    })
    aiSettingsPageDomMocks.historyRunsDataByPage = {
      0: {
        items: [run],
        total: 1,
        limit: 20,
        offset: 0,
      },
    }
    aiSettingsPageDomMocks.runDetailById = {
      'run-provider-debug': {
        run,
        events: [
          {
            id: 'event-provider-debug',
            task_run_id: 'run-provider-debug',
            event_type: 'provider_exchange',
            message: null,
            created_at: '2026-04-21T10:00:02Z',
            payload: {
              request_url: 'https://api.example.com/v1/chat/completions',
              request_host: 'api.example.com',
              request_path: '/v1/chat/completions',
              request_model: 'gpt-threat',
              request_message_count: 2,
              request_message_roles: ['system', 'user'],
              request_prompt_chars: 1234,
              request_max_tokens: 4000,
              status_code: 200,
              response_body_chars: 2048,
              response_body_sha256: 'abc123',
              response_json_summary: {
                top_level_keys: ['choices', 'usage'],
                choices_count: 1,
                usage: {
                  prompt_tokens: 12,
                  completion_tokens: 8,
                  total_tokens: 20,
                },
              },
              attempt: 1,
              max_attempts: 3,
            },
          },
        ],
      },
    }
    renderPage()

    act(() => {
      getButton('Jobs')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    act(() => {
      getButton('Request / Response')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(pageText()).toContain('Provider Exchange')
    expect(pageText()).toContain('Raw prompt payload is redacted')
    expect(pageText()).toContain('gpt-threat')
    expect(pageText()).toContain('system, user')
    expect(pageText()).toContain('Raw provider response is redacted')
    expect(pageText()).toContain('2048')
    expect(pageText()).toContain('"top_level_keys"')
    expect(pageText()).toContain('"total_tokens": 20')
  })

  it('pauses connection testing while AI generation work is queued', () => {
    const view = renderPage()

    const configurationTab = Array.from(view.querySelectorAll('button')).find((button) => button.textContent?.trim() === 'Configuration')
    expect(configurationTab).not.toBeNull()

    act(() => {
      configurationTab!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    const testSavedConnectionButton = Array.from(view.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === 'Test saved connection',
    ) as HTMLButtonElement | undefined
    expect(testSavedConnectionButton).not.toBeUndefined()
    expect(testSavedConnectionButton?.disabled).toBe(true)
    expect(pageText()).toContain(
      '1 AI task is running or queued. Local providers such as Ollama usually process one generation at a time',
    )
  })

  it('blocks daily brief reprocessing beyond the retained brief limit', () => {
    const view = renderPage()

    act(() => {
      getButton('Jobs')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    const dailyBriefDaysInput = getLabeledInput('Daily brief lookback (days)') as HTMLInputElement | null
    expect(dailyBriefDaysInput).not.toBeNull()

    act(() => {
      setInputValue(dailyBriefDaysInput!, '11')
    })

    const queueDailyBriefButton = Array.from(view.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === 'Queue daily brief',
    ) as HTMLButtonElement | undefined
    expect(queueDailyBriefButton).not.toBeUndefined()
    expect(queueDailyBriefButton?.disabled).toBe(true)
    expect(pageText()).toContain('Increase retained daily briefings before reprocessing more than 10 days.')
  })

  it('queues daily brief reprocessing with the requested day count', () => {
    const view = renderPage()

    act(() => {
      getButton('Jobs')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    const dailyBriefDaysInput = getLabeledInput('Daily brief lookback (days)') as HTMLInputElement | null
    const queueDailyBriefButton = Array.from(view.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === 'Queue daily brief',
    ) as HTMLButtonElement | undefined

    expect(dailyBriefDaysInput).not.toBeNull()
    expect(dailyBriefDaysInput?.value).toBe('1')
    expect(pageText()).not.toContain('Daily Brief Backfill')
    expect(pageText()).not.toContain('Queue Backfill')
    expect(queueDailyBriefButton).not.toBeUndefined()

    act(() => {
      setInputValue(dailyBriefDaysInput!, '3')
    })

    act(() => {
      queueDailyBriefButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(aiSettingsPageDomMocks.backfillMutate).toHaveBeenCalledWith(3)
    const notice = view.querySelector('[role="status"][aria-live="polite"][aria-atomic="true"]')
    expect(notice).not.toBeNull()
    expect(notice?.textContent).toContain('Queued daily brief reprocessing for the last 3 days (run-backfill-1).')
  })

  it('labels daily brief parent and child reprocess runs by day', () => {
    const parentRun = createAiRun('run-backfill-parent', {
      task_type: 'reprocess',
      status: 'running',
      metadata: { scope: 'daily_brief_backfill', days: 3 },
      target_count: 3,
      processed_count: 1,
      success_count: 1,
      item_title: null,
      feed_name: null,
    })
    const childRun = createAiRun('run-backfill-child', {
      task_type: 'daily_brief',
      status: 'ready',
      parent_run_id: 'run-backfill-parent',
      daily_brief_id: 'brief-1',
      metadata: {
        scope: 'daily_brief_backfill',
        brief_date: '2026-04-20',
        reference_time: '2026-04-20T23:59:59Z',
      },
      item_title: null,
      feed_name: null,
    })
    aiSettingsPageDomMocks.historyRunsDataByPage = {
      0: {
        items: [parentRun],
        total: 1,
        limit: 20,
        offset: 0,
      },
    }
    aiSettingsPageDomMocks.runDetailById = {
      'run-backfill-parent': {
        run: parentRun,
        events: [],
      },
    }
    aiSettingsPageDomMocks.childRunsDataByParent = {
      'run-backfill-parent': {
        items: [childRun],
        total: 1,
        limit: 8,
        offset: 0,
      },
    }

    renderPage()

    act(() => {
      getButton('Jobs')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(pageText()).not.toContain('Daily Brief Backfill')
    expect(pageText()).toContain('Daily Brief')
    expect(pageText()).toContain('Reprocessing daily briefs for the last 3 days, ending today.')
    expect(pageText()).toContain('Daily Brief Runs')
    expect(pageText()).toContain('Daily brief for 20/04/2026')
    expect(pageText()).not.toContain('Unknown article')
  })

  it('labels connection testing as a saved-config action and blocks it while the draft is dirty', () => {
    clearActiveAiWork()
    const view = renderPage()

    const configurationTab = Array.from(view.querySelectorAll('button')).find((button) => button.textContent?.trim() === 'Configuration')
    expect(configurationTab).not.toBeNull()

    act(() => {
      configurationTab!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    const testSavedConnectionButton = Array.from(view.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === 'Test saved connection',
    ) as HTMLButtonElement | undefined
    expect(testSavedConnectionButton).not.toBeUndefined()
    expect(testSavedConnectionButton?.disabled).toBe(false)
    expect(pageText()).toContain('Test the saved provider configuration. Unsaved draft changes are not included.')

    const baseUrlInput = Array.from(view.querySelectorAll('input')).find(
      (input) => input.value === 'https://api.example.com/v1',
    ) as HTMLInputElement | undefined
    expect(baseUrlInput).not.toBeUndefined()

    act(() => {
      setInputValue(baseUrlInput!, 'https://draft.example.com/v1')
    })

    expect(testSavedConnectionButton?.disabled).toBe(true)
    expect(pageText()).toContain(
      'Save your draft changes first. Test saved connection only checks the last saved provider settings.',
    )
  })

  it('blocks queue actions while provider settings have unsaved changes', () => {
    const view = renderPage()

    act(() => {
      getButton('Configuration')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    const baseUrlInput = Array.from(view.querySelectorAll('input')).find(
      (input) => input.value === 'https://api.example.com/v1',
    ) as HTMLInputElement | undefined
    expect(baseUrlInput).not.toBeUndefined()

    act(() => {
      setInputValue(baseUrlInput!, 'http://localhost:11434/v1')
    })

    act(() => {
      getButton('Jobs')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(pageText()).toContain(
      'Save your AI settings changes before queueing manual AI work. Queued jobs use the last saved provider configuration.',
    )
    expect(getButton('Queue daily brief')?.hasAttribute('disabled')).toBe(true)
    expect(getButton('Queue reprocess')?.hasAttribute('disabled')).toBe(true)

    act(() => {
      getButton('Queue reprocess')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(aiSettingsPageDomMocks.reprocessMutate).not.toHaveBeenCalled()
  })
})
