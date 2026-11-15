// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, expect, it, vi } from 'vitest'

import { ApiError, apiFetch } from '../api/client'
import type { OperationsOverviewResponse } from '../types/operations'
import { OperationsPage } from './OperationsPage'
import { TeamAIHandlingRestrictions } from './TeamAIHandlingRestrictions'
import { intelButton, mountIntel, settle } from './articleIntelligenceTestSupport'

vi.mock('../api/client', async (original) => ({ ...await original<typeof import('../api/client')>(), apiFetch: vi.fn() }))
;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
afterEach(() => { view?.close(); view = undefined; vi.resetAllMocks() })

it('keeps denied handling label metadata hidden through a later outage', async () => {
  vi.mocked(apiFetch).mockResolvedValue({ labels: [{ id: 'private-label', name: 'Private handling label', is_active: true }] })
  view = await mountIntel(<TeamAIHandlingRestrictions value={{}} onChange={vi.fn()} approved={[]} destinations={[]} />)
  await vi.waitFor(() => expect(view!.host.textContent).toContain('Private handling label'))
  for (const status of [403, 503]) {
    vi.mocked(apiFetch).mockRejectedValue(new ApiError(`Handling labels unavailable ${status}`, status, '/iam/data-policies'))
    await act(async () => { await view!.client.invalidateQueries({ queryKey: ['iam', 'team-ai-handling-labels'] }) }); await settle()
    await vi.waitFor(() => expect(view!.host.textContent).toContain(`Handling labels unavailable ${status}`))
    expect(view.host.textContent).not.toContain('Private handling label')
  }
  vi.mocked(apiFetch).mockResolvedValue({ labels: [{ id: 'fresh-label', name: 'Fresh handling label', is_active: true }] })
  act(() => intelButton(view!.host, 'Retry handling labels').click()); await settle()
  await vi.waitFor(() => expect(view!.host.textContent).toContain('Fresh handling label'))
})

const overview: OperationsOverviewResponse = {
  generated_at: '2026-11-15T12:00:00Z', overall_status: 'healthy',
  application: { version: 'private-ops-revision', schema_revision: 'private-schema', expected_schema_revision: 'private-schema', schema_current: true },
  components: [], storage: [], backlogs: [], issues: [],
  recovery: { latest_backup: null, latest_verify: null, latest_restore_drill: null, latest_restore: null },
}

it('keeps Operations denial latched through a later outage and same-client remount', async () => {
  vi.mocked(apiFetch).mockResolvedValue(overview)
  view = await mountIntel(<OperationsPage />)
  await vi.waitFor(() => expect(view!.host.textContent).toContain('private-ops-revision'))
  vi.mocked(apiFetch).mockRejectedValue(new ApiError('Operations forbidden', 403, '/operations/overview'))
  await act(async () => { await view!.client.invalidateQueries({ queryKey: ['operations'] }) }); await settle()
  await vi.waitFor(() => expect((view!.client.getQueryState(['operations', 'overview'])?.error as ApiError)?.status).toBe(403))
  expect(view.host.textContent).not.toContain('private-ops-revision')
  vi.mocked(apiFetch).mockRejectedValue(new ApiError('Operations unavailable', 503, '/operations/overview'))
  act(() => intelButton(view!.host, 'Retry health').click()); await settle()
  await vi.waitFor(() => expect((view!.client.getQueryState(['operations', 'overview'])?.error as ApiError)?.status).toBe(503))
  expect(view.host.textContent).not.toContain('private-ops-revision')
  await act(async () => { await view!.router.navigate('/other') }); await settle()
  await act(async () => { await view!.router.navigate('/') }); await settle()
  expect(view.host.textContent).not.toContain('private-ops-revision')
  expect(intelButton(view.host, 'Download diagnostics').disabled).toBe(true)
  vi.mocked(apiFetch).mockResolvedValue({ ...overview, application: { ...overview.application, version: 'fresh-ops-revision' } })
  act(() => intelButton(view!.host, 'Retry health').click()); await settle()
  await vi.waitFor(() => expect(view!.host.textContent).toContain('fresh-ops-revision'))
  expect(intelButton(view.host, 'Download diagnostics').disabled).toBe(false)
})
