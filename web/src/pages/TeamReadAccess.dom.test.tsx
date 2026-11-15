// @vitest-environment jsdom
import { act, useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, apiFetch } from '../api/client'
import type { TeamPage } from '../types/teams'
import { AssessmentTeamPicker } from './AssessmentTeamPicker'
import { AlertAssigneePicker, AlertQueueScopePicker } from './AlertTeamSelectors'
import { TeamIntegrationConfiguration } from './TeamIntegrationConfiguration'
import { createDefaultDraft, createRequestFromDraft } from './notificationWebhookDraft'
import { deferred, intelButton, mountIntel, settle } from './articleIntelligenceTestSupport'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...(await original<object>()), apiFetch: vi.fn() }))
vi.mock('../hooks/useCurrentUser', () => ({
  useCurrentUser: () => ({ data: { id: 'analyst', access: { permissions: ['read:teams'] } } }),
}))

let view: Awaited<ReturnType<typeof mountIntel>> | undefined
afterEach(() => { view?.close(); view = undefined; vi.clearAllMocks() })

const firstBase = '/teams/team-1/integrations/dest-1'
const secondBase = '/teams/team-2/integrations/dest-2'
const firstUrl = 'https://siem.example.com/private-team-one'
const secondUrl = 'https://siem.example.com/private-team-two'
function savedConfiguration(base = firstBase, url = firstUrl) {
  return {
    ...createRequestFromDraft(createDefaultDraft()), id: base.endsWith('dest-1') ? 'dest-1' : 'dest-2',
    user_id: 'analyst', team_id: base === firstBase ? 'team-1' : 'team-2', name: 'Synthetic destination',
    url_template: url, ownership_revision: 2, secrets_redacted: false,
    created_at: '2026-11-15T00:00:00Z', updated_at: '2026-11-15T00:00:00Z',
  }
}
function response<T>(status: number | null, path: string, data: T): Promise<T> {
  return status === null ? Promise.resolve(data) : Promise.reject(new ApiError(
    status === 403 ? 'Team access changed' : 'Team service unavailable', status, path,
  ))
}
function inputValues() { return [...view!.host.querySelectorAll('input')].map((input) => input.value) }
function optionCount(text: string) {
  return [...view!.host.querySelectorAll('option')].filter((option) => option.textContent === text).length
}
async function refresh(key: readonly unknown[]) {
  await act(async () => { await view!.client.invalidateQueries({ queryKey: key }) })
  await settle()
}
async function click(label: string) {
  act(() => intelButton(view!.host, label).click())
  await settle()
}
function teamPage(name = 'Private SOC team'): TeamPage {
  return { items: [{ id: 'team-1', name }], total: 1, page: 1, page_size: 50 } as TeamPage
}

