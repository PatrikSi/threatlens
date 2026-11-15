// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import type { ItemDetail, ItemListEntry } from '../types/api'
import { DashboardPage } from './DashboardPage'
import { mountIntel, settle } from './articleIntelligenceTestSupport'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...await original<object>(), apiFetch: vi.fn() }))
vi.mock('../hooks/useCurrentUser', () => ({ useCurrentUser: () => ({ data: { id: 'dashboard-reader', role: 'admin', access: { permissions: ['*:*'] }, features: {} }, isError: false, isLoading: false }) }))
vi.mock('../workspace/useWorkspace', () => ({ useWorkspace: () => ({ isLoading: false, isDegraded: false, effective: { role: 'admin' }, userContext: { role: 'admin' }, model: { dashboardPanelIds: ['rss'] } }) }))
vi.mock('./ArticleExtractionPanel', () => ({ ArticleExtractionPanel: () => null }))
vi.mock('./ArticleIndicatorsPanel', () => ({ ArticleIndicatorsPanel: () => null }))
vi.mock('./ArticleTeamAssessment', () => ({ ArticleTeamAssessment: () => null }))
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
afterEach(() => { view?.close(); view = undefined; localStorage.clear(); vi.clearAllMocks() })

it('keeps a known same-item withdrawal blocking row writes after closing its inspector', async () => {
  const item: ItemListEntry = { id: 'private-item', feed_id: 'feed', feed_name: 'Private source', title: 'Private dashboard row', url: 'https://evidence.invalid/article', canonical_url: null, summary: 'Private summary', published_at: null, first_seen_at: '2026-11-01T00:00:00Z', status: 'content_fetched', classification: null, is_read: false, is_starred: false, tags: [], ai_relevance_score: null, ai_relevance_label: null, ai_status: null }
  const detail: ItemDetail = { ...item, source_guid: null, classification: null, last_error: null, ai_insight: null, state: { is_read: false, is_starred: false, note: '', updated_at: item.first_seen_at }, article: { final_url: item.url, retrieved_at: item.first_seen_at, http_status: 200, content_type: 'text/html', title_extracted: item.title, text: 'Initially permitted article body', extraction_method: 'readability', language: 'en', word_count: 4, fetch_ms: 10, error: null, content_purged_at: null } }
  const other = { ...item, id: 'unrelated-item', title: 'Independently permitted row' }
  let withdrawn = false
  let writes = 0
  const writePaths: string[] = []
  const readErrors: number[] = []
  vi.mocked(apiFetch).mockImplementation((path, options) => {
    if (options?.method === 'POST') {
      writes += 1
      writePaths.push(path)
      // The initial row-only mark-read is allowed to start, but its ordinary
      // write outage leaves the source unread. The backend still fences a later
      // request; this control concerns preventing a stale UI action attempt.
      return Promise.reject(new ApiError('Read status could not be changed', withdrawn && path.includes(item.id) ? 404 : 503, path))
    }
    if (path === `/items/${item.id}`) {
      if (!withdrawn) return Promise.resolve(detail) as never
      readErrors.push(404)
      return Promise.reject(new ApiError('Item not found', 404, path))
    }
    return Promise.resolve(path.startsWith('/items?') ? { items: [item, other], total: 2, page: 1, page_size: 25 } : []) as never
  })
  view = await mountIntel(<DashboardPage />, '/')
  await settle()
  act(() => view!.host.querySelector<HTMLButtonElement>(`[aria-label="Open article details: ${item.title}"]`)!.click())
  await settle()
  expect(view.host.textContent).toContain('Initially permitted article body')
  expect(writes).toBe(1)
  withdrawn = true
  await act(async () => { await view!.client.invalidateQueries({ queryKey: ['dashboard', 'item', item.id] }) })
  await settle()
  expect(readErrors).toEqual([404])
  expect(view.host.textContent).not.toContain('Initially permitted article body')
  act(() => view!.host.querySelector<HTMLButtonElement>(`[aria-label="Close article details: ${item.title}"]`)!.click())
  await settle()
  act(() => [...view!.host.querySelectorAll('button')].find((button) => button.textContent?.includes('Preview Original'))!.click())
  await settle()
  expect(writes).toBe(1)
  const otherRow = view.host.querySelector(`[aria-label="Open article details: ${other.title}"]`)!.closest('article')!
  act(() => [...otherRow.querySelectorAll('button')].find((button) => button.textContent?.includes('Preview Original'))!.click())
  await settle()
  expect(writePaths).toEqual([`/items/${item.id}/read`, `/items/${other.id}/read`])
})
