// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { apiFetch, ApiError } from '../api/client'
import { invalidateSession } from '../api/sessionLifecycle'
import type { AIProvider, AIProviderRouting } from '../types/ai'
import { AiProviderConnections } from './AiProviderConnections'
import { useAiProviderConnections, type AiProviderConnectionsController } from './useAiProviderConnections'
;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...(await original<object>()), apiFetch: vi.fn() }))

const provider: AIProvider = {
  id: '550e8400-e29b-41d4-a716-446655440000',
  name: 'Local analysis',
  enabled: true,
  provider_type: 'openai_compatible',
  base_url: 'http://localhost:11434/v1',
  model: 'local-model',
  temperature: 0.2,
  max_completion_tokens: 5000,
  request_timeout_seconds: 300,
  request_max_retries: 3,
  version: 1,
  api_key_configured: true,
  credential_error: null,
  created_at: '2026-09-11T00:00:00Z',
  updated_at: '2026-09-11T00:00:00Z',
}
const initialRouting: AIProviderRouting = {
  version: 1,
  default_provider_id: null,
  item_enrichment_provider_id: null,
  daily_brief_provider_id: null,
  report_provider_id: null,
}
let root: Root | undefined
let client: QueryClient
let host: HTMLDivElement
let current: AiProviderConnectionsController
let savedProvider: AIProvider
let savedRouting: AIProviderRouting

function mount(withView = false) {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } },
  })
  host = document.createElement('div')
  document.body.appendChild(host)
  function Harness() {
    current = useAiProviderConnections(true)
    return withView ? <AiProviderConnections controller={current} /> : null
  }
  root = createRoot(host)
  act(() =>
    root!.render(
      <QueryClientProvider client={client}>
        <Harness />
      </QueryClientProvider>,
    ),
  )
}
async function settle() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 15))
  })
}
function body(init?: RequestInit) {
  return JSON.parse(String(init?.body))
}
function respondToRead(path: string) {
  if (path === '/ai/provider-routing') return savedRouting
  if (path.startsWith('/ai/providers?')) return { items: [savedProvider], total: 1, limit: 25, offset: 0 }
  if (path === `/ai/providers/${provider.id}`) return savedProvider
  throw new Error(`Unexpected request ${path}`)
}
beforeEach(() => {
  savedProvider = { ...provider }
  savedRouting = { ...initialRouting }
  vi.mocked(apiFetch).mockImplementation((path) => Promise.resolve(respondToRead(path)) as never)
})
afterEach(() => {
  act(() => root?.unmount())
  client?.clear()
  host?.remove()
  vi.clearAllMocks()
})

