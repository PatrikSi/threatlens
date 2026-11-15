// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, expect, it, vi } from 'vitest'

import { ApiError, apiFetch } from '../api/client'
import type { OperationsOverviewResponse } from '../types/operations'
import { OperationsPage } from './OperationsPage'
import { deferred, intelButton, mountIntel } from './articleIntelligenceTestSupport'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...await original<typeof import('../api/client')>(), apiFetch: vi.fn() }))
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
afterEach(() => { view?.close(); view = undefined; vi.resetAllMocks() })
const overview: OperationsOverviewResponse = {
  generated_at: '2026-11-15T12:00:00Z', overall_status: 'healthy',
  application: { version: 'old-operations-revision', schema_revision: 'schema', expected_schema_revision: 'schema', schema_current: true },
  components: [], storage: [], backlogs: [], issues: [],
  recovery: { latest_backup: null, latest_verify: null, latest_restore_drill: null, latest_restore: null },
}

it('requires all four recovery reads after a denial followed by partial success before observer delivery', async () => {
  vi.mocked(apiFetch).mockResolvedValue(overview)
  view = await mountIntel(<OperationsPage />)
  await vi.waitFor(() => expect(view!.host.textContent).toContain('old-operations-revision'))
  const key = ['operations', 'overview']
  const fresh = { ...overview, application: { ...overview.application, version: 'partial-operations-revision' } }
  await act(async () => {
    vi.mocked(apiFetch).mockRejectedValue(new ApiError('Operations denied', 403, '/operations/overview'))
    await expect(view!.client.fetchQuery({ queryKey: key, staleTime: 0, retry: false })).rejects.toMatchObject({ status: 403 })
    vi.mocked(apiFetch).mockResolvedValue(fresh)
    await view!.client.fetchQuery({ queryKey: key, staleTime: 0, retry: false })
  })
  await vi.waitFor(() => expect(view!.client.getQueryState(key)?.status).toBe('success'))
  await vi.waitFor(() => {
    expect(view!.host.textContent).toContain('Access to system health is no longer available')
    expect(view!.host.textContent).not.toContain('Operations denied')
  })
  expect(view.host.textContent).not.toContain('partial-operations-revision')
  expect(intelButton(view.host, 'Download diagnostics').disabled).toBe(true)
  const held = deferred<typeof fresh>()
  vi.mocked(apiFetch).mockImplementation((path) => path.startsWith('/operations/health-history') ? held.promise : Promise.resolve(fresh) as never)
  act(() => intelButton(view!.host, 'Retry health').click())
  await vi.waitFor(() => expect(vi.mocked(apiFetch).mock.calls.some(([path]) => path.startsWith('/operations/health-history'))).toBe(true))
  expect(view.host.textContent).not.toContain('partial-operations-revision')
  expect(intelButton(view.host, 'Download diagnostics').disabled).toBe(true)
  await act(async () => held.resolve(fresh))
  await vi.waitFor(() => expect(view!.host.textContent).toContain('partial-operations-revision'))
  expect(intelButton(view.host, 'Download diagnostics').disabled).toBe(false)
})
