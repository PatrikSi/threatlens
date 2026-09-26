// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import { ReviewedPublicationPanel } from './ReviewedPublicationPanel'
import { deferred, intelButton, mountIntel, settle } from './articleIntelligenceTestSupport'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...(await original<object>()), apiFetch: vi.fn() }))
vi.mock('../hooks/useCurrentUser', () => ({ useCurrentUser: () => ({ isError: false, data: { access: { permissions: ['read:items', 'read:teams', 'write:teams'] } } }) }))
const preview = { fingerprint: 'exact-revision', matched_articles: 1, excluded_or_unreviewed: 2,
  indicators: [{ item_id: 'item', ioc_id: 'ioc', type: 'domain', value: 'evil.net', title: 'Article', assessment_version: 1, evidence_count: 2 }] }
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
let refresh: ReturnType<typeof deferred<typeof preview>> | undefined
beforeEach(() => {
  refresh = undefined
  vi.mocked(apiFetch).mockImplementation((path) => {
    if (path.startsWith('/teams?')) return Promise.resolve({ items: [{ id: 'team', name: 'SOC' }], total: 1, page: 1, page_size: 50 })
    if (path.endsWith('/preview')) return refresh?.promise ?? Promise.resolve(preview)
    return Promise.resolve({ items: [], has_more: false, next_cursor: null })
  })
})
afterEach(() => { view?.close(); view = undefined; vi.clearAllMocks() })
async function open() {
  view = await mountIntel(<ReviewedPublicationPanel filters={{ q: null, feed_ids: [], tag_ids: [], tags_mode: 'any',
    classifications: [], ai_relevance_labels: [], ai_score_min: null, ai_score_max: null, is_read: null,
    is_starred: null, has_article_text: null, since: null, until: null, date_basis: 'first_seen_at', sort: 'first_seen_desc' }} />)
  act(() => intelButton(view!.host, 'Reviewed team publications ▸').click())
  await settle()
  const select = view.host.querySelector('select')!
  act(() => { select.value = 'team'; select.dispatchEvent(new Event('change', { bubbles: true })) })
  await settle()
  act(() => intelButton(view!.host, 'Preview reviewed indicators').click())
  await settle()
}

describe('reviewed publication approval', () => {
  it('hides an approved preview after the team history confirms lost access', async () => {
    await open()
    act(() => (view!.host.querySelector('input[type="checkbox"]') as HTMLInputElement).click())
    expect(view!.host.textContent).toContain('evil.net')
    const implementation = vi.mocked(apiFetch).getMockImplementation()!
    vi.mocked(apiFetch).mockImplementation((path, init) => path.includes('/indicator-publications?')
      ? Promise.reject(new ApiError('Team access removed', 403, path)) : implementation(path, init))
    await act(async () => { await view!.client.invalidateQueries({ queryKey: ['reviewed-publications', 'team'] }) })
    await settle()
    expect(view!.host.textContent).not.toContain('evil.net')
    expect(view!.host.textContent).not.toContain('Approve reviewed publication')
  })

  it('invalidates old previews during refresh and after a failed refresh', async () => {
    await open()
    act(() => (view!.host.querySelector('input[type="checkbox"]') as HTMLInputElement).click())
    expect(intelButton(view!.host, 'Approve reviewed publication').disabled).toBe(false)
    refresh = deferred()
    act(() => intelButton(view!.host, 'Preview reviewed indicators').click())
    await settle()
    expect(view!.host.textContent).not.toContain('Approve reviewed publication')
    await act(async () => refresh!.reject(new Error('Preview unavailable')))
    await settle()
    expect(view!.host.textContent).not.toContain('Approve reviewed publication')
    expect(view!.host.querySelector('[role="alert"]')?.textContent).toContain('Preview unavailable')
  })

  it('retries a failed publication with the same idempotency key and reviewed fingerprint', async () => {
    await open()
    const implementation = vi.mocked(apiFetch).getMockImplementation()!
    vi.mocked(apiFetch).mockImplementation((path, init) => {
      if (path === '/teams/team/indicator-publications' && init?.method === 'POST') return Promise.reject(new Error('Connection interrupted'))
      return implementation(path, init)
    })
    act(() => (view!.host.querySelector('input[type="checkbox"]') as HTMLInputElement).click())
    for (let attempt = 0; attempt < 2; attempt += 1) {
      act(() => intelButton(view!.host, 'Approve reviewed publication').click())
      await settle()
    }
    const requests = vi.mocked(apiFetch).mock.calls.filter(([path, init]) => path.endsWith('/indicator-publications') && init?.method === 'POST')
    expect(requests).toHaveLength(2)
    expect(requests[0][1]?.body).toBe(requests[1][1]?.body)
    expect(JSON.parse(String(requests[0][1]?.body))).toMatchObject({ preview_fingerprint: 'exact-revision', format: 'stix', marking: 'TLP:AMBER' })
  })
})
