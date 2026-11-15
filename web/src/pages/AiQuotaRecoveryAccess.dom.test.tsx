// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import { AiProviderConnections } from './AiProviderConnections'
import { useAiProviderConnections, type AiProviderConnectionsController } from './useAiProviderConnections'
import type { AiQuotaGroup } from './useAiQuotaGroups'
import { intelButton, mountIntel, settle } from './articleIntelligenceTestSupport'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...await original<typeof import('../api/client')>(), apiFetch: vi.fn() }))

const quota: AiQuotaGroup = {
  id: '550e8400-e29b-41d4-a716-446655440000', version: 1,
  name: 'Private retired quota name',
  provider_keys: ['profile:550e8400-e29b-41d4-a716-446655440001'],
  max_concurrent_requests: 2, hourly_token_budget: 10000, max_concurrent_per_team: 1,
}
const key = ['ai', 'quota-groups']
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
let current: AiProviderConnectionsController
afterEach(() => { view?.close(); view = undefined; vi.resetAllMocks() })

function quotaName() {
  return [...view!.host.querySelectorAll('label')].find((label) => label.textContent?.trim() === 'Account quota name')?.querySelector('input')
}
function editName(value: string) {
  const input = quotaName()!
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
function putRequests() { return vi.mocked(apiFetch).mock.calls.filter(([, init]) => init?.method === 'PUT') }
async function mountSelected(dirty: boolean) {
  const state = { status: 200, present: true, version: 1 }
  vi.mocked(apiFetch).mockImplementation((path, init) => {
    if (init?.method === 'PUT') return Promise.resolve({ ...quota, version: 2 })
    if (path === '/ai/quota-groups?limit=100') {
      if (state.status !== 200) return Promise.reject(new ApiError(`Quota read ${state.status}`, state.status, path))
      return Promise.resolve({ items: state.present ? [{ ...quota, version: state.version }] : [], total: state.present ? 1 : 0, limit: 100, offset: 0 })
    }
    if (path === '/ai/provider-routing') return Promise.resolve({ version: 1, default_provider_id: null, item_enrichment_provider_id: null, daily_brief_provider_id: null, report_provider_id: null })
    if (path.startsWith('/ai/providers?')) return Promise.resolve({ items: [], total: 0, limit: 25, offset: 0 })
    return new Promise(() => {})
  })
  function Harness() {
    current = useAiProviderConnections(true)
    return <AiProviderConnections controller={current} />
  }
  view = await mountIntel(<Harness />)
  await vi.waitFor(() => expect(view!.host.textContent).toContain(quota.name))
  act(() => intelButton(view!.host, quota.name).click()); await settle()
  expect(quotaName()?.value).toBe(quota.name)
  expect(view.host.textContent).toContain(quota.provider_keys[0])
  if (dirty) editName('Actual unsent quota name')
  return state
}
async function refresh(state: { status: number; present: boolean; version: number }, status: number, present = true, version = 1) {
  Object.assign(state, { status, present, version })
  await act(async () => { await view!.client.invalidateQueries({ queryKey: key, exact: true }) }); await settle()
}

it.each([false, true])('keeps a withdrawn quota editor hidden when the fresh list omits its selected group: dirty=%s', async (dirty) => {
  const state = await mountSelected(dirty)
  await refresh(state, 403)
  expect(quotaName()).toBeUndefined()
  await refresh(state, 200, false)
  expect(current.quotas.query.data?.items).toEqual([])
  expect.soft(quotaName()).toBeUndefined()
  expect.soft(view!.host.textContent).not.toContain(quota.provider_keys[0])
  act(() => current.quotas.save()); await settle()
  expect(putRequests()).toHaveLength(0)
  await refresh(state, 200, true, 3)
  await vi.waitFor(() => expect(quotaName()?.value).toBe(dirty ? 'Actual unsent quota name' : quota.name))
})

it('does not admit a retired dirty quota baseline merely because the matching group is readable again', async () => {
  const state = await mountSelected(true)
  await refresh(state, 403)
  expect(quotaName()).toBeUndefined()
  await refresh(state, 200, true, 3)
  expect(current.quotas.query.data?.items[0].version).toBe(3)
  expect(quotaName()?.value).toBe('Actual unsent quota name')
  expect(current.quotas.editor?.baseline?.version).toBe(1)
  act(() => current.quotas.save()); await settle()
  expect(putRequests()).toHaveLength(0)
})
