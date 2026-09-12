// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import { StatsPage } from './StatsPage'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...(await original<object>()), apiFetch: vi.fn() }))
const user = vi.hoisted(() => ({ role: 'analyst', access: { permissions: ['read:stats'] }, features: { ai_enabled: true } }))
vi.mock('../hooks/useCurrentUser', () => ({ useCurrentUser: () => ({ data: user, isLoading: false, isError: false }) }))
let root: Root | null = null
let host: HTMLDivElement | null = null
let client: QueryClient | null = null
async function settle() { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)) }) }
async function mount(path = '/stats') {
  vi.mocked(apiFetch).mockRejectedValue(new ApiError('Metrics temporarily unavailable', 503, '/statistics'))
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  act(() => root!.render(<MemoryRouter initialEntries={[path]}><QueryClientProvider client={client!}><StatsPage /></QueryClientProvider></MemoryRouter>))
  await settle()
}
const tab = (text: string) => Array.from(host!.querySelectorAll('nav button')).find((button) => button.textContent?.startsWith(text)) as HTMLButtonElement

afterEach(() => { act(() => root?.unmount()); client?.clear(); host?.remove(); vi.clearAllMocks(); user.role = 'analyst'; user.access.permissions = ['read:stats']; user.features.ai_enabled = true })
describe('statistics section access and navigation', () => {
  it('keeps ingestion navigation usable on a restricted AI deep link without polling AI', async () => {
    await mount('/stats?section=ai')
    expect(host!.textContent).toContain('AI statistics require the administrator role')
    expect(tab('AI statistics').disabled).toBe(true)
    expect(tab('Ingestion statistics').disabled).toBe(false)
    expect(apiFetch).not.toHaveBeenCalled()
    act(() => tab('Ingestion statistics').click()); await settle()
    expect(vi.mocked(apiFetch).mock.calls.some(([path]) => path.startsWith('/stats/'))).toBe(true)
    expect(vi.mocked(apiFetch).mock.calls.some(([path]) => path.startsWith('/ai/'))).toBe(false)
  })
  it('opens AI-only administrators on their allowed statistics section', async () => {
    user.role = 'admin'; user.access.permissions = ['read:ai']
    await mount()
    expect(tab('AI statistics').getAttribute('aria-pressed')).toBe('true')
    expect(tab('Ingestion statistics').disabled).toBe(true)
    expect(apiFetch).toHaveBeenCalled()
    expect(vi.mocked(apiFetch).mock.calls.every(([path]) => path.startsWith('/ai/'))).toBe(true)
  })
  it('keeps an allowed escape from an ingestion deep link for AI-only administrators', async () => {
    user.role = 'admin'; user.access.permissions = ['read:ai']
    await mount('/stats?section=ingestion')
    expect(apiFetch).not.toHaveBeenCalled()
    expect(host!.textContent).toContain('Ingestion statistics require read:stats')
    act(() => tab('AI statistics').click()); await settle()
    expect(vi.mocked(apiFetch).mock.calls.every(([path]) => path.startsWith('/ai/'))).toBe(true)
  })
  it('keeps AI statistics sealed for additive analyst permissions and for disabled installations', async () => {
    user.access.permissions = ['read:ai']
    await mount('/stats?section=ai')
    expect(apiFetch).not.toHaveBeenCalled()
    user.role = 'admin'; user.features.ai_enabled = false
    act(() => root!.render(<MemoryRouter initialEntries={['/stats?section=ai']}><QueryClientProvider client={client!}><StatsPage /></QueryClientProvider></MemoryRouter>))
    await settle()
    expect(host!.textContent).toContain('AI is disabled')
    expect(apiFetch).not.toHaveBeenCalled()
  })
})
