// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'
import { apiFetch } from '../api/client'
import type { ReportLibraryPage, ReportListItem } from '../types/api'
import { ReportLibrary } from './ReportLibrary'
import { useReportLibrary } from './useReportLibrary'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', () => ({ apiFetch: vi.fn() }))
let root: Root | undefined
let client: QueryClient
let container: HTMLDivElement

function Harness() {
  const library = useReportLibrary()
  return <ReportLibrary controller={{ reportLibrary: library, reportsQuery: library.query, openReport: vi.fn() }} />
}
async function settle() { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 15)) }) }
function mount() {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  act(() => root!.render(<QueryClientProvider client={client}><Harness /></QueryClientProvider>))
}
function click(text: string) {
  const button = Array.from(container.querySelectorAll('button')).find((element) => element.textContent?.trim() === text)!
  expect(button).toBeTruthy()
  act(() => button.click())
}
function change(element: HTMLInputElement | HTMLSelectElement, value: string) {
  act(() => {
    const prototype = element instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype
    Object.getOwnPropertyDescriptor(prototype, 'value')!.set!.call(element, value)
    element.dispatchEvent(new Event(element instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }))
  })
}
function result(title: string, next: string | null = null): ReportLibraryPage {
  return { items: [{ id: title, title, status: 'ready', report_type: 'custom', trigger_source: 'manual',
    period_start: '2026-09-01T00:00:00Z', period_end: '2026-09-08T00:00:00Z',
    source_count: 1, included_source_count: 1, generated_at: '2026-09-08T00:00:00Z' } as ReportListItem],
  current_cursor: `${title}-anchor`, next_cursor: next, as_of: '2026-09-08T00:00:00Z' }
}
function deferred() {
  let resolve!: (value: ReportLibraryPage) => void
  const promise = new Promise<ReportLibraryPage>((done) => { resolve = done })
  return { resolve, promise }
}
afterEach(() => {
  act(() => root?.unmount())
  root = undefined
  client?.clear()
  container?.remove()
  vi.resetAllMocks()
})

it('clears unsubmitted search and type drafts while status changes preserve them', async () => {
  vi.mocked(apiFetch).mockResolvedValue(result('Report one'))
  mount(); await settle()
  const search = container.querySelector<HTMLInputElement>('input[type="search"]')!
  const type = container.querySelector<HTMLInputElement>('input[type="text"]')!
  const status = container.querySelector<HTMLSelectElement>('[aria-label="Report status"]')!
  change(search, 'Unsubmitted search')
  change(type, 'custom')
  change(status, 'ready')
  await settle()
  expect(search.value).toBe('Unsubmitted search')
  expect(type.value).toBe('custom')
  const path = vi.mocked(apiFetch).mock.lastCall![0]
  expect(path).toContain('status=ready')
  expect(path).not.toMatch(/[?&](q|report_type)=/)
  click('Clear report filters'); await settle()
  expect(container.querySelector<HTMLInputElement>('input[type="search"]')!.value).toBe('')
  expect(container.querySelector<HTMLInputElement>('input[type="text"]')!.value).toBe('')
  expect(status.value).toBe('')
  const form = container.querySelector('form')!
  act(() => form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  await settle()
  expect(vi.mocked(apiFetch).mock.lastCall![0]).not.toMatch(/[?&](q|report_type|status)=/)
})

it('keeps new filters and their cursor after an obsolete page response finishes', async () => {
  const obsolete = deferred()
  let obsoleteSignal: AbortSignal | undefined
  vi.mocked(apiFetch).mockImplementation((path, options) => {
    const params = new URLSearchParams(path.split('?')[1])
    if (params.get('cursor') === 'old-second') {
      obsoleteSignal = options?.signal as AbortSignal
      return obsolete.promise as never
    }
    return Promise.resolve(params.get('cursor') === 'ready-second' ? result('Ready older report')
      : params.get('status') === 'ready' ? result('Ready first report', 'ready-second') : result('Old first report', 'old-second')) as never
  })
  mount(); await settle()
  click('Next reports'); await settle()
  change(container.querySelector<HTMLSelectElement>('[aria-label="Report status"]')!, 'ready')
  await settle()
  expect(obsoleteSignal?.aborted).toBe(true)
  await act(async () => obsolete.resolve(result('Obsolete report', 'obsolete-next')))
  await settle()
  expect(container.textContent).toContain('Ready first report')
  expect(container.textContent).toContain('Page 1')
  expect(container.textContent).not.toContain('Obsolete report')
  click('Next reports'); await settle()
  expect(container.textContent).toContain('Ready older report')
  expect(container.textContent).toContain('Page 2')
  expect(vi.mocked(apiFetch).mock.lastCall![0]).toContain('cursor=ready-second')
})

it('refreshes the cutoff and ignores a late pagination completion after navigation away', async () => {
  const obsolete = deferred()
  let obsoleteSignal: AbortSignal | undefined
  let newest = 'Original report'
  vi.mocked(apiFetch).mockImplementation((path, options) => {
    if (new URLSearchParams(path.split('?')[1]).has('cursor')) {
      obsoleteSignal = options?.signal as AbortSignal
      return obsolete.promise as never
    }
    return Promise.resolve(result(newest, `${newest}-next`)) as never
  })
  mount(); await settle()
  click('Next reports'); await settle()
  newest = 'New arrival'
  click('Refresh'); await settle()
  expect(obsoleteSignal?.aborted).toBe(true)
  expect(container.textContent).toContain('New arrival')
  expect(container.textContent).toContain('Page 1')
  act(() => root!.render(<QueryClientProvider client={client}><p>Another workspace</p></QueryClientProvider>))
  await act(async () => obsolete.resolve(result('Late old page')))
  await settle()
  expect(container.textContent).toBe('Another workspace')
  act(() => root!.render(<QueryClientProvider client={client}><Harness /></QueryClientProvider>))
  await settle()
  expect(container.textContent).toContain('New arrival')
  expect(container.textContent).toContain('Page 1')
  expect(container.textContent).not.toContain('Late old page')
})
