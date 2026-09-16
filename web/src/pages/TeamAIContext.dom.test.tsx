// @vitest-environment jsdom
import { act, useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import { TeamAIContextEditor } from './TeamAIContext'
import type { TeamAIContext } from '../types/teams'
import { contextFixture, deferred, editIntel, intelButton, intelField, mountIntel, settle } from './articleIntelligenceTestSupport'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...(await original<object>()), apiFetch: vi.fn() }))
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
afterEach(() => { view?.close(); view = undefined; vi.clearAllMocks() })

describe('team AI context drafts', () => {
  it('pins the draft version, disables edits during submission and preserves conflict edits', async () => {
    const pending = deferred<TeamAIContext>()
    vi.mocked(apiFetch).mockReturnValue(pending.promise)
    let refresh!: (context: TeamAIContext) => void
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
    let refresh!: (context: TeamAIContext) => void
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
