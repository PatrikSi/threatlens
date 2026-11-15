// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { apiFetch } from '../api/client'
import { isQueryAccessDenied } from '../hooks/useQueryAccessScope'
import type { ItemDetail, ItemListEntry } from '../types/api'
import { DashboardPage } from './DashboardPage'
import { deferred, intelButton, mountIntel, settle } from './articleIntelligenceTestSupport'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const collision = vi.hoisted(() => ({ seededStores: 0 }))
vi.mock('../hooks/queryAccessStore', async (original) => {
  const actual = await original<typeof import('../hooks/queryAccessStore')>()
  return {
    ...actual,
    createQueryAccessStore: () => {
      const store = actual.createQueryAccessStore()
      const OriginalArray = globalThis.Uint32Array
      // Force a conservative summary collision, without mocking any scope,
      // admission, query, or authorize method. Restore the constructor before
      // returning control to React. This is not a capacity measurement.
      vi.stubGlobal('Uint32Array', class extends OriginalArray {
        constructor(length: number) { super(length); this.fill(0xffffffff) }
      })
      try { store.get('controlled-unrelated-withdrawal').deny(0) }
      finally { vi.stubGlobal('Uint32Array', OriginalArray) }
      collision.seededStores += 1
      return store
    },
  }
})
vi.mock('../api/client', async (original) => ({ ...await original<object>(), apiFetch: vi.fn() }))
vi.mock('../hooks/useCurrentUser', () => ({ useCurrentUser: () => ({
  data: { id: 'dashboard-reader', role: 'admin', access: { permissions: ['*:*'] }, features: {} },
  isError: false, isLoading: false,
}) }))
vi.mock('../workspace/useWorkspace', () => ({ useWorkspace: () => ({
  isLoading: false, isDegraded: false, effective: { role: 'admin' },
  userContext: { role: 'admin' }, model: { dashboardPanelIds: ['rss'] },
}) }))
// Independently tested intelligence panels are outside this read-admission
// boundary. Keep the actual Dashboard, inspector, action hooks, and iframe.
vi.mock('./ArticleExtractionPanel', () => ({ ArticleExtractionPanel: () => null }))
vi.mock('./ArticleIndicatorsPanel', () => ({ ArticleIndicatorsPanel: () => null }))
vi.mock('./ArticleTeamAssessment', () => ({ ArticleTeamAssessment: () => null }))

let view: Awaited<ReturnType<typeof mountIntel>> | undefined
afterEach(() => {
  view?.close()
  view = undefined
  localStorage.clear()
  collision.seededStores = 0
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

it('recovers original preview and automatic mark-read from a conservative history collision only after a fresh article-details GET', async () => {
  const item: ItemListEntry = {
    id: 'collision-item', feed_id: 'feed', feed_name: 'Readable source', title: 'Readable Dashboard row',
    url: 'https://evidence.invalid/article', canonical_url: null, summary: 'Independently permitted list summary',
    published_at: null, first_seen_at: '2026-11-01T00:00:00Z', status: 'content_fetched', classification: null,
    is_read: false, is_starred: false, tags: [], ai_relevance_score: null, ai_relevance_label: null, ai_status: null,
  }
  const detail: ItemDetail = {
    ...item, source_guid: null, classification: null, last_error: null, ai_insight: null,
    state: { is_read: false, is_starred: false, note: '', updated_at: item.first_seen_at },
    article: {
      final_url: item.url, retrieved_at: item.first_seen_at, http_status: 200, content_type: 'text/html',
      title_extracted: item.title, text: 'Freshly verified article details', extraction_method: 'readability',
      language: 'en', word_count: 4, fetch_ms: 10, error: null, content_purged_at: null,
    },
  }
  const firstDetail = deferred<ItemDetail>()
  const detailRequests: string[] = []
  const writes: Array<{ path: string; body: unknown }> = []
  let read = false
  vi.mocked(apiFetch).mockImplementation((path, options) => {
    if (options?.method === 'POST') {
      writes.push({ path, body: JSON.parse(String(options.body)) })
      read = true
      return Promise.resolve({}) as never
    }
    if (path === `/items/${item.id}`) {
      detailRequests.push(path)
      return (detailRequests.length === 1 ? firstDetail.promise
        : Promise.resolve({ ...detail, state: { ...detail.state, is_read: read } })) as never
    }
    return Promise.resolve(path.startsWith('/items?')
      ? { items: [{ ...item, is_read: read }], total: 1, page: 1, page_size: 25 } : []) as never
  })
  view = await mountIntel(<DashboardPage />, '/')
  await settle()
  expect(collision.seededStores).toBe(1)
  expect(view.host.textContent).toContain(item.title)
  expect(detailRequests).toEqual([])
  // This never-requested article has no HTTP denial: only the controlled
  // conservative summary collision requires its own fresh verification.
  expect(isQueryAccessDenied(view.client, ['dashboard', 'item', item.id])).toBe(true)
  const previewOriginal = () => [...view!.host.querySelectorAll('button')]
    .find((button) => button.textContent?.includes('Preview Original'))!
  act(() => previewOriginal().click())
  await settle()
  expect(document.querySelector('iframe[title]')).toBeNull()
  expect(detailRequests).toEqual([])
  expect(writes).toEqual([])

  // Preview Original cannot verify access. The real article-details control
  // must issue a GET, and a pending request must not lift the conservative fence.
  const detailsButton = view.host.querySelector<HTMLButtonElement>(`[aria-label="Open article details: ${item.title}"]`)!
  expect(detailsButton.disabled).toBe(false)
  act(() => detailsButton.click())
  await vi.waitFor(() => expect(detailRequests).toEqual([`/items/${item.id}`]))
  expect(view.client.getQueryState(['dashboard', 'item', item.id])?.fetchStatus).toBe('fetching')
  expect(isQueryAccessDenied(view.client, ['dashboard', 'item', item.id])).toBe(true)
  expect(writes).toEqual([])
  act(() => previewOriginal().click())
  await settle()
  expect(document.querySelector('iframe[title]')).toBeNull()
  expect(detailRequests).toHaveLength(1)
  expect(writes).toEqual([])

  await act(async () => { firstDetail.resolve(detail); await Promise.resolve() })
  await vi.waitFor(async () => {
    await settle()
    expect(view!.client.getQueryState(['dashboard', 'item', item.id])?.status).toBe('success')
    expect(view!.host.textContent).toContain(detail.article!.text)
  })
  expect(isQueryAccessDenied(view.client, ['dashboard', 'item', item.id])).toBe(false)
  expect(isQueryAccessDenied(view.client, ['dashboard', 'item', 'another-unverified-item'])).toBe(true)
  expect(intelButton(view.host, 'Mark Read').disabled).toBe(false)
  expect(writes).toEqual([])
  act(() => view!.host.querySelector<HTMLButtonElement>(`[aria-label="Close article details: ${item.title}"]`)!.click())
  await settle()
  act(() => previewOriginal().click())
  await vi.waitFor(async () => {
    await settle()
    expect(document.querySelector('iframe[title]')?.getAttribute('src')).toContain(`/items/${item.id}/article-preview`)
    expect(writes).toEqual([{ path: `/items/${item.id}/read`, body: { is_read: true } }])
  })
})
