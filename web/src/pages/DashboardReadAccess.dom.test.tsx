// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import type { ItemDetail, ItemListEntry } from '../types/api'
import { DashboardPage } from './DashboardPage'
import { deferred, intelButton, mountIntel, settle } from './articleIntelligenceTestSupport'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...await original<object>(), apiFetch: vi.fn() }))
vi.mock('../hooks/useCurrentUser', () => ({ useCurrentUser: () => ({ data: { id: 'dashboard-reader', role: 'admin', access: { permissions: ['*:*'] }, features: { ai_daily_brief_enabled: true } }, isError: false, isLoading: false }) }))
const workspace = vi.hoisted(() => ({ isLoading: false, isDegraded: false, effective: { role: 'admin' }, userContext: { role: 'admin' }, model: { dashboardPanelIds: ['rss'] } }))
vi.mock('../workspace/useWorkspace', () => ({ useWorkspace: () => workspace }))
// These independently tested article workflows are outside this dashboard
// query/admission boundary; keep the real dashboard and article detail renderer.
vi.mock('./ArticleExtractionPanel', () => ({ ArticleExtractionPanel: () => null }))
vi.mock('./ArticleIndicatorsPanel', () => ({ ArticleIndicatorsPanel: () => null }))
vi.mock('./ArticleTeamAssessment', () => ({ ArticleTeamAssessment: () => null }))

let view: Awaited<ReturnType<typeof mountIntel>> | undefined
afterEach(() => { view?.close(); view = undefined; workspace.model.dashboardPanelIds = ['rss']; localStorage.clear(); vi.clearAllMocks() })
const item: ItemListEntry = { id: 'private-item', feed_id: 'feed', feed_name: 'Private source', title: 'Private dashboard row', url: 'https://evidence.invalid/article', canonical_url: null, summary: 'Private dashboard summary', published_at: null, first_seen_at: '2026-11-01T00:00:00Z', status: 'content_fetched', classification: null, is_read: true, is_starred: false, tags: [], ai_relevance_score: null, ai_relevance_label: null, ai_status: null }
const detail: ItemDetail = { ...item, source_guid: null, classification: null, last_error: null, ai_insight: null, state: { is_read: true, is_starred: false, note: 'Saved private note', updated_at: item.first_seen_at }, article: { final_url: item.url, retrieved_at: item.first_seen_at, http_status: 200, content_type: 'text/html', title_extracted: item.title, text: 'Private retained article body', extraction_method: 'readability', language: 'en', word_count: 4, fetch_ms: 10, error: null, content_purged_at: null } }

function list(path: string) {
  const params = new URL(path, 'https://fixture.invalid').searchParams
  const page = Number(params.get('page') ?? 1)
  return { items: [{ ...item, ...(page === 2 ? { id: 'second-item', title: 'Private second page row' } : {}) }], total: 100, page, page_size: Number(params.get('page_size') ?? 20) }
}
async function openDetail() {
  act(() => view!.host.querySelector<HTMLButtonElement>(`[aria-label="Open article details: ${item.title}"]`)!.click())
  await settle()
  expect(view!.host.textContent).toContain('Private retained article body')
}

