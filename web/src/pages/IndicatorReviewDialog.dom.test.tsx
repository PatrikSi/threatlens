// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import { IndicatorReviewDialog } from './IndicatorReviewDialog'
import { TeamIndicatorSuppressions } from './TeamIndicatorSuppressions'
import { deferred, intelButton, mountIntel, settle } from './articleIntelligenceTestSupport'
import { automationField, editAutomation, indicatorFixture, indicatorPageFixture } from './indicatorAutomationTestSupport'
import type { IndicatorSuppression } from '../types/indicators'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...(await original<object>()), apiFetch: vi.fn() }))
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
afterEach(() => { view?.close(); view = undefined; vi.clearAllMocks() })

describe('indicator review lifecycle', () => {
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
  it('keeps the edited revision after background refresh and guards navigation', async () => {
    vi.mocked(apiFetch).mockResolvedValue({ items: [rule], total: 1, page: 1, page_size: 20, can_manage: true })
    view = await mountIntel(<TeamIndicatorSuppressions teamId="team-1" writable />)
    act(() => intelButton(view!.host, 'Edit suppression').click())
    editAutomation(view.host, 'Suppression reason', 'Still checking this exception')
    act(() => view!.client.setQueryData(['indicator-suppressions', 'team-1', 1], { items: [{ ...rule, version: 2, reason: 'Changed elsewhere' }], total: 1, page: 1, page_size: 20, can_manage: true }))
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
