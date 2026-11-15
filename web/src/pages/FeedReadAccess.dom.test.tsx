// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import type { Feed } from '../types/api'
import { useQueryAccessScope } from '../hooks/useQueryAccessScope'
import { FeedDetailDialog } from './FeedDetailDialog'
import { FeedManagementDialogs } from './FeedManagementDialogs'
import { useFeedsPageController } from './useFeedsPageController'
import { deferred, intelButton, mountIntel, settle } from './articleIntelligenceTestSupport'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
const identity = vi.hoisted(() => ({ role: 'analyst' }))
vi.mock('../api/client', async (original) => ({ ...(await original<object>()), apiFetch: vi.fn() }))
vi.mock('../hooks/useCurrentUser', () => ({ useCurrentUser: () => ({ data: {
  id: 'feed-review-user', role: identity.role, access: { permissions: ['*:*'] },
} }) }))
const feed: Feed = {
  id: 'feed-1', name: 'Private source name', url: 'https://example.test/private.xml',
  description: null, site_url: null, language: null, enabled: true,
  fetch_mode: 'interval', fetch_interval_seconds: 1800, schedule_cron: null,
  etag: null, last_modified: null, last_fetch_at: null, last_success_at: null,
  error_count: 0, last_error: null, has_unreadable_url: false, created_at: '2026-11-15T00:00:00Z',
}
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
let current: ReturnType<typeof useFeedsPageController>
let feedStatus: number
let itemStatus: number
let healthStatus: number
let degradedHealthPayload: boolean
let readFeeds: Feed[]
let recoverFeeds: () => Promise<unknown>
const health = { ok: false, status: 'critical', scanned_at: '2026-11-15T00:00:00Z',
  feeds: { unreadable_records: 7 }, using_derived_app_data_encryption_key: false }
function Harness() {
  current = useFeedsPageController()
  const recovery = useQueryAccessScope(['feeds'])
  recoverFeeds = () => view!.client.fetchQuery({ queryKey: ['feeds-alternate-recovery'],
    ...recovery.queryOptions(() => apiFetch<Feed[]>('/feeds')), retry: false })
  return <><p>{current.filteredFeeds.map((row) => row.name).join(',')}</p><p data-health-count>{current.unreadableFeedInventoryCount}</p><FeedDetailDialog {...current} /><FeedManagementDialogs {...current} />{current.confirmDiscardUnsavedFeedScheduleChanges.discardDialog}</>
}
async function refresh() {
  await act(async () => { await view!.client.invalidateQueries() })
  await settle()
}
beforeEach(() => {
  identity.role = 'analyst'; feedStatus = 200; itemStatus = 200; healthStatus = 200; degradedHealthPayload = false; readFeeds = [feed]
  vi.mocked(apiFetch).mockImplementation(async (path, init) => {
    if (init?.method) return feed
    if (path === '/health/encrypted-data') {
      if (healthStatus !== 200) throw new ApiError(`Health failed ${healthStatus}`, healthStatus, path, degradedHealthPayload ? health : undefined)
      return health
    }
    const status = path === '/feeds' ? feedStatus : itemStatus
    if (status !== 200) throw new ApiError(`Read failed ${status}`, status, path)
    if (path === '/feeds') return readFeeds
    if (path.startsWith('/items?')) return { items: [{ id: 'item-1', title: 'Private recent article',
      url: 'https://example.test/article', tags: [], status: 'new', published_at: null, first_seen_at: feed.created_at }], total: 1, page: 1, page_size: 10 }
    throw new Error(`Unexpected path ${path}`)
  })
})
afterEach(() => { view?.close(); view = undefined; window.sessionStorage.clear(); vi.resetAllMocks() })

it('keeps denied feed names private through a later ordinary outage', async () => {
  view = await mountIntel(<Harness />)
  expect(view.host.textContent).toContain(feed.name)
  feedStatus = 403; await refresh()
  expect(view.host.textContent).not.toContain(feed.name)
  feedStatus = 503; await refresh()
  expect(current.feedsQuery.error).toBeInstanceOf(ApiError)
  expect(view.host.textContent).not.toContain(feed.name)
})

it('keeps denied recent articles private independently of readable feed metadata', async () => {
  view = await mountIntel(<Harness />)
  act(() => current.openFeedDetail(feed)); await settle()
  expect(document.body.textContent).toContain('Private recent article')
  itemStatus = 403; await refresh()
  expect(document.body.textContent).not.toContain('Private recent article')
  itemStatus = 503; await refresh()
  expect(document.body.textContent).not.toContain('Private recent article')
  expect(document.body.textContent).toContain(feed.name)
})

it('retains an ordinary feed outage draft while pausing a direct saved-feed write', async () => {
  view = await mountIntel(<Harness />)
  act(() => current.openFeedDetail(feed))
  act(() => current.updateFeedEditDraft({ name: 'Unsent analyst draft' }))
  feedStatus = 503; await refresh()
  expect(current.feedEditDraft?.name).toBe('Unsent analyst draft')
  expect(document.body.textContent).toContain(feed.name)
  act(() => current.onSaveFeedDetail()); await settle()
  expect(vi.mocked(apiFetch).mock.calls.filter(([, init]) => init?.method === 'PATCH')).toHaveLength(0)
})