describe('dashboard private read authority', () => {
  it.each([
    { resource: 'feeds', path: '/feeds', text: 'Private dashboard feed', value: [{ id: 'feed', name: 'Private dashboard feed', enabled: true, error_count: 0, last_success_at: '2026-11-01T00:00:00Z', last_error: null, has_unreadable_url: false }] },
    { resource: 'tags', path: '/tags', text: 'Private dashboard tag', value: [{ id: 'tag', name: 'Private dashboard tag' }] },
    { resource: 'saved views', path: '/views', text: 'Private dashboard arrangement', value: [{ id: 'saved-view', user_id: 'dashboard-reader', name: 'Private dashboard arrangement', created_at: '2026-11-01T00:00:00Z', query_json: {} }] },
    { resource: 'alert interests', path: '/alerts?include_disabled=false', text: 'Private dashboard interest', value: [{ id: 'interest', name: 'Private dashboard interest', category: 'Private interest category', keywords: [], enabled: true }] },
    { resource: 'alert matches', path: '/alerts/matches?', text: 'Private dashboard match', value: { items: [{ ...item, title: 'Private dashboard match', matches: [] }], total: 1, page: 1, page_size: 25 } },
    { resource: 'daily briefs', path: '/ai/daily-briefs', text: 'Private dashboard briefing', value: [{ id: 'brief', title: 'Private briefing title', brief_text: 'Private dashboard briefing', brief_date: '2026-11-01', status: 'ready', window_start: '2026-10-31T00:00:00Z', window_end: '2026-11-01T00:00:00Z', generated_at: '2026-11-01T00:00:00Z', item_count: 0, items: [], key_points: [], recommended_actions: [], model: 'fixture', error: null }] },
  ])('keeps denied dashboard $resource private through a later outage', async ({ resource, path: target, text, value }) => {
    workspace.model.dashboardPanelIds = ['rss', 'alerts', 'daily_brief']
    let status = 200
    const rejected: number[] = []
    vi.mocked(apiFetch).mockImplementation((path) => {
      if (path.startsWith(target)) {
        if (status === 200) return Promise.resolve(value) as never
        rejected.push(status)
        return Promise.reject(new ApiError(`${resource} unavailable`, status, path))
      }
      if (path.startsWith('/items?')) return Promise.resolve(list(path)) as never
      if (path.startsWith('/alerts/matches?')) return Promise.resolve({ items: [], total: 0, page: 1, page_size: 25 }) as never
      return Promise.resolve([]) as never
    })
    view = await mountIntel(<DashboardPage />, '/')
    await settle()
    expect(view.host.textContent).toContain(text)
    for (const next of [403, 503]) {
      status = next
      await act(async () => { await view!.client.invalidateQueries() })
      await settle()
      expect(rejected).toContain(next)
      expect(view.host.textContent).not.toContain(text)
      expect(view.host.textContent).toContain(item.title)
    }
    status = 200
    await act(async () => { intelButton(view!.host, 'Refresh dashboard').click() })
    await settle()
    expect(view.host.textContent).toContain(text)
  })

  it('keeps denied dashboard rows hidden through a later outage', async () => {
    let status = 200
    vi.mocked(apiFetch).mockImplementation((path) => path.startsWith('/items?')
      ? status === 200 ? Promise.resolve(list(path)) : Promise.reject(new ApiError('Dashboard item access withdrawn', status, path))
      : Promise.resolve([]) as never)
    view = await mountIntel(<DashboardPage />, '/')
    await settle()
    expect(view.host.textContent).toContain(item.title)
    for (const next of [403, 503]) {
      status = next
      await act(async () => { await view!.client.invalidateQueries() })
      await settle()
      expect(view.host.textContent).not.toContain(item.title)
      expect(view.host.textContent).not.toContain(item.summary)
    }
  })

  it('keeps cached sibling dashboard pages hidden after list denial while the fresh read is pending', async () => {
    let status = 200
    const pending = deferred<unknown>()
    vi.mocked(apiFetch).mockImplementation((path) => path.startsWith('/items?')
      ? status === 200 ? Promise.resolve(list(path)) : status === 403 ? Promise.reject(new ApiError('Dashboard item access withdrawn', status, path)) : pending.promise as never
      : Promise.resolve([]) as never)
    view = await mountIntel(<DashboardPage />, '/')
    await settle()
    act(() => intelButton(view!.host, 'Next').click())
    await settle()
    expect(view.host.textContent).toContain('Private second page row')
    status = 403
    await act(async () => { await view!.client.invalidateQueries() })
    await settle()
    expect(view.host.textContent).not.toContain('Private second page row')
    status = 503
    act(() => intelButton(view!.host, 'Prev').click())
    await settle()
    expect(view.host.textContent).not.toContain(item.title)
  })

  it('keeps denied expanded article bodies hidden while independently readable rows remain visible', async () => {
    let status = 200
    vi.mocked(apiFetch).mockImplementation((path) => path === `/items/${item.id}`
      ? status === 200 ? Promise.resolve(detail) : Promise.reject(new ApiError('Article access withdrawn', status, path))
      : Promise.resolve(path.startsWith('/items?') ? list(path) : []) as never)
    view = await mountIntel(<DashboardPage />, '/')
    await settle()
    await openDetail()
    for (const next of [403, 503]) {
      status = next
      await act(async () => { await view!.client.invalidateQueries() })
      await settle()
      expect(view.host.textContent).not.toContain('Private retained article body')
      expect(view.host.textContent).toContain(item.title)
      expect(view.host.querySelector('[aria-label^="Analyst notes for"]')).toBeNull()
    }
  })

  it('retains ordinary article outage context and local notes while pausing its writes', async () => {
    let unavailable = false
    vi.mocked(apiFetch).mockImplementation((path) => path === `/items/${item.id}`
      ? unavailable ? Promise.reject(new ApiError('Article temporarily unavailable', 503, path)) : Promise.resolve(detail)
      : Promise.resolve(path.startsWith('/items?') ? list(path) : []) as never)
    view = await mountIntel(<DashboardPage />, '/')
    await settle()
    await openDetail()
    const draft = view.host.querySelector<HTMLTextAreaElement>('[aria-label^="Analyst notes for"]')!
    act(() => { Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(draft, 'Unsent local note'); draft.dispatchEvent(new Event('input', { bubbles: true })) })
    unavailable = true
    await act(async () => { await view!.client.invalidateQueries() })
    await settle()
    expect(view.host.textContent).toContain('Private retained article body')
    expect(draft.value).toBe('Unsent local note')
    for (const label of ['Save Notes', 'Mark Unread', 'Star']) expect(intelButton(view.host, label).disabled).toBe(true)
    expect(vi.mocked(apiFetch).mock.calls.some(([, options]) => options?.method === 'POST')).toBe(false)
  })

  it('retires the original article preview when its dashboard source read is denied', async () => {
    let status = 200
    const rejected: number[] = []
    vi.mocked(apiFetch).mockImplementation((path) => {
      if (path.startsWith('/items?')) {
        if (status === 200) return Promise.resolve(list(path)) as never
        rejected.push(status)
        return Promise.reject(new ApiError('Dashboard item access withdrawn', status, path))
      }
      return Promise.resolve([]) as never
    })
    view = await mountIntel(<DashboardPage />, '/')
    await settle()
    act(() => [...view!.host.querySelectorAll('button')].find((button) => button.textContent?.includes('Preview Original'))!.click())
    expect(document.querySelector('iframe[title]')).not.toBeNull()
    for (const next of [403, 503]) {
      status = next
      await act(async () => { await view!.client.invalidateQueries() })
      await settle()
      expect(rejected).toContain(next)
      expect(document.querySelector('iframe[title]')).toBeNull()
      expect(document.body.textContent).not.toContain(item.title)
    }
    status = 200
    await act(async () => { intelButton(view!.host, 'Refresh dashboard').click() })
    await settle()
    expect(view.host.textContent).toContain(item.title)
    expect(document.querySelector('iframe[title]')).toBeNull()
    act(() => [...view!.host.querySelectorAll('button')].find((button) => button.textContent?.includes('Preview Original'))!.click())
    expect(document.querySelector('iframe[title]')).not.toBeNull()
  })

  it('reconciles article state and notes through canonical dashboard reads after successful writes', async () => {
    let saved = structuredClone(detail)
    const reads: string[] = []
    vi.mocked(apiFetch).mockImplementation((path, options) => {
      if (options?.method === 'POST') {
        const body = JSON.parse(String(options.body)) as { is_starred?: boolean; note?: string }
        if (path.endsWith('/star')) saved = { ...saved, state: { ...saved.state, is_starred: body.is_starred === true } }
        if (path.endsWith('/note')) saved = { ...saved, state: { ...saved.state, note: body.note ?? '' } }
        return Promise.resolve({}) as never
      }
      reads.push(path)
      if (path === `/items/${item.id}`) return Promise.resolve(saved) as never
      if (path.startsWith('/items?')) return Promise.resolve({ ...list(path), items: [{ ...item, is_starred: saved.state.is_starred }] }) as never
      return Promise.resolve([]) as never
    })
    view = await mountIntel(<DashboardPage />, '/')
    await settle()
    await openDetail()
    act(() => intelButton(view!.host, 'Star').click())
    await settle()
    expect(intelButton(view.host, 'Unstar').disabled).toBe(false)
    expect(reads.filter((path) => path === `/items/${item.id}`).length).toBeGreaterThanOrEqual(2)
    expect(reads.filter((path) => path.startsWith('/items?')).length).toBeGreaterThanOrEqual(2)
    const draft = view.host.querySelector<HTMLTextAreaElement>('[aria-label^="Analyst notes for"]')!
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(draft, 'Canonically saved note')
      draft.dispatchEvent(new Event('input', { bubbles: true }))
    })
    act(() => intelButton(view!.host, 'Save Notes').click())
    await settle()
    expect(saved.state.note).toBe('Canonically saved note')
    expect(draft.value).toBe('Canonically saved note')
    expect(view.host.textContent).toContain('Saved analyst notes.')
  })

  it('keeps a late note write from restoring denied article metadata or replacing the local draft', async () => {
    let status = 200
    let saved = detail
    const pending = deferred<unknown>()
    vi.mocked(apiFetch).mockImplementation((path, options) => {
      if (options?.method === 'POST') return pending.promise as never
      if (path === `/items/${item.id}`) return status === 200
        ? Promise.resolve(saved) as never : Promise.reject(new ApiError('Article unavailable', status, path))
      return Promise.resolve(path.startsWith('/items?') ? list(path) : []) as never
    })
    view = await mountIntel(<DashboardPage />, '/')
    await settle()
    await openDetail()
    const draft = view.host.querySelector<HTMLTextAreaElement>('[aria-label^="Analyst notes for"]')!
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(draft, 'Unsent local note')
      draft.dispatchEvent(new Event('input', { bubbles: true }))
    })
    act(() => intelButton(view!.host, 'Save Notes').click())
    await settle()
    expect(vi.mocked(apiFetch).mock.calls.some(([path, options]) => path.endsWith('/note') && options?.method === 'POST')).toBe(true)
    for (const next of [403, 503]) {
      status = next
      await act(async () => { await view!.client.invalidateQueries({ queryKey: ['dashboard', 'item', item.id] }) })
      await settle()
      expect(view.host.textContent).not.toContain('Private retained article body')
    }
    await act(async () => { pending.resolve({}); await pending.promise })
    await settle()
    expect(view.host.textContent).not.toContain('Private retained article body')
    expect(view.host.querySelector('[aria-label^="Analyst notes for"]')).toBeNull()
    saved = { ...detail, state: { ...detail.state, note: 'Fresh server note' }, article: { ...detail.article!, text: 'Fresh permitted article body' } }
    status = 200
    await act(async () => { intelButton(view!.host, 'Refresh dashboard').click() })
    await settle()
    expect(view.host.textContent).toContain('Fresh permitted article body')
    expect(view.host.querySelector<HTMLTextAreaElement>('[aria-label^="Analyst notes for"]')?.value).toBe('Unsent local note')
  })
})
