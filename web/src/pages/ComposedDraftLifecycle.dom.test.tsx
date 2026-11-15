// @vitest-environment jsdom
import { act, StrictMode, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiFetch } from '../api/client'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import type { ReportCapabilities, ReportDetail, ReportLibraryPage, ReportPreview, ReportQueueResponse } from '../types/api'
import { TeamsPage } from './TeamsPage'
import { DEFAULT_REPORT_PROMPT } from './reportingPageModel'
import { resetPendingReportingKeys } from './reportingRequestCoordinator'
import { useReportingController, type ReportingController } from './useReportingController'
import { deferred, intelButton, mountIntel, settle } from './articleIntelligenceTestSupport'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...(await original<object>()), apiFetch: vi.fn() }))
vi.mock('../hooks/useCurrentUser', () => ({ useCurrentUser: () => ({ data: { id: 'analyst', role: 'admin', access: {
  permissions: ['read:iam', 'write:iam', 'write:ai', 'read:reports', 'write:reports'],
} } }) }))
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
afterEach(() => {
  view?.close(); view = undefined; resetPendingReportingKeys()
  localStorage.clear(); sessionStorage.clear(); vi.clearAllMocks()
})

function editInput(input: HTMLInputElement, value: string) {
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
function Form({ name, ignoreSearchChanges = false }: { name: string; ignoreSearchChanges?: boolean }) {
  const [value, setValue] = useState('')
  const discard = useUnsavedChangesWarning(Boolean(value), `Discard ${name}?`, { ignoreSearchChanges })
  return <>{discard.discardDialog}<label>{name}<input value={value} onChange={(event) => setValue(event.target.value)} /></label></>
}

describe('composed navigation protection', () => {
  it('preserves dirty team settings alongside the clean AI policy editor', async () => {
    const team = { id: 'team-1', key: 'soc', name: 'SOC', description: '', membership_group_id: 'members', manager_group_id: 'managers', active: true, revision: 1, can_manage: true }
    const policy = { team_id: 'team-1', version: 1, configured: true, approved_provider_keys: ['legacy'], selected_provider_key: 'legacy', label_destinations: {}, destinations: [], can_manage: true, can_approve: true }
    vi.mocked(apiFetch).mockImplementation(async (path) => {
      if (path === '/teams/admin?page=1&page_size=25') return { items: [team], total: 1, page: 1, page_size: 25 }
      if (path === '/teams/admin/team-1') return team
      if (path === '/ai/team-governance/team-1') return policy
      return []
    })
    view = await mountIntel(<TeamsPage />, '/?mode=admin&team=team-1')
    await settle()
    expect(view.host.textContent).toContain('Approve destination policy')
    const input = [...view.host.querySelectorAll('label')].find((label) => label.textContent === 'Name')!.control as HTMLInputElement
    editInput(input, 'Unsaved SOC')
    await act(async () => { await view!.router.navigate('/other') })
    expect(view.router.state.location.pathname).toBe('/')
    expect(document.querySelectorAll('[role="alertdialog"]')).toHaveLength(1)
    act(() => intelButton(document.body, 'Cancel').click())
    expect(input.value).toBe('Unsaved SOC')
    await act(async () => { await view!.router.navigate('/other') })
    act(() => intelButton(document.body, 'Discard changes').click())
    await settle()
    expect(view.router.state.location.pathname).toBe('/other')
  })

  it('aggregates dirty forms, preserves search exemptions and hands off after a host unmounts', async () => {
    function Forms() {
      const [showFirst, setShowFirst] = useState(true)
      return <>{showFirst && <Form name="First" ignoreSearchChanges />}<Form name="Second" />
        <button onClick={() => setShowFirst(false)}>Remove first form</button></>
    }
    view = await mountIntel(<StrictMode><Forms /></StrictMode>, '/')
    editInput(view.host.querySelector('input')!, 'First draft')
    await act(async () => { await view!.router.navigate('/?query=updated') })
    expect(view.router.state.location.search).toBe('?query=updated')
    editInput(view.host.querySelectorAll('input')[1]!, 'Second draft')
    await act(async () => { await view!.router.navigate('/other') })
    expect(document.querySelectorAll('[role="alertdialog"]')).toHaveLength(1)
    expect(document.querySelector('[role="alertdialog"]')?.textContent).toContain('Discard First? Discard Second?')
    act(() => intelButton(view!.host, 'Remove first form').click())
    expect(document.querySelectorAll('[role="alertdialog"]')).toHaveLength(1)
    act(() => intelButton(document.body, 'Cancel').click())
    await settle()
    await act(async () => { await view!.router.navigate('/other') })
    expect(view.router.state.location.pathname).toBe('/')
    expect(document.querySelector('[role="alertdialog"]')?.textContent).toContain('Discard Second?')
  })
})

const REPORT_ONE = '11111111-1111-4111-8111-111111111111'
const REPORT_TWO = '22222222-2222-4222-8222-222222222222'

function reportDetail(id: string): ReportDetail {
  return {
    id, owner_user_id: 'analyst', title: `Report ${id}`, template_id: null, schedule_id: null,
    report_type: 'custom', status: 'ready', trigger_source: 'manual', generation_stage: 'ready',
    period_start: '2026-11-01T00:00:00Z', period_end: '2026-11-02T00:00:00Z',
    source_count: 0, included_source_count: 0, model_calls: 1, provider: 'local', model: 'fixture',
    error_code: null, error: null, generated_at: '2026-11-02T00:00:00Z', created_at: '2026-11-01T00:00:00Z',
    filters: { q: null, feed_ids: [], tag_ids: [], tags_mode: 'any', classifications: [],
      ai_relevance_labels: [], ai_score_min: null, ai_score_max: null, is_read: null, is_starred: null,
      has_article_text: null, since: null, until: null, date_basis: 'published_at_or_first_seen_at', sort: 'published_at_desc' },
    prompt: DEFAULT_REPORT_PROMPT, sections_config: [], metrics: {}, coverage: {}, summary_text: 'Saved report',
    estimated_input_tokens: 0, prompt_tokens: 0, completion_tokens: 0, total_tokens: 0,
    context_window_tokens: 8192, generation_batches: 1, delivery_requested: false, delivery_mode: 'summary',
    sections: [], sources: [],
  }
}

async function pendingReportAction(operation: 'delete' | 'retry') {
  const pending = deferred<void | ReportQueueResponse>()
  const writes: Array<{ path: string; method: string }> = []
  const capabilities: ReportCapabilities = {
    reporting_enabled: true, ai_configured: true, feeds: [], tags: [], classifications: [],
    max_sources: 100, preview_limit: 25, context_window_tokens: 8192, reserved_output_tokens: 1024,
    source_token_cap: 1000, max_model_calls: 12, safety_percent: 15,
  }
  const library: ReportLibraryPage = { items: [], current_cursor: 'initial', next_cursor: null, as_of: '2026-11-02T00:00:00Z' }
  const preview: ReportPreview = {
    total_matches: 0, articles_with_text: 0, items_with_iocs: 0, items: [],
    estimate: { context_window_tokens: 8192, reserved_output_tokens: 1024, safety_margin_tokens: 100,
      usable_input_tokens: 7000, estimated_source_tokens: 0, estimated_fixed_prompt_tokens: 100,
      estimated_batches: 1, estimated_model_calls: 1, selected_source_count: 0, omitted_source_count: 0,
      coverage_percent: 100, warnings: [] },
  }
  const path = `/reports/${REPORT_ONE}${operation === 'retry' ? '/retry' : ''}`
  const method = operation === 'delete' ? 'DELETE' : 'POST'
  vi.mocked(apiFetch).mockImplementation((request, init) => {
    if (request === path && init?.method === method) {
      writes.push({ path: request, method: init.method })
      return pending.promise as never
    }
    if (request === '/reports/preview' && init?.method === 'POST') return Promise.resolve(preview) as never
    if (init?.method) throw new Error(`Unexpected reporting write ${init.method} ${request}`)
    if (request === '/reports/capabilities') return Promise.resolve(capabilities) as never
    if (request === '/reports/templates' || request === '/reports/schedules') return Promise.resolve([]) as never
    if (request.startsWith('/reports/library?')) return Promise.resolve(library) as never
    if (request === `/reports/${REPORT_ONE}` || request === `/reports/${REPORT_TWO}`) return Promise.resolve(reportDetail(request.split('/').at(-1)!)) as never
    throw new Error(`Unexpected reporting read ${request}`)
  })
  let controller!: ReportingController
  function Reporting() {
    controller = useReportingController()
    return <div data-testid="live-reporting-controller">
      <p>Reports {controller.reportDetailQuery.data?.id}</p>
      <p>{controller.feedback?.message}</p>
    </div>
  }
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } })
  const router = createMemoryRouter([
    { path: '/reporting/:reportId', element: <Reporting />, errorElement: <p data-testid="reporting-route-error">Reporting fixture crashed</p> },
    { path: '/other', element: <p>Other page</p> },
  ], { initialEntries: [`/reporting/${REPORT_ONE}?report=first`] })
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  view = { client, router, root, host, close: () => { act(() => root.unmount()); router.dispose(); client.clear(); host.remove() } }
  act(() => root.render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>))
  const currentMutation = () => operation === 'delete' ? controller.deleteMutation : controller.retryMutation
  await vi.waitFor(() => {
    expect(controller.detailReadAvailable).toBe(true)
    expect(controller.reportDetailQuery.data?.id).toBe(REPORT_ONE)
    expect(client.getQueryState(['reports', 'detail', REPORT_ONE])?.fetchStatus).toBe('idle')
    expect(host.textContent).toContain(`Reports ${REPORT_ONE}`)
    expect(host.querySelector('[data-testid="reporting-route-error"]')).toBeNull()
  })
  act(() => currentMutation().mutate(REPORT_ONE))
  await vi.waitFor(() => {
    expect(writes).toEqual([{ path, method }])
    expect(currentMutation().isPending).toBe(true)
  })
  const mutation = () => client.getMutationCache().getAll().find((entry) => entry.state.variables === REPORT_ONE)
  return { pending, writes, expectedWrite: { path, method }, currentMutation, mutation, controller: () => controller }
}

