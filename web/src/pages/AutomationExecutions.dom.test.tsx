// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import { AutomationExecutions } from './AutomationExecutions'
import {
  intelButton,
  mountIntel,
  settle,
} from './articleIntelligenceTestSupport'
;(
  globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({
  ...(await original<object>()),
  apiFetch: vi.fn(),
}))
const currentUser = vi.hoisted(() => ({
  data: { access: { permissions: ['read:items', 'read:notifications', 'write:notifications', 'read:investigations', 'write:investigations'] } },
  isError: false,
}))
vi.mock('../hooks/useCurrentUser', () => ({ useCurrentUser: () => currentUser }))
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
afterEach(() => {
  view?.close()
  view = undefined
  vi.clearAllMocks()
  currentUser.data.access.permissions = ['read:items', 'read:notifications', 'write:notifications', 'read:investigations', 'write:investigations']
  currentUser.isError = false
})
const entry = {
  id: 'execution-1',
  action_id: 'action-1',
  status: 'completed',
  external_job_id: 'remote-job',
  sequence: 2,
  findings: 'One reviewed finding',
  investigation_note_id: null,
  policy_state: 'withdrawn',
  policy_revision: 1,
  policy_acknowledged_revision: 0,
  updated_at: '2026-09-26T00:00:00Z',
}

