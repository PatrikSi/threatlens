// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { apiFetch, ApiError } from '../api/client'
import { TeamHuntWorklist } from './TeamHuntWorklist'
import { assessmentFixture, deferred, intelButton, mountIntel, settle } from './articleIntelligenceTestSupport'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...(await original<object>()), apiFetch: vi.fn() }))
vi.mock('./ArticleTeamAssessment', () => ({ AssessmentWorkspace: ({ itemId }: { itemId: string }) => <p>Existing review workspace: {itemId}</p> }))
const entry = {
  assessment_id: 'assessment-1', assessment_version: 7, item_id: 'item-1', item_title: 'Source article',
  team_id: 'team-1', status: 'pending', generated_at: null, evidence_age_seconds: 100,
  hunt: assessmentFixture.assessment!.result!.hunts[0], claim: { version: 3, owner_user_id: null },
  owner_name: null, reviewer_name: null, reviewed_at: null, can_claim: true, can_release: false, can_schedule: true, investigation: null,
}
const page = { items: [entry], next_cursor: null, has_more: false, limit: 25 }
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
const views = { items: [], can_manage: false }
beforeEach(() => { vi.mocked(apiFetch).mockImplementation((path) => Promise.resolve(path.endsWith('/hunts/views') ? views : page)) })
afterEach(() => { view?.close(); view = undefined; vi.clearAllMocks() })
const queue = <TeamHuntWorklist teamId="team-1" writable canInvestigate unavailable={false} />

describe('team hunt queue lifecycle', () => {
  it('pins both claim and assessment versions and blocks duplicate pending actions', async () => {
    const pending = deferred<unknown>()
    vi.mocked(apiFetch).mockImplementation((path, init) => init?.method ? pending.promise : Promise.resolve(path.endsWith('/hunts/views') ? views : page))
    view = await mountIntel(queue)
    act(() => {
      intelButton(view!.host, 'Claim hunt').click()
      intelButton(view!.host, 'Claim hunt').click()
    })
    await settle()
    const writes = vi.mocked(apiFetch).mock.calls.filter(([, init]) => init?.method === 'POST')
    expect(writes).toHaveLength(1)
    expect(JSON.parse(String(writes[0][1]?.body))).toEqual({ action: 'claim', expected_version: 3, expected_assessment_version: 7 })
    expect(intelButton(view.host, 'Claim hunt').disabled).toBe(true)
    await act(async () => pending.reject(new ApiError('Ownership changed. Refresh before retrying.', 409, writes[0][0])))
    await settle()
    expect(view.host.textContent).toContain('Ownership changed')
    expect(intelButton(view.host, 'Claim hunt').disabled).toBe(false)
  })

  it('continues empty permission-filtered pages and exposes recovery from a stale cursor', async () => {
    vi.mocked(apiFetch).mockImplementation((path) => path.endsWith('/hunts/views') ? Promise.resolve(views) : path.includes('cursor=next')
      ? Promise.reject(new ApiError('This hunt page cursor is invalid.', 422, path))
      : Promise.resolve({ ...page, items: [], next_cursor: 'next', has_more: true }))
    view = await mountIntel(queue, '/?panel=hunts&hunt_status=pending&other=retained')
    expect(view.host.textContent).toContain('Showing 0 accessible suggestions')
    act(() => intelButton(view!.host, 'Next page').click())
    await settle()
    expect(view.router.state.location.search).toContain('hunt_cursor=next')
    expect(view.router.state.location.search).toContain('other=retained')
    expect(view.host.textContent).toContain('This hunt page cursor is invalid')
    act(() => intelButton(view!.host, 'Return to first hunt page').click())
    await settle()
    expect(view.router.state.location.search).not.toContain('hunt_cursor')
    expect(view.router.state.location.search).toContain('hunt_status=pending')
  })

  it('reuses the existing review workspace and hides protected entries after access loss', async () => {
    view = await mountIntel(queue)
    act(() => intelButton(view!.host, 'Review hunt evidence').click())
    await settle()
    expect(document.querySelector('[role="dialog"]')?.textContent).toContain('Existing review workspace: item-1')
    vi.mocked(apiFetch).mockRejectedValue(new ApiError('Membership changed.', 404, '/teams/team-1/hunts'))
    await act(async () => { await view!.client.invalidateQueries({ queryKey: ['team-hunts'] }) })
    await settle()
    expect(view.host.textContent).not.toContain('Source article')
    expect(document.querySelector('[role="dialog"]')).toBeNull()
  })
  it.each(['/?hunt_status=accepted', '/?hunt_cursor=next', '/?team=team-2', '/other'])(
    'requires deliberate discard before leaving a schedule through %s', async (destination) => {
      view = await mountIntel(queue)
      const priority = [...view.host.querySelectorAll('label')].find((label) => label.textContent?.trim() === 'Review prioritylownormalhighurgent')!.querySelector('select')!
      act(() => { priority.value = 'urgent'; priority.dispatchEvent(new Event('change', { bubbles: true })) })
      act(() => { void view!.router.navigate(destination) })
      await settle()
      expect(document.querySelector('[role="alertdialog"]')?.textContent).toContain('unsaved hunt review schedule')
      act(() => intelButton(document, 'Cancel').click())
      await settle()
      expect(priority.value).toBe('urgent')
      expect(view.router.state.location.pathname + view.router.state.location.search).not.toBe(destination)
      act(() => { void view!.router.navigate(destination) })
      await settle()
      act(() => intelButton(document, 'Discard changes').click())
      await settle()
      expect(view.router.state.location.pathname + view.router.state.location.search).toBe(destination)
    },
  )

  it('retains polling-removed drafts and their baseline, but hides them on explicit access loss', async () => {
    view = await mountIntel(queue)
    const input = view.host.querySelector('input[type="datetime-local"]')!
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, '2026-10-01T12:00')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    vi.mocked(apiFetch).mockResolvedValue({ ...page, items: [] })
    await act(async () => { await view!.client.invalidateQueries({ queryKey: ['team-hunts'] }) })
    await settle()
    expect(view.host.textContent).toContain('unsaved schedule left this page')
    expect((input as HTMLInputElement).value).toBe('2026-10-01T12:00')
    expect(input.isConnected).toBe(true)
    expect(intelButton(view.host, 'Save review schedule').disabled).toBe(true)
    expect(intelButton(view.host, 'Reload review schedule').disabled).toBe(false)
    vi.mocked(apiFetch).mockRejectedValue(new ApiError('Membership changed.', 403, '/teams/team-1/hunts'))
    await act(async () => { await view!.client.invalidateQueries({ queryKey: ['team-hunts'] }) })
    await settle()
    expect(input.isConnected).toBe(false)
    expect(view.host.textContent).not.toContain('Source article')
    expect(document.querySelector('[role="alertdialog"]')).toBeNull()
  })

})
