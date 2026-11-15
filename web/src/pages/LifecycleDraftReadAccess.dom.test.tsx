// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import type { LifecycleOverviewResponse, LifecyclePolicy, LifecyclePreview } from '../types/lifecycle'
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
function retentionField() {
  const field = [...view!.host.querySelectorAll('label')].find((entry) => entry.textContent?.startsWith('Retain for (days)'))?.querySelector('input')
  expect(field).toBeDefined()
  return field!
}
async function editPolicy() {
  vi.mocked(apiFetch).mockResolvedValue(overviewFixture())
  view = await mountIntel(<DataLifecyclePage />)
  await vi.waitFor(() => expect(view!.host.textContent).toContain('Fetched article content'))
  act(() => view!.host.querySelector<HTMLButtonElement>('[aria-label="Configure Fetched article content lifecycle policy"]')!.click())
  act(() => {
    const input = retentionField()
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, '500')
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
  expect(retentionField().value).toBe('500')
}

it('keeps a dirty lifecycle draft while retiring its saved baseline through denial and recovery', async () => {
  await editPolicy()
  for (const status of [403, 503]) {
    vi.mocked(apiFetch).mockRejectedValue(new ApiError(`Lifecycle catalog unavailable ${status}`, status, '/operations/lifecycle'))
    await act(async () => { await view!.client.invalidateQueries({ queryKey: ['operations', 'lifecycle'], exact: true }) })
    await vi.waitFor(() => expect((view!.client.getQueryState(['operations', 'lifecycle'])?.error as ApiError)?.status).toBe(status))
    await vi.waitFor(() => expect(view!.host.textContent).toContain(`Lifecycle catalog unavailable ${status}`))
    expect(retentionField().value).toBe('500')
    expect(view!.host.textContent).not.toContain('admin@example.com')
    expect(intelButton(view!.host, 'Save policy').disabled).toBe(true)
    expect(intelButton(view!.host, 'Preview impact').disabled).toBe(true)
  }
  vi.mocked(apiFetch).mockResolvedValue(overviewFixture())
  await act(async () => { await view!.client.invalidateQueries({ queryKey: ['operations', 'lifecycle'], exact: true }) })
  await vi.waitFor(() => expect(view!.client.getQueryState(['operations', 'lifecycle'])?.status).toBe('success'))
  expect(retentionField().value).toBe('500')
  expect(intelButton(view!.host, 'Save policy').disabled).toBe(true)
  expect(vi.mocked(apiFetch).mock.calls.some(([, init]) => ['POST', 'PUT'].includes(init?.method ?? ''))).toBe(false)
  act(() => intelButton(view!.host, 'Discard draft and reload policy').click())
  await vi.waitFor(() => expect(retentionField().value).toBe('365'))
  expect(intelButton(view!.host, 'Preview impact').disabled).toBe(false)
})

it('keeps a dirty lifecycle draft on an ordinary outage while pausing policy submission', async () => {
  await editPolicy()
  vi.mocked(apiFetch).mockRejectedValue(new ApiError('Lifecycle catalog unavailable', 503, '/operations/lifecycle'))
  await act(async () => { await view!.client.invalidateQueries({ queryKey: ['operations', 'lifecycle'], exact: true }) })
  await vi.waitFor(() => expect((view!.client.getQueryState(['operations', 'lifecycle'])?.error as ApiError)?.status).toBe(503))
  await vi.waitFor(() => expect(view!.host.textContent).toContain('could not be loaded'))
  expect(retentionField().value).toBe('500')
  expect(intelButton(view!.host, 'Save policy').disabled).toBe(true)
  expect(intelButton(view!.host, 'Preview impact').disabled).toBe(true)
  expect(vi.mocked(apiFetch).mock.calls.some(([, init]) => ['POST', 'PUT'].includes(init?.method ?? ''))).toBe(false)
})

it.each(['preview', 'save'] as const)('does not restore a late lifecycle %s response after its catalog read was denied', async (action) => {
  await editPolicy()
  const preview = deferred<LifecyclePreview>()
  const save = deferred<LifecyclePolicy>()
  let unavailable = false
  vi.mocked(apiFetch).mockImplementation((path, init) => {
    if (init?.method === 'POST') return preview.promise as never
    if (init?.method === 'PUT') return save.promise as never
    if (unavailable) return Promise.reject(new ApiError('Catalog denied during write', 403, path))
    return Promise.resolve(overviewFixture()) as never
  })
  act(() => intelButton(view!.host, action === 'preview' ? 'Preview impact' : 'Save policy').click())
  await vi.waitFor(() => expect(vi.mocked(apiFetch).mock.calls.some(([, init]) => init?.method === (action === 'preview' ? 'POST' : 'PUT'))).toBe(true))
  unavailable = true
  await act(async () => { await view!.client.invalidateQueries({ queryKey: ['operations', 'lifecycle'], exact: true }) })
  await vi.waitFor(() => expect(view!.host.textContent).toContain('Catalog denied during write'))
  await act(async () => {
    if (action === 'preview') preview.resolve({
      id: 'late-preview', target_key: 'article_content', policy_revision: 3,
      cutoff_at: '2025-11-15T10:00:00Z', eligible_count: 98765, protected_count: 0, protected_counts: {},
      oldest_candidate_at: null, eligible_bytes: 0, count_is_lower_bound: false, is_partial: false,
      generated_at: '2026-11-15T10:00:00Z', expires_at: '2099-11-15T10:15:00Z',
      observed_at: '2026-11-15T10:00:00Z',
    })
    else save.resolve({ ...overviewFixture().targets[0].policy, revision: 4, retention_days: 500, updated_by: 'private-late-writer@example.test' })
  })
  await vi.waitFor(() => expect(view!.client.getMutationCache().getAll().some((mutation) => mutation.state.status === 'success')).toBe(true))
  await vi.waitFor(() => expect(intelButton(view!.host, 'Save policy').disabled).toBe(true))
  expect(retentionField().value).toBe('500')
  expect(view!.host.textContent).not.toContain('98,765')
  expect(view!.host.textContent).not.toContain('private-late-writer@example.test')
  expect(view!.host.textContent).not.toContain('Preview generated. No records were changed.')
  expect(view!.host.textContent).not.toContain('Policy saved.')
  expect(intelButton(view!.host, 'Save policy').disabled).toBe(true)
  expect((view!.client.getQueryState(['operations', 'lifecycle'])?.error as ApiError)?.status).toBe(403)
})

it('preserves an explicit history dataset filter when catalog metadata is unavailable', async () => {
  vi.mocked(apiFetch).mockImplementation((path) => path === '/operations/lifecycle'
    ? Promise.reject(new ApiError('Catalog metadata unavailable', 403, path))
    : Promise.resolve({ runs: [], total: 0, page: 1, page_size: 25 }) as never)
  view = await mountIntel(<DataLifecyclePage />, '/?tab=history&target=article_content')
  await vi.waitFor(() => expect(view!.host.textContent).toContain('No lifecycle runs match these filters'))
  expect(view.router.state.location.search).toContain('target=article_content')
  const historyReads = vi.mocked(apiFetch).mock.calls.filter(([path]) => path.startsWith('/operations/lifecycle/runs?'))
  expect(historyReads.length).toBeGreaterThan(0)
  expect(historyReads.every(([path]) => path.includes('target_key=article_content'))).toBe(true)
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
