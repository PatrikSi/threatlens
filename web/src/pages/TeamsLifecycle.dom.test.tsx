// @vitest-environment jsdom
import { act, useState, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, apiFetch } from '../api/client'
import type { Team, TeamMemberPage } from '../types/teams'
import { TeamCreateForm } from './TeamCreateForm'
import { TeamMembersPanel } from './TeamMembersPanel'
import { TeamSettingsEditor } from './TeamSettingsEditor'
import { TeamsPage } from './TeamsPage'
;(
  globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({
  ...(await original<typeof import('../api/client')>()),
  apiFetch: vi.fn(),
}))
vi.mock('../hooks/useCurrentUser', () => ({
  useCurrentUser: () => ({
    data: {
      access: {
        permissions: ['read:teams', 'write:teams', 'read:views', 'write:views'],
      },
    },
  }),
}))
const team: Team = {
  id: 'team-1',
  key: 'soc',
  name: 'SOC',
  description: 'Original description',
  membership_group_id: 'group-1',
  manager_group_id: 'group-2',
  active: true,
  revision: 1,
  can_manage: true,
  created_at: '2026-09-12T00:00:00Z',
  updated_at: '2026-09-12T00:00:00Z',
}
const groups = [
  { id: 'group-1', name: 'Members', is_system: false },
  { id: 'group-2', name: 'Managers', is_system: false },
]
let root: Root
let client: QueryClient
let router: ReturnType<typeof createMemoryRouter>
let container: HTMLDivElement
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: Error) => void
  const promise = new Promise<T>((done, fail) => {
    resolve = done
    reject = fail
  })
  return { promise, resolve, reject }
}
async function settle() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 15))
  })
}
async function mount(element: ReactNode, entry = '/teams') {
  client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Infinity },
      mutations: { retry: false },
    },
  })
  router = createMemoryRouter(
    [
      { path: '/teams', element },
      { path: '/elsewhere', element: <p>Destination</p> },
    ],
    { initialEntries: [entry] },
  )
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  act(() =>
    root.render(
      <QueryClientProvider client={client}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    ),
  )
  await settle()
}
function control(label: string) {
  const found = [...container.querySelectorAll('label')]
    .find((entry) => entry.textContent?.trim().startsWith(label))
    ?.querySelector('input, textarea, select')
  expect(found, label).toBeDefined()
  return found as HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement
}
function edit(label: string, value: string) {
  const element = control(label)
  const prototype =
    element instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : element instanceof HTMLSelectElement
        ? HTMLSelectElement.prototype
        : HTMLInputElement.prototype
  act(() => {
    Object.getOwnPropertyDescriptor(prototype, 'value')!.set!.call(
      element,
      value,
    )
    element.dispatchEvent(
      new Event(element instanceof HTMLSelectElement ? 'change' : 'input', {
        bubbles: true,
      }),
    )
  })
}
function button(name: string, scope: ParentNode = document) {
  const found = [...scope.querySelectorAll('button')].find(
    (entry) => entry.textContent?.trim() === name,
  )
  expect(found, name).toBeDefined()
  return found!
}
function click(name: string, scope?: ParentNode) {
  act(() => button(name, scope).click())
}
beforeEach(() => {
  vi.mocked(apiFetch).mockResolvedValue([])
})
afterEach(() => {
  act(() => root?.unmount())
  router?.dispose()
  client?.clear()
  document.body.replaceChildren()
  vi.resetAllMocks()
})

