// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { apiFetch } from '../api/client'
import type { LifecycleOverviewResponse, LifecycleRun } from '../types/lifecycle'
import { DataLifecyclePage } from './DataLifecyclePage'
import { deferred, intelButton, mountIntel } from './articleIntelligenceTestSupport'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...await original<typeof import('../api/client')>(), apiFetch: vi.fn() }))
vi.mock('../hooks/useCurrentUser', () => ({ useCurrentUser: () => ({
  data: { id: 'operator', role: 'admin', access: { permissions: ['read:operations', 'write:operations'], durable_permissions: ['read:operations', 'write:operations'] },
    authentication: { credential_kind: 'opaque_session', recently_authenticated: true, sensitive_actions_ready: true } },
  isLoading: false, isError: false, error: null, refetch: vi.fn(),
}) }))
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
afterEach(() => { view?.close(); view = undefined; vi.resetAllMocks() })
function enter(control: HTMLInputElement | HTMLTextAreaElement, value: string) {
  const prototype = control instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype
  Object.getOwnPropertyDescriptor(prototype, 'value')!.set!.call(control, value)
  control.dispatchEvent(new Event('input', { bubbles: true }))
}

it.each(['/other', '/?tab=history&status=failed'])('keeps navigation to %s after cleanup queueing when its acknowledgment arrives', async (destination) => {
  const overview = overviewFixture()
  overview.targets[0].latest_preview = {
    id: 'current-preview', target_key: 'article_content', policy_revision: 3,
    cutoff_at: '2025-11-15T10:00:00Z', eligible_count: 24, protected_count: 3, protected_counts: { starred: 3 },
    oldest_candidate_at: null, eligible_bytes: 0, count_is_lower_bound: false, is_partial: false,
    generated_at: '2026-11-15T10:00:00Z', expires_at: '2099-11-15T10:15:00Z', observed_at: '2026-11-15T10:00:00Z',
  }
  const queued = deferred<LifecycleRun>()
  vi.mocked(apiFetch).mockImplementation((path, init) => init?.method === 'POST' ? queued.promise as never
    : Promise.resolve(path.startsWith('/operations/lifecycle/runs?')
      ? { runs: path.includes('status=failed') ? [] : [runFixture()], total: path.includes('status=failed') ? 0 : 1, page: 1, page_size: 25 }
      : overview) as never)
  view = await mountIntel(<DataLifecyclePage />)
  await vi.waitFor(() => expect(view!.host.textContent).toContain('Fetched article content'))
  act(() => view!.host.querySelector<HTMLButtonElement>('[aria-label="Configure Fetched article content lifecycle policy"]')!.click())
  expect(intelButton(view.host, 'Run cleanup').disabled).toBe(false)
  act(() => intelButton(view!.host, 'Run cleanup').click())
  const dialog = document.querySelector('[role=alertdialog]')!
  act(() => { enter(dialog.querySelector('textarea')!, 'Approved cleanup in an operator review'); enter(dialog.querySelector('input')!, 'PURGE') })
  expect(intelButton(dialog, 'Queue cleanup').disabled).toBe(false)
  act(() => intelButton(dialog, 'Queue cleanup').click())
  await vi.waitFor(() => expect(vi.mocked(apiFetch).mock.calls.some(([path, init]) => path === '/operations/lifecycle/runs' && init?.method === 'POST')).toBe(true))
  await act(async () => { await view!.router.navigate(destination) })
  const expected = new URL(destination, 'https://local.test')
  if (destination === '/other') await vi.waitFor(() => expect(view!.host.textContent).toContain('Other page'))
  else await vi.waitFor(() => expect(view!.host.querySelector('[aria-labelledby="lifecycle-history-heading"]')?.parentElement?.hasAttribute('hidden')).toBe(false))
  await act(async () => queued.resolve(runFixture()))
  await vi.waitFor(() => expect(view!.client.getMutationCache().getAll().some((mutation) => mutation.state.status === 'success')).toBe(true))
  expect(view.router.state.location.pathname).toBe(expected.pathname)
  expect(view.router.state.location.search).toBe(expected.search)
  if (destination === '/other') expect(view.host.textContent).toContain('Other page')
})

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
