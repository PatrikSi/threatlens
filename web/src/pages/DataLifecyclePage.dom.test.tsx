// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api/client'
import type {
  LifecycleOverviewResponse,
  LifecyclePreview,
  LifecycleRun,
} from '../types/lifecycle'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const lifecycleMocks = vi.hoisted(() => ({
  canWrite: true,
  meError: null as Error | null,
  meRefetch: vi.fn(),
  loadOverview: vi.fn(),
  loadRuns: vi.fn(),
  previewPolicy: vi.fn(),
  updatePolicy: vi.fn(),
  startRun: vi.fn(),
  cancelRun: vi.fn(),
}))

vi.mock('../hooks/useCurrentUser', () => ({
  useCurrentUser: () => ({
    data: lifecycleMocks.meError ? undefined : {
      id: 'operator-1',
      email: 'operator@example.com',
      role: 'admin',
      access: {
        permissions: lifecycleMocks.canWrite
          ? ['read:operations', 'write:operations']
          : ['read:operations'],
        durable_permissions: lifecycleMocks.canWrite
          ? ['read:operations', 'write:operations']
          : ['read:operations'],
      },
      authentication: {
        credential_kind: 'opaque_session',
        recently_authenticated: true,
        sensitive_actions_ready: true,
      },
    },
    isLoading: false,
    isError: Boolean(lifecycleMocks.meError),
    error: lifecycleMocks.meError,
    refetch: lifecycleMocks.meRefetch,
  }),
}))

vi.mock('./lifecycleApi', async (importOriginal) => ({
  ...await importOriginal<typeof import('./lifecycleApi')>(),
  loadLifecycleOverview: lifecycleMocks.loadOverview,
  loadLifecycleRuns: lifecycleMocks.loadRuns,
  previewLifecyclePolicy: lifecycleMocks.previewPolicy,
  updateLifecyclePolicy: lifecycleMocks.updatePolicy,
  startLifecycleRun: lifecycleMocks.startRun,
  cancelLifecycleRun: lifecycleMocks.cancelRun,
}))

import { DataLifecyclePage } from './DataLifecyclePage'

let root: Root | null = null
let container: HTMLDivElement | null = null

beforeEach(() => {
  lifecycleMocks.canWrite = true
  lifecycleMocks.meError = null
  lifecycleMocks.loadOverview.mockResolvedValue(overviewFixture())
  lifecycleMocks.loadRuns.mockResolvedValue({
    runs: [runFixture()],
    total: 1,
    page: 1,
    page_size: 25,
  })
  lifecycleMocks.previewPolicy.mockResolvedValue(previewFixture())
  lifecycleMocks.updatePolicy.mockResolvedValue({
    ...overviewFixture().targets[0].policy,
    revision: 4,
  })
  lifecycleMocks.startRun.mockResolvedValue(runFixture())
  lifecycleMocks.cancelRun.mockResolvedValue({
    ...runFixture(),
    status: 'running',
    cancel_requested: true,
    cancellation_requested_at: '2026-09-02T09:03:00Z',
    cancellation_requested_by: 'admin@example.com',
    cancellation_reason: 'Approved operator cancellation request',
  })
})

afterEach(() => {
  act(() => root?.unmount())
  root = null
  container?.remove()
  container = null
  document.body.innerHTML = ''
  vi.clearAllMocks()
})

