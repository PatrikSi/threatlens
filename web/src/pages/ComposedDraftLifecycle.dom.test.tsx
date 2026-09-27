// @vitest-environment jsdom
import { act, StrictMode, useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiFetch } from '../api/client'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import { TeamsPage } from './TeamsPage'
import { useReportingController, type ReportingController } from './useReportingController'
import { deferred, intelButton, mountIntel, settle } from './articleIntelligenceTestSupport'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...(await original<object>()), apiFetch: vi.fn() }))
vi.mock('../hooks/useCurrentUser', () => ({ useCurrentUser: () => ({ data: { id: 'analyst', role: 'admin', access: { permissions: ['read:iam', 'write:iam', 'write:ai'] } } }) }))
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
afterEach(() => { view?.close(); view = undefined; vi.clearAllMocks() })

function editInput(input: HTMLInputElement, value: string) {
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
function Form({ name, ignoreSearchChanges = false }: { name: string; ignoreSearchChanges?: boolean }) {
  const [value, setValue] = useState('')
  const discard = useUnsavedChangesWarning(Boolean(value), `Discard ${name}?`, { ignoreSearchChanges })
  return <>{discard.discardDialog}<label>{name}<input value={value} onChange={(event) => setValue(event.target.value)} /></label></>
}

describe('composed navigation protection', () => {
  it('preserves dirty team settings alongside the clean AI policy editor', async () => {
    const team = { id: 'team-1', key: 'soc', name: 'SOC', description: '', membership_group_id: 'members', manager_group_id: 'managers', active: true, revision: 1, can_manage: true }
    const policy = { team_id: 'team-1', version: 1, configured: true, approved_provider_keys: ['legacy'], selected_provider_key: 'legacy', label_destinations: {}, destinations: [], can_manage: true, can_approve: true }
    vi.mocked(apiFetch).mockImplementation(async (path) => {
      if (path === '/teams/admin?page=1&page_size=25') return { items: [team], total: 1, page: 1, page_size: 25 }
      if (path === '/teams/admin/team-1') return team
      if (path === '/ai/team-governance/team-1') return policy
      return []
    })
    view = await mountIntel(<TeamsPage />, '/?mode=admin&team=team-1')
    await settle()
    expect(view.host.textContent).toContain('Approve destination policy')
    const input = [...view.host.querySelectorAll('label')].find((label) => label.textContent === 'Name')!.control as HTMLInputElement
    editInput(input, 'Unsaved SOC')
    await act(async () => { await view!.router.navigate('/other') })
    expect(view.router.state.location.pathname).toBe('/')
    expect(document.querySelectorAll('[role="alertdialog"]')).toHaveLength(1)
    act(() => intelButton(document.body, 'Cancel').click())
    expect(input.value).toBe('Unsaved SOC')
    await act(async () => { await view!.router.navigate('/other') })
    act(() => intelButton(document.body, 'Discard changes').click())
    await settle()
    expect(view.router.state.location.pathname).toBe('/other')
  })

  it('aggregates dirty forms, preserves search exemptions and hands off after a host unmounts', async () => {
    function Forms() {
      const [showFirst, setShowFirst] = useState(true)
      return <>{showFirst && <Form name="First" ignoreSearchChanges />}<Form name="Second" />
        <button onClick={() => setShowFirst(false)}>Remove first form</button></>
    }
    view = await mountIntel(<StrictMode><Forms /></StrictMode>, '/')
    editInput(view.host.querySelector('input')!, 'First draft')
    await act(async () => { await view!.router.navigate('/?query=updated') })
    expect(view.router.state.location.search).toBe('?query=updated')
    editInput(view.host.querySelectorAll('input')[1]!, 'Second draft')
    await act(async () => { await view!.router.navigate('/other') })
    expect(document.querySelectorAll('[role="alertdialog"]')).toHaveLength(1)
    expect(document.querySelector('[role="alertdialog"]')?.textContent).toContain('Discard First? Discard Second?')
    act(() => intelButton(view!.host, 'Remove first form').click())
    expect(document.querySelectorAll('[role="alertdialog"]')).toHaveLength(1)
    act(() => intelButton(document.body, 'Cancel').click())
    await settle()
    await act(async () => { await view!.router.navigate('/other') })
    expect(view.router.state.location.pathname).toBe('/')
    expect(document.querySelector('[role="alertdialog"]')?.textContent).toContain('Discard Second?')
  })
})

it.each(['delete', 'retry'] as const)('does not navigate after a late report %s completion', async (operation) => {
  const pending = deferred<unknown>()
  let controller!: ReportingController
  vi.mocked(apiFetch).mockImplementation(async (_path, init) => init?.method ? pending.promise : [])
  function Reporting() { controller = useReportingController(); return <p>Reports</p> }
  view = await mountIntel(<Reporting />, '/')
  act(() => { if (operation === 'delete') controller.deleteMutation.mutate('report-1'); else controller.retryMutation.mutate('report-1') })
  await settle()
  await act(async () => { await view!.router.navigate('/other') })
  await act(async () => pending.resolve(operation === 'delete' ? undefined : { report_id: 'report-1', status: 'queued' }))
  await settle()
  expect(view.router.state.location.pathname).toBe('/other')
})

it.each(['delete', 'retry'] as const)('does not show an old report %s failure in a new reporting view', async (operation) => {
  const pending = deferred<unknown>()
  let controller!: ReportingController
  vi.mocked(apiFetch).mockImplementation(async (_path, init) => init?.method ? pending.promise : [])
  function Reporting() { controller = useReportingController(); return <p>Reports</p> }
  view = await mountIntel(<Reporting />, '/?report=first')
  act(() => { if (operation === 'delete') controller.deleteMutation.mutate('report-1'); else controller.retryMutation.mutate('report-1') })
  await settle()
  await act(async () => { await view!.router.navigate('/?report=second') })
  await act(async () => pending.reject(new Error('Previous report failed')))
  await settle()
  expect(view.router.state.location.search).toBe('?report=second')
  expect(controller.feedback).toBeNull()
})
