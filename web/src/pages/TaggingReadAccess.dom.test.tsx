// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import type { TaggingRule, TaggingRulePreviewResponse, TaggingSettingsBundleResponse } from '../types/api'
import { createDefaultRuleDraft } from './taggingSettingsModel'
import { TaggingSettingsPage } from './TaggingSettingsPage'
import { useTaggingSettingsController } from './useTaggingSettingsController'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...(await original<typeof import('../api/client')>()), apiFetch: vi.fn() }))
vi.mock('../hooks/useCurrentUser', () => ({ useCurrentUser: () => ({ data: { access: { permissions: ['write:tagging', 'read:tagging'] } }, isError: false, isLoading: false }) }))
const rule: TaggingRule = { ...createDefaultRuleDraft(), id: 'rule-1', name: 'Private inventory rule', tag_name: 'private-tag', pattern: 'private-pattern', min_classification_confidence: null, created_at: '2026-11-15T09:00:00Z', updated_at: '2026-11-15T09:00:00Z' }
const preview: TaggingRulePreviewResponse = { total: 1, items: [{ id: 'private-item', title: 'Private preview article', feed_name: 'Private preview feed', classification: null, first_seen_at: rule.created_at, current_tags: ['private-tag'], matched_sections: ['title'] }] }
let bundle: TaggingSettingsBundleResponse
let status: number
let root: Root | undefined
let client: QueryClient
let router: ReturnType<typeof createMemoryRouter>
let host: HTMLDivElement
let current: ReturnType<typeof useTaggingSettingsController>
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done }); return { promise, resolve } }
async function settle() { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 15)) }) }
async function mount(handlers = false) {
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } })
  function Harness() { current = useTaggingSettingsController(); return <p>Tagging controller mounted</p> }
  router = createMemoryRouter([{ path: '/tagging', element: handlers ? <Harness /> : <TaggingSettingsPage /> }, { path: '/elsewhere', element: <p>Destination</p> }], { initialEntries: ['/tagging'] })
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  act(() => root!.render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>))
  await settle()
}
function button(name: string, scope: ParentNode = document) {
  const result = [...scope.querySelectorAll<HTMLButtonElement>('button')].find((entry) => entry.textContent?.trim() === name)
  expect(result, name).toBeDefined(); return result!
}
function click(name: string, scope?: ParentNode) { act(() => button(name, scope).click()) }
function selectRule() {
  const row = [...host.querySelectorAll<HTMLButtonElement>('button')].find((entry) => entry.querySelector('p')?.textContent === rule.name)
  expect(row).toBeDefined(); act(() => row!.click())
}
function input(id: string) { const value = document.getElementById(id); expect(value, id).toBeInstanceOf(HTMLInputElement); return value as HTMLInputElement }
function edit(id: string, value: string) { const element = input(id); act(() => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(element, value); element.dispatchEvent(new Event('input', { bubbles: true })) }) }
async function refresh(next: number) { status = next; await act(async () => { await client.invalidateQueries({ queryKey: ['tagging', 'settings'], exact: true }) }); await settle() }
function writes() { return vi.mocked(apiFetch).mock.calls.filter(([, options]) => options?.method) }
function read(path: string) {
  if (path === '/feeds') return Promise.resolve([])
  if (path === '/tagging/settings') return status === 200 ? Promise.resolve(bundle) : Promise.reject(new ApiError(`Tagging read ${status}`, status, path))
  throw new Error(`Unexpected GET ${path}`)
}
beforeEach(() => {
  status = 200
  bundle = { settings: { id: 'settings', enabled_categories: ['vulnerability'], min_auto_tag_confidence: 0.45, secondary_tag_limit: 2, created_at: rule.created_at, updated_at: rule.updated_at }, rules: [rule] }
  vi.mocked(apiFetch).mockImplementation((path, options) => options?.method
    ? Promise.resolve(path.endsWith('/preview') ? preview : path.endsWith('/reapply') ? { queued: true, task_id: 'task-1', celery_task_id: null, dispatch_token: null } : path.includes('/rules') ? rule : bundle.settings)
    : read(path))
})
afterEach(() => { act(() => root?.unmount()); root = undefined; router?.dispose(); client?.clear(); host?.remove(); document.body.replaceChildren(); vi.resetAllMocks() })

