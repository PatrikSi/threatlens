// @vitest-environment jsdom
import { act } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import type { AIProvider, AIProviderRouting } from '../types/ai'
import { useAiProviderConnections, type AiProviderConnectionsController } from './useAiProviderConnections'
import type { AiQuotaGroup } from './useAiQuotaGroups'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...(await original<typeof import('../api/client')>()), apiFetch: vi.fn() }))
const provider: AIProvider = {
  id: '550e8400-e29b-41d4-a716-446655440000', name: 'Saved provider', enabled: true,
  provider_type: 'openai_compatible', base_url: 'http://localhost:11434/v1', model: 'local-model',
  temperature: 0.2, max_completion_tokens: 5000, request_timeout_seconds: 300, request_max_retries: 3,
  version: 1, api_key_configured: true, credential_error: null,
  created_at: '2026-09-11T00:00:00Z', updated_at: '2026-09-11T00:00:00Z',
}
const routing: AIProviderRouting = { version: 1, default_provider_id: null, item_enrichment_provider_id: null, daily_brief_provider_id: null, report_provider_id: null }
const quota: AiQuotaGroup = { id: provider.id, version: 1, name: 'Saved account quota', provider_keys: ['legacy'], max_concurrent_requests: 2, hourly_token_budget: 10000, max_concurrent_per_team: 1 }
let root: Root | undefined
let client: QueryClient
let host: HTMLDivElement
let current: AiProviderConnectionsController
let denied: string | undefined
let readVersion: number
let finishWrite: (value: AIProvider | AIProviderRouting | AiQuotaGroup) => void

async function settle() { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 15)) }) }
async function mount() {
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } })
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  function Harness() { current = useAiProviderConnections(true); return <p>Configuration controller mounted</p> }
  act(() => root!.render(<QueryClientProvider client={client}><Harness /></QueryClientProvider>))
  await settle()
}
async function refresh(key: readonly unknown[]) {
  await act(async () => { await client.invalidateQueries({ queryKey: key, exact: true }) })
  await settle()
}
beforeEach(() => {
  denied = undefined
  readVersion = 1
  vi.mocked(apiFetch).mockImplementation((path, options) => {
    if (options?.method === 'PUT') return new Promise((resolve) => { finishWrite = resolve as typeof finishWrite })
    if (denied && path.startsWith(denied)) return Promise.reject(new ApiError('Configuration read denied', 403, path))
    if (path === '/ai/quota-groups?limit=100') return Promise.resolve({ items: [{ ...quota, version: readVersion }], total: 1, limit: 100, offset: 0 })
    if (path === '/ai/provider-routing') return Promise.resolve({ ...routing, version: readVersion })
    if (path.startsWith('/ai/providers?')) return Promise.resolve({ items: [{ ...provider, version: readVersion }], total: 1, limit: 25, offset: 0 })
    if (path === `/ai/providers/${provider.id}`) return Promise.resolve({ ...provider, version: readVersion })
    throw new Error(`Unexpected request: ${path}`)
  })
})
afterEach(() => { act(() => root?.unmount()); root = undefined; client?.clear(); host?.remove(); vi.resetAllMocks() })

it('ignores an old provider save completion after read denial without replacing the preserved draft', async () => {
  await mount()
  act(() => current.select(provider))
  await settle()
  act(() => current.updateDraft('name', 'Unsent provider draft'))
  act(() => current.save())
  await settle()
  expect(vi.mocked(apiFetch).mock.calls.filter(([, options]) => options?.method === 'PUT')).toHaveLength(1)
  denied = `/ai/providers/${provider.id}`
  await refresh(['ai', 'providers', 'detail', provider.id])
  await act(async () => { finishWrite({ ...provider, version: 2, name: 'Old committed response' }) })
  await settle()
  denied = undefined
  readVersion = 3
  await refresh(['ai', 'providers', 'detail', provider.id])
  expect(current.editor?.draft.name).toBe('Unsent provider draft')
  expect(current.editor?.baseline?.version).toBe(1)
  expect(current.editorDirty).toBe(true)
  expect(current.selectedProvider.data?.version).toBe(3)
})

it('ignores an old quota save completion after read denial without replacing the preserved draft', async () => {
  await mount()
  act(() => current.quotas.select(quota))
  act(() => current.quotas.update('name', 'Unsent quota draft'))
  act(() => current.quotas.save())
  await settle()
  expect(vi.mocked(apiFetch).mock.calls.filter(([, options]) => options?.method === 'PUT')).toHaveLength(1)
  denied = '/ai/quota-groups?'
  await refresh(['ai', 'quota-groups'])
  await act(async () => { finishWrite({ ...quota, version: 2, name: 'Old committed quota response' }) })
  await settle()
  denied = undefined
  readVersion = 3
  await refresh(['ai', 'quota-groups'])
  expect(current.quotas.editor?.draft.name).toBe('Unsent quota draft')
  expect(current.quotas.editor?.baseline?.version).toBe(1)
  expect(current.quotas.dirty).toBe(true)
  expect(current.quotas.query.data?.items[0].version).toBe(3)
})

it('ignores an old routing completion after read denial without accepting its stale cache write or clearing the draft', async () => {
  await mount()
  act(() => current.assign('default_provider_id', provider.id))
  await settle()
  act(() => current.saveRouting())
  await settle()
  expect(vi.mocked(apiFetch).mock.calls.filter(([, options]) => options?.method === 'PUT')).toHaveLength(1)
  denied = '/ai/provider-routing'
  await refresh(['ai', 'provider-routing'])
  await act(async () => { finishWrite({ ...routing, version: 2, default_provider_id: provider.id }) })
  await settle()
  denied = undefined
  readVersion = 3
  await refresh(['ai', 'provider-routing'])
  expect(current.routing.data?.version).toBe(3)
  expect(current.routingDirty).toBe(true)
  expect(current.visibleRouting?.default_provider_id).toBe(provider.id)
  expect(current.visibleRouting?.version).toBe(1)
})
