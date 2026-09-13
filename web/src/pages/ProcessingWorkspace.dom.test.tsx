// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { useQueryClient, type QueryClient } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import { invalidateSession } from '../api/sessionLifecycle'
import { AuthProvider, useAuth } from '../components/AuthContext'
import { SessionQueryProvider } from '../components/SessionQueryProvider'
import { processingFeedId, processingRunFixture, processingRunId, processingWorkFixture } from '../testing/processingFixtures'
import type { ProcessingPage, ProcessingRecoveryRequest, ProcessingRun, ProcessingWork } from '../types/processing'
import { ProcessingWorkspace } from './ProcessingWorkspace'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...await original<typeof import('../api/client')>(), apiFetch: vi.fn() }))
let root: Root
let router: ReturnType<typeof createMemoryRouter>
let client: QueryClient
let changeSession: () => void
let work: ProcessingPage<ProcessingWork>
let run: ProcessingRun
let permissions: string[]
let workFailure: Error | null
let runFailure: Error | null
let recovery: (body: ProcessingRecoveryRequest) => Promise<ProcessingRun>
let cancel: (version: number) => Promise<ProcessingRun>

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}
async function settle() { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)) }) }
function button(label: string, scope: ParentNode = document) {
  const found = [...scope.querySelectorAll('button')].find((entry) => entry.textContent?.trim() === label)
  expect(found, label).toBeDefined()
  return found!
}
function click(label: string, scope: ParentNode = document) { act(() => button(label, scope).click()) }
function text() { return document.body.textContent ?? '' }
function selectRow() { act(() => (document.querySelector('input[type=checkbox]') as HTMLInputElement).click()) }
function Probe() {
  client = useQueryClient()
  client.setDefaultOptions({ queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } })
  changeSession = useAuth().markAuthenticated
  return <RouterProvider router={router} />
}
async function mount(query = '') {
  router = createMemoryRouter([
    { path: '/settings/operations', element: <ProcessingWorkspace /> },
    { path: '/elsewhere', element: <p>Elsewhere</p> },
  ], { initialEntries: [`/settings/operations?view=processing${query}`] })
  const container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  act(() => root.render(<AuthProvider><SessionQueryProvider><Probe /></SessionQueryProvider></AuthProvider>))
  await settle()
  await settle()
}
async function refresh() { await act(async () => { await client.invalidateQueries({ queryKey: ['processing'] }) }); await settle() }
function requests(method: string) { return vi.mocked(apiFetch).mock.calls.filter(([, init]) => init?.method === method) }

beforeEach(() => {
  work = { items: [processingWorkFixture()], has_more: false, next_cursor: null }
  run = processingRunFixture()
  permissions = ['read:operations', 'read:items', 'write:operations']
  workFailure = null
  runFailure = null
  recovery = async () => run
  cancel = async () => ({ ...run, status: 'cancelled', version: 2, cancelled_count: 1, can_cancel: false })
  vi.mocked(apiFetch).mockImplementation(async (path, init) => {
    if (path === '/auth/me') return { id: 'operator', access: { permissions } } as never
    if (path.startsWith('/processing/work?')) { if (workFailure) throw workFailure; return work as never }
    if (path === '/processing/recovery-runs' && init?.method === 'POST') return await recovery(JSON.parse(String(init.body))) as never
    if (path.endsWith('/cancel')) return await cancel(JSON.parse(String(init?.body)).expected_version) as never
    if (path.startsWith('/processing/recovery-runs?')) return { items: [run], has_more: false, next_cursor: null } as never
    if (path === `/processing/recovery-runs/${processingRunId}`) { if (runFailure) throw runFailure; return run as never }
    throw new Error(`Unexpected test request ${path}`)
  })
})
afterEach(() => {
  act(() => root?.unmount())
  router?.dispose()
  client?.clear()
  invalidateSession()
  vi.resetAllMocks()
  vi.unstubAllGlobals()
  localStorage.clear()
  sessionStorage.clear()
  document.body.replaceChildren()
})

