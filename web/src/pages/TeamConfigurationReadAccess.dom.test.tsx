// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import { TeamAIProviderPicker } from './TeamAIProviderPicker'
import { mountIntel, settle } from './articleIntelligenceTestSupport'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...(await original<typeof import('../api/client')>()), apiFetch: vi.fn() }))
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
afterEach(() => { view?.close(); view = undefined; vi.resetAllMocks() })

it('keeps denied team provider choices hidden through a later outage', async () => {
  let status = 200
  vi.mocked(apiFetch).mockImplementation((path) => status === 200
    ? Promise.resolve({ items: [{ id: 'private-provider', name: 'Private provider destination', enabled: true }], total: 1 })
    : Promise.reject(new ApiError('Provider choices unavailable', status, path)))
  view = await mountIntel(<TeamAIProviderPicker onAdd={vi.fn()} />)
  expect(view.host.textContent).toContain('Private provider destination')
  for (const next of [403, 503]) {
    status = next
    await act(async () => { await view!.client.invalidateQueries({ queryKey: ['ai', 'team-provider-choices'] }) })
    await settle()
    expect(view.host.textContent).not.toContain('Private provider destination')
    expect(view.host.textContent).toContain('Legacy provider settings')
  }
})
