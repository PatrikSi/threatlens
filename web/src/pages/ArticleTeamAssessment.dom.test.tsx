// @vitest-environment jsdom
import { act, useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import { ArticleTeamAssessment, AssessmentWorkspace } from './ArticleTeamAssessment'
import type { TeamAssessmentResponse } from '../types/articleIntelligence'
import { assessmentFixture, deferred, editIntel, intelButton, intelField, mountIntel, settle } from './articleIntelligenceTestSupport'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
const identity = vi.hoisted(() => ({ isError: false, error: null as Error | null, data: { id: 'user-1', access: { permissions: ['read:items', 'read:teams', 'write:teams', 'write:investigations'] }, features: { ai_enabled: true } } }))
vi.mock('../api/client', async (original) => ({ ...(await original<object>()), apiFetch: vi.fn() }))
vi.mock('../hooks/useCurrentUser', () => ({ useCurrentUser: () => identity }))
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
beforeEach(() => {
  identity.isError = false
  identity.error = null
  identity.data.features.ai_enabled = true
  vi.mocked(apiFetch).mockImplementation((path) => Promise.resolve(path.startsWith('/teams?') ? { items: [{ id: 'team-1', name: 'Endpoint team' }, { id: 'team-2', name: 'Cloud team' }], total: 2, page: 1, page_size: 50 } : assessmentFixture))
})
afterEach(() => { view?.close(); view = undefined; vi.clearAllMocks() })

function reviewedResponse(): TeamAssessmentResponse {
  const next = structuredClone(assessmentFixture)
  next.assessment!.version = 2
  next.assessment!.result!.hunts[0].review_status = 'accepted'
  next.assessment!.result!.hunts[0].review_note = 'Checked required logs.'
  return next
}

describe('team assessment asynchronous lifecycle', () => {
  it('pins review state, disables pending editing and creates investigations only after acceptance', async () => {
    const pending = deferred<TeamAssessmentResponse>()
    vi.mocked(apiFetch).mockImplementation((path, init) => init?.method ? pending.promise : Promise.resolve(assessmentFixture))
    view = await mountIntel(<AssessmentWorkspace itemId="item-1" teamId="team-1" canWrite canCreate />)
    expect(view.host.textContent).not.toContain('Create team investigation')
    editIntel(view.host, 'Review note', 'Checked required logs.')
    await settle()
    act(() => intelButton(view!.host, 'Accept suggestion').click())
    await settle()
    expect(intelField(view.host, 'Review note').matches(':disabled')).toBe(true)
    const request = vi.mocked(apiFetch).mock.calls.find(([, init]) => init?.method === 'PATCH')!
    expect(request[0]).toBe('/items/item-1/team-assessment/hunts/hunt-1')
    expect(JSON.parse(String(request[1]?.body))).toEqual({ team_id: 'team-1', expected_version: 1, status: 'accepted', note: 'Checked required logs.' })
    await act(async () => pending.resolve(reviewedResponse()))
    await settle()
    expect(intelButton(view.host, 'Create team investigation').disabled).toBe(false)
    expect(intelField(view.host, 'Review note').value).toBe('Checked required logs.')
    expect(view.client.getQueryData(['team-assessment-drafts', 'item-1', 'team-1'])).toEqual({})
  })

  it('rejects an older in-flight refresh after a review response is saved', async () => {
    const refresh = deferred<TeamAssessmentResponse>()
    let reads = 0
    vi.mocked(apiFetch).mockImplementation((_path, init) => {
      if (init?.method === 'PATCH') return Promise.resolve(reviewedResponse())
      reads += 1
      return reads > 1 ? refresh.promise : Promise.resolve(assessmentFixture)
    })
    view = await mountIntel(<AssessmentWorkspace itemId="item-1" teamId="team-1" canWrite canCreate />)
    act(() => { void view!.client.invalidateQueries({ queryKey: ['team-assessments', 'item-1', 'team-1'] }) })
    await settle()
    act(() => intelButton(view!.host, 'Accept suggestion').click())
    await settle()
    expect(intelButton(view.host, 'Create team investigation').disabled).toBe(false)
    await act(async () => refresh.resolve(assessmentFixture))
    await settle()
    expect(view.client.getQueryData<TeamAssessmentResponse>(['team-assessments', 'item-1', 'team-1'])?.assessment?.version).toBe(2)
    expect(intelButton(view.host, 'Create team investigation').disabled).toBe(false)
  })

  it('retains notes through collapse and binds them to their original assessment revision', async () => {
    let display!: (show: boolean) => void
    function Harness() {
      const [show, setShow] = useState(true)
      display = setShow
      return show ? <AssessmentWorkspace itemId="item-1" teamId="team-1" canWrite canCreate /> : <p>Collapsed</p>
    }
    view = await mountIntel(<Harness />)
    editIntel(view.host, 'Review note', 'Unsubmitted analyst note')
    await settle()
    act(() => display(false))
    const changed = reviewedResponse()
    act(() => view!.client.setQueryData(['team-assessments', 'item-1', 'team-1'], changed))
    act(() => display(true))
    await settle()
    expect(intelField(view.host, 'Review note').value).toBe('Unsubmitted analyst note')
    expect(view.host.textContent).toContain('earlier assessment revision')
    expect(intelButton(view.host, 'Accept suggestion').disabled).toBe(true)
    expect(intelButton(view.host, 'Create team investigation').disabled).toBe(true)
    act(() => intelButton(view!.host, 'Reload saved reviews').click())
    expect(document.querySelector('[role="alertdialog"]')?.textContent).toContain('Discard unsaved hunt review notes?')
    act(() => intelButton(document, 'Discard changes').click())
    await settle()
    expect(intelField(view.host, 'Review note').value).toBe('Checked required logs.')
  })

  it('keeps late queue responses scoped to the team that submitted them', async () => {
    const pending = deferred<TeamAssessmentResponse>()
    const second = structuredClone(assessmentFixture)
    second.assessment!.team_id = 'team-2'
    second.assessment!.result!.relevance_reasons = ['Cloud-team-specific assessment']
    vi.mocked(apiFetch).mockImplementation((path, init) => {
      if (init?.method) return pending.promise
      if (path.startsWith('/teams?')) return Promise.resolve({ items: [{ id: 'team-1', name: 'Endpoint team' }, { id: 'team-2', name: 'Cloud team' }], total: 2, page: 1, page_size: 50 })
      return Promise.resolve(path.includes('team_id=team-2') ? second : assessmentFixture)
    })
    view = await mountIntel(<ArticleTeamAssessment itemId="item-1" />)
    act(() => intelButton(view!.host, 'Regenerate team assessment').click())
    await settle()
    await act(async () => { await view!.router.navigate('/?assessment_team=team-2') })
    await settle()
    expect(view.host.textContent).toContain('Cloud-team-specific assessment')
    await act(async () => pending.resolve({ ...assessmentFixture, assessment: { ...assessmentFixture.assessment!, status: 'queued' } }))
    await settle()
    expect(view.host.textContent).toContain('Cloud-team-specific assessment')
    expect(view.host.textContent).not.toContain('Team assessment queued.')
    expect(view.client.getQueryData<TeamAssessmentResponse>(['team-assessments', 'item-1', 'team-1'])?.assessment?.status).toBe('queued')
    expect(view.client.getQueryData<TeamAssessmentResponse>(['team-assessments', 'item-1', 'team-2'])?.assessment?.status).toBe('ready')
  })

  it('preserves drafts through session verification outages and hides revoked team results', async () => {
    let refresh!: () => void
    function Harness() {
      const [, render] = useState(0)
      refresh = () => render((value) => value + 1)
      return <ArticleTeamAssessment itemId="item-1" />
    }
    view = await mountIntel(<Harness />)
    editIntel(view.host, 'Review note', 'Keep through session outage')
    await settle()
    act(() => { identity.isError = true; identity.error = new Error('Temporary network error'); refresh() })
    expect(intelField(view.host, 'Review note').value).toBe('Keep through session outage')
    expect(intelField(view.host, 'Review note').matches(':disabled')).toBe(true)
    expect(view.host.textContent).toContain('Session verification is temporarily unavailable')
    act(() => { identity.isError = false; identity.error = null; refresh() })
    expect(intelField(view.host, 'Review note').value).toBe('Keep through session outage')
    vi.mocked(apiFetch).mockRejectedValue(new ApiError('Team not found', 404, '/items/item-1/team-assessment'))
    await act(async () => { await view!.client.invalidateQueries({ queryKey: ['team-assessments', 'item-1', 'team-1'] }) })
    await settle()
    expect(view.host.textContent).not.toContain('Review unusual services')
    expect(view.host.querySelector('textarea')).toBeNull()
  })

  it('keeps historical suggestions readable and reviewable when AI generation is disabled', async () => {
    identity.data.features.ai_enabled = false
    const data = { ...assessmentFixture, ai_enabled: false, hunt_suggestions_enabled: false }
    vi.mocked(apiFetch).mockImplementation((path) => Promise.resolve(path.startsWith('/teams?') ? { items: [], total: 0, page: 1, page_size: 50 } : data))
    view = await mountIntel(<ArticleTeamAssessment itemId="item-1" />)
    expect(view.host.textContent).toContain('AI is disabled on this server')
    expect(view.host.textContent).toContain('Suggested hunt cards are off')
    expect(intelButton(view.host, 'Regenerate team assessment').disabled).toBe(true)
    expect(intelButton(view.host, 'Accept suggestion').matches(':disabled')).toBe(false)
    expect(view.host.querySelector('a[href="https://attack.mitre.org/techniques/T1543/003/"]')).not.toBeNull()
  })
})
