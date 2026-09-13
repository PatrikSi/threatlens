// @vitest-environment jsdom
import { act, useReducer } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'
import { useReportBuilderDraft } from './useReportBuilderDraft'
import { reportBuilderFromTemplate, validateReportBuilder } from './reportingPageModel'
import type { ReportTemplate } from '../types/api'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
const defaults = reportBuilderFromTemplate(undefined)
const template: ReportTemplate = {
  id: 'template-1', owner_user_id: 'user', builtin_key: null, name: 'Template', description: '', report_type: 'custom', visibility: 'private',
  prompt: defaults.prompt, sections: defaults.sections,
  default_filters: validateReportBuilder(defaults.filterDraft, defaults.prompt, defaults.sections).filters!,
  resource_version: 'revision-1', created_at: '2026-09-08T00:00:00Z', updated_at: '2026-09-08T00:00:00Z',
}
let root: Root | undefined
let router: ReturnType<typeof createMemoryRouter>
let templates: ReportTemplate[]
let draft: ReturnType<typeof useReportBuilderDraft>
let rerender: () => void
function Harness() { const [, update] = useReducer((n: number) => n + 1, 0); rerender = update; draft = useReportBuilderDraft(templates, true); return <>{draft.discardDialog}<span>Builder</span></> }
function render() {
  templates = [structuredClone(template)]
  router = createMemoryRouter([{ path: '/reporting', element: <Harness /> }, { path: '/dashboard', element: <p>Dashboard</p> }], { initialEntries: ['/reporting'] })
  const element = document.createElement('div'); document.body.append(element); root = createRoot(element)
  act(() => root!.render(<RouterProvider router={router} />))
}
function refresh(next: ReportTemplate[]) { templates = next; act(() => rerender()) }
function click(label: string) { act(() => Array.from(document.querySelectorAll('button')).find((button) => button.textContent?.trim() === label)!.click()) }
afterEach(() => { act(() => root?.unmount()); router?.dispose(); document.body.innerHTML = '' })
describe('report builder draft hydration', () => {
  it('preserves all edits across unrelated refreshes and tracks the loaded template revision', () => {
    render()
    expect(draft.dirty).toBe(false)
    act(() => {
      draft.setPrompt((current) => ({ ...current, objective: 'My investigation' }))
      draft.setSections((current) => current.map((section) => ({ ...section, title: 'Custom heading' })))
      draft.setFilterDraft((current) => ({ ...current, q: 'CVE' }))
      draft.setExcludedItemIds(['private-source'])
    })
    refresh([{ ...template, name: 'Renamed', resource_version: 'revision-2' }, { ...template, id: 'other' }])
    expect(draft.prompt.objective).toBe('My investigation')
    expect(draft.sections[0].title).toBe('Custom heading')
    expect(draft.filterDraft.q).toBe('CVE')
    expect(draft.excludedItemIds).toEqual(['private-source'])
    expect(draft.selectedTemplate?.resource_version).toBe('revision-1')
    expect(draft.templateRevisionChanged).toBe(true)
    act(() => draft.reloadTemplate())
    click('Cancel')
    expect(draft.prompt.objective).toBe('My investigation')
    act(() => draft.reloadTemplate())
    click('Discard changes')
    expect(draft.selectedTemplate?.resource_version).toBe('revision-2')
    expect(draft.dirty).toBe(false)
  })

  it('protects navigation and preserves a draft when its template disappears', async () => {
    render()
    act(() => draft.setTitle('Unsaved title'))
    refresh([])
    expect(draft.templateUnavailable).toBe(true)
    expect(draft.title).toBe('Unsaved title')
    const unload = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(unload)
    expect(unload.defaultPrevented).toBe(true)
    await act(async () => { await router.navigate('/dashboard') })
    expect(router.state.location.pathname).toBe('/reporting')
    click('Cancel')
    expect(draft.title).toBe('Unsaved title')
    await act(async () => { await router.navigate('/dashboard') })
    click('Discard changes')
    expect(router.state.location.pathname).toBe('/dashboard')
  })

  it('marks only the submitted snapshot clean and keeps subsequent edits pending', () => {
    render()
    act(() => draft.setTitle('Submitted'))
    const submitted = draft.fingerprint
    act(() => draft.setTitle('Newer edit'))
    act(() => expect(draft.acceptSubmission(submitted)).toBe(false))
    expect(draft.dirty).toBe(true)
    act(() => expect(draft.acceptSubmission(draft.fingerprint)).toBe(true))
    expect(draft.dirty).toBe(false)
  })
})
