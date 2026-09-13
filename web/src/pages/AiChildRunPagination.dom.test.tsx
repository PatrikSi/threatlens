// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, useRef } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import type { AITaskRunListResponse, AITaskRunResponse } from '../types/api'
import type { ActivityTabProps } from './AiActivityTypes'
import { useAiActivityRunState } from './useAiActivityRunState'
import { SelectedRunSection } from './AiTaskRunDetail'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...(await original<object>()), apiFetch: vi.fn() }))
let root: Root
let host: HTMLDivElement
let client: QueryClient
const parent = { id: 'parent', task_type: 'reprocess', status: 'ready', metadata: {}, target_count: 1000,
  processed_count: 1000, success_count: 1000, error_count: 0, skipped_count: 0, trigger_source: 'manual' } as AITaskRunResponse
function pageData(offset: number, total = 1000, parentId = 'parent'): AITaskRunListResponse {
  return { total, limit: 50, offset, items: Array.from({ length: Math.min(50, Math.max(0, total - offset)) }, (_, i) => ({
    ...parent, id: `${parentId}-child-${offset + i}`, task_type: 'item_enrichment', item_title: `${parentId} article ${offset + i}`,
  })) }
}
function Fixture({ parentId = 'parent' }: { parentId?: string }) {
  const ref = useRef<HTMLDivElement>(null)
  const detail = { data: { run: { ...parent, id: parentId }, events: [] }, isLoading: false, isError: false } as unknown as ActivityTabProps['runDetailQuery']
  const state = useAiActivityRunState({ runPage: 0, setRunPage: () => {}, selectedRunId: parentId, runDetailQuery: detail,
    runsQuery: { data: { items: [parent], total: 1 }, isPlaceholderData: false } as ActivityTabProps['runsQuery'] })
  return <SelectedRunSection selectedRunSectionRef={ref} runDetailQuery={detail} briefSources={[]}
    briefSourcesLoading={false} briefSourcesErrorMessage="" onCancelRun={() => {}} cancelingRunId={null} runState={state} />
}
function render(parentId = 'parent') {
  act(() => root.render(<MemoryRouter><QueryClientProvider client={client}><Fixture parentId={parentId} /></QueryClientProvider></MemoryRouter>))
}
function mount() {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  host = document.createElement('div'); document.body.append(host); root = createRoot(host); render()
}
function button(label: string) { return [...host.querySelectorAll('button')].find((entry) => entry.textContent?.trim() === label)! }
async function settle() { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 15)) }) }
async function click(label: string) { act(() => button(label).click()); await settle() }
afterEach(() => { act(() => root?.unmount()); client?.clear(); host?.remove(); vi.resetAllMocks() })

it('visits all 1000 children with bounded requests and returns to the first page', async () => {
  const offsets: number[] = []
  vi.mocked(apiFetch).mockImplementation((path) => {
    const params = new URL(path, 'https://fixture.invalid').searchParams
    expect(params.get('limit')).toBe('50')
    const offset = Number(params.get('offset')); offsets.push(offset)
    return Promise.resolve(pageData(offset)) as never
  })
  mount(); await settle()
  for (let page = 0; page < 20; page++) {
    expect(host.textContent).toContain(`Showing ${page * 50 + 1}–${page * 50 + 50} of 1000 queued article runs`)
    expect(host.textContent).toContain(`parent article ${page * 50}`)
    expect(host.textContent).toContain(`parent article ${page * 50 + 49}`)
    if (page < 19) await click('Next page')
  }
  expect(offsets).toEqual(Array.from({ length: 20 }, (_, i) => i * 50))
  expect(button('Next page').disabled).toBe(true)
  await click('Previous page'); expect(host.textContent).toContain('Showing 901–950')
  await click('First page'); expect(host.textContent).toContain('Showing 1–50')
})

it('retains the accepted page through a delayed failure and retries the failed page', async () => {
  let rejectPage!: (reason: Error) => void
  const pending = new Promise<AITaskRunListResponse>((_resolve, reject) => { rejectPage = reject })
  let requests = 0
  vi.mocked(apiFetch).mockImplementation((path) => {
    const offset = Number(new URL(path, 'https://fixture.invalid').searchParams.get('offset'))
    if (offset === 50 && ++requests === 1) return pending as never
    return Promise.resolve(pageData(offset)) as never
  })
  mount(); await settle(); await click('Next page')
  expect(host.textContent).toContain('parent article 0')
  expect(host.textContent).toContain('Loading article runs page 2')
  expect(button('Next page').disabled).toBe(true)
  await act(async () => rejectPage(new Error('Temporary page outage'))); await settle()
  expect(host.textContent).toContain('Temporary page outage')
  expect(host.textContent).toContain('parent article 0')
  expect(button('First page').disabled).toBe(false)
  await click('Retry loading article runs')
  expect(host.textContent).toContain('Showing 51–100')
  expect(host.textContent).not.toContain('Temporary page outage')
})

it('offers retry for an initial error and clears stale rows when access is denied', async () => {
  let first = true
  vi.mocked(apiFetch).mockImplementation(() => {
    if (first) { first = false; return Promise.reject(new Error('Initial outage')) }
    return Promise.resolve(pageData(0)) as never
  })
  mount(); await settle(); expect(host.textContent).toContain('Initial outage')
  await click('Retry loading article runs'); expect(host.textContent).toContain('parent article 0')
  vi.mocked(apiFetch).mockRejectedValue(new ApiError('Access denied', 403, '/ai/ops/runs'))
  await act(async () => { await client.invalidateQueries({ queryKey: ['ai', 'ops', 'child-runs'] }) }); await settle()
  expect(host.textContent).not.toContain('parent article 0')
})

it('resets pagination and isolates late results when the selected parent changes', async () => {
  let resolveOld!: (value: AITaskRunListResponse) => void
  const oldPage = new Promise<AITaskRunListResponse>((resolve) => { resolveOld = resolve })
  let oldSignal: AbortSignal | null | undefined
  vi.mocked(apiFetch).mockImplementation((path, options) => {
    const params = new URL(path, 'https://fixture.invalid').searchParams
    const id = params.get('parent_run_id')!; const offset = Number(params.get('offset'))
    if (id === 'parent' && offset === 50) { oldSignal = options?.signal; return oldPage as never }
    return Promise.resolve(pageData(offset, 1000, id)) as never
  })
  mount(); await settle(); await click('Next page'); render('other'); await settle()
  expect(oldSignal?.aborted).toBe(true)
  expect(host.textContent).toContain('other article 0')
  expect(button('Previous page').disabled).toBe(true)
  await act(async () => resolveOld(pageData(50))); await settle()
  expect(host.textContent).not.toContain('parent article')
  expect(host.textContent).toContain('other article 0')
  render('parent'); await settle()
  expect(host.textContent).toContain('parent article 0')
  expect(button('Previous page').disabled).toBe(true)
})

it('returns to the last available page when retention reduces the total', async () => {
  let total = 1000
  vi.mocked(apiFetch).mockImplementation((path) => {
    const offset = Number(new URL(path, 'https://fixture.invalid').searchParams.get('offset'))
    return Promise.resolve(pageData(offset, total)) as never
  })
  mount(); await settle(); await click('Next page'); await click('Next page')
  total = 60
  await act(async () => { await client.invalidateQueries({ queryKey: ['ai', 'ops', 'child-runs'] }) }); await settle()
  await vi.waitFor(async () => { await settle(); expect(host.textContent).toContain('Showing 51–60 of 60') })
  expect(button('Next page').disabled).toBe(true)
})
