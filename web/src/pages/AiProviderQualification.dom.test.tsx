// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AiProviderQualification } from './AiProviderQualification'
import { ApiError, apiFetch } from '../api/client'
import { deferred, intelButton, mountIntel, settle } from './articleIntelligenceTestSupport'
vi.mock('../api/client', async (original) => ({ ...await original<typeof import('../api/client')>(), apiFetch: vi.fn() }))
;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
afterEach(() => { view?.close(); view = undefined; vi.resetAllMocks() })
const runId = 'a855d5bd-8cba-41a6-a314-cc18027dfe96'
const input = () => ({ run_id: runId, status: 'queued', provider_version: 3, token_budget: 24000, reserved_tokens: 0, features: ['report'], results: [], error: null })

describe('provider feature qualification', () => {
  it('retains a committed queue receipt without its private run link after history denial', async () => {
    let denied = false
    vi.mocked(apiFetch).mockImplementation((_path, options) => options?.method === 'POST'
      ? Promise.resolve(input()) : denied ? Promise.reject(new ApiError('Qualification history denied', 403, '/qualifications')) : Promise.resolve([]))
    view = await mountIntel(<AiProviderQualification providerId="provider-1" version={3} />)
    act(() => intelButton(view!.host, 'Feature qualification ▸').click()); await settle()
    act(() => (view!.host.querySelectorAll('input[type=checkbox]')[3] as HTMLInputElement).click())
    act(() => intelButton(view!.host, 'Queue feature qualification').click()); await settle()
    await vi.waitFor(() => expect(view!.host.textContent).toContain('Qualification queued'))
    expect(view.host.querySelector('a')?.getAttribute('href')).toContain(runId)
    denied = true
    act(() => intelButton(view!.host, 'Refresh qualifications').click()); await settle()
    await vi.waitFor(() => expect(view!.host.textContent).toContain('Qualification history denied'))
    expect(view.host.textContent).toContain('Qualification queued')
    expect(view.host.textContent).not.toContain(runId)
    expect(view.host.querySelector('a')).toBeNull()
  })

  it('preserves explicitly authorized qualification while the initial history read is pending', async () => {
    const pending = deferred<unknown>()
    vi.mocked(apiFetch).mockImplementation((_path, options) => options?.method === 'POST' ? Promise.resolve(input()) : pending.promise)
    view = await mountIntel(<AiProviderQualification providerId="provider-1" version={3} />)
    act(() => intelButton(view!.host, 'Feature qualification ▸').click()); await settle()
    act(() => (view!.host.querySelectorAll('input[type=checkbox]')[3] as HTMLInputElement).click())
    expect(intelButton(view.host, 'Queue feature qualification').disabled).toBe(false)
    act(() => intelButton(view!.host, 'Queue feature qualification').click()); await settle()
    await vi.waitFor(() => expect(view!.host.textContent).toContain('Qualification queued'))
    const submitted = vi.mocked(apiFetch).mock.calls.find((call) => call[1]?.method === 'POST')!
    expect(JSON.parse(String(submitted[1]!.body))).toMatchObject({ provider_version: 3, authorize_provider_calls: true, token_budget: 24000 })
    await act(async () => pending.resolve([])); await settle()
    expect(view.host.querySelector('fieldset')?.disabled).toBe(true)
  })

  it('keeps denied qualification history hidden through a later outage and reopening', async () => {
    vi.mocked(apiFetch).mockResolvedValue([{ ...input(), error: 'Private qualification history' }])
    view = await mountIntel(<AiProviderQualification providerId="provider-1" version={3} />)
    act(() => intelButton(view!.host, 'Feature qualification ▸').click()); await settle()
    await vi.waitFor(() => expect(view!.host.textContent).toContain('Private qualification history'))
    vi.mocked(apiFetch).mockRejectedValue(new ApiError('Qualifications forbidden', 403, '/ai/providers/provider-1/qualifications'))
    act(() => intelButton(view!.host, 'Refresh qualifications').click()); await settle()
    await vi.waitFor(() => expect(view!.host.textContent).toContain('Qualifications forbidden'))
    expect(view.host.textContent).not.toContain('Private qualification history')
    vi.mocked(apiFetch).mockRejectedValue(new ApiError('Qualifications unavailable', 503, '/ai/providers/provider-1/qualifications'))
    act(() => intelButton(view!.host, 'Refresh qualifications').click()); await settle()
    await vi.waitFor(() => expect(view!.host.textContent).toContain('Qualifications unavailable'))
    expect(view.host.textContent).not.toContain('Private qualification history')
    act(() => intelButton(view!.host, 'Feature qualification ▾').click())
    act(() => intelButton(view!.host, 'Feature qualification ▸').click()); await settle()
    expect(view.host.textContent).not.toContain('Private qualification history')
    vi.mocked(apiFetch).mockResolvedValue([{ ...input(), error: 'Fresh qualification history' }])
    act(() => intelButton(view!.host, 'Refresh qualifications').click()); await settle()
    await vi.waitFor(() => expect(view!.host.textContent).toContain('Fresh qualification history'))
  })

  it('requires explicit call authorization and disables editing while pending', async () => {
    const pending = deferred<unknown>()
    vi.mocked(apiFetch).mockImplementation((_path, options) => options?.method === 'POST' ? pending.promise : Promise.resolve([]))
    view = await mountIntel(<AiProviderQualification providerId="provider-1" version={3} />)
    act(() => intelButton(view!.host, 'Feature qualification ▸').click())
    await settle()
    expect(intelButton(view.host, 'Queue feature qualification').disabled).toBe(true)
    const checkbox = [...view.host.querySelectorAll('input[type=checkbox]')].at(-1) as HTMLInputElement
    act(() => checkbox.click())
    act(() => intelButton(view!.host, 'Queue feature qualification').click())
    await settle()
    const submitted = vi.mocked(apiFetch).mock.calls.find((call) => call[1]?.method === 'POST')!
    expect(JSON.parse(String(submitted[1]!.body))).toMatchObject({ provider_version: 3, authorize_provider_calls: true, token_budget: 24000 })
    expect(view.host.querySelector('fieldset')?.disabled).toBe(true)
    await act(async () => pending.resolve(input()))
    await settle()
    expect(view.host.textContent).toContain('Qualification queued')
    expect(view.host.querySelector('a')?.getAttribute('href')).toBe(`/settings/ai?run=${runId}`)
    expect(view.host.textContent).toContain('does not establish semantic quality')
  })

  it('links each stored qualification to its exact run', async () => {
    vi.mocked(apiFetch).mockResolvedValue([input()])
    view = await mountIntel(<AiProviderQualification providerId="provider-1" version={3} />)
    act(() => intelButton(view!.host, 'Feature qualification ▸').click())
    await settle()
    expect(view.host.querySelector('article a')?.getAttribute('href')).toBe(`/settings/ai?run=${runId}`)
    expect(view.host.querySelector('article a')?.textContent).toContain(runId)
  })

  it('retries an uncertain queue response with the same request ID', async () => {
    const bodies: string[] = []
    vi.mocked(apiFetch).mockImplementation((_path, options) => {
      if (options?.method !== 'POST') return Promise.resolve([])
      bodies.push(String(options.body))
      return Promise.reject(new Error('Response lost'))
    })
    view = await mountIntel(<AiProviderQualification providerId="provider-1" version={3} />)
    act(() => intelButton(view!.host, 'Feature qualification ▸').click())
    await settle()
    act(() => (view!.host.querySelectorAll('input[type=checkbox]')[3] as HTMLInputElement).click())
    act(() => intelButton(view!.host, 'Queue feature qualification').click())
    await settle()
    act(() => intelButton(view!.host, 'Queue feature qualification').click())
    await settle()
    expect(bodies).toHaveLength(2)
    expect(JSON.parse(bodies[0]).request_id).toBe(JSON.parse(bodies[1]).request_id)
    expect(view.host.textContent).toContain('Qualification could not be loaded or queued')
  })
})
