// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AiProviderQualification } from './AiProviderQualification'
import { apiFetch } from '../api/client'
import { deferred, intelButton, mountIntel, settle } from './articleIntelligenceTestSupport'
vi.mock('../api/client', async (original) => ({ ...await original<typeof import('../api/client')>(), apiFetch: vi.fn() }))
;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
afterEach(() => { view?.close(); view = undefined; vi.resetAllMocks() })
const input = () => ({ run_id: 'run-1', status: 'queued', provider_version: 3, token_budget: 24000, reserved_tokens: 0, features: ['report'], results: [], error: null })

describe('provider feature qualification', () => {
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
    expect(view.host.textContent).toContain('does not establish semantic quality')
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
