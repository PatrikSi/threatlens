// @vitest-environment jsdom

import { act } from 'react'
import { createRoot, Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const aiSettingsPageDomMocks = vi.hoisted(() => ({
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
    provider_type: 'openai_compatible',
    base_url: 'https://api.example.com/v1',
    model: 'gpt-threat',
    temperature: 0.2,
    max_completion_tokens: 4000,
    request_timeout_seconds: 120,
    request_max_retries: 2,
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
  emptyItemsData: {
    items: [],
    total: 0,
    page: 1,
    page_size: 12,
  },
  cancelMutate: vi.fn(),
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

vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => aiSettingsPageDomMocks.queryClient,
  useQuery: ({ queryKey }: { queryKey: unknown[] }) => {
    const key = queryKey.join(':')
    const baseResult = {
      isLoading: false,
      isError: false,
      error: null,
      data: undefined,
    }

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
        data: aiSettingsPageDomMocks.liveData,
      }
    }

    if (key === 'ai:ops:runs:queued-top') {
      return {
        ...baseResult,
        data: aiSettingsPageDomMocks.queuedRunsData,
      }
    }

    if (key === 'ai:ops:runs:running-top') {
      return {
        ...baseResult,
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
        data: [],
      }
    }

    if (String(queryKey[0]) === 'ai' && String(queryKey[1]) === 'ops' && String(queryKey[2]) === 'runs' && key.includes(':30:')) {
      return {
        ...baseResult,
        data: aiSettingsPageDomMocks.emptyRunsData,
      }
    }

    return baseResult
  },
  useMutation: (options: {
    mutationKey?: unknown
    onMutate?: (value: string) => void
    onSuccess?: (result: unknown, value: unknown) => void
    onSettled?: () => void
  }) => {
    const mutationKey = Array.isArray(options?.mutationKey) ? options.mutationKey.join(':') : String(options?.mutationKey ?? '')
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
          aiSettingsPageDomMocks.reprocessMutate(payload)
          options.onSuccess?.({ task_id: 'task-reprocess-1' }, payload)
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

let root: Root | null = null
let container: HTMLDivElement | null = null

function renderPage() {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => {
    root?.render(<AiSettingsPage />)
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

afterEach(() => {
  act(() => {
    root?.unmount()
  })
  root = null
  container?.remove()
  container = null
  document.body.innerHTML = ''
  aiSettingsPageDomMocks.cancelMutate.mockReset()
  aiSettingsPageDomMocks.reprocessMutate.mockReset()
  aiSettingsPageDomMocks.settingsData.ai_configured = true
  aiSettingsPageDomMocks.settingsError = false
  routerMocks.useBlocker.mockReset()
  routerMocks.useBlocker.mockImplementation(() => ({
    state: 'unblocked' as const,
    proceed: vi.fn(),
    reset: vi.fn(),
  }))
})

describe('AiSettingsPage DOM workflows', () => {
  it('blocks queued AI work when the saved endpoint is not configured', () => {
    aiSettingsPageDomMocks.settingsData.ai_configured = false
    renderPage()

    act(() => {
      getButton('Jobs')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(getButton('Queue Daily Brief')?.hasAttribute('disabled')).toBe(true)
    expect(getButton('Queue Reprocess')?.hasAttribute('disabled')).toBe(true)
  })

  it('blocks queued AI work when settings readiness cannot be loaded', () => {
    aiSettingsPageDomMocks.settingsError = true
    renderPage()

    act(() => {
      getButton('Jobs')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(pageText()).toContain('AI settings could not be loaded.')
    expect(getButton('Queue Daily Brief')?.hasAttribute('disabled')).toBe(true)
    expect(getButton('Queue Reprocess')?.hasAttribute('disabled')).toBe(true)
  })

  it('renders accessible tab and selection controls, then wires the queued-task cancellation dialog', () => {
    const view = renderPage()

    expect(view.querySelector('label[for="ai-overview-window-days"]')?.textContent).toContain('Overview time window')
    expect(view.querySelector<HTMLSelectElement>('#ai-overview-window-days')?.getAttribute('aria-label')).toBe(
      'Overview time window',
    )

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
      .find((label) => label.textContent?.includes('Lookback Days'))
      ?.querySelector('input')
    const queueButton = Array.from(view.querySelectorAll('button')).find((button) => button.textContent?.includes('Queue Reprocess'))

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

    const lookbackInput = getLabeledInput('Lookback Days') as HTMLInputElement | null
    const startTimeInput = getLabeledInput('Start Time') as HTMLInputElement | null
    const queueButton = getButton('Queue Reprocess')

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
    expect(notice?.textContent).toContain('Queued AI reprocessing task task-reprocess-1.')
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

    const startTimeInput = getLabeledInput('Start Time') as HTMLInputElement | null
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

    const lookbackInput = getLabeledInput('Lookback Days') as HTMLInputElement | null
    expect(lookbackInput).not.toBeNull()

    act(() => {
      setInputValue(lookbackInput!, '14')
    })

    expect(lookbackInput?.value).toBe('14')

    act(() => {
      getButton('Clear Scope')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(pageText()).toContain('Clear reprocess scope?')
    expect(lookbackInput?.value).toBe('14')

    act(() => {
      getButton('Clear scope')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(lookbackInput?.value).toBe('7')
  })

  it('labels connection testing as a saved-config action and blocks it while the draft is dirty', () => {
    const view = renderPage()

    const configurationTab = Array.from(view.querySelectorAll('button')).find((button) => button.textContent?.trim() === 'Configuration')
    expect(configurationTab).not.toBeNull()

    act(() => {
      configurationTab!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    const testSavedConnectionButton = Array.from(view.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === 'Test Saved Connection',
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
      'Save your draft changes first. Test Saved Connection only checks the last saved provider settings.',
    )
  })
})