describe('tagging private read history and actual drafts', () => {
  it('keeps denied tagging inventory and untouched defaults hidden through a later outage', async () => {
    await mount(); expect(host.textContent).toContain(rule.name)
    expect(input('tagging-auto-confidence').value).toBe('0.45')
    for (const next of [403, 503]) {
      await refresh(next)
      expect(host.textContent).not.toContain(rule.name)
      expect(document.getElementById('tagging-auto-confidence')).toBeNull()
      expect(document.getElementById('tagging-rule-name')).toBeNull()
    }
  })

  it('preserves a hidden dirty rule until explicit reload of the fresh inventory baseline', async () => {
    await mount(); selectRule(); edit('tagging-rule-name', 'Unsent tagging rule draft')
    await refresh(403)
    expect(document.getElementById('tagging-rule-name')).toBeNull()
    bundle = { ...bundle, rules: [{ ...rule, name: 'Fresh server rule', updated_at: '2026-11-15T10:00:00Z' }] }
    await refresh(200)
    expect(input('tagging-rule-name').value).toBe('Unsent tagging rule draft')
    expect(button('Save rule').disabled).toBe(true)
    expect(button('Preview rule').disabled).toBe(true)
    click('Reload saved rule')
    expect(document.querySelector('[role="alertdialog"]')).not.toBeNull()
    click('Cancel', document.querySelector('[role="alertdialog"]')!)
    expect(input('tagging-rule-name').value).toBe('Unsent tagging rule draft')
    click('Reload saved rule'); click('Discard changes', document.querySelector('[role="alertdialog"]')!)
    expect(input('tagging-rule-name').value).toBe('Fresh server rule')
  })

  it('preserves hidden dirty defaults until explicit reload of their fresh baseline', async () => {
    await mount(); edit('tagging-auto-confidence', '0.73')
    await refresh(403)
    expect(document.getElementById('tagging-auto-confidence')).toBeNull()
    bundle = { ...bundle, settings: { ...bundle.settings, min_auto_tag_confidence: 0.8 } }
    await refresh(200)
    expect(input('tagging-auto-confidence').value).toBe('0.73')
    expect(button('Save defaults').disabled).toBe(true)
    click('Reload saved defaults')
    expect(document.querySelector('[role="alertdialog"]')).not.toBeNull()
    click('Cancel', document.querySelector('[role="alertdialog"]')!)
    expect(input('tagging-auto-confidence').value).toBe('0.73')
    click('Reload saved defaults'); click('Discard changes', document.querySelector('[role="alertdialog"]')!)
    expect(input('tagging-auto-confidence').value).toBe('0.8')
  })

  it('retains ordinary outage context and local inputs while pausing rule/default/reapply actions', async () => {
    await mount(); selectRule(); edit('tagging-rule-name', 'Local rule draft'); edit('tagging-auto-confidence', '0.73')
    await refresh(503)
    expect(host.textContent).toContain(rule.name)
    expect(input('tagging-rule-name').value).toBe('Local rule draft')
    expect(input('tagging-auto-confidence').value).toBe('0.73')
    for (const name of ['Save defaults', 'Save rule', 'Preview rule', 'Queue retagging']) expect(button(name).disabled, name).toBe(true)
    expect(writes()).toHaveLength(0)
  })

  it('checks current read authority in direct rule/default/preview/delete/reapply handlers', async () => {
    await mount(true)
    act(() => { current.onSelectRule(rule); current.onRequestDeleteRule(rule); current.onRequestReapplyTagging() })
    await settle()
    await refresh(503)
    act(() => {
      current.onSaveSettings(); current.onSaveRule(); current.onPreviewRule(); current.onConfirmDeleteRule(); current.onConfirmReapplyTagging()
    })
    await settle()
    expect(writes()).toHaveLength(0)
  })

  it.each(['delete', 'reapply'] as const)('pauses an opened %s intent during an outage and retires it after denial', async (kind) => {
    await mount(); selectRule()
    click(kind === 'delete' ? 'Delete rule' : 'Queue retagging')
    const label = kind === 'delete' ? 'Delete rule' : 'Queue full retagging'
    const initialDialog = document.querySelector('[role="alertdialog"]')!
    expect(initialDialog).not.toBeNull()
    await refresh(503)
    const dialog = document.querySelector('[role="alertdialog"]')!
    expect(button(label, dialog).disabled).toBe(true)
    expect(button('Cancel', dialog).disabled).toBe(false)
    click(label, dialog); expect(writes()).toHaveLength(0)
    await refresh(403)
    expect(document.querySelector('[role="alertdialog"]')).toBeNull()
    await refresh(200)
    expect(document.querySelector('[role="alertdialog"]')).toBeNull()
  })

  it('ignores a pending private preview after inventory read denial and a later outage', async () => {
    const pending = deferred<TaggingRulePreviewResponse>()
    vi.mocked(apiFetch).mockImplementation((path, options) => options?.method ? pending.promise : read(path))
    await mount(); selectRule(); click('Preview rule'); await settle()
    await refresh(403)
    await act(async () => { pending.resolve(preview) }); await settle()
    await refresh(503)
    expect(host.textContent).not.toContain('Private preview article')
    expect(host.textContent).not.toContain('Preview loaded.')
  })

  it('ignores an old rule save after inventory denial without accepting its baseline or private row', async () => {
    const pending = deferred<TaggingRule>()
    vi.mocked(apiFetch).mockImplementation((path, options) => options?.method ? pending.promise : read(path))
    await mount(); selectRule(); edit('tagging-rule-name', 'Submitted local draft'); click('Save rule'); await settle()
    await refresh(403)
    await act(async () => { pending.resolve({ ...rule, name: 'Private late saved row', tag_name: 'old-normalized-server-tag' }) }); await settle()
    await refresh(503)
    expect(host.textContent).not.toContain('Private late saved row')
    await refresh(200)
    expect(input('tagging-rule-name').value).toBe('Submitted local draft')
    expect(input('tagging-rule-tag-name').value).toBe('private-tag')
    expect(host.textContent).not.toContain('Tagging rule updated.')
    expect(button('Save rule').disabled).toBe(true)
  })

  it('ignores an old defaults completion after denial without falsely accepting the dirty baseline', async () => {
    const pending = deferred<TaggingSettingsBundleResponse['settings']>()
    vi.mocked(apiFetch).mockImplementation((path, options) => options?.method ? pending.promise : read(path))
    await mount(); edit('tagging-auto-confidence', '0.73'); click('Save defaults'); await settle()
    await refresh(403)
    await act(async () => { pending.resolve({ ...bundle.settings, min_auto_tag_confidence: 0.73 }) }); await settle()
    await refresh(503)
    expect(host.textContent).not.toContain('Tagging settings updated.')
    bundle = { ...bundle, settings: { ...bundle.settings, min_auto_tag_confidence: 0.9 } }
    await refresh(200)
    expect(input('tagging-auto-confidence').value).toBe('0.73')
    expect(button('Save defaults').disabled).toBe(true)
  })

  it('does not turn untouched saved settings into an unsaved draft when their read is denied', async () => {
    await mount(); expect(input('tagging-auto-confidence').value).toBe('0.45')
    await refresh(403)
    expect(document.getElementById('tagging-auto-confidence')).toBeNull()
    await act(async () => { await router.navigate('/elsewhere') })
    expect(router.state.location.pathname).toBe('/elsewhere')
    expect(document.querySelector('[role="alertdialog"]')).toBeNull()
  })

  it('retires independently denied preview matches through a later preview outage while inventory remains readable', async () => {
    let previewStatus = 200
    vi.mocked(apiFetch).mockImplementation((path, options) => options?.method ? previewStatus === 200 ? Promise.resolve(preview) : Promise.reject(new ApiError(`Preview read ${previewStatus}`, previewStatus, path)) : read(path))
    await mount(); selectRule(); click('Preview rule'); await settle()
    expect(host.textContent).toContain('Private preview article')
    for (const next of [403, 503]) {
      previewStatus = next; click('Preview rule'); await settle()
      expect(host.textContent).not.toContain('Private preview article')
      expect(host.textContent).toContain(rule.name)
    }
  })
})