describe('DataLifecyclePage', () => {
  it('lets read-only operators generate aggregate previews without exposing mutations', async () => {
    lifecycleMocks.canWrite = false
    const view = await renderPage('/settings/lifecycle')

    expect(view.textContent).toContain('Read-only access')
    act(() => findButton(view, 'Configure Fetched article content')?.click())

    const previewButton = findButton(view, 'Preview impact')
    expect(previewButton?.disabled).toBe(false)
    expect(findButton(view, 'Save policy')?.disabled).toBe(true)
    expect(findButton(view, 'Run cleanup')?.disabled).toBe(true)

    await act(async () => {
      previewButton?.click()
      await flushPromises()
    })
    expect(lifecycleMocks.previewPolicy).toHaveBeenCalledOnce()
    expect(view.textContent).toContain('24')
    expect(view.textContent).toContain('No records were changed')

    act(() => findButton(view, 'Close Fetched article content')?.click())
    const summaryRow = findButton(view, 'Configure Fetched article content')
      ?.closest('tr')
    expect(summaryRow?.textContent).toContain('24 / 3')
    expect(summaryRow?.textContent).not.toContain('Preview required')
  })

  it('requires a matching preview and destructive confirmation when a safeguard is disabled', async () => {
    const view = await renderPage('/settings/lifecycle')
    act(() => findButton(view, 'Configure Fetched article content')?.click())
    const safeguard = Array.from(view.querySelectorAll('label')).find(
      (label) => label.textContent?.includes('Keep starred articles'),
    )?.querySelector<HTMLInputElement>('input')

    act(() => safeguard?.click())
    expect(findButton(view, 'Save policy')?.disabled).toBe(true)

    await act(async () => {
      findButton(view, 'Preview impact')?.click()
      await flushPromises()
    })
    expect(lifecycleMocks.previewPolicy.mock.calls[0][0].draft.options).toEqual({
      protect_starred: false,
    })
    expect(findButton(view, 'Save policy')?.disabled).toBe(false)

    act(() => findButton(view, 'Save policy')?.click())
    expect(document.body.textContent).toContain('Type PURGE to confirm')
    expect(document.body.textContent).toContain(
      'Disabled safeguards: Keep starred articles',
    )
    expect(document.body.textContent).toContain('Eligible records')
    expect(document.body.textContent).toContain('Next scheduled run')

    const dialog = document.body.querySelector<HTMLElement>('[role="alertdialog"]')
    act(() => {
      setTextAreaValue(
        dialog!.querySelector('textarea')!,
        'Apply approved retention standard',
      )
      setInputValue(dialog!.querySelector('input')!, 'PURGE')
    })
    await act(async () => {
      findButton(dialog!, 'Save destructive policy')?.click()
      await flushPromises()
    })
    expect(lifecycleMocks.updatePolicy).toHaveBeenCalledWith(
      'article_content',
      expect.objectContaining({
        confirmation: 'PURGE',
        preview_id: 'preview-1',
        reason: 'Apply approved retention standard',
      }),
      expect.any(String),
    )
  })

  it('keeps local drafts when a policy row is collapsed and reopened', async () => {
    const view = await renderPage('/settings/lifecycle')
    act(() => findButton(view, 'Configure Fetched article content')?.click())
    const retention = Array.from(view.querySelectorAll('label')).find(
      (label) => label.textContent?.includes('Retain for (days)'),
    )?.querySelector<HTMLInputElement>('input')

    act(() => setInputValue(retention!, '300'))
    act(() => findButton(view, 'Close Fetched article content')?.click())
    act(() => findButton(view, 'Configure Fetched article content')?.click())

    expect(retention?.value).toBe('300')
    expect(findButton(view, 'Discard changes')).toBeDefined()
  })

  it('marks expired previews and does not leave a successful preview message', async () => {
    lifecycleMocks.previewPolicy.mockResolvedValue({
      ...previewFixture(),
      expires_at: '2020-01-01T00:00:00Z',
    })
    const view = await renderPage('/settings/lifecycle')
    act(() => findButton(view, 'Configure Fetched article content')?.click())
    const safeguard = Array.from(view.querySelectorAll('label')).find(
      (label) => label.textContent?.includes('Keep starred articles'),
    )?.querySelector<HTMLInputElement>('input')
    act(() => safeguard?.click())

    await act(async () => {
      findButton(view, 'Preview impact')?.click()
      await flushPromises()
      await flushPromises()
    })

    expect(view.textContent).toContain('Expired')
    expect(view.textContent).not.toContain('Preview generated. No records were changed.')
    expect(findButton(view, 'Save policy')?.disabled).toBe(true)
  })

  it('shows bounded run impact and clears hidden history filters after starting', async () => {
    lifecycleMocks.previewPolicy.mockResolvedValue({
      ...previewFixture(),
      count_is_lower_bound: true,
      is_partial: true,
    })
    const view = await renderPage('/settings/lifecycle?target=article_content&status=failed&trigger=scheduled')
    act(() => findButton(view, 'Configure Fetched article content')?.click())
    await act(async () => {
      findButton(view, 'Preview impact')?.click()
      await flushPromises()
    })
    act(() => findButton(view, 'Run cleanup')?.click())

    const dialog = document.body.querySelector<HTMLElement>('[role="alertdialog"]')
    expect(dialog?.textContent).toContain('At least 24')
    expect(dialog?.textContent).toContain('Run cap')
    expect(dialog?.textContent).toContain('10,000 records')
    act(() => {
      setTextAreaValue(dialog!.querySelector('textarea')!, 'Approved manual cleanup run')
      setInputValue(dialog!.querySelector('input')!, 'PURGE')
    })
    await act(async () => {
      findButton(dialog!, 'Queue cleanup')?.click()
      await flushPromises()
      await flushPromises()
    })

    expect(lifecycleMocks.loadRuns).toHaveBeenLastCalledWith(expect.objectContaining({
      targetKey: undefined,
      status: undefined,
      trigger: undefined,
    }))

    act(() => findButton(view, 'Policies')?.click())
    expect(findButton(view, 'Run cleanup')?.disabled).toBe(true)
    expect(view.textContent).toContain('Generate a server-side preview before cleanup')
  })

  it('drops stale target filters before loading run history', async () => {
    await renderPage('/settings/lifecycle?tab=history&target=retired_dataset')

    expect(lifecycleMocks.loadRuns).toHaveBeenCalled()
    for (const [request] of lifecycleMocks.loadRuns.mock.calls) {
      expect(request.targetKey).toBeUndefined()
    }
  })

  it('fails closed and explains when the current access state cannot be loaded', async () => {
    lifecycleMocks.meError = new Error('identity endpoint unavailable')
    const view = await renderPage('/settings/lifecycle')

    expect(view.textContent).toContain('Changes are locked')
    expect(view.textContent).toContain('identity endpoint unavailable')
    act(() => findButton(view, 'Configure Fetched article content')?.click())
    expect(findButton(view, 'Save policy')?.disabled).toBe(true)
    act(() => findButton(view, 'Retry access check')?.click())
    expect(lifecycleMocks.meRefetch).toHaveBeenCalledOnce()
  })

  it('keeps destructive save errors and conflict recovery inside the dialog', async () => {
    lifecycleMocks.updatePolicy.mockRejectedValue(new ApiError(
      'The lifecycle policy changed; reload before saving.',
      409,
      '/operations/lifecycle/policies/article_content',
    ))
    const view = await renderPage('/settings/lifecycle')
    act(() => findButton(view, 'Configure Fetched article content')?.click())
    const safeguard = Array.from(view.querySelectorAll('label')).find(
      (label) => label.textContent?.includes('Keep starred articles'),
    )?.querySelector<HTMLInputElement>('input')
    act(() => safeguard?.click())
    await act(async () => {
      findButton(view, 'Preview impact')?.click()
      await flushPromises()
    })
    act(() => findButton(view, 'Save policy')?.click())
    const dialog = document.body.querySelector<HTMLElement>('[role="alertdialog"]')
    act(() => {
      setTextAreaValue(dialog!.querySelector('textarea')!, 'Apply approved retention standard')
      setInputValue(dialog!.querySelector('input')!, 'PURGE')
    })
    await act(async () => {
      findButton(dialog!, 'Save destructive policy')?.click()
      await flushPromises()
    })

    expect(dialog?.textContent).toContain('The lifecycle policy changed')
    expect(findButton(dialog!, 'Discard draft and reload server policy')).toBeDefined()
  })

  it('keeps an expired-preview conflict recoverable without discarding the policy draft', async () => {
    lifecycleMocks.updatePolicy.mockRejectedValue(new ApiError(
      'This lifecycle preview expired; create a new preview.',
      409,
      '/operations/lifecycle/policies/article_content',
      null,
      { code: 'lifecycle_conflict' },
    ))
    const view = await renderPage('/settings/lifecycle')
    act(() => findButton(view, 'Configure Fetched article content')?.click())
    const safeguard = Array.from(view.querySelectorAll('label')).find(
      (label) => label.textContent?.includes('Keep starred articles'),
    )?.querySelector<HTMLInputElement>('input')
    act(() => safeguard?.click())
    await act(async () => {
      findButton(view, 'Preview impact')?.click()
      await flushPromises()
    })
    act(() => findButton(view, 'Save policy')?.click())
    const dialog = document.body.querySelector<HTMLElement>('[role="alertdialog"]')
    act(() => {
      setTextAreaValue(dialog!.querySelector('textarea')!, 'Apply approved retention standard')
      setInputValue(dialog!.querySelector('input')!, 'PURGE')
    })
    await act(async () => {
      findButton(dialog!, 'Save destructive policy')?.click()
      await flushPromises()
    })

    expect(dialog?.textContent).toContain('This lifecycle preview expired')
    expect(findButton(dialog!, 'Discard draft and reload server policy')).toBeUndefined()
    act(() => findButton(dialog!, 'Cancel')?.click())
    expect(findButton(view, 'Discard changes')).toBeDefined()
    expect(view.textContent).not.toContain('This policy changed on the server')
  })

  it('shows captured policy context and prevents duplicate cancellation requests', async () => {
    const run = runFixture()
    run.cancel_requested = true
    run.cancellation_requested_at = '2026-09-02T09:02:00Z'
    run.cancellation_requested_by = 'operator@example.com'
    run.cancellation_reason = 'Unexpected source impact observed'
    run.details = {
      continuation_count: 2,
      transient_retry_count: 1,
      remaining_count_is_lower_bound: true,
    }
    lifecycleMocks.loadRuns.mockResolvedValue({
      runs: [run],
      total: 1,
      page: 1,
      page_size: 25,
    })
    const view = await renderPage('/settings/lifecycle?tab=history')

    expect(view.textContent).toContain('Cancellation requested')
    expect(findButton(view, 'Cancel')).toBeUndefined()
    act(() => Array.from(view.querySelectorAll<HTMLButtonElement>('button')).find(
      (button) => button.getAttribute('aria-label')?.startsWith('Show details'),
    )?.click())
    expect(view.textContent).toContain('Captured retention')
    expect(view.textContent).toContain('365 days')
    expect(view.textContent).toContain('10,000')
    expect(view.textContent).toContain('Keep starred articles')
    expect(view.textContent).toContain('Unexpected source impact observed')
    expect(view.textContent).toContain('2 continuations')
    expect(view.textContent).toContain('At least 10')
  })

  it('refreshes stale run state after a cancellation conflict', async () => {
    lifecycleMocks.cancelRun.mockRejectedValue(new ApiError(
      'Only queued or running lifecycle runs can be cancelled.',
      409,
      '/operations/lifecycle/runs/run-1/cancel',
      null,
      { code: 'lifecycle_conflict' },
    ))
    const view = await renderPage('/settings/lifecycle?tab=history')
    act(() => findButton(view, 'Cancel')?.click())
    const dialog = document.body.querySelector<HTMLElement>('[role="alertdialog"]')
    act(() => setTextAreaValue(
      dialog!.querySelector('textarea')!,
      'Cancel after detecting changed run state',
    ))
    await act(async () => {
      findButton(dialog!, 'Request cancellation')?.click()
      await flushPromises()
    })

    expect(dialog?.textContent).toContain('Only queued or running')
    await act(async () => {
      findButton(dialog!, 'Refresh run history')?.click()
      await flushPromises()
    })
    expect(document.body.querySelector('[role="alertdialog"]')).toBeNull()
    expect(lifecycleMocks.loadRuns.mock.calls.length).toBeGreaterThan(1)
  })
})

