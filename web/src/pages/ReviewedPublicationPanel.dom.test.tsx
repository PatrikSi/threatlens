// @vitest-environment jsdom
import { act, useState } from 'react'
import type { ArticleExportFilters } from '../types/exports'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiFetch, apiDownload } from '../api/client'
import { ReviewedPublicationPanel } from './ReviewedPublicationPanel'
import { deferred, intelButton, mountIntel, settle } from './articleIntelligenceTestSupport'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...(await original<object>()), apiFetch: vi.fn(), apiDownload: vi.fn() }))
vi.mock('../hooks/useCurrentUser', () => ({ useCurrentUser: () => ({ isError: false, data: { access: { permissions: ['read:items', 'read:teams', 'write:teams'] } } }) }))
const preview = { fingerprint: 'exact-revision', matched_articles: 1, excluded_or_unreviewed: 2,
  indicators: [{ item_id: 'item', ioc_id: 'ioc', type: 'domain', value: 'evil.net', title: 'Article', assessment_version: 1, source_revision: 2, extraction_revision: 3, expires_at: null, evidence_count: 2 }] }
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
let changeFilters: ((filters: ArticleExportFilters) => void) | undefined
let currentFilters: ArticleExportFilters
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
  currentFilters = { q: null, feed_ids: [], tag_ids: [], tags_mode: 'any',
    classifications: [], ai_relevance_labels: [], ai_score_min: null, ai_score_max: null, is_read: null,
    is_starred: null, has_article_text: null, since: null, until: null, date_basis: 'first_seen_at', sort: 'first_seen_desc' }
  function Workspace() {
    const [filters, setFilters] = useState(currentFilters)
    changeFilters = setFilters
    return <ReviewedPublicationPanel filters={filters} />
  }
  view = await mountIntel(<Workspace />)
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

it('keeps publishing available after an artifact 404 and clears the error after refresh', async () => {
  const publication = { id: 'gone', format: 'stix', status: 'active', revision: 1,
    withdrawn_count: 0, indicator_count: 1, created_at: '2026-09-12T00:00:00Z' }
  let missing = false
  const implementation = vi.mocked(apiFetch).getMockImplementation()!
  vi.mocked(apiFetch).mockImplementation((path, init) => path.includes('/indicator-publications?')
    ? Promise.resolve({ items: missing ? [] : [publication], has_more: false, next_cursor: null })
    : implementation(path, init))
  vi.mocked(apiDownload).mockRejectedValue(new ApiError('Publication unavailable', 404, '/download'))
  await open()
  expect(view!.host.textContent).toContain('Approve reviewed publication')
  act(() => intelButton(view!.host, 'Download publication').click())
  await settle()
  expect(view!.host.textContent).toContain('Approve reviewed publication')
  expect(view!.host.textContent).toContain('Publication consumers')
  missing = true
  act(() => intelButton(view!.host, 'Refresh publications').click())
  await settle()
  expect(view!.host.textContent).toContain('No accessible reviewed publications on this page')
  expect(view!.host.textContent).toContain('Approve reviewed publication')
  expect(view!.host.textContent).toContain('evil.net')
  expect(view!.host.textContent).not.toContain('Publication unavailable')
})

it('does not restore approval when recovering team access', async () => {
  await open()
  act(() => (view!.host.querySelector('input[type="checkbox"]') as HTMLInputElement).click())
  const implementation = vi.mocked(apiFetch).getMockImplementation()!
  vi.mocked(apiFetch).mockImplementation((path, init) => path.includes('/indicator-publications?')
    ? Promise.reject(new ApiError('Team access removed', 403, path)) : implementation(path, init))
  await act(async () => { await view!.client.invalidateQueries({ queryKey: ['reviewed-publications', 'team'] }) })
  await settle()
  expect(view!.host.textContent).not.toContain('Approve reviewed publication')
  vi.mocked(apiFetch).mockImplementation(implementation)
  act(() => intelButton(view!.host, 'Refresh publications').click())
  await settle()
  expect(view!.host.textContent).not.toContain('Approve reviewed publication')
  act(() => intelButton(view!.host, 'Preview reviewed indicators').click())
  await settle()
  expect(intelButton(view!.host, 'Approve reviewed publication').disabled).toBe(true)
  expect((view!.host.querySelector('input[type="checkbox"]') as HTMLInputElement).checked).toBe(false)
})

it('keeps a newer publication pending when an older history refresh completes', async () => {
  await open()
  const history = deferred<object>()
  const publish = deferred<object>()
  const implementation = vi.mocked(apiFetch).getMockImplementation()!
  vi.mocked(apiFetch).mockImplementation((path, init) => {
    if (path.includes('/indicator-publications?')) return history.promise
    if (path.endsWith('/indicator-publications') && init?.method === 'POST') return publish.promise
    return implementation(path, init)
  })
  act(() => intelButton(view!.host, 'Refresh publications').click())
  await settle()
  act(() => (view!.host.querySelector('input[type="checkbox"]') as HTMLInputElement).click())
  act(() => intelButton(view!.host, 'Approve reviewed publication').click())
  await settle()
  expect(intelButton(view!.host, 'Saving publication…').matches(':disabled')).toBe(true)
  await act(async () => history.resolve({ items: [], has_more: false, next_cursor: null }))
  await settle()
  expect(intelButton(view!.host, 'Saving publication…').matches(':disabled')).toBe(true)
  await act(async () => publish.reject(new Error('Publication interrupted')))
  await settle()
  expect(view!.host.textContent).toContain('Publication interrupted')
})


it.each(['scope', 'denial'])('closes selected evidence on %s changes and ignores late responses', async (change) => {
  await open()
  const pending = deferred<object>()
  const implementation = vi.mocked(apiFetch).getMockImplementation()!
  vi.mocked(apiFetch).mockImplementation((path, init) => path.includes('/indicators?')
    ? pending.promise : implementation(path, init))
  act(() => intelButton(view!.host, 'Review evidence').click())
  await settle()
  expect(document.querySelector('[role="dialog"]')).not.toBeNull()
  if (change === 'scope') {
    act(() => changeFilters!({ ...currentFilters, q: 'updated scope' }))
  } else {
    vi.mocked(apiFetch).mockImplementation((path, init) => path.includes('/indicator-publications?')
      ? Promise.reject(new ApiError('Membership removed', 403, path)) : implementation(path, init))
    await act(async () => { await view!.client.invalidateQueries({ queryKey: ['reviewed-publications', 'team'] }) })
  }
  await settle()
  expect(document.querySelector('[role="dialog"]')).toBeNull()
  await act(async () => pending.resolve({ items: [], total: 0 }))
  await settle()
  expect(document.querySelector('[role="dialog"]')).toBeNull()
})

it('closes evidence when a changed revision requires a new preview', async () => {
  await open()
  const implementation = vi.mocked(apiFetch).getMockImplementation()!
  vi.mocked(apiFetch).mockImplementation((path, init) => path.includes('/indicators?')
    ? Promise.resolve({ items: [], source_revision: 20, extraction_revision: 30 }) : implementation(path, init))
  act(() => intelButton(view!.host, 'Review evidence').click())
  await settle()
  expect(document.querySelector('[role="dialog"]')).not.toBeNull()
  refresh = deferred()
  act(() => intelButton(document, 'Refresh publication preview').click())
  await settle()
  expect(document.querySelector('[role="dialog"]')).toBeNull()
  await act(async () => refresh!.resolve(preview))
  await settle()
  expect(document.querySelector('[role="dialog"]')).toBeNull()
  expect(intelButton(view!.host, 'Approve reviewed publication').disabled).toBe(true)
})
