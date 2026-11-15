// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import { IndicatorReviewDialog } from './IndicatorReviewDialog'
import { TeamIndicatorSuppressions } from './TeamIndicatorSuppressions'
import { ArticleIndicatorsPanel } from './ArticleIndicatorsPanel'
import { deferred, intelButton, mountIntel, settle } from './articleIntelligenceTestSupport'
import { automationField, editAutomation, indicatorFixture, indicatorPageFixture } from './indicatorAutomationTestSupport'
import type { IndicatorSuppression } from '../types/indicators'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...(await original<object>()), apiFetch: vi.fn() }))
vi.mock('../hooks/useCurrentUser', () => ({ useCurrentUser: () => ({ isError: false, data: { access: { permissions: ['read:items', 'read:teams'] } } }) }))
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
afterEach(() => { view?.close(); view = undefined; vi.clearAllMocks() })

async function waitForReadError(key: readonly unknown[], status: number, message?: string) {
  await vi.waitFor(() => {
    expect((view!.client.getQueryState(key)?.error as ApiError)?.status).toBe(status)
    if (message) expect(document.body.querySelector('[role="alert"]')?.textContent).toContain(message)
    else expect(document.body.querySelector('[role="alert"]')).not.toBeNull()
  })
}

describe('indicator review lifecycle', () => {
  it('does not restore denied indicator history during a later outage and pauses its review', async () => {
    vi.mocked(apiFetch).mockResolvedValue({ items: [{ version: 1, snapshot: { reason: 'Private historical verdict' }, created_at: '2026-09-26T00:00:00Z', actor_user_id: null }], total: 1, page: 1, page_size: 10 })
    view = await mountIntel(<IndicatorReviewDialog itemId="item-1" teamId="team-1" indicator={indicatorFixture} baseline={indicatorPageFixture} writable onClose={vi.fn()} />)
    editAutomation(document, 'Review reason', 'Unsaved analyst review')
    const details = document.querySelector('details')!
    act(() => { details.open = true; details.dispatchEvent(new Event('toggle')) })
    await settle()
    expect(document.body.textContent).toContain('Private historical verdict')
    vi.mocked(apiFetch).mockRejectedValue(new ApiError('History access removed', 403, '/items/item-1/indicators/ioc-1/assessment/history'))
    await act(async () => { await view!.client.invalidateQueries({ queryKey: ['indicator-history'] }) })
    await settle()
    await waitForReadError(['indicator-history', 'item-1', 'ioc-1', 'team-1', 1], 403)
    expect(document.body.textContent).not.toContain('Private historical verdict')
    vi.mocked(apiFetch).mockRejectedValue(new ApiError('History unavailable', 503, '/items/item-1/indicators/ioc-1/assessment/history'))
    await act(async () => { await view!.client.invalidateQueries({ queryKey: ['indicator-history'] }) })
    await settle()
    await waitForReadError(['indicator-history', 'item-1', 'ioc-1', 'team-1', 1], 503)
    expect(document.body.textContent).not.toContain('Private historical verdict')
    expect(automationField(document, 'Review reason').value).toBe('Unsaved analyst review')
    expect(automationField(document, 'Review reason').matches(':disabled')).toBe(true)
  })

  it('does not restore a cached indicator page after a sibling page denies access', async () => {
    const recovery = deferred<unknown>()
    let firstReads = 0
    vi.mocked(apiFetch).mockImplementation((path) => {
      if (path.startsWith('/teams?')) return Promise.resolve({ items: [{ id: 'team-1', name: 'Endpoint team' }], total: 1, page: 1, page_size: 50 }) as ReturnType<typeof apiFetch>
      if (path.includes('page=2&')) return Promise.reject(new ApiError('Indicators access removed', 403, path))
      if (!path.includes('team_id=team-1')) return Promise.resolve({ ...indicatorPageFixture, total: 21 }) as ReturnType<typeof apiFetch>
      firstReads += 1
      return (firstReads === 1 ? Promise.resolve({ ...indicatorPageFixture, total: 21 }) : recovery.promise) as ReturnType<typeof apiFetch>
    })
    view = await mountIntel(<ArticleIndicatorsPanel itemId="item-1" />)
    const panel = view.host.querySelector('details')!
    act(() => { panel.open = true; panel.dispatchEvent(new Event('toggle')) })
    await settle()
    editAutomation(view.host, 'Indicator assessment team', 'team-1')
    await settle()
    expect(view.host.textContent).toContain('suspicious.test')
    act(() => intelButton(view!.host, 'Next indicators').click())
    await settle()
    await waitForReadError(['item-indicators', 'item-1', 'team-1', 2], 403, 'Indicators access removed')
    expect(view.host.textContent).not.toContain('suspicious.test')
    act(() => intelButton(view!.host, 'Previous indicator page').click())
    await settle()
    expect(view.host.textContent).not.toContain('suspicious.test')
    expect(view.host.textContent).not.toContain('Review indicator')
    await act(async () => recovery.resolve({ ...indicatorPageFixture, total: 21 }))
    await settle()
    expect(view.host.textContent).toContain('suspicious.test')
  })

  it('submits the reviewed revisions, locks pending inputs and preserves notes after a conflict', async () => {
    const pending = deferred<unknown>()
    vi.mocked(apiFetch).mockReturnValue(pending.promise)
    const close = vi.fn()
    view = await mountIntel(<IndicatorReviewDialog itemId="item-1" teamId="team-1" indicator={indicatorFixture} baseline={indicatorPageFixture} writable onClose={close} />)
    expect(document.body.textContent).toContain('Maliciousness confidence: not scored')
    editAutomation(document, 'Review reason', 'Reviewed the control infrastructure passage.')
    editAutomation(document, 'Analyst verdict', 'malicious')
    act(() => intelButton(document, 'Save team verdict').click())
    await settle()
    expect(automationField(document, 'Review reason').matches(':disabled')).toBe(true)
    expect(JSON.parse(String(vi.mocked(apiFetch).mock.calls[0][1]?.body))).toMatchObject({ expected_version: 0, source_revision: 7, extraction_revision: 4, verdict: 'malicious' })
    await act(async () => pending.reject(new ApiError('The source changed. Refresh the indicators.', 409, '/items/item-1/indicators/ioc-1/assessment')))
    await settle()
    expect(automationField(document, 'Review reason').value).toContain('control infrastructure')
    expect(document.body.textContent).toContain('The source changed.')
    expect(close).not.toHaveBeenCalled()
    act(() => intelButton(document, 'Close review').click())
    await settle()
    expect(document.querySelector('[role="alertdialog"]')?.textContent).toContain('Discard this unsaved indicator review?')
  })
  it('disables editing for history-only access and pages history within the API bound', async () => {
    vi.mocked(apiFetch).mockResolvedValue({ items: [], total: 11, page: 1, page_size: 10 })
    view = await mountIntel(<IndicatorReviewDialog itemId="item-1" teamId="team-1" indicator={indicatorFixture} baseline={indicatorPageFixture} writable={false} onClose={vi.fn()} />)
    expect(automationField(document, 'Review reason').matches(':disabled')).toBe(true)
    const details = document.querySelector('details')!
    act(() => { details.open = true; details.dispatchEvent(new Event('toggle')) })
    await settle()
    act(() => intelButton(document, 'Next reviews').click())
    await settle()
    expect(vi.mocked(apiFetch).mock.calls.map(([url]) => url)).toContain('/items/item-1/indicators/ioc-1/assessment/history?team_id=team-1&page=2&page_size=10')
  })
})

