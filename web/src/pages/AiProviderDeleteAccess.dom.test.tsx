// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import type { AIProvider } from '../types/ai'
import { AiProviderConnections } from './AiProviderConnections'
import { useAiProviderConnections, type AiProviderConnectionsController } from './useAiProviderConnections'
import { intelButton, mountIntel, settle } from './articleIntelligenceTestSupport'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...await original<typeof import('../api/client')>(), apiFetch: vi.fn() }))
const provider: AIProvider = {
  id: '550e8400-e29b-41d4-a716-446655440000', name: 'Selected private provider', enabled: true,
  provider_type: 'openai_compatible', base_url: 'https://private-provider.example.test/v1', model: 'local-model',
  temperature: 0.2, max_completion_tokens: 5000, request_timeout_seconds: 300, request_max_retries: 3,
  version: 1, api_key_configured: true, credential_error: null,
  created_at: '2026-11-15T00:00:00Z', updated_at: '2026-11-15T00:00:00Z',
}
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
let current: AiProviderConnectionsController
afterEach(() => { view?.close(); view = undefined; vi.resetAllMocks() })

it('retires the provider delete confirmation across denied detail and fresh matching recovery', async () => {
  let status = 200
  vi.mocked(apiFetch).mockImplementation((path, init) => {
    if (init?.method === 'DELETE') return Promise.resolve(undefined)
    if (path === '/ai/provider-routing') return Promise.resolve({ version: 1, default_provider_id: null, item_enrichment_provider_id: null, daily_brief_provider_id: null, report_provider_id: null })
    if (path.startsWith('/ai/providers?')) return Promise.resolve({ items: [provider], total: 1, limit: 25, offset: 0 })
    if (path === `/ai/providers/${provider.id}`) return status === 200 ? Promise.resolve(provider)
      : Promise.reject(new ApiError('Provider detail denied', status, path))
    if (path === '/ai/quota-groups?limit=100') return Promise.resolve({ items: [], total: 0, limit: 100, offset: 0 })
    return new Promise(() => {})
  })
  function Harness() {
    current = useAiProviderConnections(true)
    return <AiProviderConnections controller={current} />
  }
  view = await mountIntel(<Harness />)
  act(() => current.select(provider)); await settle()
  const remove = intelButton(view.host, 'Delete provider')
  expect(remove.matches(':disabled')).toBe(false)
  act(() => remove.click()); await settle()
  expect(document.querySelector('[role="alertdialog"]')?.textContent).toContain('Delete provider?')
  status = 403
  await act(async () => { await view!.client.invalidateQueries({ queryKey: ['ai', 'providers', 'detail', provider.id], exact: true }) }); await settle()
  expect(document.querySelector('[role="alertdialog"]')).toBeNull()
  status = 200
  await act(async () => { await view!.client.invalidateQueries({ queryKey: ['ai', 'providers', 'detail', provider.id], exact: true }) }); await settle()
  expect(current.selectedProvider.data?.id).toBe(provider.id)
  expect.soft(document.querySelector('[role="alertdialog"]')).toBeNull()
  act(() => current.remove()); await settle()
  expect(vi.mocked(apiFetch).mock.calls.filter(([, init]) => init?.method === 'DELETE')).toHaveLength(0)
})