describe('team read access history', () => {
  it('keeps denied configuration hidden through an outage until a new successful read', async () => {
    let status: number | null = null
    let saved = savedConfiguration()
    vi.mocked(apiFetch).mockImplementation((path) => path === `${firstBase}/configuration`
      ? response(status, path, saved) as never : Promise.resolve([]) as never)
    view = await mountIntel(<TeamIntegrationConfiguration base={firstBase} unavailable={false} onChanged={vi.fn()} />)
    expect(inputValues()).toContain(firstUrl)

    status = 403
    await refresh(['teams', 'integration-configuration', firstBase])
    expect(inputValues()).not.toContain(firstUrl)
    status = 503
    await click('Reload configuration')
    expect(view.host.textContent).toContain('Team service unavailable')
    expect(inputValues()).not.toContain(firstUrl)

    status = null
    saved = { ...savedConfiguration(firstBase, 'https://siem.example.com/reauthorized'), ownership_revision: 3 }
    await click('Reload configuration')
    expect(inputValues()).toContain(saved.url_template)
    expect(inputValues()).not.toContain(firstUrl)
    expect(vi.mocked(apiFetch).mock.calls.every(([, options]) => !options?.method)).toBe(true)
  })

  it('preserves ordinary configuration outage context while pausing writes', async () => {
    let status: number | null = null
    vi.mocked(apiFetch).mockImplementation((path) => path === `${firstBase}/configuration`
      ? response(status, path, savedConfiguration()) as never : Promise.resolve([]) as never)
    view = await mountIntel(<TeamIntegrationConfiguration base={firstBase} unavailable={false} onChanged={vi.fn()} />)
    status = 503
    await refresh(['teams', 'integration-configuration', firstBase])
    expect(inputValues()).toContain(firstUrl)
    expect(intelButton(view.host, 'Save team destination').disabled).toBe(true)
    expect(view.host.querySelector<HTMLInputElement>('input')?.matches(':disabled')).toBe(true)
  })

  it('does not quarantine another team destination when one configuration is denied', async () => {
    let denied = false
    vi.mocked(apiFetch).mockImplementation((path) => {
      if (path === `${firstBase}/configuration`) return response(denied ? 403 : null, path, savedConfiguration()) as never
      if (path === `${secondBase}/configuration`) return Promise.resolve(savedConfiguration(secondBase, secondUrl)) as never
      return Promise.resolve([]) as never
    })
    view = await mountIntel(<>
      <TeamIntegrationConfiguration base={firstBase} unavailable={false} onChanged={vi.fn()} />
      <TeamIntegrationConfiguration base={secondBase} unavailable={false} onChanged={vi.fn()} />
    </>)
    expect(inputValues()).toContain(secondUrl)
    denied = true
    await refresh(['teams', 'integration-configuration', firstBase])
    expect(inputValues()).not.toContain(firstUrl)
    expect(inputValues()).toContain(secondUrl)
  })

  it('retains configuration denial across remount and a later outage', async () => {
    let status: number | null = null
    let display!: (visible: boolean) => void
    function Harness() {
      const [visible, setVisible] = useState(true)
      display = setVisible
      return visible ? <TeamIntegrationConfiguration base={firstBase} unavailable={false} onChanged={vi.fn()} /> : <p>Configuration closed</p>
    }
    vi.mocked(apiFetch).mockImplementation((path) => path === `${firstBase}/configuration`
      ? response(status, path, savedConfiguration()) as never : Promise.resolve([]) as never)
    view = await mountIntel(<Harness />)
    status = 403
    await refresh(['teams', 'integration-configuration', firstBase])
    act(() => display(false))
    status = 503
    act(() => display(true))
    await settle()
    await click('Reload configuration')
    expect(inputValues()).not.toContain(firstUrl)
  })

  it('shares team-list denial between assessment and alert pickers until a fresh success', async () => {
    let status: number | null = null
    let page = teamPage()
    vi.mocked(apiFetch).mockImplementation((path) => response(status, path, page) as never)
    view = await mountIntel(<>
      <AssessmentTeamPicker value="" onChange={vi.fn()} />
      <AlertQueueScopePicker value="personal" onChange={vi.fn()} />
    </>)
    expect(optionCount('Private SOC team')).toBe(2)
    status = 403
    await refresh(['teams', 'list', false])
    expect(optionCount('Private SOC team')).toBe(0)
    status = 503
    await click('Retry teams')
    expect(optionCount('Private SOC team')).toBe(0)

    status = null
    page = teamPage('Freshly authorized SOC team')
    await click('Retry teams')
    expect(optionCount('Freshly authorized SOC team')).toBe(2)
  })

  it('preserves ordinary team-list outage context in both same-cache readers', async () => {
    let status: number | null = null
    vi.mocked(apiFetch).mockImplementation((path) => response(status, path, teamPage()) as never)
    view = await mountIntel(<>
      <AssessmentTeamPicker value="" onChange={vi.fn()} />
      <AlertQueueScopePicker value="personal" onChange={vi.fn()} />
    </>)
    status = 503
    await refresh(['teams', 'list', false])
    expect(optionCount('Private SOC team')).toBe(2)
    expect(view.host.textContent).toContain('Team service unavailable')
  })

  it('does not revive a cached team page when switching picker readers after denial', async () => {
    let display!: (assessment: boolean) => void
    const recovery = deferred<TeamPage>()
    let firstRead = true
    function Harness() {
      const [assessment, setAssessment] = useState(true)
      display = setAssessment
      return assessment ? <AssessmentTeamPicker value="" onChange={vi.fn()} />
        : <AlertQueueScopePicker value="personal" onChange={vi.fn()} />
    }
    vi.mocked(apiFetch).mockImplementation((path) => {
      if (path.includes('page=2')) return Promise.reject(new ApiError('Team access changed', 403, path))
      if (firstRead) {
        firstRead = false
        return Promise.resolve({ ...teamPage(), total: 51 }) as never
      }
      return recovery.promise as never
    })
    view = await mountIntel(<Harness />)
    expect(optionCount('Private SOC team')).toBe(1)
    await click('Next teams')
    expect(view.host.textContent).toContain('Team access changed')
    act(() => display(false))
    await settle()
    expect(optionCount('Private SOC team')).toBe(0)
    await act(async () => recovery.resolve(teamPage('Reauthorized team')))
    await settle()
    expect(optionCount('Reauthorized team')).toBe(1)
  })

  it('keeps denied assignee emails hidden through an outage until a fresh success', async () => {
    let status: number | null = null
    let email = 'private-analyst@example.com'
    vi.mocked(apiFetch).mockImplementation((path) => response(status, path, {
      items: [{ id: 'analyst-1', email }], total: 1, page: 1, page_size: 50,
    }) as never)
    view = await mountIntel(<AlertAssigneePicker teamId="team-1" value="" disabled={false} onChange={vi.fn()} />)
    expect(optionCount(email)).toBe(1)
    status = 403
    await refresh(['teams', 'team-1', 'members'])
    expect(optionCount(email)).toBe(0)
    status = 503
    await click('Retry teammates')
    expect(optionCount(email)).toBe(0)
    status = null
    email = 'reauthorized-analyst@example.com'
    await click('Retry teammates')
    expect(optionCount(email)).toBe(1)
  })

  it('preserves ordinary assignee outage context while pausing selection', async () => {
    let status: number | null = null
    vi.mocked(apiFetch).mockImplementation((path) => response(status, path, {
      items: [{ id: 'analyst-1', email: 'private-analyst@example.com' }], total: 1, page: 1, page_size: 50,
    }) as never)
    view = await mountIntel(<AlertAssigneePicker teamId="team-1" value="" disabled={false} onChange={vi.fn()} />)
    status = 503
    await refresh(['teams', 'team-1', 'members'])
    expect(optionCount('private-analyst@example.com')).toBe(1)
    expect(view.host.querySelector('select')?.disabled).toBe(true)
  })

  it('keeps another team member list available when one team is denied', async () => {
    let denied = false
    vi.mocked(apiFetch).mockImplementation((path) => path.includes('/team-1/')
      ? response(denied ? 403 : null, path, { items: [{ id: 'analyst-1', email: 'private-first@example.com' }], total: 1, page: 1, page_size: 50 }) as never
      : Promise.resolve({ items: [{ id: 'analyst-2', email: 'available-second@example.com' }], total: 1, page: 1, page_size: 50 }) as never)
    view = await mountIntel(<>
      <AlertAssigneePicker teamId="team-1" value="" disabled={false} onChange={vi.fn()} />
      <AlertAssigneePicker teamId="team-2" value="" disabled={false} onChange={vi.fn()} />
    </>)
    denied = true
    await refresh(['teams', 'team-1', 'members'])
    expect(optionCount('private-first@example.com')).toBe(0)
    expect(optionCount('available-second@example.com')).toBe(1)
  })
})