describe('processing work with actual router, session cache and mutations', () => {
  it('restores URL scope, clears selection across pages, and preserves Back navigation', async () => {
    work.has_more = true
    work.next_cursor = 'opaque-next'
    await mount(`&work_stage=tagging&work_state=attention&work_feed=${processingFeedId}`)
    expect(vi.mocked(apiFetch).mock.calls.some(([path]) => path.includes(`stage=tagging&state=attention&feed_id=${processingFeedId}`))).toBe(true)
    selectRow()
    expect(text()).toContain('1 selected on this page')
    click('Next work page')
    await settle()
    expect(router.state.location.search).toContain('work_cursor=opaque-next')
    expect(text()).toContain('0 selected on this page')
    await act(async () => { await router.navigate(-1) })
    expect(router.state.location.search).not.toContain('work_cursor')
    expect(text()).toContain('0 selected on this page')
    selectRow()
    await act(async () => { await router.navigate('/settings/operations?view=processing&work_state=pending') })
    await settle()
    await act(async () => { await router.navigate(-1) })
    expect(text()).toContain('0 selected on this page')
  })

  it('requires review of a changed work revision before recovery', async () => {
    await mount()
    selectRow()
    work = { ...work, items: [processingWorkFixture({ revision: 'revision-2' })] }
    await refresh()
    expect(button('Review selected recovery').disabled).toBe(true)
    expect(text()).toContain('Selected work changed')
    click('Clear selection')
    selectRow()
    click('Review selected recovery')
    click('Queue selected recovery')
    await settle()
    const body = JSON.parse(String(requests('POST')[0][1]?.body)) as ProcessingRecoveryRequest
    expect(body.items).toEqual([{ item_id: work.items[0].item_id, stage: 'tagging', revision: 'revision-2' }])
    expect(router.state.location.search).toContain(`work_run=${processingRunId}`)
  })

  it('reuses its idempotency key after ambiguous acceptance and restores the accepted run', async () => {
    vi.stubGlobal('crypto', { getRandomValues: crypto.getRandomValues.bind(crypto) })
    const accepted: ProcessingRecoveryRequest[] = []
    recovery = async (request) => {
      accepted.push(request)
      if (accepted.length === 1) throw new Error('Connection lost after acceptance')
      return run
    }
    await mount()
    selectRow()
    click('Review selected recovery')
    click('Queue selected recovery')
    await settle()
    expect(text()).toContain('reuses the same request key')
    click('Cancel', document.querySelector('[role=alertdialog]')!)
    click('Review selected recovery')
    click('Queue selected recovery')
    await settle()
    expect(accepted).toHaveLength(2)
    expect(accepted[1]).toEqual(accepted[0])
    expect(accepted[0].idempotency_key).toMatch(/^[\da-f-]{36}$/)
    expect(router.state.location.search).toContain(`work_run=${processingRunId}`)
    expect(text()).toContain('Queued: 0 completed')
    await act(async () => { await router.navigate('/elsewhere'); await router.navigate(-1) })
    await settle()
    expect(text()).toContain('Queued: 0 completed')
  })

  it('keeps selected work and shows actionable feedback when request preparation fails', async () => {
    await mount()
    selectRow()
    const originalCrypto = crypto
    vi.stubGlobal('crypto', undefined)
    click('Review selected recovery')
    expect(text()).toContain('Secure random generation is unavailable')
    expect(text()).toContain('Your selection has been kept and no request was sent')
    expect(text()).toContain('1 selected on this page')
    expect(document.querySelector('[role=alertdialog]')).toBeNull()
    expect(requests('POST')).toHaveLength(0)
    vi.stubGlobal('crypto', originalCrypto)
    click('Review selected recovery')
    expect(document.querySelector('[role=alertdialog]')).not.toBeNull()
    expect(text()).not.toContain('Secure random generation is unavailable')
  })

  it('blocks stale review resubmission after a selection conflict', async () => {
    recovery = async () => { throw new ApiError('Work changed', 409, '/processing/recovery-runs') }
    await mount()
    selectRow()
    click('Review selected recovery')
    click('Queue selected recovery')
    await settle()
    expect(button('Queue selected recovery').disabled).toBe(true)
    expect(text()).toContain('Close this review and refresh')
    expect(requests('POST')).toHaveLength(1)
  })

  it('keeps last known data during outages but hides it after a permission denial', async () => {
    await mount()
    selectRow()
    workFailure = new ApiError('Unavailable', 503, '/processing/work')
    await refresh()
    expect(text()).toContain('Gateway advisory')
    expect(text()).toContain('last known results')
    expect(button('Review selected recovery').disabled).toBe(true)
    workFailure = new ApiError('Permission changed', 403, '/processing/work')
    await refresh()
    expect(document.querySelector('table')).toBeNull()
    expect(document.querySelector('input[type=checkbox]')).toBeNull()
  })

  it('does not navigate back after acceptance finishes outside the workspace', async () => {
    const pending = deferred<ProcessingRun>()
    recovery = () => pending.promise
    await mount()
    selectRow()
    click('Review selected recovery')
    click('Queue selected recovery')
    await settle()
    await act(async () => { await router.navigate('/elsewhere') })
    await act(async () => pending.resolve(run))
    await settle()
    expect(router.state.location.pathname).toBe('/elsewhere')
    expect(router.state.location.search).toBe('')
  })

  it('rejects a previous session completion without restoring its run or selection', async () => {
    const pending = deferred<ProcessingRun>()
    recovery = () => pending.promise
    await mount()
    selectRow()
    click('Review selected recovery')
    click('Queue selected recovery')
    await settle()
    const retired = client
    act(() => changeSession())
    await settle()
    expect(client).not.toBe(retired)
    await act(async () => pending.resolve(run))
    await settle()
    expect(router.state.location.search).not.toContain('work_run')
    expect(client.getQueryData(['processing', 'run', processingRunId])).toBeUndefined()
    expect(text()).toContain('0 selected on this page')
    recovery = async () => run
    selectRow()
    click('Review selected recovery')
    click('Queue selected recovery')
    await settle()
    const submitted = requests('POST').map(([, init]) => JSON.parse(String(init?.body)) as ProcessingRecoveryRequest)
    expect(submitted).toHaveLength(2)
    expect(submitted[1].idempotency_key).not.toBe(submitted[0].idempotency_key)
  })

  it('rechecks the run version after a cancellation conflict without losing completed results', async () => {
    const versions: number[] = []
    cancel = async (version) => {
      versions.push(version)
      if (versions.length === 1) {
        run = { ...run, version: 2, status: 'running', completed_count: 1, total_count: 2 }
        throw new ApiError('Run changed', 409, '/processing/recovery-runs/cancel')
      }
      return { ...run, status: 'cancelled', version: 3, cancelled_count: 1, can_cancel: false }
    }
    await mount(`&work_run=${processingRunId}`)
    click('Cancel remaining work')
    click('Cancel remaining work', document.querySelector('[role=alertdialog]')!)
    await settle()
    expect(document.querySelector('[role=alertdialog]')).toBeNull()
    expect(text()).toContain('1 completed')
    click('Cancel remaining work')
    click('Cancel remaining work', document.querySelector('[role=alertdialog]')!)
    await settle()
    expect(versions).toEqual([1, 2])
    expect(text()).toContain('1 completed, 0 failed, 1 cancelled')
  })

  it('removes cached run results when the selected run becomes unavailable', async () => {
    await mount(`&work_run=${processingRunId}`)
    expect(document.querySelector('[aria-label="Selected recovery results"]')).not.toBeNull()
    runFailure = new ApiError('Unavailable', 404, '/processing/recovery-runs/run')
    await refresh()
    expect(document.querySelector('[aria-label="Selected recovery results"]')).toBeNull()
    expect(text()).toContain('This run could not be loaded')
  })

  it('offers readable status without write access and requires both read scopes', async () => {
    permissions = ['read:operations', 'read:items']
    await mount()
    expect(text()).toContain('Gateway advisory')
    expect((document.querySelector('input[type=checkbox]') as HTMLInputElement).disabled).toBe(true)
    permissions = ['read:operations']
    await act(async () => { await client.invalidateQueries({ queryKey: ['auth', 'me'] }) })
    await settle()
    expect(text()).not.toContain('Gateway advisory')
    expect(text()).toContain('permission to read operations and articles')
  })

  it('does not misrepresent access-limited recovery as an empty result', async () => {
    run = { ...run, access_limited: true, total_count: 0, items: [] }
    await mount(`&work_run=${processingRunId}`)
    expect(text()).toContain('Source details are unavailable with current access')
    expect(text()).toContain('Source details restricted')
    expect(text()).not.toContain('0 completed, 0 failed')
    expect(button('Cancel remaining work').disabled).toBe(false)
  })
})
