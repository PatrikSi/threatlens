// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, apiFetch } from '../api/client'
import type { AlertOccurrence } from '../types/alerts'
import { AlertOccurrencesWorkspace } from './AlertOccurrencesWorkspace'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...(await original<object>()), apiFetch: vi.fn() }))
vi.mock('../hooks/useCurrentUser', () => ({
  useCurrentUser: () => ({ data: { id: 'reader', role: 'analyst', features: {} }, isError: false }),
}))

const occurrence: AlertOccurrence = {
  id: 'private-occurrence', alert_interest_id: 'rule', rule_id_snapshot: 'rule', owner_user_id: 'reader',
  item_id: 'source', item_id_snapshot: 'source', integration_event_id: null, rule_revision: 1,
  item_content_hash: 'a'.repeat(64), alert_name_snapshot: 'Private watchlist',
  alert_category_snapshot: 'vulnerability', alert_keywords_snapshot: ['vpn'], matched_keywords: ['vpn'],
  source_snapshot_json: { item: { title: 'Sensitive source title', summary: 'Sensitive source excerpt', url: 'https://example.invalid/advisory' } },
  severity_snapshot: 'high', lifecycle_state: 'new', is_suppressed: false, suppressed_at: null,
  suppression_reason: null, is_snoozed: false, snoozed_until: null, snooze_reason: null,
  closure_disposition: null, acknowledged_at: null, acknowledged_by_user_id: null,
  investigating_at: null, investigating_by_user_id: null, closed_at: null, closed_by_user_id: null,
  version: 1, created_at: '2026-09-12T12:00:00Z', updated_at: '2026-09-12T12:00:00Z',
}
let root: Root | null = null
let host: HTMLDivElement | null = null
let client: QueryClient | null = null

async function settle() {
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)) })
}

function resultFor(path: string) {
  if (path.startsWith('/alerts?')) return [{ id: 'rule', name: 'Private watchlist', enabled: true }]
  if (path.includes('/activity?')) return {
    items: [{ id: 'activity', occurrence_id: occurrence.id, actor_user_id: 'reader', action: 'created',
      details_json: { reason: 'Sensitive activity context' }, created_at: occurrence.created_at }],
    total: 1, page: 1, page_size: 25,
  }
  if (path === `/alerts/occurrences/${occurrence.id}`) return occurrence
  return { items: [occurrence], total: 1, page: 1, page_size: 25 }
}

async function mount() {
  vi.mocked(apiFetch).mockImplementation((path) => Promise.resolve(resultFor(path)) as never)
  client = new QueryClient({ defaultOptions: { queries: { retry: false, retryDelay: 0 } } })
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  act(() => root!.render(<MemoryRouter initialEntries={['/alerts?view=occurrences&occurrence=private-occurrence']}>
    <QueryClientProvider client={client!}><AlertOccurrencesWorkspace /></QueryClientProvider>
  </MemoryRouter>))
  await settle()
  expect(host.textContent).toContain('Sensitive source excerpt')
  expect(host.textContent).toContain('Sensitive activity context')
}

afterEach(() => {
  act(() => root?.unmount())
  client?.clear()
  host?.remove()
  root = null
  client = null
  host = null
  vi.resetAllMocks()
})

describe('alert access during real query-cache refreshes', () => {
  it.each([401, 403, 404, 503])('handles HTTP %s without presenting withdrawn access as an outage', async (status) => {
    await mount()
    vi.mocked(apiFetch).mockRejectedValue(new ApiError('Occurrence refresh rejected', status, '/alerts/occurrences'))
    await act(async () => { await client!.invalidateQueries() })
    await settle()

    expect(host!.textContent).toContain('Occurrence refresh rejected')
    for (const sensitive of ['Sensitive source title', 'Sensitive source excerpt', 'Sensitive activity context', 'Private watchlist']) {
      if (status === 503) expect(host!.textContent).toContain(sensitive)
      else expect(host!.textContent).not.toContain(sensitive)
    }
    vi.mocked(apiFetch).mockImplementation((path) => Promise.resolve(resultFor(path)) as never)
    await act(async () => { await client!.invalidateQueries() })
    await settle()
    expect(host!.textContent).toContain('Sensitive source excerpt')
  })

  it.each([403, 404])('hides independently denied activity after HTTP %s', async (status) => {
    await mount()
    vi.mocked(apiFetch).mockImplementation((path) => path.includes('/activity?')
      ? Promise.reject(new ApiError('Activity access withdrawn', status, path))
      : Promise.resolve(resultFor(path)) as never)
    await act(async () => { await client!.invalidateQueries({ queryKey: ['alerts', 'occurrences', 'activity'] }) })
    await settle()

    expect(host!.textContent).toContain('Activity access withdrawn')
    expect(host!.textContent).not.toContain('Sensitive activity context')
    expect(host!.textContent).toContain('Sensitive source excerpt')
  })
})
