// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import { AlertsPage } from './AlertsPage'
import { mountIntel, settle } from './articleIntelligenceTestSupport'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...await original<object>(), apiFetch: vi.fn() }))
vi.mock('../hooks/useCurrentUser', () => ({ useCurrentUser: () => ({ data: { id: 'analyst', role: 'analyst', features: {}, access: { permissions: ['*:*'] } }, isError: false }) }))
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
afterEach(() => { view?.close(); view = undefined; vi.clearAllMocks() })
const rule = { id: 'private-rule', user_id: 'analyst', name: 'Private watchlist rule', category: 'software', keywords: ['private-keyword'], enabled: true, severity: 'high', revision: 3, row_version: 4, durable_since: null, suppression_until: null, suppression_reason: null, created_at: '2026-11-01T00:00:00Z', updated_at: '2026-11-01T00:00:00Z' }

describe('rule authority during real query refreshes', () => {
  it('hides private corpus preview matches after denial and keeps the local keyword draft through a later outage', async () => {
    let status = 200
    vi.mocked(apiFetch).mockImplementation((path) => path === '/alerts/preview'
      ? status === 200 ? Promise.resolve({ items: [{ id: 'private-match', title: 'Private corpus match', summary: 'Private preview excerpt', feed_name: 'Private feed', first_seen_at: rule.created_at, matches: [{ category: 'software', matched_keywords: ['operator-keyword'] }] }], total: 1, page: 1, page_size: 5 })
        : Promise.reject(new ApiError('Preview read unavailable', status, path))
      : Promise.resolve([]) as never)
    view = await mountIntel(<AlertsPage />, '/?view=rules&queue_scope=personal')
    const keywords = view.host.querySelector<HTMLTextAreaElement>('#alert-interest-keywords')!
    act(() => { Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(keywords, 'operator-keyword'); keywords.dispatchEvent(new Event('input', { bubbles: true })) })
    await settle()
    expect(view.host.textContent).toContain('Private corpus match')
    for (const next of [403, 503]) {
      status = next
      await act(async () => { await view!.client.invalidateQueries({ queryKey: ['alerts', 'preview'] }) })
      await settle()
      expect(view.host.textContent).not.toContain('Private corpus match')
      expect(view.host.textContent).not.toContain('Private preview excerpt')
      expect(keywords.value).toBe('operator-keyword')
    }
  })

  it('keeps denied cached rules hidden through an outage and sibling filter re-entry', async () => {
    let status = 200
    vi.mocked(apiFetch).mockImplementation((path) => path.startsWith('/alerts?')
      ? status === 200 ? Promise.resolve([rule]) : Promise.reject(new ApiError('Rule read unavailable', status, path))
      : Promise.resolve([]) as never)
    view = await mountIntel(<AlertsPage />, '/?view=rules&queue_scope=personal')
    expect(view.host.textContent).toContain(rule.name)
    const showDisabled = [...view.host.querySelectorAll('label')].find((label) => label.textContent?.includes('Include disabled'))?.querySelector('input')
    expect(showDisabled).toBeTruthy()
    act(() => showDisabled!.click())
    await settle()
    status = 403
    await act(async () => { await view!.client.invalidateQueries({ queryKey: ['alerts', true] }) })
    await settle()
    expect(view.host.textContent).not.toContain(rule.name)
    status = 503
    act(() => showDisabled!.click())
    await settle()
    expect(view.host.textContent).not.toContain(rule.name)
  })

  it('retains ordinary outage rule context while pausing edit, toggle, delete and an existing delete confirmation', async () => {
    let unavailable = false
    vi.mocked(apiFetch).mockImplementation((path) => path.startsWith('/alerts?')
      ? unavailable ? Promise.reject(new ApiError('Rules temporarily unavailable', 503, path)) : Promise.resolve([rule])
      : Promise.resolve([]) as never)
    view = await mountIntel(<AlertsPage />, '/?view=rules&queue_scope=personal')
    const deleteButton = view.host.querySelector<HTMLButtonElement>(`button[aria-label="Delete alert rule ${rule.name}"]`)!
    act(() => deleteButton.click())
    expect(document.querySelector('[role="alertdialog"]')).not.toBeNull()
    unavailable = true
    await act(async () => { await view!.client.invalidateQueries({ queryKey: ['alerts', false] }) })
    await settle()
    expect(view.host.textContent).toContain(rule.name)
    for (const action of ['Edit', 'Disable', 'Delete']) {
      expect(view.host.querySelector<HTMLButtonElement>(`button[aria-label="${action} alert rule ${rule.name}"]`)!.disabled).toBe(true)
    }
    const confirm = [...document.querySelectorAll('button')].find((button) => button.textContent?.trim() === 'Delete alert')
    expect(!confirm || confirm.disabled).toBe(true)
    expect(vi.mocked(apiFetch).mock.calls.some(([, options]) => ['PATCH', 'DELETE'].includes(options?.method ?? ''))).toBe(false)
  })
})
