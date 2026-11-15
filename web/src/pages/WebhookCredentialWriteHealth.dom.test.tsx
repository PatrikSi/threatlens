// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import type { WebhookCredentialProfile } from '../types/webhookAutomation'
import { WebhookCredentialProfiles } from './WebhookCredentialProfiles'
import { deferred, intelButton, mountIntel, settle } from './articleIntelligenceTestSupport'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...await original<typeof import('../api/client')>(), apiFetch: vi.fn() }))
const first: WebhookCredentialProfile = { id: 'profile-a', name: 'Saved profile A', enabled: true,
  auth_type: 'none', header_name: null, auth_configured: false, signing_configured: false, revision: 1 }
const second: WebhookCredentialProfile = { ...first, id: 'profile-b', name: 'Cached sibling profile B' }
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
afterEach(() => { view?.close(); view = undefined; vi.resetAllMocks() })
function profileSelect() {
  return [...document.querySelectorAll('label')].find((label) => label.textContent?.trim().startsWith('Saved profile'))!.querySelector('select')!
}
function profileName() {
  return [...document.querySelectorAll('label')].find((label) => label.textContent?.trim().startsWith('Profile name'))!.querySelector('input')!
}
async function chooseProfile(id: string) {
  act(() => {
    const select = profileSelect()
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!.call(select, id)
    select.dispatchEvent(new Event('change', { bubbles: true }))
  }); await settle()
}
function editName(value: string) {
  act(() => {
    const input = profileName()
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

it('does not reopen cached sibling credential writes after a partial save acknowledges an errored collection', async () => {
  const saved = deferred<WebhookCredentialProfile>()
  let mode: 'good' | 'failed' | 'held' = 'good'
  vi.mocked(apiFetch).mockImplementation((path, init) => {
    if (path.endsWith('/profile-a') && init?.method === 'PATCH') return saved.promise
    if (path.endsWith('/profile-b') && init?.method === 'PATCH') return Promise.resolve({ ...second, revision: 2, name: 'Changed stale sibling' })
    if (path === '/notifications/credential-profiles') {
      if (mode === 'failed') return Promise.reject(new ApiError('Canonical credential collection unavailable', 503, path))
      if (mode === 'held') return new Promise(() => {})
      return Promise.resolve([first, second])
    }
    throw new Error(`Unexpected request: ${path}`)
  })
  view = await mountIntel(<WebhookCredentialProfiles writable onClose={vi.fn()} onDirtyChange={vi.fn()} />)
  await vi.waitFor(() => expect(profileSelect().textContent).toContain(second.name))
  await chooseProfile(first.id)
  expect(profileName().value).toBe(first.name)
  editName('Submitted profile A')
  act(() => intelButton(document.body, 'Save credential profile').click())
  await vi.waitFor(() => expect(vi.mocked(apiFetch).mock.calls.filter(([, init]) => init?.method === 'PATCH')).toHaveLength(1))
  mode = 'failed'
  await act(async () => { await view!.client.invalidateQueries({ queryKey: ['notifications', 'credential-profiles'], exact: true }) })
  await vi.waitFor(() => expect(document.body.textContent).toContain('Canonical credential collection unavailable'))
  mode = 'held'
  await act(async () => saved.resolve({ ...first, revision: 2, name: 'Submitted profile A' }))
  await vi.waitFor(() => expect(view!.client.getMutationCache().getAll().some((mutation) => mutation.state.status === 'success')).toBe(true))
  await settle()
  expect.soft(profileSelect().disabled).toBe(true)
  if (!profileSelect().disabled) {
    await chooseProfile(second.id)
    expect(profileName().value).toBe(second.name)
    editName('Changed stale sibling')
    const save = intelButton(document.body, 'Save credential profile')
    expect.soft(save.matches(':disabled')).toBe(true)
    act(() => save.click()); await settle()
  }
  expect(vi.mocked(apiFetch).mock.calls.filter(([path, init]) => path.endsWith('/profile-b') && init?.method === 'PATCH')).toHaveLength(0)
})

it('pauses a queued saved credential write after collection503 before observer delivery', async () => {
  let failed = false
  vi.mocked(apiFetch).mockImplementation((path, init) => {
    if (path.endsWith('/profile-b') && init?.method === 'PATCH') return Promise.resolve({ ...second, revision: 2, name: 'Queued credential edit' })
    if (path === '/notifications/credential-profiles') return failed
      ? Promise.reject(new ApiError('Canonical collection read failed', 503, path)) : Promise.resolve([first, second])
    throw new Error(`Unexpected request: ${path}`)
  })
  view = await mountIntel(<WebhookCredentialProfiles writable onClose={vi.fn()} onDirtyChange={vi.fn()} />)
  await vi.waitFor(() => expect(profileSelect().textContent).toContain(second.name))
  await chooseProfile(second.id)
  editName('Queued credential edit')
  const save = intelButton(document.body, 'Save credential profile')
  expect(save.matches(':disabled')).toBe(false)
  let observed = false
  const unsubscribe = view.client.getQueryCache().subscribe((event) => {
    if (!observed && event.type === 'updated' && event.action.type === 'error'
      && JSON.stringify(event.query.queryKey) === JSON.stringify(['notifications', 'credential-profiles'])) {
      observed = true
      save.click()
    }
  })
  failed = true
  try {
    await act(async () => { await view!.client.invalidateQueries({ queryKey: ['notifications', 'credential-profiles'], exact: true }) }); await settle()
    expect(observed).toBe(true)
    expect(vi.mocked(apiFetch).mock.calls.filter(([, init]) => init?.method === 'PATCH')).toHaveLength(0)
  } finally { unsubscribe() }
})