it.each(['delete', 'retry'] as const)('does not navigate after a late report %s completion', async (operation) => {
  const action = await pendingReportAction(operation)
  await act(async () => { await view!.router.navigate('/other') })
  await act(async () => action.pending.resolve(operation === 'delete' ? undefined : {
    report_id: REPORT_ONE, task_run_id: '33333333-3333-4333-8333-333333333333', celery_task_id: null, status: 'queued',
  }))
  await vi.waitFor(() => expect(action.mutation()?.state.status).toBe('success'))
  expect(action.writes).toEqual([action.expectedWrite])
  expect(view!.router.state.location.pathname).toBe('/other')
  expect(view!.host.textContent).toBe('Other page')
  expect(view!.host.querySelector('[data-testid="reporting-route-error"]')).toBeNull()
})

it.each(['delete', 'retry'] as const)('does not show an old report %s failure in a new reporting view', async (operation) => {
  const action = await pendingReportAction(operation)
  await act(async () => { await view!.router.navigate(`/reporting/${REPORT_TWO}?report=second`) })
  await vi.waitFor(() => {
    expect(action.controller().reportDetailQuery.data?.id).toBe(REPORT_TWO)
    expect(action.controller().detailReadAvailable).toBe(true)
    expect(view!.host.textContent).toContain(`Reports ${REPORT_TWO}`)
  })
  await act(async () => action.pending.reject(new Error('Previous report failed')))
  await vi.waitFor(() => expect(action.currentMutation().isError).toBe(true))
  expect(action.writes).toEqual([action.expectedWrite])
  expect(view!.router.state.location.pathname).toBe(`/reporting/${REPORT_TWO}`)
  expect(view!.router.state.location.search).toBe('?report=second')
  expect(action.controller().feedback).toBeNull()
  expect(view!.host.textContent).toContain(`Reports ${REPORT_TWO}`)
  expect(view!.host.textContent).not.toContain('Previous report failed')
  expect(view!.host.querySelector('[data-testid="reporting-route-error"]')).toBeNull()
})