describe('automation execution lifecycle', () => {
  it.each([
    { access: 'no investigation access', permissions: ['read:items', 'read:notifications', 'write:notifications'] },
    { access: 'read-only investigation access', permissions: ['read:items', 'read:notifications', 'write:notifications', 'read:investigations'] },
  ])('retains readable findings without offering attachment with $access', async ({ permissions }) => {
    currentUser.data.access.permissions = permissions
    vi.mocked(apiFetch).mockImplementation((path) => Promise.resolve(path.startsWith('/investigations')
      ? { investigations: [{ id: 'investigation-1', title: 'Readable investigation', version: 7, status: 'open', current_user_role: 'owner', team_id: null }], total: 1 }
      : { items: [entry], next_cursor: null }))
    view = await mountIntel(<AutomationExecutions writable onClose={vi.fn()} />)
    await settle()
    expect(document.body.textContent).toContain('One reviewed finding')
    expect(document.body.textContent).toContain('remote-job · completed')
    expect(document.body.querySelector('select')).toBeNull()
    expect([...document.querySelectorAll('button')].some((button) => button.textContent === 'Attach findings')).toBe(false)
    expect(vi.mocked(apiFetch).mock.calls.some(([path]) => path.startsWith('/investigations'))).toBe(false)
  })

  it('allows an authorized attachment and retires its controls when investigation write access changes', async () => {
    currentUser.data.access.permissions = currentUser.data.access.permissions.filter((value) => value !== 'read:investigations')
    vi.mocked(apiFetch).mockImplementation((path) => Promise.resolve(path.startsWith('/investigations')
      ? { investigations: [{ id: 'investigation-1', title: 'Editable investigation', version: 7, status: 'open', current_user_role: 'owner', team_id: null }], total: 1 }
      : { items: [entry], next_cursor: null }))
    view = await mountIntel(<AutomationExecutions writable onClose={vi.fn()} />)
    await settle()
    const select = document.querySelector('select')!
    act(() => {
      select.value = 'investigation-1'
      select.dispatchEvent(new Event('change', { bubbles: true }))
    })
    expect(intelButton(document, 'Attach findings').disabled).toBe(false)
    act(() => intelButton(document, 'Attach findings').click())
    await settle()
    const attachment = vi.mocked(apiFetch).mock.calls.find(([path, options]) => path.endsWith('/findings') && options?.method === 'POST')!
    expect(attachment[0]).toBe('/notifications/automation/executions/execution-1/findings')
    expect(JSON.parse(String(attachment[1]?.body))).toEqual({ investigation_id: 'investigation-1', expected_investigation_version: 7, expected_sequence: 2 })
    currentUser.data.access.permissions = currentUser.data.access.permissions.filter((value) => value !== 'write:investigations')
    await act(async () => { await view!.client.invalidateQueries({ queryKey: ['notifications', 'automation-executions'] }) })
    await settle()
    expect(document.body.querySelector('select')).toBeNull()
    expect(document.body.textContent).toContain('One reviewed finding')
  })

  it('offers only writable investigation memberships and retires a selected destination after its role changes', async () => {
    const investigations = [
      { id: 'owner', title: 'Owned investigation', version: 1, status: 'open', current_user_role: 'owner', team_id: null },
      { id: 'editor', title: 'Edited investigation', version: 2, status: 'open', current_user_role: 'editor', team_id: null },
      { id: 'viewer', title: 'Read-only investigation', version: 3, status: 'open', current_user_role: 'viewer', team_id: null },
      { id: 'shared', title: 'Shared investigation without membership', version: 4, status: 'open', current_user_role: null, team_id: null },
      { id: 'archived', title: 'Archived investigation', version: 5, status: 'archived', current_user_role: 'owner', team_id: null },
      { id: 'team', title: 'Team investigation', version: 6, status: 'open', current_user_role: 'editor', team_id: 'team-1' },
    ]
    vi.mocked(apiFetch).mockImplementation((path) => Promise.resolve(path.startsWith('/investigations')
      ? { investigations: investigations.map((investigation) => ({ ...investigation })), total: investigations.length }
      : { items: [entry], next_cursor: null }))
    view = await mountIntel(<AutomationExecutions writable onClose={vi.fn()} />)
    await settle()
    const select = document.querySelector('select')!
    expect([...select.options].map((option) => option.value)).toEqual(['', 'owner', 'editor'])
    act(() => {
      select.value = 'editor'
      select.dispatchEvent(new Event('change', { bubbles: true }))
    })
    expect(intelButton(document, 'Attach findings').disabled).toBe(false)
    investigations[1].current_user_role = 'viewer'
    await act(async () => { await view!.client.invalidateQueries({ queryKey: ['automation', 'investigations'] }) })
    await settle()
    expect([...select.options].map((option) => option.value)).toEqual(['', 'owner'])
    expect(intelButton(document, 'Attach findings').disabled).toBe(true)
    expect(document.body.textContent).toContain('One reviewed finding')
    expect(vi.mocked(apiFetch).mock.calls.some(([, options]) => options?.method === 'POST')).toBe(false)
  })

  it('removes a selected team destination when team write access changes', async () => {
    currentUser.data.access.permissions.push('write:teams')
    vi.mocked(apiFetch).mockImplementation((path) => Promise.resolve(path.startsWith('/investigations')
      ? { investigations: [{ id: 'team-investigation', title: 'Team investigation', version: 7, status: 'open', current_user_role: 'editor', team_id: 'team-1' }], total: 1 }
      : { items: [entry], next_cursor: null }))
    view = await mountIntel(<AutomationExecutions writable onClose={vi.fn()} />)
    await settle()
    const select = document.querySelector('select')!
    act(() => {
      select.value = 'team-investigation'
      select.dispatchEvent(new Event('change', { bubbles: true }))
    })
    expect(intelButton(document, 'Attach findings').disabled).toBe(false)
    currentUser.data.access.permissions = currentUser.data.access.permissions.filter((value) => value !== 'write:teams')
    await act(async () => { await view!.client.invalidateQueries({ queryKey: ['notifications', 'automation-executions'] }) })
    await settle()
    expect([...select.options].map((option) => option.value)).toEqual([''])
    expect(intelButton(document, 'Attach findings').disabled).toBe(true)
    expect(document.body.textContent).toContain('One reviewed finding')
    expect(vi.mocked(apiFetch).mock.calls.some(([, options]) => options?.method === 'POST')).toBe(false)
  })

  it('keeps the selected destination in pagination and archived-history requests', async () => {
    vi.mocked(apiFetch)
      .mockResolvedValueOnce({ items: [entry], next_cursor: 'cursor-1' })
      .mockResolvedValue({ items: [], next_cursor: null })
    view = await mountIntel(
      <AutomationExecutions
        webhookId="destination-1"
        writable={false}
        onClose={vi.fn()}
      />,
    )
    await settle()
    expect(vi.mocked(apiFetch).mock.calls[0][0]).toBe(
      '/notifications/automation/executions?limit=25&webhook_id=destination-1',
    )
    act(() => intelButton(document, 'Next executions').click())
    await settle()
    expect(
      vi
        .mocked(apiFetch)
        .mock.calls.some(
          ([path]) =>
            path ===
            '/notifications/automation/executions?limit=25&webhook_id=destination-1&after=cursor-1',
        ),
    ).toBe(true)
    act(() =>
      (
        document.querySelector('input[type="checkbox"]') as HTMLInputElement
      ).click(),
    )
    await settle()
    expect(
      vi
        .mocked(apiFetch)
        .mock.calls.some(
          ([path]) =>
            path ===
            '/notifications/automation/executions?limit=25&webhook_id=destination-1&include_archived=true',
        ),
    ).toBe(true)
  })

  it('separates execution status from policy acknowledgement and paginates bounded requests', async () => {
    vi.mocked(apiFetch)
      .mockResolvedValueOnce({ items: [entry], next_cursor: 'cursor-1' })
      .mockResolvedValue({ items: [], next_cursor: null })
    view = await mountIntel(
      <AutomationExecutions writable={false} onClose={vi.fn()} />,
    )
    await settle()
    expect(document.body.textContent).toContain(
      'receiver acknowledgement pending',
    )
    expect(document.body.textContent).toContain('One reviewed finding')
    expect(document.body.textContent).toContain('remote-job · completed')
    act(() => intelButton(document, 'Next executions').click())
    await settle()
    expect(
      vi
        .mocked(apiFetch)
        .mock.calls.some(
          ([path]) =>
            path ===
            '/notifications/automation/executions?limit=25&after=cursor-1',
        ),
    ).toBe(true)
    expect(document.body.textContent).not.toContain('One reviewed finding')
  })
  it('hides cached findings after access is revoked and exposes a recoverable error', async () => {
    vi.mocked(apiFetch)
      .mockResolvedValueOnce({ items: [entry], next_cursor: null })
      .mockRejectedValue(
        new ApiError(
          'Your access changed',
          403,
          '/notifications/automation/executions',
        ),
      )
    view = await mountIntel(
      <AutomationExecutions writable={false} onClose={vi.fn()} />,
    )
    await settle()
    expect(document.body.textContent).toContain('One reviewed finding')
    act(() => intelButton(document, 'Refresh executions').click())
    await settle()
    expect(document.body.textContent).not.toContain('One reviewed finding')
    expect(document.body.textContent).toContain('Your access changed')
    expect(intelButton(document, 'Retry executions').disabled).toBe(false)
  })
})
