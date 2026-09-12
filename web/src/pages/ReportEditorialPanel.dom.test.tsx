// @vitest-environment jsdom
import { act, useState } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { apiFetch } from '../api/client'
import { invalidateSession } from '../api/sessionLifecycle'
import type { ReportDetail } from '../types/api'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import { ReportEditorialPanel } from './ReportEditorialPanel'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', () => ({ apiFetch: vi.fn() }))
let root: Root | undefined
let client: QueryClient
let container: HTMLDivElement
let refreshReport: (report: ReportDetail) => void
const original = { id: 'report-one', title: 'Original', status: 'ready', publication_status: 'draft', editorial_version: 1, review_required: true,
  summary_text: 'Summary', sections: [{ key: 'summary', title: 'Summary', body_markdown: 'Claim [S1].' }] } as ReportDetail
function Harness() {
  const [report, setReport] = useState(original)
  const [dirty, setDirty] = useState(false)
  const discard = useUnsavedChangesWarning(dirty)
  refreshReport = setReport
  return <>{discard.discardDialog}<ReportEditorialPanel report={report} canManage canReview onRefresh={vi.fn()} onDirtyChange={setDirty} discard={discard} /></>
}
function mount() {
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
  const router = createMemoryRouter([{ path: '/', element: <Harness /> }])
  act(() => root!.render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>))
}
async function settle() { await act(async () => { await new Promise((done) => setTimeout(done, 15)) }) }
function click(text: string) {
  const button = Array.from(document.querySelectorAll('button')).find((entry) => entry.textContent?.trim() === text)!
  expect(button).toBeTruthy(); act(() => button.click())
}
function change(value: string) {
  const input = container.querySelector('input')!
  act(() => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value); input.dispatchEvent(new Event('input', { bubbles: true })) })
}
function deferred() {
  let resolve!: (report: ReportDetail) => void
  const promise = new Promise<ReportDetail>((done) => { resolve = done })
  return { promise, resolve }
}
afterEach(() => { act(() => root?.unmount()); client?.clear(); container?.remove(); vi.resetAllMocks(); invalidateSession() })

it('preserves an edited baseline through refresh and renders its discard confirmation', async () => {
  mount(); click('Edit draft'); change('Unsaved title')
  act(() => refreshReport({ ...original, title: 'New remote title', editorial_version: 2 }))
  expect(container.querySelector('input')?.value).toBe('Unsaved title')
  expect(container.textContent).toContain('A newer revision is available')
  click('Discard editor'); await settle()
  expect(document.querySelector('[role="alertdialog"]')).toBeTruthy()
  click('Discard changes'); await settle()
  click('Edit draft')
  expect(container.querySelector('input')?.value).toBe('New remote title')
})

it('freezes submitted fields and ignores old-session completion', async () => {
  const request = deferred(); vi.mocked(apiFetch).mockReturnValue(request.promise as never)
  mount(); click('Edit draft'); change('Submitted title'); click('Save draft'); await settle()
  expect(container.querySelector('input')?.closest('fieldset')?.disabled).toBe(true)
  expect(JSON.parse(vi.mocked(apiFetch).mock.lastCall![1]!.body as string).expected_version).toBe(1)
  invalidateSession(); await act(async () => request.resolve({ ...original, title: 'Submitted title', editorial_version: 2 })); await settle()
  expect(client.getQueryData(['reports', 'detail', original.id])).toBeUndefined()
  expect(container.querySelector('input')?.value).toBe('Submitted title')
})

it('sends the displayed approval revision and shows actionable errors', async () => {
  vi.mocked(apiFetch).mockRejectedValue(new Error('The report changed since you opened it.'))
  mount(); act(() => refreshReport({ ...original, publication_status: 'review', editorial_version: 4, revision_current: true }))
  click('Approve this revision'); await settle()
  expect(JSON.parse(vi.mocked(apiFetch).mock.lastCall![1]!.body as string)).toMatchObject({ action: 'approve', expected_version: 4 })
  expect(container.textContent).toContain('Refresh current status')
})