it('retires an old feed deletion intent after denial and later successful recovery', async () => {
  view = await mountIntel(<Harness />)
  act(() => current.setPendingDeleteFeed(feed))
  feedStatus = 403; await refresh()
  feedStatus = 200; await refresh()
  expect(current.pendingDeleteFeed).toBeNull()
  act(() => current.onConfirmDeleteFeed()); await settle()
  expect(vi.mocked(apiFetch).mock.calls.filter(([, init]) => init?.method === 'DELETE')).toHaveLength(0)
})

it('preserves a hidden dirty feed editor without accepting a late save baseline', async () => {
  const pending = deferred<Feed>()
  const original = vi.mocked(apiFetch).getMockImplementation()!
  vi.mocked(apiFetch).mockImplementation((path, init) => init?.method === 'PATCH' ? pending.promise : original(path, init))
  view = await mountIntel(<Harness />)
  act(() => current.openFeedDetail(feed))
  act(() => current.updateFeedEditDraft({ name: 'Submitted feed draft' }))
  act(() => current.onSaveFeedDetail()); await settle()
  expect(vi.mocked(apiFetch).mock.calls.filter(([, init]) => init?.method === 'PATCH')).toHaveLength(1)
  feedStatus = 403; await refresh()
  expect(document.querySelector('#feed-edit-url')).toBeNull()
  await act(async () => pending.resolve({ ...feed, name: 'Old committed feed response' }))
  await settle()
  feedStatus = 200; await refresh()
  expect(current.feedEditDraft?.name).toBe('Submitted feed draft')
  expect(current.feedEditDirty).toBe(true)
  act(() => current.onSaveFeedDetail()); await settle()
  expect(vi.mocked(apiFetch).mock.calls.filter(([, init]) => init?.method === 'PATCH')).toHaveLength(1)
  act(() => current.onReloadFeedDetail()); await settle()
  act(() => intelButton(document.body, 'Discard changes').click()); await settle()
  expect(current.feedEditDraft?.name).toBe(feed.name)
  expect(current.feedEditDirty).toBe(false)
})

it('preserves readable recent article context during an ordinary item outage', async () => {
  view = await mountIntel(<Harness />)
  act(() => current.openFeedDetail(feed)); await settle()
  itemStatus = 503; await refresh()
  expect(document.body.textContent).toContain('Private recent article')
  expect(document.body.textContent).toContain('Read failed 503')
  expect(current.feedEditDraft?.name).toBe(feed.name)
})

it('keeps denied encrypted inventory counts private through a later health outage', async () => {
  identity.role = 'admin'
  view = await mountIntel(<Harness />)
  expect(view.host.querySelector('[data-health-count]')?.textContent).toBe('7')
  healthStatus = 403; await refresh()
  expect(view.host.querySelector('[data-health-count]')?.textContent).toBe('0')
  healthStatus = 503; await refresh()
  expect(view.host.querySelector('[data-health-count]')?.textContent).toBe('0')
  expect(view.host.textContent).toContain(feed.name)
})

it('accepts an authorized degraded health response without calling it healthy', async () => {
  identity.role = 'admin'; healthStatus = 503; degradedHealthPayload = true
  view = await mountIntel(<Harness />)
  expect(current.encryptedDataHealthQuery.data?.status).toBe('critical')
  expect(view.host.querySelector('[data-health-count]')?.textContent).toBe('7')
})

it('keeps navigation protection for an unsaved schedule while its inventory is hidden', async () => {
  view = await mountIntel(<Harness />)
  act(() => current.updateFeedDraft(feed, { intervalSeconds: '3600' })); await settle()
  feedStatus = 403; await refresh()
  await act(async () => { void view!.router.navigate('/other') }); await settle()
  expect(view.router.state.location.pathname).toBe('/')
  expect(document.querySelector('[role="alertdialog"]')).not.toBeNull()
  expect(document.body.textContent).not.toContain(feed.name)
})

it('retires a feed confirmation when denial and alternate-reader recovery are batched', async () => {
  view = await mountIntel(<Harness />)
  act(() => current.setPendingDeleteFeed(feed)); await settle()
  expect(document.querySelector('[role="alertdialog"]')?.textContent).toContain(feed.name)
  await act(async () => {
    feedStatus = 403
    await view!.client.refetchQueries({ queryKey: ['feeds'], exact: true })
    feedStatus = 200; readFeeds = []
    await recoverFeeds()
  }); await settle()
  expect(view.client.getQueryState(['feeds'])?.error).toBeInstanceOf(ApiError)
  expect(current.pendingDeleteFeed).toBeNull()
  expect(document.querySelector('[role="alertdialog"]')?.textContent ?? '').not.toContain(feed.name)
})
