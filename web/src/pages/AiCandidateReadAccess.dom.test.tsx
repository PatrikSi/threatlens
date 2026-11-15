// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import type { AISettings } from '../types/api'
import { AiSettingsPage } from './AiSettingsPage'
import { createRequestFromDraft, DEFAULT_DRAFT } from './aiSettingsDraft'
import { intelButton, mountIntel, settle } from './articleIntelligenceTestSupport'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...(await original<object>()), apiFetch: vi.fn() }))
vi.mock('../hooks/useCurrentUser', () => ({ useCurrentUser: () => ({ data: {
  id: 'admin', role: 'admin', features: { ai_enabled: true }, access: { permissions: ['*:*'] },
} }) }))
const settings: AISettings = {
  ...createRequestFromDraft({ ...DEFAULT_DRAFT, model: 'local-model', base_url: 'https://model.example.test/v1' }),
  id: 'settings', ai_enabled: true, ai_configured: true, api_key_configured: true,
  created_at: '2026-11-15T00:00:00Z', updated_at: '2026-11-15T00:00:00Z',
  prompt_previews: { item_enrichment: { label: 'Articles', system_prompt: 'Summarize', notes: [] },
    daily_brief: { label: 'Briefs', system_prompt: 'Summarize', notes: [] } },
}
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
let readStatus: number
let feedStatus: number
beforeEach(() => {
  readStatus = 200; feedStatus = 200
  vi.mocked(apiFetch).mockImplementation((path) => {
    if (path === '/ai/settings') return Promise.resolve(settings)
    if (path.startsWith('/feeds')) return feedStatus === 200
      ? Promise.resolve([{ id: 'feed-1', name: 'Private reprocess source' }])
      : Promise.reject(new ApiError(`Feed read ${feedStatus}`, feedStatus, path))
    if (path.startsWith('/items?')) return readStatus === 200
      ? Promise.resolve({ items: [{ id: 'item-1', title: 'Private candidate article', feed_name: 'Private candidate source' }], total: 1, page: 1, page_size: 12 })
      : Promise.reject(new ApiError(`Candidate read ${readStatus}`, readStatus, path))
    if (path === '/ai/reprocess') return Promise.resolve({ queued: true, run_id: 'accepted-run', task_id: 'task' })
    return new Promise(() => {})
  })
})
afterEach(() => { view?.close(); view = undefined; vi.resetAllMocks() })
async function mount() {
  view = await mountIntel(<AiSettingsPage />)
  act(() => intelButton(view!.host, 'Jobs').click()); await settle()
}
async function search(value: string) {
  const input = view!.host.querySelector<HTMLInputElement>('input[placeholder="Search items by title, summary, or URL"]')!
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  }); await settle()
}
async function refreshCandidates() {
  await act(async () => { await view!.client.invalidateQueries({ predicate: (query) =>
    query.queryKey.includes('ai-reprocess-picker') || query.queryKey.includes('reprocess-items') }) }); await settle()
}
function candidate() { return [...view!.host.querySelectorAll('button')].find((button) => button.textContent?.startsWith('Private candidate article'))! }

it('keeps denied candidate article metadata private through a later outage', async () => {
  await mount(); await search('vpn')
  expect(view!.host.textContent).toContain('Private candidate article')
  readStatus = 403; await refreshCandidates()
  expect(view!.host.textContent).not.toContain('Private candidate article')
  readStatus = 503; await refreshCandidates()
  expect(view!.host.textContent).not.toContain('Private candidate source')
})

it('retires selected candidate metadata after denial without broadening the queued scope', async () => {
  await mount(); await search('vpn')
  act(() => candidate().click()); await settle()
  expect(view!.host.textContent).toContain('Private candidate article · remove')
  readStatus = 403; await refreshCandidates()
  expect(view!.host.textContent).not.toContain('Private candidate article')
  expect(intelButton(view!.host, 'Queue reprocess').disabled).toBe(true)
  readStatus = 200; await refreshCandidates()
  expect(view!.host.textContent).not.toContain('Private candidate article · remove')
  expect(intelButton(view!.host, 'Queue reprocess').disabled).toBe(true)
})

it('pauses selected-article queueing during an ordinary outage while preserving its context', async () => {
  await mount(); await search('vpn')
  act(() => candidate().click()); await settle()
  readStatus = 503; await refreshCandidates()
  expect(view!.host.textContent).toContain('Private candidate article · remove')
  expect(intelButton(view!.host, 'Queue reprocess').disabled).toBe(true)
})

it('does not restore a cached candidate search after a sibling search denial', async () => {
  await mount(); await search('vpn')
  expect(view!.host.textContent).toContain('Private candidate article')
  readStatus = 403; await search('denied search')
  const failed = view!.client.getQueryCache().getAll().find((query) => query.queryKey.includes('denied search'))!
  expect(failed.state.error).toBeInstanceOf(ApiError)
  expect((failed.state.error as ApiError).status).toBe(403)
  await search('vpn')
  expect(view!.host.textContent).not.toContain('Private candidate article')
})

it('allows the independent initial lookback queue without an article picker read', async () => {
  await mount()
  expect(vi.mocked(apiFetch).mock.calls.some(([path]) => path.startsWith('/items?'))).toBe(false)
  expect(intelButton(view!.host, 'Queue reprocess').disabled).toBe(false)
})

it.each([403, 503])('pauses an existing feed target while its catalogue read fails: %s', async (status) => {
  await mount()
  const input = [...view!.host.querySelectorAll('label')].find((label) => label.textContent?.trim() === 'Private reprocess source')!.querySelector('input')!
  act(() => input.click()); await settle()
  expect(input.checked).toBe(true)
  expect(intelButton(view!.host, 'Queue reprocess').disabled).toBe(false)
  feedStatus = status
  await act(async () => { await view!.client.invalidateQueries({ queryKey: ['feeds', 'ai-reprocess'] }) }); await settle()
  expect(intelButton(view!.host, 'Queue reprocess').disabled).toBe(true)
  feedStatus = 200
  await act(async () => { await view!.client.invalidateQueries({ queryKey: ['feeds', 'ai-reprocess'] }) }); await settle()
  expect(intelButton(view!.host, 'Queue reprocess').disabled).toBe(status === 403)
})

it('requires deliberate reset before replacing a withdrawn article selection with a lookback scope', async () => {
  await mount(); await search('vpn')
  act(() => candidate().click()); await settle()
  readStatus = 403; await refreshCandidates()
  expect(intelButton(view!.host, 'Queue reprocess').disabled).toBe(true)
  act(() => intelButton(view!.host, 'Reset scope').click()); await settle()
  act(() => intelButton(document.body, 'Clear scope').click()); await settle()
  expect(intelButton(view!.host, 'Queue reprocess').disabled).toBe(false)
  act(() => intelButton(view!.host, 'Queue reprocess').click()); await settle()
  const request = vi.mocked(apiFetch).mock.calls.find(([path]) => path === '/ai/reprocess')!
  expect(JSON.parse(String(request[1]?.body))).toMatchObject({ item_ids: [], feed_ids: [], days: 7, limit: 100 })
})
