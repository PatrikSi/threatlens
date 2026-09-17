// @vitest-environment jsdom
import { act, useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import { AssessmentWorkspace } from '../pages/ArticleTeamAssessment'
import { assessmentFixture, deferred, editIntel, intelButton, intelField, mountIntel, settle } from '../pages/articleIntelligenceTestSupport'
import type { TeamAssessmentResponse } from '../types/articleIntelligence'
import { HuntDraftRecovery } from './HuntDraftRecovery'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
const identity = vi.hoisted(() => ({ isError: false, error: null, data: { access: { permissions: ['read:items', 'read:teams', 'write:teams', 'write:investigations'] } } }))
vi.mock('../api/client', async (original) => ({ ...(await original<object>()), apiFetch: vi.fn() }))
vi.mock('../hooks/useCurrentUser', () => ({ useCurrentUser: () => identity }))
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
let collapse!: () => void
function Fixture() {
  const [show, setShow] = useState(true)
  collapse = () => setShow(false)
  return <><HuntDraftRecovery />{show && <AssessmentWorkspace itemId="item-1" teamId="team-1" canWrite canCreate />}</>
}
beforeEach(() => { vi.mocked(apiFetch).mockResolvedValue(assessmentFixture) })
afterEach(() => { view?.close(); view = undefined; vi.clearAllMocks() })

async function draftAndCollapse() {
  view = await mountIntel(<Fixture />)
  editIntel(view.host, 'Review note', 'Retained unsaved review')
  await settle()
  act(() => collapse())
  act(() => intelButton(view!.host, 'Resume hunt reviews').click())
  act(() => intelButton(document, 'Article item-1 · Team team-1 · 1 unsaved').click())
  await settle()
}

describe('session hunt draft recovery', () => {
  it('recovers collapsed drafts only after fresh access verification succeeds', async () => {
    const fresh = deferred<TeamAssessmentResponse>()
    let reads = 0
    vi.mocked(apiFetch).mockImplementation(() => ++reads === 1 ? Promise.resolve(assessmentFixture) : fresh.promise)
    await draftAndCollapse()
    expect(document.querySelector('textarea')).toBeNull()
    expect(document.querySelector('[role="dialog"]')?.textContent).toContain('Loading team assessment')
    await act(async () => fresh.resolve(assessmentFixture))
    await settle()
    expect(intelField(document, 'Review note').value).toBe('Retained unsaved review')
    act(() => intelButton(document, 'Discard all hunt review drafts').click())
    expect(document.querySelector('[role="alertdialog"]')?.textContent).toContain('every article and team')
    act(() => intelButton(document, 'Discard all drafts').click())
    await settle()
    expect(document.querySelector('[role="dialog"]')?.textContent).toContain('No unsaved hunt review notes remain')
    const event = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(event)
    expect(event.defaultPrevented).toBe(false)
  })

  it.each([403, 404])('removes retained drafts if resource access returns %s', async (status) => {
    let reads = 0
    vi.mocked(apiFetch).mockImplementation(() => ++reads === 1 ? Promise.resolve(assessmentFixture) : Promise.reject(new ApiError('Assessment unavailable', status, '/items/item-1/team-assessment')))
    await draftAndCollapse()
    expect(document.querySelector('textarea')).toBeNull()
    expect(document.querySelector('[role="dialog"]')?.textContent).not.toContain('Retained unsaved review')
    expect(view!.client.getQueryData(['team-assessment-drafts', 'item-1', 'team-1'])).toEqual({})
  })

  it('keeps notes private and recoverable during a temporary verification failure', async () => {
    let reads = 0
    vi.mocked(apiFetch).mockImplementation(() => ++reads === 1 ? Promise.resolve(assessmentFixture) : Promise.reject(new ApiError('Temporarily unavailable', 503, '/items/item-1/team-assessment')))
    await draftAndCollapse()
    expect(document.querySelector('textarea')).toBeNull()
    expect(view!.client.getQueryData(['team-assessment-drafts', 'item-1', 'team-1'])).toMatchObject({ 'hunt-1': { note: 'Retained unsaved review' } })
    const event = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(event)
    expect(event.defaultPrevented).toBe(true)
    vi.mocked(apiFetch).mockResolvedValue(assessmentFixture)
    act(() => intelButton(document, 'Retry assessment').click())
    await settle()
    expect(intelField(document, 'Review note').value).toBe('Retained unsaved review')
  })
})
