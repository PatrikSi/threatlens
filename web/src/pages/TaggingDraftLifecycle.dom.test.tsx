// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { apiFetch } from '../api/client'
import type { TaggingRule, TaggingSettingsBundleResponse } from '../types/api'
import { createDefaultRuleDraft } from './taggingSettingsModel'
import { TaggingSettingsPage } from './TaggingSettingsPage'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', () => ({ apiFetch: vi.fn() }))
vi.mock('../hooks/useCurrentUser', () => ({ useCurrentUser: () => ({ data: { access: { permissions: ['write:tagging'] } } }) }))
const rule: TaggingRule = {
  ...createDefaultRuleDraft(), id: 'rule-1', name: 'Original rule', tag_name: 'vpn', pattern: 'vpn',
  min_classification_confidence: null, created_at: '2026-09-08T00:00:00Z', updated_at: '2026-09-08T00:00:00Z',
}
const secondRule = { ...rule, id: 'rule-2', name: 'Second rule' }
let bundle: TaggingSettingsBundleResponse
let root: Root
let client: QueryClient
let router: ReturnType<typeof createMemoryRouter>
let container: HTMLDivElement

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}
async function settle() { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 10)) }) }
async function mount() {
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } })
  router = createMemoryRouter([
    { path: '/tagging', element: <TaggingSettingsPage /> },
    { path: '/elsewhere', element: <p>Destination</p> },
  ], { initialEntries: ['/tagging'] })
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  act(() => root.render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>))
  await settle()
}
function button(name: string, scope: ParentNode = document) {
  const found = [...scope.querySelectorAll('button')].find((element) => element.textContent?.trim() === name)
  expect(found, `button ${name}`).toBeDefined()
  return found!
}
function click(name: string, scope: ParentNode = document) { act(() => button(name, scope).click()) }
function select(name: string) {
  const found = [...container.querySelectorAll('button')].find((element) => element.querySelector('p')?.textContent === name)
  expect(found).toBeDefined()
  act(() => found!.click())
}
function field(id: string) { return document.getElementById(`tagging-rule-${id}`) as HTMLInputElement | HTMLTextAreaElement }
function edit(id: string, value: string) {
  const element = field(id)
  const prototype = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype
  act(() => {
    Object.getOwnPropertyDescriptor(prototype, 'value')!.set!.call(element, value)
    element.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
beforeEach(() => {
  bundle = {
    settings: { id: 'settings', enabled_categories: ['vulnerability'], min_auto_tag_confidence: 0.45,
      secondary_tag_limit: 2, created_at: rule.created_at, updated_at: rule.updated_at },
    rules: [rule, secondRule],
  }
  vi.mocked(apiFetch).mockImplementation((path) => Promise.resolve(path === '/tagging/settings' ? bundle : []) as never)
})
afterEach(() => {
  act(() => root?.unmount())
  router?.dispose()
  client?.clear()
  document.body.replaceChildren()
  vi.resetAllMocks()
})

describe('tagging rule asynchronous lifecycle with a real router and cache', () => {
  it('rebases accepted fields while preserving later edits and their navigation warning', async () => {
    const pending = deferred<TaggingRule>()
    vi.mocked(apiFetch).mockImplementation((path, init) => init?.method === 'PATCH'
      ? pending.promise : Promise.resolve(path === '/tagging/settings' ? bundle : []) as never)
    await mount()
    select(rule.name)
    edit('name', 'Submitted')
    click('Save rule')
    await settle()
    edit('name', 'Later unsaved name')
    bundle = { ...bundle, rules: [{ ...rule, name: 'Submitted', tag_name: 'normalized' }, secondRule] }
    await act(async () => pending.resolve(bundle.rules[0]))
    await settle()
    expect(field('name').value).toBe('Later unsaved name')
    expect(field('tag-name').value).toBe('normalized')
    await act(async () => { void router.navigate('/elsewhere') })
    expect(document.querySelector('[role="alertdialog"]')).not.toBeNull()
    click('Cancel', document.querySelector('[role="alertdialog"]')!)
    expect(router.state.location.pathname).toBe('/tagging')
    expect(field('name').value).toBe('Later unsaved name')
  })

  it.each(['edit', 'reselect'] as const)('rejects a preview after an intervening %s', async (transition) => {
    const pending = deferred<{ total: number; items: [] }>()
    vi.mocked(apiFetch).mockImplementation((path, init) => init?.method === 'POST'
      ? pending.promise : Promise.resolve(path === '/tagging/settings' ? bundle : []) as never)
    await mount()
    select(rule.name)
    click('Preview rule')
    await settle()
    if (transition === 'edit') edit('pattern', 'changed input')
    else { select(secondRule.name); select(rule.name) }
    await act(async () => pending.resolve({ total: 10, items: [] }))
    await settle()
    expect(container.textContent).toContain('Run a preview to inspect')
    expect(container.textContent).not.toContain('10 preview matches')
    expect(container.textContent).not.toContain('Preview loaded.')
  })

  it('keeps an accepted create selected while canceling an older inventory response', async () => {
    const save = deferred<TaggingRule>()
    const inventory = deferred<TaggingSettingsBundleResponse>()
    let reads = 0
    vi.mocked(apiFetch).mockImplementation((path, init) => init?.method === 'POST' ? save.promise
      : path === '/tagging/settings' ? (++reads === 1 ? Promise.resolve(bundle) : inventory.promise)
      : Promise.resolve([]) as never)
    await mount()
    edit('name', 'Created rule')
    edit('tag-name', 'vpn')
    edit('pattern', 'vpn')
    await act(async () => { void client.invalidateQueries({ queryKey: ['tagging', 'settings'] }) })
    click('Create rule')
    await settle()
    const saved = { ...rule, id: 'new-rule', name: 'Created rule' }
    await act(async () => save.resolve(saved))
    await settle()
    expect(field('name').value).toBe('Created rule')
    expect(button('Save rule')).toBeDefined()
    expect(client.getQueryData<TaggingSettingsBundleResponse>(['tagging', 'settings'])?.rules).toContainEqual(saved)
    // A stale inventory must never own (and clear) the accepted editor baseline.
    await act(async () => inventory.resolve(bundle))
    await settle()
    expect(field('name').value).toBe('Created rule')
    expect(button('Save rule')).toBeDefined()
  })

  it('does not replace a subsequently reselected editor when an earlier save completes', async () => {
    const pending = deferred<TaggingRule>()
    vi.mocked(apiFetch).mockImplementation((path, init) => init?.method === 'PATCH'
      ? pending.promise : Promise.resolve(path === '/tagging/settings' ? bundle : []) as never)
    await mount()
    select(rule.name)
    edit('name', 'Submitted')
    click('Save rule')
    await settle()
    select(secondRule.name)
    click('Discard changes')
    select(rule.name)
    edit('name', 'Reselected draft')
    await act(async () => pending.resolve({ ...rule, name: 'Submitted' }))
    await settle()
    expect(field('name').value).toBe('Reselected draft')
    expect(field('tag-name').value).toBe('vpn')
    expect(container.textContent).not.toContain('Tagging rule updated.')
  })

  it('keeps a different selection when deletion completes', async () => {
    const pending = deferred<void>()
    vi.mocked(apiFetch).mockImplementation((path, init) => init?.method === 'DELETE'
      ? pending.promise : Promise.resolve(path === '/tagging/settings' ? bundle : []) as never)
    await mount()
    select(rule.name)
    click('Delete rule')
    click('Delete rule', document.querySelector('[role="alertdialog"]')!)
    await settle()
    select(secondRule.name)
    await act(async () => pending.resolve())
    await settle()
    expect(field('name').value).toBe(secondRule.name)
  })

  it('shows scoped incomplete work and explains retained tags and deliberate recovery', async () => {
    bundle.tagging_recovery = { pending: 7, retrying: 4, needs_attention: 3,
      errors: [{ code: 'worker_unavailable', count: 4, message: 'Rule evaluator is unavailable.' }] }
    await mount()
    const status = container.querySelector('[aria-label="Incomplete tagging"]')!
    expect(status.textContent).toContain('7 accessible items: 4 awaiting automatic retry, 3 need attention')
    expect(status.textContent).toContain('may be out of date')
    expect(status.textContent).toContain('Rule evaluator is unavailable. (4 items)')
    expect(status.textContent).toContain('affected time window')
  })
})
