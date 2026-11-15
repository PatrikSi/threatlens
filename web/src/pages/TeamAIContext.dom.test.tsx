// @vitest-environment jsdom
import { act, useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import { TeamAIContext, TeamAIContextEditor } from './TeamAIContext'
import type { TeamAIContext as TeamAIContextProfile } from '../types/teams'
import { contextFixture, deferred, editIntel, intelButton, intelField, mountIntel, settle } from './articleIntelligenceTestSupport'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...(await original<object>()), apiFetch: vi.fn() }))
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
afterEach(() => { view?.close(); view = undefined; vi.clearAllMocks() })

describe('team AI context drafts', () => {
  it('preserves a hidden AI context draft until explicit reload of the fresh baseline', async () => {
    let status = 200
    let latest = contextFixture
    vi.mocked(apiFetch).mockImplementation((path) => status === 200 ? Promise.resolve(latest) : Promise.reject(new ApiError('Context denied', status, path)))
    view = await mountIntel(<TeamAIContext teamId="team-1" writable />)
    editIntel(view.host, 'Relevance criteria', 'Unsent team AI context draft')
    status = 403
    await act(async () => { await view!.client.invalidateQueries({ queryKey: ['teams', 'ai-context', 'team-1'] }) })
    await settle()
    expect(view.host.querySelector('textarea')).toBeNull()
    expect(view.host.textContent).not.toContain('Unsent team AI context draft')
    latest = { ...contextFixture, version: 2, relevance_criteria: 'Fresh saved AI context' }
    status = 200
    await act(async () => { await view!.client.invalidateQueries({ queryKey: ['teams', 'ai-context', 'team-1'] }) })
    await settle()
    expect(intelField(view.host, 'Relevance criteria').value).toBe('Unsent team AI context draft')
    expect(view.host.textContent).toContain('draft still uses revision 1')
    act(() => intelButton(view!.host, 'Reload saved AI context').click())
    expect(document.querySelector('[role="alertdialog"]')).not.toBeNull()
    act(() => intelButton(document.body, 'Cancel').click())
    expect(intelField(view.host, 'Relevance criteria').value).toBe('Unsent team AI context draft')
    act(() => intelButton(view!.host, 'Reload saved AI context').click())
    act(() => intelButton(document.body, 'Discard changes').click())
    expect(intelField(view.host, 'Relevance criteria').value).toBe('Fresh saved AI context')
  })

  it('keeps denied saved team AI context hidden through a later outage', async () => {
    let status = 200
    const privateContext = { ...contextFixture, relevance_criteria: 'Private team relevance policy' }
    vi.mocked(apiFetch).mockImplementation((path) => status === 200 ? Promise.resolve(privateContext)
      : Promise.reject(new ApiError('Context unavailable', status, path)))
    view = await mountIntel(<TeamAIContext teamId="team-1" writable />)
    expect(intelField(view.host, 'Relevance criteria').value).toBe(privateContext.relevance_criteria)
    for (const next of [403, 503]) {
      status = next
      await act(async () => { await view!.client.invalidateQueries({ queryKey: ['teams', 'ai-context', 'team-1'] }) })
      await settle()
      expect(view.host.querySelector('textarea')).toBeNull()
    }
  })

  it('pins the draft version, disables edits during submission and preserves conflict edits', async () => {
    const pending = deferred<TeamAIContextProfile>()
    vi.mocked(apiFetch).mockReturnValue(pending.promise)
    let refresh!: (context: TeamAIContextProfile) => void
    function Harness() {
      const [context, setContext] = useState(contextFixture)
      refresh = setContext
      return <TeamAIContextEditor context={context} writable />
    }
    view = await mountIntel(<Harness />)
    editIntel(view.host, 'Technology stack', 'Linux\nKubernetes')
    act(() => refresh({ ...contextFixture, version: 2, technology_stack: ['Windows'] }))
    expect(intelField(view.host, 'Technology stack').value).toBe('Linux\nKubernetes')
    act(() => intelButton(view!.host, 'Save AI context').click())
    await settle()
    expect(intelField(view.host, 'Technology stack').matches(':disabled')).toBe(true)
    expect(JSON.parse(String(vi.mocked(apiFetch).mock.calls[0][1]?.body))).toMatchObject({ expected_version: 1, technology_stack: ['Linux', 'Kubernetes'] })
    await act(async () => pending.reject(new ApiError('Context changed; reload it before saving.', 409, '/teams/team-1/ai-context')))
    await settle()
    expect(intelField(view.host, 'Technology stack').value).toBe('Linux\nKubernetes')
    expect(view.host.textContent).toContain('Context changed; reload it before saving.')
    act(() => { void view!.router.navigate('/other') })
    await settle()
    expect(document.querySelector('[role="alertdialog"]')?.textContent).toContain('Discard unsaved team AI context?')
  })

  it('lets read-only members deliberately refresh an updated saved profile', async () => {
    let refresh!: (context: TeamAIContextProfile) => void
    function Harness() {
      const [context, setContext] = useState({ ...contextFixture, can_manage: false })
      refresh = setContext
      return <TeamAIContextEditor context={context} writable={false} />
    }
    view = await mountIntel(<Harness />)
    act(() => refresh({ ...contextFixture, version: 2, can_manage: false, technology_stack: ['Cloud services'] }))
    expect(intelField(view.host, 'Technology stack').matches(':disabled')).toBe(true)
    act(() => intelButton(view!.host, 'Reload saved AI context').click())
    expect(intelField(view.host, 'Technology stack').value).toBe('Cloud services')
    expect(vi.mocked(apiFetch)).not.toHaveBeenCalled()
  })
})