async function renderPage(path: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const router = createMemoryRouter([
    { path: '/settings/lifecycle', element: <DataLifecyclePage /> },
  ], { initialEntries: [path] })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => {
    root?.render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    )
  })
  for (let attempt = 0; attempt < 8; attempt += 1) {
    await act(async () => {
      await flushPromises()
    })
  }
  return container
}

function findButton(view: ParentNode, text: string) {
  return Array.from(view.querySelectorAll<HTMLButtonElement>('button')).find(
    (button) => button.textContent?.trim() === text
      || button.getAttribute('aria-label')?.startsWith(text),
  )
}

function flushPromises() {
  return new Promise((resolve) => window.setTimeout(resolve, 0))
}

function setInputValue(input: HTMLInputElement, value: string) {
  const descriptor = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype,
    'value',
  )
  descriptor?.set?.call(input, value)
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

function setTextAreaValue(input: HTMLTextAreaElement, value: string) {
  const descriptor = Object.getOwnPropertyDescriptor(
    window.HTMLTextAreaElement.prototype,
    'value',
  )
  descriptor?.set?.call(input, value)
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

function overviewFixture(): LifecycleOverviewResponse {
  return {
    generated_at: '2026-09-02T09:00:00Z',
    targets: [{
      key: 'article_content',
      label: 'Fetched article content',
      category: 'intelligence',
      description: 'Remove extracted article payloads while preserving source records.',
      action_description: 'Purges extracted payload fields in place.',
      cutoff_description: 'Article publication time, falling back to first-seen time.',
      min_retention_days: 7,
      max_retention_days: 3650,
      default_retention_days: 365,
      safeguards: [{
        key: 'protect_starred',
        label: 'Keep starred articles',
        description: 'Retain content for starred articles.',
        default_enabled: true,
      }],
      policy: {
        target_key: 'article_content',
        enabled: true,
        retention_days: 365,
        schedule_cadence: 'daily',
        schedule_hour_utc: 2,
        schedule_weekday: null,
        max_records_per_run: 10_000,
        options: { protect_starred: true },
        revision: 3,
        next_run_at: '2026-09-03T02:00:00Z',
        last_run_at: null,
        last_run_status: null,
        configuration_updated_at: '2026-09-02T08:00:00Z',
        updated_at: '2026-09-02T08:00:00Z',
        updated_by: 'admin@example.com',
      },
      latest_preview: null,
    }],
  }
}

function previewFixture(): LifecyclePreview {
  return {
    id: 'preview-1',
    target_key: 'article_content',
    policy_revision: 3,
    cutoff_at: '2025-09-02T09:00:00Z',
    eligible_count: 24,
    protected_count: 3,
    protected_counts: { starred: 3 },
    oldest_candidate_at: '2024-01-01T00:00:00Z',
    eligible_bytes: 4096,
    count_is_lower_bound: false,
    is_partial: false,
    generated_at: '2026-09-02T09:00:00Z',
    expires_at: '2099-09-02T09:15:00Z',
    observed_at: '2026-09-02T09:00:00Z',
  }
}

function runFixture(): LifecycleRun {
  return {
    id: 'run-1',
    target_key: 'article_content',
    trigger_source: 'manual',
    status: 'running',
    policy_revision: 3,
    policy_snapshot: {
      retention_days: 365,
      schedule_cadence: 'daily',
      schedule_hour_utc: 2,
      options: { protect_starred: true },
    },
    cutoff_at: '2025-09-02T09:00:00Z',
    scheduled_for: null,
    max_records: 10_000,
    reason: 'Apply approved retention policy',
    requested_by: 'admin@example.com',
    evaluated_count: 100,
    affected_count: 24,
    affected_bytes: 4096,
    protected_count: 3,
    skipped_count: 0,
    batch_count: 1,
    remaining_count: 10,
    details: {},
    stop_reason: null,
    error_code: null,
    error_message: null,
    cancel_requested: false,
    cancellation_requested_at: null,
    cancellation_requested_by: null,
    cancellation_reason: null,
    queued_at: '2026-09-02T09:01:00Z',
    started_at: '2026-09-02T09:01:10Z',
    heartbeat_at: '2026-09-02T09:02:00Z',
    finished_at: null,
    created_at: '2026-09-02T09:01:00Z',
    updated_at: '2026-09-02T09:02:00Z',
  }
}
