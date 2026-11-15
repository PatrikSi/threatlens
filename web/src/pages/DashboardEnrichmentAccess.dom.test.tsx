// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import type { ItemDetail, ItemListEntry } from '../types/api'
import { DashboardPage } from './DashboardPage'
import { intelButton, mountIntel, settle } from './articleIntelligenceTestSupport'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...await original<object>(), apiFetch: vi.fn() }))
vi.mock('../hooks/useCurrentUser', () => ({ useCurrentUser: () => ({ data: { id: 'dashboard-reader', role: 'admin', access: { permissions: ['*:*'] }, features: { ai_structured_extraction_enabled: true } }, isError: false, isLoading: false }) }))
vi.mock('../workspace/useWorkspace', () => ({ useWorkspace: () => ({ isLoading: false, isDegraded: false, effective: { role: 'admin' }, userContext: { role: 'admin' }, model: { dashboardPanelIds: ['rss'] } }) }))
// Keep the real extraction panel/continuation; independent team review widgets
// do not participate in the article detail refresh/admission contract.
vi.mock('./ArticleIndicatorsPanel', () => ({ ArticleIndicatorsPanel: () => null }))
vi.mock('./ArticleTeamAssessment', () => ({ ArticleTeamAssessment: () => null }))
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
afterEach(() => { view?.close(); view = undefined; localStorage.clear(); vi.clearAllMocks() })
const item: ItemListEntry = { id: 'private-item', feed_id: 'feed', feed_name: 'Private source', title: 'Private dashboard row', url: 'https://evidence.invalid/article', canonical_url: null, summary: 'Private summary', published_at: null, first_seen_at: '2026-11-01T00:00:00Z', status: 'content_fetched', classification: null, is_read: true, is_starred: false, tags: [], ai_relevance_score: null, ai_relevance_label: null, ai_status: null }
const detail: ItemDetail = {
  ...item, source_guid: null, classification: null, last_error: null,
  state: { is_read: true, is_starred: false, note: '', updated_at: item.first_seen_at },
  article: { final_url: item.url, retrieved_at: item.first_seen_at, http_status: 200, content_type: 'text/html', title_extracted: item.title, text: 'Previous article evidence body', extraction_method: 'readability', language: 'en', word_count: 4, fetch_ms: 10, error: null, content_purged_at: null },
  ai_insight: {
    status: 'ready', summary_text: null, relevance_score: null, relevance_label: null, relevance_reasons: [], model: null, generated_at: null, error: null,
    extraction_progress: { planner_version: 1, source_hash: 'a'.repeat(64), progress_revision: 'b'.repeat(64), normalized_text_chars: 100000, processed_chars: 64000, uncovered_chars: 36000, reserved_tokens: 50000, token_budget: 64000, call_limit: 8, output_limited: false, sections: [] },
  },
}
function normalRead(path: string, saved: ItemDetail) {
  if (path === `/items/${item.id}`) return saved
  if (path.startsWith('/items?')) return { items: [item], total: 1, page: 1, page_size: 25 }
  return []
}
async function openDetail() {
  act(() => view!.host.querySelector<HTMLButtonElement>(`[aria-label="Open article details: ${item.title}"]`)!.click())
  await settle()
  expect(view!.host.textContent).toContain('Previous article evidence body')
}

describe('dashboard article evidence admission', () => {
  it.each(['manual evidence refresh', 'successful extraction continuation'])('reconciles dashboard detail after %s', async (action) => {
    let saved = detail
    let detailReads = 0
    vi.mocked(apiFetch).mockImplementation((path, options) => {
      if (options?.method === 'POST') {
        saved = { ...detail, article: { ...detail.article!, text: 'Fresh article evidence body' } }
        return Promise.resolve({ run_id: 'continued-run' }) as never
      }
      if (path === `/items/${item.id}`) detailReads += 1
      return Promise.resolve(normalRead(path, saved)) as never
    })
    view = await mountIntel(<DashboardPage />, '/')
    await settle()
    await openDetail()
    if (action === 'manual evidence refresh') saved = { ...detail, article: { ...detail.article!, text: 'Fresh article evidence body' } }
    act(() => intelButton(view!.host, action === 'manual evidence refresh' ? 'Refresh article evidence' : 'Authorize additional article sections').click())
    await settle()
    if (action === 'successful extraction continuation') expect(vi.mocked(apiFetch).mock.calls.some(([path, options]) => path.endsWith('/continue') && options?.method === 'POST')).toBe(true)
    expect(detailReads).toBeGreaterThanOrEqual(2)
    expect(view.host.textContent).toContain('Fresh article evidence body')
  })

  it('pauses extraction continuation when current article details are unavailable', async () => {
    let unavailable = false
    vi.mocked(apiFetch).mockImplementation((path) => path === `/items/${item.id}` && unavailable
      ? Promise.reject(new ApiError('Article temporarily unavailable', 503, path))
      : Promise.resolve(normalRead(path, detail)) as never)
    view = await mountIntel(<DashboardPage />, '/')
    await settle()
    await openDetail()
    expect(intelButton(view.host, 'Authorize additional article sections').disabled).toBe(false)
    act(() => [...view!.host.querySelectorAll('button')].find((button) => button.textContent?.includes('Preview Original'))!.click())
    expect(document.querySelector('iframe[title]')).not.toBeNull()
    unavailable = true
    await act(async () => { await view!.client.invalidateQueries({ queryKey: ['dashboard', 'item', item.id] }) })
    await settle()
    expect(view.host.textContent).toContain('Previous article evidence body')
    expect(document.querySelector('iframe[title]')).not.toBeNull()
    const button = intelButton(view.host, 'Authorize additional article sections')
    expect(button.disabled).toBe(true)
    act(() => button.click())
    expect(vi.mocked(apiFetch).mock.calls.some(([, options]) => options?.method === 'POST')).toBe(false)
  })

  it('retires an open original preview after its article detail read is denied while rows remain readable', async () => {
    let status = 200
    const rejected: number[] = []
    vi.mocked(apiFetch).mockImplementation((path) => {
      if (path === `/items/${item.id}` && status !== 200) {
        rejected.push(status)
        return Promise.reject(new ApiError('Article access withdrawn', status, path))
      }
      return Promise.resolve(normalRead(path, detail)) as never
    })
    view = await mountIntel(<DashboardPage />, '/')
    await settle()
    await openDetail()
    act(() => [...view!.host.querySelectorAll('button')].find((button) => button.textContent?.includes('Preview Original'))!.click())
    expect(document.querySelector('iframe[title]')).not.toBeNull()
    for (const next of [403, 503]) {
      status = next
      await act(async () => { await view!.client.invalidateQueries({ queryKey: ['dashboard', 'item', item.id] }) })
      await settle()
      expect(rejected).toContain(next)
      expect(view.host.textContent).toContain(item.title)
      expect(document.querySelector('iframe[title]')).toBeNull()
    }
    status = 200
    await act(async () => { intelButton(view!.host, 'Refresh dashboard').click() })
    await settle()
    expect(view.host.textContent).toContain('Previous article evidence body')
    expect(document.querySelector('iframe[title]')).toBeNull()
    act(() => [...view!.host.querySelectorAll('button')].find((button) => button.textContent?.includes('Preview Original'))!.click())
    expect(document.querySelector('iframe[title]')).not.toBeNull()
  })
})