describe('AI provider lifecycle with a real query cache', () => {
  it('keeps the original provider version and draft after a concurrent refresh and conflict', async () => {
    let submitted: unknown
    vi.mocked(apiFetch).mockImplementation((path, init) => {
      if (init?.method === 'PUT') {
        submitted = body(init)
        return Promise.reject(new ApiError('Provider changed. Reload its saved configuration.', 409, path))
      }
      return Promise.resolve(respondToRead(path)) as never
    })
    mount()
    await settle()
    act(() => current.select(provider))
    act(() => current.updateDraft('name', 'My unsaved edit'))
    savedProvider = { ...provider, version: 2, model: 'new-server-model' }
    await act(async () => {
      await client.invalidateQueries({ queryKey: ['ai', 'providers'] })
    })
    expect(current.editor?.baseline?.version).toBe(1)
    expect(current.editor?.draft.name).toBe('My unsaved edit')
    act(() => current.save())
    await settle()
    expect(submitted).toMatchObject({ version: 1, name: 'My unsaved edit', model: 'local-model' })
    expect(current.notice?.message).toContain('Reload its saved configuration')
    expect(current.editorDirty).toBe(true)
    act(() => current.select('reload'))
    expect(current.pendingSelection).toBe('reload')
    act(() => current.confirmSelection())
    expect(current.editor?.baseline?.version).toBe(2)
    expect(current.editor?.draft.model).toBe('new-server-model')
  })

  it('locks editing and duplicate submissions until save settles, then clears replacement credentials', async () => {
    let complete!: (value: AIProvider) => void
    vi.mocked(apiFetch).mockImplementation((path, init) =>
      init?.method === 'PUT'
        ? (new Promise((resolve) => {
            complete = resolve
          }) as never)
        : (Promise.resolve(respondToRead(path)) as never),
    )
    mount()
    await settle()
    act(() => current.select(provider))
    act(() => current.updateDraft('api_key', 'replacement-secret'))
    act(() => {
      current.save()
      current.save()
      current.updateDraft('name', 'Late edit')
      current.select('new')
    })
    await settle()
    expect(current.editor?.draft.name).toBe(provider.name)
    expect(vi.mocked(apiFetch).mock.calls.filter(([, init]) => init?.method === 'PUT')).toHaveLength(1)
    savedProvider = { ...provider, version: 2 }
    await act(async () => complete(savedProvider))
    await settle()
    expect(current.editor?.draft.api_key).toBe('')
    expect(current.editor?.baseline?.version).toBe(2)
    expect(current.editorDirty).toBe(false)
  })

  it('reuses a stable new-provider identifier after an uncertain create response and selects the saved result', async () => {
    const ids: string[] = []
    vi.mocked(apiFetch).mockImplementation((path, init) => {
      if (init?.method === 'POST') {
        const submitted = body(init)
        ids.push(submitted.id)
        if (ids.length === 1) return Promise.reject(new Error('Connection interrupted'))
        return Promise.resolve({ ...provider, ...submitted }) as never
      }
      return Promise.resolve(
        path.startsWith('/ai/providers/') ? { ...provider, id: ids[0] } : respondToRead(path),
      ) as never
    })
    mount()
    await settle()
    act(() => current.select('new'))
    act(() => {
      current.updateDraft('name', 'Hosted reports')
      current.updateDraft('base_url', 'https://provider.test/v1')
      current.updateDraft('model', 'report-model')
    })
    act(() => current.save())
    await settle()
    expect(current.editorDirty).toBe(true)
    act(() => current.save())
    await settle()
    expect(ids).toHaveLength(2)
    expect(ids[0]).toBe(ids[1])
    expect(current.editor?.baseline?.id).toBe(ids[0])
    expect(current.editorDirty).toBe(false)
  })

  it('pins the routing version to the first edit and keeps conflicting assignment drafts', async () => {
    let submitted: unknown
    vi.mocked(apiFetch).mockImplementation((path, init) => {
      if (init?.method === 'PUT') {
        submitted = body(init)
        return Promise.reject(new ApiError('Assignments changed.', 409, path))
      }
      return Promise.resolve(respondToRead(path)) as never
    })
    mount()
    await settle()
    act(() => current.assign('default_provider_id', provider.id))
    savedRouting = { ...initialRouting, version: 2, report_provider_id: provider.id }
    await act(async () => {
      await client.invalidateQueries({ queryKey: ['ai', 'provider-routing'] })
    })
    act(() => current.assign('daily_brief_provider_id', provider.id))
    act(() => current.saveRouting())
    await settle()
    expect(submitted).toMatchObject({
      version: 1,
      report_provider_id: null,
      default_provider_id: provider.id,
      daily_brief_provider_id: provider.id,
    })
    expect(current.routingDirty).toBe(true)
    act(() => current.reloadRouting())
    expect(current.visibleRouting?.version).toBe(2)
    expect(current.routingDirty).toBe(false)
  })

  it('rejects an old-session save completion before applying its provider data', async () => {
    let complete!: (value: AIProvider) => void
    vi.mocked(apiFetch).mockImplementation((path, init) =>
      init?.method === 'PUT'
        ? (new Promise((resolve) => {
            complete = resolve
          }) as never)
        : (Promise.resolve(respondToRead(path)) as never),
    )
    mount()
    await settle()
    act(() => current.select(provider))
    act(() => current.updateDraft('name', 'Submitted'))
    act(() => current.save())
    await settle()
    invalidateSession()
    await act(async () => complete({ ...provider, name: 'Previous account result', version: 2 }))
    await settle()
    expect(current.editor?.draft.name).toBe('Submitted')
    expect(current.editor?.baseline?.version).toBe(1)
    expect(current.notice?.error).toBe(true)
  })

  it('tests the saved provider version and invalidates the displayed result on edits', async () => {
    let submitted: unknown
    vi.mocked(apiFetch).mockImplementation((path, init) => {
      if (path.endsWith('/test-connection')) {
        submitted = body(init)
        return Promise.resolve({
          success: true,
          provider: 'openai_compatible',
          model: provider.model,
          latency_ms: 25,
          error: null,
        }) as never
      }
      return Promise.resolve(respondToRead(path)) as never
    })
    mount()
    await settle()
    act(() => current.select(provider))
    act(() => current.test())
    await settle()
    expect(submitted).toEqual({ version: 1 })
    expect(current.testResult?.success).toBe(true)
    act(() => current.updateDraft('model', 'another-model'))
    expect(current.testResult).toBeNull()
    act(() => current.test())
    expect(vi.mocked(apiFetch).mock.calls.filter(([path]) => path.endsWith('/test-connection'))).toHaveLength(1)
  })

  it('clears earlier success while a repeated connection test is pending or fails', async () => {
    let count = 0
    vi.mocked(apiFetch).mockImplementation((path) => {
      if (path.endsWith('/test-connection')) {
        count += 1
        return count === 1
          ? (Promise.resolve({
              success: true,
              provider: 'openai_compatible',
              model: provider.model,
              latency_ms: 25,
              error: null,
            }) as never)
          : Promise.reject(new Error('Provider unavailable'))
      }
      return Promise.resolve(respondToRead(path)) as never
    })
    mount()
    await settle()
    act(() => current.select(provider))
    act(() => current.test())
    await settle()
    expect(current.testResult?.success).toBe(true)
    act(() => current.test())
    expect(current.testResult).toBeNull()
    await settle()
    expect(current.testResult).toBeNull()
    expect(current.notice?.message).toContain('Provider unavailable')
  })

  it('retains the selected draft while paging and searching the server collection', async () => {
    mount()
    await settle()
    act(() => current.select(provider))
    act(() => current.updateDraft('name', 'Unsaved'))
    act(() => current.setPage(1))
    await settle()
    expect(vi.mocked(apiFetch).mock.calls.some(([path]) => path.includes('offset=25'))).toBe(true)
    act(() => current.setSearch('Hosted & local'))
    await settle()
    expect(vi.mocked(apiFetch).mock.calls.some(([path]) => path.includes('search=Hosted%20%26%20local'))).toBe(true)
    expect(current.editor?.draft.name).toBe('Unsaved')
    expect(current.page).toBe(0)
  })

  it('keeps failed deletion visible and retains the selected provider', async () => {
    vi.mocked(apiFetch).mockImplementation((path, init) =>
      init?.method === 'DELETE'
        ? Promise.reject(new ApiError('Provider is now assigned. Reassign it first.', 409, path))
        : (Promise.resolve(respondToRead(path)) as never),
    )
    mount(true)
    await settle()
    act(() => {
      current.select(provider)
      current.setDeleteTarget(provider)
    })
    act(() => current.remove())
    await settle()
    expect(current.deleteTarget?.id).toBe(provider.id)
    expect(current.editor?.baseline?.id).toBe(provider.id)
    expect(document.querySelector('[role="alertdialog"]')?.textContent).toContain('Reassign it first')
  })

  it('renders named fields and empty credentials without inheriting or displaying saved keys', async () => {
    mount(true)
    await settle()
    act(() => current.select(provider))
    const password = host.querySelector<HTMLInputElement>('input[type="password"]')
    expect(password?.value).toBe('')
    expect(password?.getAttribute('autocomplete')).toBe('new-password')
    expect(host.textContent).toContain('Named providers never inherit the legacy environment key')
    expect(host.querySelector('table caption')?.textContent).toBe('Saved AI provider connections')
    expect(host.textContent).toContain('1–1 of 1 providers')
    const endpoint = host.querySelector<HTMLInputElement>('input[aria-label="Provider base URL"]')!
    expect(endpoint.getAttribute('aria-describedby')).toContain('provider-endpoint-help')
    expect(document.getElementById('provider-endpoint-help')?.textContent).toContain('https://generativelanguage.googleapis.com/v1beta/openai/')
    expect(document.getElementById('provider-endpoint-help')?.textContent).toContain('different request format')
  })

  it('exposes recoverable API errors when connected to a server without provider support', async () => {
    vi.mocked(apiFetch).mockRejectedValue(new ApiError('Not Found', 404, '/ai/providers'))
    mount(true)
    await settle()
    expect(host.textContent).toContain('Legacy settings remain available below')
    expect(host.textContent).toContain('Retry loading providers')
    expect([...host.querySelectorAll('button')].find((button) => button.textContent === 'Add provider')?.disabled).toBe(
      true,
    )
  })

  it('saves a larger default completion budget with its help and validation attached', async () => {
    let submitted: Record<string, unknown> | undefined
    vi.mocked(apiFetch).mockImplementation((path, init) => {
      if (init?.method === 'PUT') {
        submitted = body(init)
        savedProvider = { ...provider, max_completion_tokens: Number(submitted?.max_completion_tokens), version: 2 }
        return Promise.resolve(savedProvider) as never
      }
      return Promise.resolve(respondToRead(path)) as never
    })
    mount(true)
    await settle()
    act(() => current.select(provider))
    act(() => current.updateDraft('max_completion_tokens', '131073'))
    const input = host.querySelector<HTMLInputElement>('input[aria-label="Provider default completion tokens"]')!
    expect(input.getAttribute('aria-invalid')).toBe('true')
    expect(input.getAttribute('aria-describedby')).toContain('provider-error-max_completion_tokens')
    act(() => current.save())
    expect(submitted).toBeUndefined()
    act(() => current.updateDraft('max_completion_tokens', '131072'))
    expect(input.getAttribute('aria-invalid')).toBe('false')
    expect(input.getAttribute('aria-describedby')).toContain('provider-completion-token-help')
    expect(host.querySelector('#provider-completion-token-help')?.textContent).toContain('Reports use their own')
    act(() => current.save())
    await settle()
    expect(submitted).toMatchObject({ version: 1, max_completion_tokens: 131072 })
    expect(current.editor?.draft.max_completion_tokens).toBe('131072')
  })

  it('warns only for a newer server revision, not an older cached detail after saving', async () => {
    vi.mocked(apiFetch).mockImplementation((path, init) =>
      init?.method === 'PUT'
        ? (Promise.resolve({ ...provider, version: 2 }) as never)
        : (Promise.resolve(respondToRead(path)) as never),
    )
    mount(true)
    await settle()
    act(() => current.select(provider))
    act(() => current.updateDraft('name', 'Submitted name'))
    act(() => current.save())
    await settle()
    expect(current.editor?.baseline?.version).toBe(2)
    expect(current.selectedProvider.data?.version).toBe(1)
    expect(host.textContent).not.toContain('This provider changed on the server')
    act(() => client.setQueryData(['ai', 'providers', 'detail', provider.id], { ...provider, version: 3 }))
    await settle()
    expect(host.textContent).toContain('This provider changed on the server')
    expect(current.editor?.baseline?.version).toBe(2)
  })

  it('retries an isolated detail failure without replacing the dirty provider draft', async () => {
    let detailCalls = 0
    vi.mocked(apiFetch).mockImplementation((path) => {
      if (path === `/ai/providers/${provider.id}`) {
        detailCalls += 1
        return detailCalls === 1
          ? Promise.reject(new Error('Detail temporarily unavailable'))
          : (Promise.resolve({ ...provider, version: 2 }) as never)
      }
      return Promise.resolve(respondToRead(path)) as never
    })
    mount(true)
    await settle()
    act(() => current.select(provider))
    act(() => current.updateDraft('name', 'Keep my draft'))
    await settle()
    const retry = [...host.querySelectorAll('button')].find((button) =>
      button.textContent?.includes('Retry saved provider refresh'),
    )
    expect(retry).toBeDefined()
    act(() => retry!.click())
    await settle()
    expect(detailCalls).toBe(2)
    expect(current.selectedProvider.isError).toBe(false)
    expect(current.selectedProvider.data?.version).toBe(2)
    expect(current.editor?.baseline?.version).toBe(1)
    expect(current.editor?.draft.name).toBe('Keep my draft')
    expect(current.editorDirty).toBe(true)
  })

  it('associates replacement-key validation errors with the password control', async () => {
    mount(true)
    await settle()
    act(() => current.select(provider))
    act(() => current.updateDraft('api_key', 'invalid\u0001key'))
    const input = host.querySelector<HTMLInputElement>('input[type="password"]')!
    const descriptions = input.getAttribute('aria-describedby')!.split(' ')
    expect(descriptions).toContain('provider-key-help')
    expect(descriptions).toContain('provider-key-error')
    expect(document.getElementById('provider-key-error')?.textContent).toContain('printable ASCII')
    act(() => current.updateDraft('api_key', ''))
    expect(input.getAttribute('aria-describedby')).toBe('provider-key-help')
    expect(document.getElementById('provider-key-error')).toBeNull()
  })

  it('restores focus to Add provider after deletion and does not steal later focus', async () => {
    vi.mocked(apiFetch).mockImplementation((path, init) =>
      init?.method === 'DELETE'
        ? (Promise.resolve(undefined) as never)
        : (Promise.resolve(respondToRead(path)) as never),
    )
    mount(true)
    await settle()
    act(() => current.select(provider))
    await settle()
    const remove = [...host.querySelectorAll('button')].find(
      (button) => button.textContent?.trim() === 'Delete provider',
    )!
    act(() => {
      remove.focus()
      remove.click()
    })
    act(() => current.remove())
    await settle()
    await vi.waitFor(() => expect(document.activeElement?.textContent?.trim()).toBe('Add provider'))
    expect(current.completedDeletes).toBe(1)
    const search = host.querySelector<HTMLInputElement>('input')!
    search.focus()
    act(() => current.setSearch('Changed search'))
    await settle()
    expect(document.activeElement).toBe(search)
  })
  it('saves visible compatibility controls and retains omitted temperature after refresh', async () => {
    let submitted: Record<string, unknown> | undefined
    vi.mocked(apiFetch).mockImplementation((path, init) => {
      if (init?.method === 'PUT') {
        submitted = body(init)
        savedProvider = { ...savedProvider, ...submitted, version: 2 }
        return Promise.resolve(savedProvider) as never
      }
      return Promise.resolve(respondToRead(path)) as never
    })
    mount(true)
    await settle()
    act(() => current.select(provider))
    await settle()
    act(() => {
      host.querySelector('summary')!.click()
      for (const [label, value] of [
        ['Request format', 'chat_completions_modern'], ['Reasoning effort', 'minimal'], ['JSON response mode', 'json_object'],
      ]) {
        const field = host.querySelector<HTMLSelectElement>(`select[aria-label="${label}"]`)!
        field.value = value
        field.dispatchEvent(new Event('change', { bubbles: true }))
      }
      for (const [label, value] of [
        ['Provider temperature', ''], ['Model context limit (tokens)', '32768'], ['Model output limit (tokens)', '8192'],
        ['Concurrent request limit', '3'], ['Rolling-hour token budget', '1000000'],
      ]) {
        const field = host.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`)!
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(field, value)
        field.dispatchEvent(new Event('input', { bubbles: true }))
      }
    })
    expect(current.editorDirty).toBe(true)
    act(() => [...host.querySelectorAll('button')].find((button) => button.textContent?.trim() === 'Save provider')!.click())
    await settle()
    expect(submitted).toMatchObject({
      version: 1, request_dialect: 'chat_completions_modern', temperature: null,
      reasoning_effort: 'minimal', structured_output_mode: 'json_object',
      model_context_window_tokens: 32768, model_max_output_tokens: 8192,
      max_concurrent_requests: 3, hourly_token_budget: 1000000,
    })
    expect(current.editorDirty).toBe(false)
    expect(current.editor?.draft.temperature).toBe('')
    expect(current.editor?.draft.reasoning_effort).toBe('minimal')
  })

})
