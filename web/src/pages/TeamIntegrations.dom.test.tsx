// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import { TeamIntegrations } from './TeamIntegrations'
import { TeamIntegrationConfiguration } from './TeamIntegrationConfiguration'
import {
  createDefaultDraft,
  createRequestFromDraft,
} from './notificationWebhookDraft'
import {
  deferred,
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
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
afterEach(() => {
  view?.close()
  view = undefined
  vi.clearAllMocks()
})
const destination = {
  id: 'dest',
  name: 'SIEM',
  enabled: true,
  custodian_user_id: 'analyst',
  ownership_revision: 2,
  event_type: 'hunt.approved',
}
function field(label: string): HTMLInputElement {
  const control = [...view!.host.querySelectorAll('label')].find((entry) =>
    entry.textContent?.startsWith(label),
  )?.control
  if (!(control instanceof HTMLInputElement))
    throw new Error(`Missing input ${label}`)
  return control
}
function edit(input: HTMLInputElement, value: string) {
  act(() => {
    Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      'value',
    )!.set!.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

describe('team destination lifecycle', () => {
  it('pins adoption to the selected revision and disables duplicate submissions', async () => {
    const pending = deferred<object>()
    vi.mocked(apiFetch).mockImplementation((path, options) =>
      options?.method === 'POST'
        ? pending.promise
        : Promise.resolve(
            path === '/notifications/webhooks'
              ? [
                  {
                    id: 'personal',
                    name: 'Personal SIEM',
                    ownership_revision: 3,
                  },
                ]
              : { items: [] },
          ),
    )
    view = await mountIntel(
      <TeamIntegrations teamId="team" unavailable={false} />,
    )
    const select = view.host.querySelector('select')!
    act(() => {
      select.value = 'personal'
      select.dispatchEvent(new Event('change', { bubbles: true }))
    })
    act(() => intelButton(view!.host, 'Transfer to this team').click())
    await settle()
    expect(
      intelButton(view.host, 'Transfer to this team').matches(':disabled'),
    ).toBe(true)
    const call = vi
      .mocked(apiFetch)
      .mock.calls.find(([, options]) => options?.method === 'POST')!
    expect(call[0]).toBe('/teams/team/integrations/personal/adopt')
    expect(JSON.parse(String(call[1]?.body))).toEqual({ expected_revision: 3 })
    await act(async () =>
      pending.reject(new ApiError('Ownership changed', 409, call[0])),
    )
    await settle()
    expect(select.value).toBe('personal')
    expect(view.host.textContent).toContain('Ownership changed')
  })

  it('withholds one-time credentials after confirmed access loss', async () => {
    let denied = false
    vi.mocked(apiFetch).mockImplementation((path, options) => {
      if (path.endsWith('/receiver-credentials')) {
        if (denied)
          return Promise.reject(new ApiError('Access changed', 403, path))
        return Promise.resolve(
          options?.method === 'POST'
            ? { token: 'one-time-secret' }
            : { items: [] },
        )
      }
      return Promise.resolve(
        path === '/notifications/webhooks' ? [] : { items: [destination] },
      )
    })
    view = await mountIntel(
      <TeamIntegrations teamId="team" unavailable={false} />,
    )
    act(() => intelButton(view!.host, 'Receiver credentials').click())
    await settle()
    act(() => intelButton(view!.host, 'Create receiver credential').click())
    await settle()
    expect(field('New receiver token').value).toBe('one-time-secret')
    denied = true
    await act(async () => {
      await view!.client.invalidateQueries({
        queryKey: ['teams', 'receiver-credentials'],
      })
    })
    await settle()
    expect(view.host.querySelector('input[readonly]')).toBeNull()
    expect(view.host.textContent).toContain('Access changed')
  })

  it('keeps draft revision through refresh and preserves edits on a conflict', async () => {
    const saved = {
      ...createRequestFromDraft(createDefaultDraft()),
      id: 'dest',
      user_id: 'analyst',
      team_id: 'team',
      ownership_revision: 2,
      name: 'Original',
      created_at: '2026-09-27T00:00:00Z',
      updated_at: '2026-09-27T00:00:00Z',
    }
    const pending = deferred<object>()
    vi.mocked(apiFetch).mockImplementation((path, options) =>
      options?.method === 'PUT'
        ? pending.promise
        : Promise.resolve(path === '/feeds' ? [] : saved),
    )
    view = await mountIntel(
      <TeamIntegrationConfiguration
        base="/teams/team/integrations/dest"
        unavailable={false}
        onChanged={vi.fn()}
      />,
    )
    edit(field('Destination name'), 'Unsaved edit')
    act(() =>
      view!.client.setQueryData(
        ['teams', 'integration-configuration', '/teams/team/integrations/dest'],
        { ...saved, ownership_revision: 3, name: 'Changed remotely' },
      ),
    )
    expect(field('Destination name').value).toBe('Unsaved edit')
    act(() => intelButton(view!.host, 'Save team destination').click())
    await settle()
    expect(field('Destination name').matches(':disabled')).toBe(true)
    expect(
      vi
        .mocked(apiFetch)
        .mock.calls.find(([, options]) => options?.method === 'PUT')?.[0],
    ).toContain('expected_revision=2')
    await act(async () =>
      pending.reject(new ApiError('Ownership changed', 409, '/configuration')),
    )
    await settle()
    expect(field('Destination name').value).toBe('Unsaved edit')
    act(() => {
      void view!.router.navigate('/other')
    })
    await settle()
    expect(
      document.querySelector('[role="alertdialog"]')?.textContent,
    ).toContain('Discard unsaved team destination changes?')
  })
})

it('reloads the current configuration revision after pausing a destination', async () => {
  let revision = 2
  let enabled = true
  const attempted: string[] = []
  vi.mocked(apiFetch).mockImplementation(async (path) => {
    const row = { ...destination, enabled, ownership_revision: revision }
    if (path === '/notifications/webhooks' || path === '/feeds') return []
    if (path === '/teams/team/integrations') return { items: [row] }
    if (path.endsWith('/enabled')) {
      revision += 1
      enabled = false
      return { ...row, enabled, ownership_revision: revision }
    }
    if (path.endsWith('/configuration')) return {
      ...createRequestFromDraft(createDefaultDraft()), ...row,
      user_id: 'analyst', team_id: 'team', created_at: '2026-09-27T00:00:00Z', updated_at: '2026-09-27T00:00:00Z',
    }
    if (path.includes('/configuration?')) {
      attempted.push(path)
      throw new ApiError('Reload and adopt this destination before editing its configuration', 409, path)
    }
    throw new Error(`Unexpected request: ${path}`)
  })
  view = await mountIntel(<TeamIntegrations teamId="team" unavailable={false} />)
  act(() => intelButton(view!.host, 'Edit destination configuration').click())
  await settle()
  act(() => intelButton(view!.host, 'Pause destination').click())
  await settle()
  await settle()
  expect(intelButton(view.host, 'Enable destination')).toBeTruthy()
  edit(field('Destination name'), 'Draft change')
  act(() => intelButton(view!.host, 'Save team destination').click())
  await settle()
  expect(view.host.textContent).toContain('Reload and adopt')
  act(() => intelButton(view!.host, 'Reload saved destination').click())
  await settle()
  act(() => intelButton(document.body, 'Discard changes').click())
  await settle()
  expect(field('Destination name').value).toBe('SIEM')
  edit(field('Destination name'), 'Retry')
  act(() => intelButton(view!.host, 'Save team destination').click())
  await settle()
  expect(attempted).toEqual([
    '/teams/team/integrations/dest/configuration?expected_revision=2',
    '/teams/team/integrations/dest/configuration?expected_revision=3',
  ])
  expect(vi.mocked(apiFetch).mock.calls.filter(([path]) => path.endsWith('/configuration'))).toHaveLength(3)
})