describe('team workspace asynchronous lifecycle', () => {
  it('pins the draft revision across refresh and preserves the draft after conflict', async () => {
    const pending = deferred<Team>()
    vi.mocked(apiFetch).mockReturnValue(pending.promise)
    let refresh!: (next: Team) => void
    function Harness() {
      const [latest, setLatest] = useState(team)
      refresh = setLatest
      return (
        <TeamSettingsEditor
          team={latest}
          groups={groups}
          administer={false}
          onSaved={vi.fn()}
        />
      )
    }
    await mount(<Harness />)
    edit('Name', 'My unfinished title')
    act(() => refresh({ ...team, revision: 2, name: 'Someone else' }))
    expect(container.textContent).toContain('This team changed elsewhere')
    click('Save team details')
    await settle()
    expect(
      JSON.parse(vi.mocked(apiFetch).mock.calls[0][1]!.body as string)
        .expected_revision,
    ).toBe(1)
    expect(control('Name').matches(':disabled')).toBe(true)
    await act(async () =>
      pending.reject(
        new ApiError('Reload before saving', 409, '/teams/team-1'),
      ),
    )
    await settle()
    expect(control('Name').value).toBe('My unfinished title')
    expect(container.textContent).toContain('Reload before saving')
    await act(async () => {
      void router.navigate('/elsewhere')
    })
    expect(document.querySelector('[role="alertdialog"]')).not.toBeNull()
    click('Cancel', document.querySelector('[role="alertdialog"]')!)
    expect(router.state.location.pathname).toBe('/teams')
  })

  it('reconciles normalized save fields and clears only the accepted draft', async () => {
    const pending = deferred<Team>()
    vi.mocked(apiFetch).mockReturnValue(pending.promise)
    await mount(
      <TeamSettingsEditor
        team={team}
        groups={groups}
        administer
        onSaved={vi.fn()}
      />,
    )
    edit('Name', '  Saved title  ')
    click('Save team details')
    await settle()
    await act(async () =>
      pending.resolve({ ...team, name: 'Saved title', revision: 2 }),
    )
    await settle()
    expect(control('Name').value).toBe('Saved title')
    await act(async () => {
      await router.navigate('/elsewhere')
    })
    expect(router.state.location.pathname).toBe('/elsewhere')
    expect(document.querySelector('[role="alertdialog"]')).toBeNull()
  })

  it('keeps a failed create editable without losing its group selections', async () => {
    const pending = deferred<Team>()
    vi.mocked(apiFetch).mockReturnValue(pending.promise)
    await mount(<TeamCreateForm groups={groups} onCreated={vi.fn()} />)
    edit('Team name', 'SOC')
    edit('Stable key', 'soc')
    edit('Member group', 'group-1')
    edit('Manager group', 'group-2')
    click('Create team')
    await settle()
    expect(control('Team name').matches(':disabled')).toBe(true)
    await act(async () =>
      pending.reject(new ApiError('Choose another team key', 409, '/teams')),
    )
    await settle()
    expect(control('Team name').value).toBe('SOC')
    expect(control('Manager group').value).toBe('group-2')
    expect(control('Team name').matches(':disabled')).toBe(false)
    expect(container.textContent).toContain('Choose another team key')
  })

  it('recovers a failed bounded membership page without discarding paging context', async () => {
    const first: TeamMemberPage = {
      items: [
        {
          id: 'member-1',
          email: 'one@example.test',
          account_role: 'analyst',
          is_manager: false,
        },
      ],
      total: 51,
      page: 1,
      page_size: 50,
    }
    let secondFails = true
    vi.mocked(apiFetch).mockImplementation((path) =>
      path.includes('page=1')
        ? Promise.resolve(first)
        : secondFails
          ? Promise.reject(new Error('Temporary membership outage'))
          : Promise.resolve({
              ...first,
              page: 2,
              items: [
                {
                  ...first.items[0],
                  id: 'member-51',
                  email: 'last@example.test',
                },
              ],
            }),
    )
    await mount(<TeamMembersPanel teamId={team.id} />)
    click('Next members')
    await settle()
    expect(container.textContent).toContain('Temporary membership outage')
    secondFails = false
    click('Retry members')
    await settle()
    expect(container.textContent).toContain('Page 2 of 2')
    expect(container.textContent).toContain('last@example.test')
    expect(button('Next members').disabled).toBe(true)
  })

  it('hides previously cached team content when current membership is revoked', async () => {
    let revoked = false
    vi.mocked(apiFetch).mockImplementation((path) => {
      if (path.startsWith('/teams?'))
        return Promise.resolve({
          items: [team],
          total: 1,
          page: 1,
          page_size: 25,
        })
      if (path === '/teams/team-1')
        return revoked
          ? Promise.reject(new ApiError('Team not found', 404, path))
          : Promise.resolve(team)
      if (path.includes('/members'))
        return Promise.resolve({ items: [], total: 0, page: 1, page_size: 50 })
      return Promise.resolve([])
    })
    await mount(<TeamsPage />, '/teams?team=team-1')
    expect(container.querySelector('main')).toBeNull()
    expect(container.querySelector('a[href="/alerts?view=occurrences&queue_scope=team&team_id=team-1"]')).not.toBeNull()
    expect(
      container.querySelector('[aria-label="Team settings"]'),
    ).not.toBeNull()
    revoked = true
    await act(async () => {
      await client.invalidateQueries({ queryKey: ['teams', 'detail'] })
    })
    await settle()
    expect(container.querySelector('[aria-label="Team settings"]')).toBeNull()
    expect(
      container.querySelector('[aria-label="Current team members"]'),
    ).toBeNull()
    expect(container.textContent).toContain('Team not found')
  })
})
