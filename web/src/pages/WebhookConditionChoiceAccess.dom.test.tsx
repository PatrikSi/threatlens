// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import { mountIntel, settle } from './articleIntelligenceTestSupport'
import { useWebhookConditionChoices } from './useWebhookConditionChoices'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...(await original<object>()), apiFetch: vi.fn() }))
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
let current: ReturnType<typeof useWebhookConditionChoices>
afterEach(() => { view?.close(); view = undefined; vi.resetAllMocks() })

it('keeps a cached team choice page private after a sibling page was denied', async () => {
  vi.mocked(apiFetch).mockImplementation(async (path) => {
    if (path.includes('page=2')) throw new ApiError('Team page denied', 403, path)
    return { items: [{ id: 'team-1', name: 'Private cached team', active: true }], total: 101, page: 1, page_size: 100 }
  })
  function Harness() {
    current = useWebhookConditionChoices('team_id')
    return <p>{current.choices.map((choice) => choice.label).join(',')}</p>
  }
  view = await mountIntel(<Harness />)
  expect(view.host.textContent).toContain('Private cached team')
  act(() => current.setPage(2)); await settle()
  expect(current.error).toBeInstanceOf(ApiError)
  expect(view.host.textContent).not.toContain('Private cached team')
  act(() => current.setPage(1))
  expect(view.host.textContent).not.toContain('Private cached team')
})

it('keeps static local condition choices usable without remote catalogue reads', async () => {
  function Harness() {
    current = useWebhookConditionChoices('ioc_type')
    return <p>{current.choices.map((choice) => choice.label).join(',')}</p>
  }
  view = await mountIntel(<Harness />)
  expect(view.host.textContent).toContain('domain')
  expect(current.remote).toBe(false)
  expect(vi.mocked(apiFetch)).not.toHaveBeenCalled()
})
