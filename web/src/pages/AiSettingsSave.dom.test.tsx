// @vitest-environment jsdom
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiFetch } from '../api/client'
import type { AISettings, AISettingsUpdateRequest } from '../types/api'
import { AiSettingsPage } from './AiSettingsPage'
import { createRequestFromDraft, DEFAULT_DRAFT } from './aiSettingsDraft'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...(await original<object>()), apiFetch: vi.fn() }))
vi.mock('../hooks/useCurrentUser', () => ({ useCurrentUser: () => ({ data: {
  id: 'admin', role: 'admin', features: { ai_enabled: true }, access: { permissions: ['*:*'] },
} }) }))
vi.mock('react-router-dom', async (original) => ({
  ...(await original<object>()), useBlocker: () => ({ state: 'unblocked' }),
}))

const baseline: AISettings = {
  ...createRequestFromDraft({ ...DEFAULT_DRAFT, model: 'model-before-save', base_url: 'https://model.example.test/v1' }),
  id: 'settings', ai_enabled: true, ai_configured: true, api_key_configured: true,
  created_at: '2026-09-12T00:00:00Z', updated_at: '2026-09-12T00:00:00Z',
  prompt_previews: {
    item_enrichment: { label: 'Articles', system_prompt: 'Summarize', notes: [] },
    daily_brief: { label: 'Briefs', system_prompt: 'Summarize', notes: [] },
  },
}
let root: Root | undefined
let client: QueryClient
let host: HTMLDivElement
function input(label: string) {
  const field = [...host.querySelectorAll('label')].find((node) => node.textContent?.startsWith(label))
  return field!.querySelector('input')!
}
function change(label: string, value: string) {
  const field = input(label)
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(field, value)
  field.dispatchEvent(new Event('input', { bubbles: true }))
}
function select(label: string, value: string) {
  const field = host.querySelector<HTMLSelectElement>(`select[aria-label="${label}"]`)!
  field.value = value
  field.dispatchEvent(new Event('change', { bubbles: true }))
}
function button(label: string) {
  return [...host.querySelectorAll('button')].find((node) => node.textContent?.trim() === label)!
}
async function settle() {
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)) })
}
afterEach(() => { act(() => root?.unmount()); client?.clear(); host?.remove(); vi.clearAllMocks() })

describe('saved AI settings with a real query cache', () => {
  it.each(['delayed', 'failed'])('keeps accepted model and report budgets through a %s refresh and subsequent edits', async (outcome) => {
    let saved = baseline
    let reads = 0
    const writes: AISettingsUpdateRequest[] = []
    let resolveRefresh!: (value: AISettings) => void
    let rejectRefresh!: (reason: Error) => void
    const refresh = new Promise<AISettings>((resolve, reject) => { resolveRefresh = resolve; rejectRefresh = reject })
    vi.mocked(apiFetch).mockImplementation((path, init) => {
      if (path !== '/ai/settings') return new Promise(() => {})
      if (init?.method === 'PUT') {
        const payload = JSON.parse(String(init.body)) as AISettingsUpdateRequest
        writes.push(payload)
        saved = { ...saved, ...payload }
        return Promise.resolve(saved) as never
      }
      reads += 1
      return (reads === 1 ? Promise.resolve(saved) : refresh) as never
    })
    client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
    host = document.createElement('div')
    document.body.appendChild(host)
    root = createRoot(host)
    act(() => root!.render(<QueryClientProvider client={client}><MemoryRouter><AiSettingsPage /></MemoryRouter></QueryClientProvider>))
    await settle()
    act(() => button('Configuration').click())
    await settle()
    act(() => {
      change('Model', 'model-after-save')
      change('Default completion tokens', '16000')
      change('Model Context Window', '65536')
      change('Initial report completion tokens', '8000')
      change('Temperature', '')
      change('Model context limit (tokens)', '65536')
      change('Model output limit (tokens)', '16384')
      change('Concurrent request limit', '2')
      change('Rolling-hour token budget', '100000')
      select('Request format', 'chat_completions_modern')
      select('Reasoning effort', 'low')
      select('JSON response mode', 'json_object')
    })
    expect(button('Save changes').disabled).toBe(false)
    act(() => button('Save changes').click())
    await settle()
    expect(writes).toHaveLength(1)
    expect(writes[0]).toMatchObject({
      temperature: null, request_dialect: 'chat_completions_modern', reasoning_effort: 'low',
      structured_output_mode: 'json_object', model_context_window_tokens: 65536, model_max_output_tokens: 16384,
      max_concurrent_requests: 2, hourly_token_budget: 100000,
    })
    expect(reads).toBeGreaterThan(1)
    expect(input('Model').value).toBe('model-after-save')
    expect(input('Default completion tokens').value).toBe('16000')
    expect(input('Initial report completion tokens').value).toBe('8000')
    expect(client.getQueryData<AISettings>(['ai', 'settings'])?.model).toBe('model-after-save')
    expect(host.querySelector('#saved-report-budgets-title')?.parentElement?.textContent).toContain('8,000 tokens')
    act(() => change('Company name', 'Later unsaved edit'))
    await act(async () => {
      if (outcome === 'failed') rejectRefresh(new Error('Settings refresh unavailable'))
      else resolveRefresh(saved)
    })
    await settle()
    expect(input('Model').value).toBe('model-after-save')
    expect(input('Default completion tokens').value).toBe('16000')
    expect(input('Company name').value).toBe('Later unsaved edit')
    if (outcome === 'delayed') {
      act(() => button('Save changes').click())
      await settle()
      expect(writes[1]).toMatchObject({ model: 'model-after-save', max_completion_tokens: 16000, company_name: 'Later unsaved edit', temperature: null, reasoning_effort: 'low', model_max_output_tokens: 16384 })
    } else {
      expect(host.textContent).toContain('Settings refresh unavailable')
      expect(client.getQueryData<AISettings>(['ai', 'settings'])?.max_completion_tokens).toBe(16000)
    }
  })
})