describe('team suppression drafts', () => {
  const rule: IndicatorSuppression = { id: 'rule-1', team_id: 'team-1', ioc_type: 'domain', value: 'suspicious.test', version: 1, reason: 'Lab system', active: true, expires_at: null, expired: false, updated_at: '2026-09-26T00:00:00Z' }
  it('does not restore denied suppression reasons during a later outage', async () => {
    vi.mocked(apiFetch).mockResolvedValue({ items: [rule], total: 1, page: 1, page_size: 20, can_manage: true })
    view = await mountIntel(<TeamIndicatorSuppressions teamId="team-1" writable />)
    expect(view.host.textContent).toContain('Lab system')
    vi.mocked(apiFetch).mockRejectedValue(new ApiError('Suppression access removed', 403, '/teams/team-1/indicator-suppressions'))
    await act(async () => { await view!.client.invalidateQueries({ queryKey: ['indicator-suppressions'] }) })
    await settle()
    await waitForReadError(['indicator-suppressions', 'team-1', 1], 403, 'Suppression access removed')
    expect(view.host.textContent).not.toContain('Lab system')
    vi.mocked(apiFetch).mockRejectedValue(new ApiError('Suppression service unavailable', 503, '/teams/team-1/indicator-suppressions'))
    await act(async () => { await view!.client.invalidateQueries({ queryKey: ['indicator-suppressions'] }) })
    await settle()
    await waitForReadError(['indicator-suppressions', 'team-1', 1], 503, 'Suppression service unavailable')
    expect(view.host.textContent).not.toContain('Lab system')
    expect(view.host.textContent).not.toContain('Edit suppression')
  })

  it('does not rearm an old suppression draft after denial and fresh recovery', async () => {
    vi.mocked(apiFetch).mockResolvedValue({ items: [rule], total: 1, page: 1, page_size: 20, can_manage: true })
    view = await mountIntel(<TeamIndicatorSuppressions teamId="team-1" writable />)
    act(() => intelButton(view!.host, 'Edit suppression').click())
    editAutomation(view.host, 'Suppression reason', 'Keep my unsubmitted exception notes')
    vi.mocked(apiFetch).mockRejectedValue(new ApiError('Suppression access removed', 403, '/teams/team-1/indicator-suppressions'))
    await act(async () => { await view!.client.invalidateQueries({ queryKey: ['indicator-suppressions'] }) })
    await settle()
    await waitForReadError(['indicator-suppressions', 'team-1', 1], 403, 'Suppression access removed')
    vi.mocked(apiFetch).mockResolvedValue({ items: [rule], total: 1, page: 1, page_size: 20, can_manage: true })
    await act(async () => { await view!.client.invalidateQueries({ queryKey: ['indicator-suppressions'] }) })
    await settle()
    expect(automationField(view.host, 'Suppression reason').value).toBe('Keep my unsubmitted exception notes')
    expect(intelButton(view.host, 'Save suppression').matches(':disabled')).toBe(true)
  })

  it('does not restore an untouched suppression baseline after fresh list recovery', async () => {
    vi.mocked(apiFetch).mockResolvedValue({ items: [rule], total: 1, page: 1, page_size: 20, can_manage: true })
    view = await mountIntel(<TeamIndicatorSuppressions teamId="team-1" writable />)
    act(() => intelButton(view!.host, 'Edit suppression').click())
    expect(automationField(view.host, 'Suppression reason').value).toBe('Lab system')
    vi.mocked(apiFetch).mockRejectedValue(new ApiError('Suppression access removed', 403, '/teams/team-1/indicator-suppressions'))
    await act(async () => { await view!.client.invalidateQueries({ queryKey: ['indicator-suppressions'] }) })
    await settle()
    await waitForReadError(['indicator-suppressions', 'team-1', 1], 403, 'Suppression access removed')
    vi.mocked(apiFetch).mockResolvedValue({ items: [{ ...rule, version: 2, reason: 'Freshly verified exception' }], total: 1, page: 1, page_size: 20, can_manage: true })
    await act(async () => { await view!.client.invalidateQueries({ queryKey: ['indicator-suppressions'] }) })
    await settle()
    expect(view.host.textContent).toContain('Freshly verified exception')
    expect(view.host.querySelector('textarea')).toBeNull()
    act(() => intelButton(view!.host, 'Edit suppression').click())
    expect(automationField(view.host, 'Suppression reason').value).toBe('Freshly verified exception')
  })

  it('does not restore denied suppression history during a later outage', async () => {
    vi.mocked(apiFetch).mockImplementation((path) => Promise.resolve(path.includes('/history') ? { items: [{ version: 1, snapshot: { reason: 'Private suppression history' }, created_at: '2026-09-26T00:00:00Z', actor_user_id: null }], total: 1, page: 1, page_size: 10 } : { items: [rule], total: 1, page: 1, page_size: 20, can_manage: true }) as ReturnType<typeof apiFetch>)
    view = await mountIntel(<TeamIndicatorSuppressions teamId="team-1" writable />)
    act(() => intelButton(view!.host, 'Edit suppression').click())
    const details = view.host.querySelector('details')!
    act(() => { details.open = true; details.dispatchEvent(new Event('toggle')) })
    await settle()
    expect(view.host.textContent).toContain('Private suppression history')
    const denied = new ApiError('History access removed', 403, '/teams/team-1/indicator-suppressions/rule-1/history')
    const beforeDenial = vi.mocked(apiFetch).mock.calls.length
    vi.mocked(apiFetch).mockRejectedValue(denied)
    await act(async () => { await view!.client.invalidateQueries({ queryKey: ['suppression-history'] }) })
    await settle()
    const denialIndex = vi.mocked(apiFetch).mock.calls.findIndex(([path], index) => index >= beforeDenial && path.includes('/rule-1/history?'))
    expect(denialIndex).toBeGreaterThanOrEqual(0)
    await expect(vi.mocked(apiFetch).mock.results[denialIndex].value).rejects.toBe(denied)
    // Scope denial unmounts history and TanStack may revert its cache error
    // during cancellation. The actual denied read and masked UI are authority.
    await vi.waitFor(() => expect(view!.host.querySelector('[role="alert"]')).not.toBeNull())
    expect(view.host.textContent).not.toContain('Private suppression history')
    vi.mocked(apiFetch).mockRejectedValue(new ApiError('History unavailable', 503, '/teams/team-1/indicator-suppressions/rule-1/history'))
    await act(async () => { await view!.client.refetchQueries({ queryKey: ['suppression-history'], type: 'all' }) })
    await settle()
    await waitForReadError(['suppression-history', 'rule-1', 1], 503)
    expect(view.host.textContent).not.toContain('Private suppression history')
  })

  it('keeps the edited revision after background refresh and guards navigation', async () => {
    vi.mocked(apiFetch).mockResolvedValue({ items: [rule], total: 1, page: 1, page_size: 20, can_manage: true })
    view = await mountIntel(<TeamIndicatorSuppressions teamId="team-1" writable />)
    act(() => intelButton(view!.host, 'Edit suppression').click())
    editAutomation(view.host, 'Suppression reason', 'Still checking this exception')
    vi.mocked(apiFetch).mockResolvedValue({ items: [{ ...rule, version: 2, reason: 'Changed elsewhere' }], total: 1, page: 1, page_size: 20, can_manage: true })
    await act(async () => { await view!.client.invalidateQueries({ queryKey: ['indicator-suppressions', 'team-1', 1] }) })
    await settle()
    expect(automationField(view.host, 'Suppression reason').value).toBe('Still checking this exception')
    expect(intelButton(view.host, 'Save suppression').disabled).toBe(true)
    expect(view.host.textContent).toContain('draft remains at revision 1')
    act(() => { void view!.router.navigate('/other') })
    await settle()
    expect(document.querySelector('[role="alertdialog"]')?.textContent).toContain('Discard unsaved indicator suppression changes?')
  })
  it('keeps a failed submission editable and sends an explicit expiry and optimistic version', async () => {
    const pending = deferred<IndicatorSuppression>()
    vi.mocked(apiFetch).mockImplementation((url, options) => options?.method === 'PATCH' ? pending.promise as ReturnType<typeof apiFetch> : Promise.resolve({ items: [rule], total: 1, can_manage: true }) as ReturnType<typeof apiFetch>)
    view = await mountIntel(<TeamIndicatorSuppressions teamId="team-1" writable />)
    act(() => intelButton(view!.host, 'Edit suppression').click())
    editAutomation(view.host, 'Suppression reason', 'Renewed exception')
    act(() => intelButton(view!.host, 'Save suppression').click())
    await settle()
    expect(automationField(view.host, 'Suppression reason').matches(':disabled')).toBe(true)
    const request = vi.mocked(apiFetch).mock.calls.find(([, options]) => options?.method === 'PATCH')!
    expect(JSON.parse(String(request[1]?.body))).toMatchObject({ expected_version: 1, expires_at: null, active: true, reason: 'Renewed exception' })
    await act(async () => pending.reject(new ApiError('Temporary service failure', 503, '/teams/team-1/indicator-suppressions/rule-1')))
    await settle()
    expect(automationField(view.host, 'Suppression reason').value).toBe('Renewed exception')
    expect(automationField(view.host, 'Suppression reason').matches(':disabled')).toBe(false)
  })
})
