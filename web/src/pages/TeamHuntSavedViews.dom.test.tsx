// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { apiFetch } from '../api/client'
import { TeamHuntSavedViews, type HuntFilters } from './TeamHuntSavedViews'
import { deferred, intelButton, mountIntel, settle } from './articleIntelligenceTestSupport'

vi.mock('../api/client', async (original) => ({ ...await original<typeof import('../api/client')>(), apiFetch: vi.fn() }))
;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
afterEach(() => { view?.close(); view = undefined; vi.resetAllMocks() })

const filters: HuntFilters = { status: 'pending', ownership: 'all', order: 'newest', priority: null, overdue: false }
const savedView = { id: 'view-1', version: 1, name: 'Pending hunts', filters }

it('submits one deletion under rapid confirmation and keeps the selected view protected until it completes', async () => {
  const deletion = deferred<undefined>()
  let deleted = false
  vi.mocked(apiFetch).mockImplementation((_path, options) => {
    if (options?.method === 'DELETE') return deletion.promise.then(() => { deleted = true; return undefined })
    return Promise.resolve({ items: deleted ? [] : [savedView], can_manage: true })
  })
  view = await mountIntel(<TeamHuntSavedViews teamId="team-1" filters={filters} onApply={vi.fn()} unavailable={false} />)
  const select = view.host.querySelector('select')!
  act(() => { select.value = savedView.id; select.dispatchEvent(new Event('change', { bubbles: true })) })
  act(() => intelButton(view!.host, 'Delete saved hunt view').click())
  const confirm = intelButton(document.body, 'Delete view')
  act(() => { confirm.click(); confirm.click() })
  await settle()

  expect(vi.mocked(apiFetch).mock.calls.filter(([, options]) => options?.method === 'DELETE')).toHaveLength(1)
  expect(intelButton(document.body, 'Working...').disabled).toBe(true)
  expect(intelButton(document.body, 'Cancel').disabled).toBe(true)
  expect(select.closest('fieldset')!.disabled).toBe(true)
  expect(select.value).toBe(savedView.id)
  expect(view.host.querySelector('input')!.value).toBe(savedView.name)
  act(() => intelButton(document.body, 'Cancel').click())
  expect(document.body.querySelector('[role="alertdialog"]')).not.toBeNull()

  await act(async () => deletion.resolve(undefined))
  await settle()
  expect(document.body.querySelector('[role="alertdialog"]')).toBeNull()
  expect(select.value).toBe('')
  expect(view.host.querySelector('input')!.value).toBe('')
  expect(select.closest('fieldset')!.disabled).toBe(false)
})
