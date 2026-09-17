// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import { ReportSourceEvidenceDialog } from './ReportSourceEvidenceDialog'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...await original<typeof import('../api/client')>(), apiFetch: vi.fn() }))

let root: Root | undefined
let host: HTMLDivElement
let client: QueryClient
const close = vi.fn()
const refresh = vi.fn()
const first = {
  report_id: 'report', citation_key: 'S1', editorial_version: 3,
  source_revision: 'a'.repeat(64), evidence_text: 'The retained first passage.',
  offset: 0, next_offset: 8000, total_characters: 8005,
}

async function settle() { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)) }) }
function mount() {
  host = document.createElement('div'); document.body.append(host)
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  root = createRoot(host)
  act(() => root!.render(<QueryClientProvider client={client}>
    <ReportSourceEvidenceDialog reportId="report" citationKey="S1" editorialVersion={3} onClose={close} onRefresh={refresh} />
  </QueryClientProvider>))
}
function click(label: string) {
  const button = [...document.querySelectorAll<HTMLButtonElement>('button')].find((entry) => entry.textContent === label)
  expect(button).toBeTruthy()
  act(() => button!.click())
}
afterEach(() => { act(() => root?.unmount()); client?.clear(); host?.remove(); vi.clearAllMocks() })

it('reads bounded pages pinned to the original source and report revisions', async () => {
  vi.mocked(apiFetch).mockResolvedValueOnce(first).mockResolvedValueOnce({ ...first, offset: 8000, next_offset: null, evidence_text: 'Final' })
  mount(); await settle()
  expect(document.querySelector('[aria-label="Retained source passage"]')?.textContent).toBe(first.evidence_text)
  expect(document.body.textContent).toContain('Report revision 3')
  click('Next passage'); await settle()
  const request = String(vi.mocked(apiFetch).mock.calls[1][0])
  expect(request).toContain('editorial_version=3')
  expect(request).toContain('offset=8000')
  expect(request).toContain(`source_revision=${first.source_revision}`)
  expect(document.querySelector('[aria-label="Retained source passage"]')?.textContent).toBe('Final')
  expect(document.body.textContent).toContain('Characters 8001–8005 of 8005')
})

it('hides a cached passage after a failed permission refresh', async () => {
  vi.mocked(apiFetch).mockResolvedValueOnce(first).mockRejectedValue(new ApiError('Access changed', 403, '/reports/report/sources/S1/evidence'))
  mount(); await settle()
  await act(async () => { await client.refetchQueries({ queryKey: ['report-source-evidence'] }) })
  await settle()
  expect(document.querySelector('[aria-label="Retained source passage"]')).toBeNull()
  expect(document.querySelector('[role="alert"]')?.textContent).toContain('Retained evidence could not be loaded')
})

it('offers a report refresh after a revision conflict without mixing old text', async () => {
  vi.mocked(apiFetch).mockRejectedValue(new ApiError('The report changed', 409, '/reports/report/sources/S1/evidence'))
  mount(); await settle()
  click('Refresh report')
  expect(close).toHaveBeenCalledOnce()
  expect(refresh).toHaveBeenCalledOnce()
  expect(document.querySelector('[aria-label="Retained source passage"]')).toBeNull()
})

it('discloses legacy sources with no retained passage', async () => {
  vi.mocked(apiFetch).mockResolvedValue({ ...first, evidence_text: '', total_characters: 0, next_offset: null })
  mount(); await settle()
  expect(document.body.textContent).toContain('No passage was retained for this source')
  expect(document.querySelector('[aria-label="Retained source passage"]')).toBeNull()
})
