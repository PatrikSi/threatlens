// @vitest-environment jsdom
import { TestDataRouter } from '../../tests/helpers/TestDataRouter'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ReportDetail, ReportPreview, ReportSchedule, ReportTemplate } from '../types/api'


;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const reportingPageMocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  apiDownload: vi.fn(),
  navigate: vi.fn(),
  routeReportId: 'report-1' as string | undefined,
  userRole: 'analyst' as 'admin' | 'analyst' | 'viewer',
  userPermissions: ['write:reports'] as string[],
  anchorClick: vi.fn(),
  createObjectURL: vi.fn(() => 'blob:threatlens-report'),
  revokeObjectURL: vi.fn(),
}))

vi.mock('../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/client')>()),
  apiFetch: reportingPageMocks.apiFetch,
  apiDownload: reportingPageMocks.apiDownload,
}))

vi.mock('../hooks/useCurrentUser', () => ({
  useCurrentUser: () => ({
    data: {
      id: 'analyst-1',
      email: 'analyst@example.com',
      role: reportingPageMocks.userRole,
      access: { permissions: reportingPageMocks.userPermissions },
      is_active: true,
    },
  }),
}))

vi.mock('react-router-dom', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-router-dom')>()),
  useNavigate: () => reportingPageMocks.navigate,
  useLocation: () => ({ key: 'reporting-test' }),
  useBlocker: () => ({ state: 'unblocked', proceed: vi.fn(), reset: vi.fn() }),
  useParams: () => ({ reportId: reportingPageMocks.routeReportId }),
}))

import { ApiError, ApiTransportError } from '../api/client'
import { ReportingPage } from './ReportingPage'
import { resetPendingReportingKeys } from './reportingRequestCoordinator'


const CAPABILITIES = {
  reporting_enabled: false,
  ai_configured: true,
  feeds: [],
  tags: [],
  classifications: [],
  max_sources: 100,
  preview_limit: 25,
  context_window_tokens: 8192,
  reserved_output_tokens: 1024,
  source_token_cap: 1000,
  max_model_calls: 12,
  safety_percent: 15,
}

const EMPTY_FILTERS = {
  q: null,
  feed_ids: [],
  tag_ids: [],
  tags_mode: 'any' as const,
  classifications: [],
  ai_relevance_labels: [],
  ai_score_min: null,
  ai_score_max: null,
  is_read: null,
  is_starred: null,
  has_article_text: null,
  since: null,
  until: null,
  date_basis: 'published_at_or_first_seen_at' as const,
  sort: 'published_at_desc' as const,
}

function reportDetail(status: ReportDetail['status'] = 'ready', id = 'report-1'): ReportDetail {
  return {
    id,
    template_id: null,
    schedule_id: null,
    owner_user_id: 'analyst-1',
    title: 'Weekly threat landscape',
    report_type: 'custom',
    status,
    trigger_source: 'manual',
    generation_stage: status === 'ready' ? 'ready' : status,
    period_start: '2026-08-10T00:00:00Z',
    period_end: '2026-08-17T00:00:00Z',
    source_count: 3,
    included_source_count: 2,
    model_calls: 3,
    provider: 'local',
    model: 'test-model',
    error_code: status === 'error' ? 'provider_error' : null,
    error: status === 'error' ? 'The local model stopped responding.' : null,
    generated_at: status === 'ready' ? '2026-08-17T09:00:00Z' : null,
    created_at: '2026-08-17T08:55:00Z',
    filters: EMPTY_FILTERS,
    prompt: {
      audience: 'security_team',
      objective: 'Summarize material security developments.',
      tone: 'analytical',
      detail_level: 'standard',
      use_company_context: true,
      custom_instructions: null,
      focus_topics: [],
      excluded_topics: [],
    },
    sections_config: [
      { key: 'summary', title: 'Executive summary', enabled: true },
    ],
    metrics: {},
    coverage: { coverage_percent: 66.7, warnings: [] },
    summary_text: 'A concise summary.',
    estimated_input_tokens: 1200,
    prompt_tokens: 900,
    completion_tokens: 300,
    total_tokens: 1200,
    context_window_tokens: 8192,
    generation_batches: 1,
    delivery_requested: false,
    delivery_mode: 'summary',
    sections: [
      {
        key: 'summary',
        title: 'Executive summary',
        position: 0,
        status: status === 'ready' ? 'ready' : 'pending',
        body_markdown: status === 'ready' ? 'Material developments were observed.' : '',
        key_points: [],
        citations: [],
        error: null,
      },
    ],
    sources: [
      {
        citation_key: 'S1',
        item_id: 'item-1',
        included: true,
        rank: 1,
        exclusion_reason: null,
        title: 'Critical advisory',
        feed_name: 'CERT feed',
        url: 'https://example.com/advisory',
        classification: 'vulnerability',
        relevance_score: 0.9,
        relevance_label: 'high',
        published_at: '2026-08-16T10:00:00Z',
        first_seen_at: '2026-08-16T10:05:00Z',
        tags: ['critical'],
        iocs: [],
        estimated_tokens: 500,
      },
    ],
  }
}

const REPORT_TEMPLATE: ReportTemplate = {
  id: 'template-1',
  owner_user_id: 'analyst-1',
  builtin_key: null,
  name: 'Threat landscape',
  description: 'Threat landscape reporting template.',
  report_type: 'custom',
  visibility: 'private',
  prompt: {
    audience: 'security_team',
    objective: 'Summarize material security developments.',
    tone: 'analytical',
    detail_level: 'standard',
    use_company_context: true,
    custom_instructions: null,
    focus_topics: [],
    excluded_topics: [],
  },
  sections: [{ key: 'summary', title: 'Executive summary', enabled: true }],
  default_filters: EMPTY_FILTERS,
  created_at: '2026-08-17T08:00:00Z',
  updated_at: '2026-08-17T08:00:00Z',
}

const REPORT_PREVIEW: ReportPreview = {
  total_matches: 1, articles_with_text: 1, items_with_iocs: 1,
  items: [{
    id: 'private-report-source', title: 'Private report preview article', url: 'https://example.invalid/private',
    feed_name: 'Private preview feed', published_at: null, first_seen_at: '2026-11-15T00:00:00Z',
    classification: 'vulnerability', ai_relevance_score: null, ai_relevance_label: null, tags: [],
    is_read: false, is_starred: false, has_article_text: true, ioc_count: 1,
    estimated_tokens: 100, selected: true, exclusion_reason: null,
  }],
  estimate: {
    context_window_tokens: 8192, reserved_output_tokens: 1024, safety_margin_tokens: 400,
    usable_input_tokens: 6768, estimated_source_tokens: 100, estimated_fixed_prompt_tokens: 200,
    estimated_batches: 1, estimated_model_calls: 1, selected_source_count: 1, omitted_source_count: 0,
    coverage_percent: 100, warnings: ['Private preview estimate warning'],
  },
}

async function renderPreviewBuilder() {
  reportingPageMocks.routeReportId = undefined
  reportingPageMocks.apiFetch.mockImplementation((path: string) => {
    if (path === '/reports/capabilities') return Promise.resolve({ ...CAPABILITIES, reporting_enabled: true })
    if (path === '/reports/templates') return Promise.resolve([REPORT_TEMPLATE])
    if (path.startsWith('/reports/library?')) return Promise.reject(new ApiError('Report library unavailable to this author', 403, path))
    if (path === '/reports/preview') return Promise.resolve(REPORT_PREVIEW)
    return Promise.reject(new Error(`Unexpected API path: ${path}`))
  })
  const view = renderPage()
  await vi.waitFor(async () => {
    await act(async () => { await new Promise((done) => setTimeout(done, 15)) })
    expect(view.textContent).toContain('Private report preview article')
    expect(button(view, 'Generate report').disabled).toBe(false)
  })
  return view
}

async function rejectPreview(view: HTMLDivElement, status: number) {
  const fallback = reportingPageMocks.apiFetch.getMockImplementation()!
  reportingPageMocks.apiFetch.mockImplementation((path: string) => path === '/reports/preview'
    ? Promise.reject(new ApiError(`Preview rejected ${status}`, status, path)) : fallback(path))
  await act(async () => { await queryClient!.invalidateQueries({ queryKey: ['reports', 'preview'] }) })
  await act(async () => { await new Promise((done) => setTimeout(done, 15)) })
  await vi.waitFor(() => expect(view.textContent).toContain(`Preview rejected ${status}`))
}

function reportSchedule(id: string, name: string): ReportSchedule {
  return {
    id,
    owner_user_id: 'analyst-1',
    template_id: REPORT_TEMPLATE.id,
    name,
    enabled: true,
    cadence: 'weekly',
    day_of_week: 0,
    day_of_month: 1,
    hour: 9,
    minute: 0,
    timezone: 'UTC',
    window_type: 'previous_complete_week',
    rolling_days: 7,
    filters: EMPTY_FILTERS,
    custom_instructions: null,
    delivery_enabled: false,
    delivery_mode: 'summary',
    skip_empty: true,
    missed_run_policy: 'latest',
    next_run_at: '2026-08-24T09:00:00Z',
    last_run_at: null,
    created_at: '2026-08-17T08:00:00Z',
    updated_at: '2026-08-17T08:00:00Z',
  }
}

let queryClient: QueryClient | null = null
let root: Root | null = null
let container: HTMLDivElement | null = null

function renderPage() {
  queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => {
    root?.render(
      <QueryClientProvider client={queryClient!}>
        <TestDataRouter><ReportingPage /></TestDataRouter>
      </QueryClientProvider>,
    )
  })
  return container
}

function rerenderPage() {
  act(() => {
    root?.render(
      <QueryClientProvider client={queryClient!}>
        <TestDataRouter><ReportingPage /></TestDataRouter>
      </QueryClientProvider>,
    )
  })
}

async function waitForReport(view: HTMLDivElement) {
  await act(async () => {
    await vi.waitFor(() => {
      expect(view.textContent).toContain('Weekly threat landscape')
    })
  })
}

async function openReportingTab(view: HTMLDivElement, tab: 'Schedules' | 'Templates') {
  await act(async () => {
    await vi.waitFor(() => expect(view.textContent).toContain('Intelligence reporting'))
  })
  act(() => button(view, tab).click())
}

function rowByName(view: HTMLDivElement, name: string): HTMLElement {
  const row = Array.from(view.querySelectorAll('article')).find((entry) => entry.textContent?.includes(name))
  if (!row) throw new Error(`Row not found: ${name}`)
  return row
}

function rowButton(row: HTMLElement, label: string): HTMLButtonElement {
  const match = Array.from(row.querySelectorAll('button')).find((entry) => entry.textContent?.trim() === label)
  if (!match) throw new Error(`Row button not found: ${label}`)
  return match
}

function button(view: HTMLDivElement, label: string): HTMLButtonElement {
  const match = Array.from(view.querySelectorAll('button')).find(
    (entry) => entry.textContent?.trim() === label,
  )
  if (!match) throw new Error(`Button not found: ${label}`)
  return match
}

beforeEach(() => {
  resetPendingReportingKeys()
  reportingPageMocks.routeReportId = 'report-1'
  reportingPageMocks.userRole = 'analyst'
  reportingPageMocks.userPermissions = ['write:reports']
  reportingPageMocks.apiFetch.mockImplementation((path: string) => {
    if (path === '/reports/capabilities') return Promise.resolve(CAPABILITIES)
    if (path === '/reports/templates') return Promise.resolve([])
    if (path.startsWith('/reports/library?')) return Promise.resolve({ items: [], current_cursor: 'first', next_cursor: null, as_of: '2026-09-08T00:00:00Z' })
    if (path === '/reports/report-1') return Promise.resolve(reportDetail())
    return Promise.reject(new Error(`Unexpected API path: ${path}`))
  })
  reportingPageMocks.apiDownload.mockResolvedValue({
    blob: new Blob(['report']),
    filename: 'weekly-landscape.pdf',
    contentType: 'application/pdf',
  })
  vi.stubGlobal('URL', {
    ...URL,
    createObjectURL: reportingPageMocks.createObjectURL,
    revokeObjectURL: reportingPageMocks.revokeObjectURL,
  })
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(
    reportingPageMocks.anchorClick,
  )
})

afterEach(async () => {
  await act(async () => {
    root?.unmount()
    await Promise.resolve()
  })
  queryClient?.clear()
  queryClient = null
  root = null
  container?.remove()
  container = null
  document.body.innerHTML = ''
  reportingPageMocks.apiFetch.mockReset()
  reportingPageMocks.apiDownload.mockReset()
  reportingPageMocks.navigate.mockReset()
  reportingPageMocks.anchorClick.mockReset()
  reportingPageMocks.createObjectURL.mockClear()
  reportingPageMocks.revokeObjectURL.mockClear()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  resetPendingReportingKeys()
})

describe('ReportingPage detail actions', () => {
  it('keeps denied report previews private through a later advisory outage', async () => {
    const view = await renderPreviewBuilder()
    await rejectPreview(view, 403)
    expect(view.textContent).not.toContain('Private report preview article')
    expect(view.textContent).not.toContain('Private preview estimate warning')
    expect(button(view, 'Generate report').disabled).toBe(true)
    await rejectPreview(view, 503)
    expect(view.textContent).not.toContain('Private report preview article')
    expect(view.textContent).not.toContain('Private preview estimate warning')
    expect(button(view, 'Generate report').disabled).toBe(true)
    expect(reportingPageMocks.apiFetch.mock.calls.some(([path]) => path === '/reports')).toBe(false)
    reportingPageMocks.apiFetch.mockImplementation((path: string) => path === '/reports/preview'
      ? Promise.resolve({ ...REPORT_PREVIEW, items: [{ ...REPORT_PREVIEW.items[0], title: 'Fresh report preview article' }] })
      : path === '/reports/templates' ? Promise.resolve([REPORT_TEMPLATE])
        : path === '/reports/capabilities' ? Promise.resolve({ ...CAPABILITIES, reporting_enabled: true })
          : Promise.reject(new ApiError('Report library unavailable to this author', 403, path)))
    await act(async () => { await queryClient!.invalidateQueries({ queryKey: ['reports', 'preview'] }) })
    await vi.waitFor(() => expect(view.textContent).toContain('Fresh report preview article'))
    expect(button(view, 'Generate report').disabled).toBe(false)
  })

  it('keeps report preview outages advisory before any access denial and preserves the draft', async () => {
    const view = await renderPreviewBuilder()
    const title = [...view.querySelectorAll('label')].find((label) => label.textContent?.trim() === 'Report title')?.control as HTMLInputElement
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(title, 'My unsaved reporting draft')
      title.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await rejectPreview(view, 503)
    expect(view.textContent).toContain('Private report preview article')
    expect(view.textContent).toContain('You can retry the estimate or let the server validate')
    expect(title.value).toBe('My unsaved reporting draft')
    expect(button(view, 'Generate report').disabled).toBe(false)
  })

  it('does not resume report generation when a denied preview becomes an advisory outage', async () => {
    const view = await renderPreviewBuilder()
    await rejectPreview(view, 403)
    expect(button(view, 'Generate report').disabled).toBe(true)
    await rejectPreview(view, 503)
    expect(button(view, 'Generate report').disabled).toBe(true)
    expect(reportingPageMocks.apiFetch.mock.calls.some(([path]) => path === '/reports')).toBe(false)
  })

  it('does not restore denied report detail during a later outage', async () => {
    const view = renderPage()
    await waitForReport(view)
    reportingPageMocks.apiFetch.mockRejectedValue(new ApiError('Report access removed', 403, '/reports/report-1'))
    await act(async () => { await queryClient!.invalidateQueries({ queryKey: ['reports', 'detail', 'report-1'] }) })
    await act(async () => { await new Promise((done) => setTimeout(done, 15)) })
    await vi.waitFor(() => {
      expect((queryClient!.getQueryState(['reports', 'detail', 'report-1'])?.error as ApiError)?.status).toBe(403)
      expect(view.querySelector('[role="alert"]')?.textContent).toContain('Report access removed')
    })
    expect(view.textContent).not.toContain('A concise summary.')
    reportingPageMocks.apiFetch.mockRejectedValue(new ApiError('Report service unavailable', 503, '/reports/report-1'))
    await act(async () => { await queryClient!.invalidateQueries({ queryKey: ['reports', 'detail', 'report-1'] }) })
    await act(async () => { await new Promise((done) => setTimeout(done, 15)) })
    await vi.waitFor(() => {
      expect((queryClient!.getQueryState(['reports', 'detail', 'report-1'])?.error as ApiError)?.status).toBe(503)
      expect(view.querySelector('[role="alert"]')?.textContent).toContain('Report service unavailable')
    })
    expect(view.textContent).not.toContain('Weekly threat landscape')
    expect(view.textContent).not.toContain('A concise summary.')
    expect([...view.querySelectorAll('button')].some((entry) => entry.textContent?.trim() === 'Delete')).toBe(false)
    reportingPageMocks.apiFetch.mockResolvedValue(reportDetail())
    await act(async () => { await queryClient!.invalidateQueries({ queryKey: ['reports', 'detail', 'report-1'] }) })
    await act(async () => { await new Promise((done) => setTimeout(done, 15)) })
    expect(view.textContent).toContain('Weekly threat landscape')
  })

  it('pauses report actions while retaining readable detail during an ordinary outage', async () => {
    const fetch = reportingPageMocks.apiFetch.getMockImplementation()!
    reportingPageMocks.apiFetch.mockImplementation((path: string) => path === '/reports/report-1'
      ? Promise.resolve({ ...reportDetail(), publication_status: 'draft', editorial_version: 1, review_required: true }) : fetch(path))
    const view = renderPage()
    await waitForReport(view)
    act(() => button(view, 'Edit draft').click())
    const titleInput = [...view.querySelectorAll('label')].find((label) => label.textContent?.startsWith('Report title'))?.control as HTMLInputElement
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(titleInput, 'My unsaved report title')
      titleInput.dispatchEvent(new Event('input', { bubbles: true }))
    })
    reportingPageMocks.apiFetch.mockRejectedValue(new ApiError('Report service unavailable', 503, '/reports/report-1'))
    await act(async () => { await queryClient!.invalidateQueries({ queryKey: ['reports', 'detail', 'report-1'] }) })
    await act(async () => { await new Promise((done) => setTimeout(done, 15)) })
    await vi.waitFor(() => {
      expect((queryClient!.getQueryState(['reports', 'detail', 'report-1'])?.error as ApiError)?.status).toBe(503)
      expect(view.querySelector('[role="alert"]')?.textContent).toContain('Report service unavailable')
    })
    expect(view.textContent).toContain('Weekly threat landscape')
    expect(titleInput.value).toBe('My unsaved report title')
    expect(titleInput.matches(':disabled')).toBe(true)
    expect(button(view, 'Delete').disabled).toBe(true)
    for (const format of ['Markdown', 'HTML', 'PDF']) expect(button(view, format).disabled).toBe(true)
  })

  it('lets a viewer with additive report write access use the report builder', async () => {
    reportingPageMocks.routeReportId = undefined
    reportingPageMocks.userRole = 'viewer'
    reportingPageMocks.userPermissions = ['write:reports']
    const view = renderPage()

    await act(async () => {
      await vi.waitFor(() => expect(view.textContent).toContain('Build a report'))
    })

    expect(view.textContent).not.toContain('Schedules')
  })

  it('keeps an owned report read-only without report write access', async () => {
    reportingPageMocks.userRole = 'viewer'
    reportingPageMocks.userPermissions = ['read:reports']
    reportingPageMocks.apiFetch.mockImplementation((path: string) => {
      if (path === '/reports/capabilities') return Promise.resolve(CAPABILITIES)
      if (path === '/reports/templates') return Promise.resolve([])
      if (path.startsWith('/reports/library?')) return Promise.resolve({ items: [], current_cursor: 'first', next_cursor: null, as_of: '2026-09-08T00:00:00Z' })
      if (path === '/reports/report-1') return Promise.resolve(reportDetail('error'))
      return Promise.reject(new Error(`Unexpected API path: ${path}`))
    })
    const view = renderPage()

    await waitForReport(view)

    expect(view.textContent).toContain('This report is read-only')
    expect(Array.from(view.querySelectorAll('button')).some(
      (entry) => entry.textContent?.trim() === 'Retry',
    )).toBe(false)
    expect(Array.from(view.querySelectorAll('button')).some(
      (entry) => entry.textContent?.trim() === 'Delete',
    )).toBe(false)
  })

  it('renders template mutation actions as read-only without report write access', async () => {
    reportingPageMocks.routeReportId = undefined
    reportingPageMocks.userRole = 'viewer'
    reportingPageMocks.userPermissions = ['read:reports']
    reportingPageMocks.apiFetch.mockImplementation((path: string) => {
      if (path === '/reports/capabilities') return Promise.resolve(CAPABILITIES)
      if (path === '/reports/templates') return Promise.resolve([REPORT_TEMPLATE])
      if (path.startsWith('/reports/library?')) return Promise.resolve({ items: [], current_cursor: 'first', next_cursor: null, as_of: '2026-09-08T00:00:00Z' })
      return Promise.reject(new Error(`Unexpected API path: ${path}`))
    })
    const view = renderPage()

    await openReportingTab(view, 'Templates')
    await act(async () => {
      await vi.waitFor(() => expect(view.textContent).toContain('Threat landscape'))
    })

    const template = rowByName(view, 'Threat landscape')
    expect(view.textContent).toContain('Reporting is read-only')
    expect(template.textContent).toContain('Read-only')
    expect(Array.from(template.querySelectorAll('button')).map(
      (entry) => entry.textContent?.trim(),
    )).toEqual([])
  })

  it('describes queued work without claiming that generation has started', async () => {
    reportingPageMocks.apiFetch.mockImplementation((path: string) => {
      if (path === '/reports/capabilities') return Promise.resolve(CAPABILITIES)
      if (path === '/reports/templates') return Promise.resolve([])
      if (path.startsWith('/reports/library?')) return Promise.resolve({ items: [], current_cursor: 'first', next_cursor: null, as_of: '2026-09-08T00:00:00Z' })
      if (path === '/reports/report-1') return Promise.resolve(reportDetail('queued'))
      return Promise.reject(new Error(`Unexpected API path: ${path}`))
    })
    const view = renderPage()

    await waitForReport(view)

    expect(view.textContent).toContain('Waiting for report generation')
    expect(view.textContent).not.toContain('processing bounded evidence batches')
  })

  it('shows an actionable state when the report queue has no consumer', async () => {
    const waitingReport = reportDetail('queued')
    waitingReport.generation_stage = 'waiting_for_worker'
    reportingPageMocks.apiFetch.mockImplementation((path: string) => {
      if (path === '/reports/capabilities') return Promise.resolve(CAPABILITIES)
      if (path === '/reports/templates') return Promise.resolve([])
      if (path.startsWith('/reports/library?')) return Promise.resolve({ items: [], current_cursor: 'first', next_cursor: null, as_of: '2026-09-08T00:00:00Z' })
      if (path === '/reports/report-1') return Promise.resolve(waitingReport)
      return Promise.reject(new Error(`Unexpected API path: ${path}`))
    })
    const view = renderPage()

    await waitForReport(view)

    expect(view.textContent).toContain('Waiting for an AI report worker')
    expect(view.textContent).toContain('recreate the worker-ai service')
    expect(view.textContent).toContain('resume automatically')
  })

  it('shows download failures and suppresses concurrent detail actions', async () => {
    let rejectDownload: ((error: Error) => void) | undefined
    reportingPageMocks.apiDownload.mockImplementation(
      () => new Promise((_resolve, reject) => {
        rejectDownload = reject
      }),
    )
    const view = renderPage()
    await waitForReport(view)

    await act(async () => {
      button(view, 'Markdown').click()
      await vi.waitFor(() => expect(button(view, 'Markdown').disabled).toBe(true))
    })

    expect(button(view, 'HTML').disabled).toBe(true)
    expect(button(view, 'PDF').disabled).toBe(true)
    expect(button(view, 'Delete').disabled).toBe(true)
    button(view, 'HTML').click()
    expect(reportingPageMocks.apiDownload).toHaveBeenCalledTimes(1)

    await act(async () => {
      rejectDownload?.(new Error('PDF renderer unavailable'))
      await vi.waitFor(() => {
        expect(view.textContent).toContain('PDF renderer unavailable')
      })
    })
    expect(view.querySelector('[role="alert"]')?.textContent).toContain(
      'The report download could not be prepared',
    )
  })

  it('downloads through the mutation and reports the filename', async () => {
    const view = renderPage()
    await waitForReport(view)

    await act(async () => {
      button(view, 'PDF').click()
      await vi.waitFor(() => expect(reportingPageMocks.anchorClick).toHaveBeenCalled())
    })

    expect(view.textContent).toContain('Report downloaded: weekly-landscape.pdf')
    expect(reportingPageMocks.apiDownload).toHaveBeenCalledWith(
      '/reports/report-1/download?format=pdf',
      expect.objectContaining({ timeoutMs: 60_000, signal: expect.any(AbortSignal) }),
    )
  })

  it('aborts a download silently when a different report is selected', async () => {
    let requestSignal: AbortSignal | undefined
    reportingPageMocks.apiFetch.mockImplementation((path: string) => {
      if (path === '/reports/capabilities') return Promise.resolve(CAPABILITIES)
      if (path === '/reports/templates') return Promise.resolve([])
      if (path.startsWith('/reports/library?')) return Promise.resolve({ items: [], current_cursor: 'first', next_cursor: null, as_of: '2026-09-08T00:00:00Z' })
      if (path === '/reports/report-1') return Promise.resolve(reportDetail())
      if (path === '/reports/report-2') return Promise.resolve(reportDetail('ready', 'report-2'))
      return Promise.reject(new Error(`Unexpected API path: ${path}`))
    })
    reportingPageMocks.apiDownload.mockImplementation((_path: string, options?: RequestInit) => {
      requestSignal = options?.signal ?? undefined
      return new Promise((_resolve, reject) => {
        requestSignal?.addEventListener('abort', () => {
          reject(new DOMException('The operation was aborted.', 'AbortError'))
        }, { once: true })
      })
    })
    const view = renderPage()
    await waitForReport(view)

    act(() => button(view, 'PDF').click())
    await act(async () => {
      await vi.waitFor(() => expect(requestSignal).toBeDefined())
    })
    reportingPageMocks.routeReportId = 'report-2'
    rerenderPage()

    await act(async () => {
      await vi.waitFor(() => expect(requestSignal?.aborted).toBe(true))
    })
    expect(reportingPageMocks.anchorClick).not.toHaveBeenCalled()
    expect(view.textContent).not.toContain('The report download could not be prepared')
  })

  it('aborts an active download when the report page unmounts', async () => {
    let requestSignal: AbortSignal | undefined
    reportingPageMocks.apiDownload.mockImplementation((_path: string, options?: RequestInit) => {
      requestSignal = options?.signal ?? undefined
      return new Promise((_resolve, reject) => {
        requestSignal?.addEventListener('abort', () => {
          reject(new DOMException('The operation was aborted.', 'AbortError'))
        }, { once: true })
      })
    })
    const view = renderPage()
    await waitForReport(view)

    act(() => button(view, 'PDF').click())
    await act(async () => {
      await vi.waitFor(() => expect(requestSignal).toBeDefined())
    })
    await act(async () => {
      root?.unmount()
      root = null
      await Promise.resolve()
    })

    expect(requestSignal?.aborted).toBe(true)
    expect(reportingPageMocks.anchorClick).not.toHaveBeenCalled()
  })

  it.each([401, 403, 404, 503])('handles a loaded report refresh returning HTTP %s according to current access', async (status) => {
    let detailRequests = 0
    reportingPageMocks.apiFetch.mockImplementation((path: string) => {
      if (path === '/reports/capabilities') return Promise.resolve(CAPABILITIES)
      if (path === '/reports/templates') return Promise.resolve([])
      if (path.startsWith('/reports/library?')) return Promise.resolve({ items: [], current_cursor: 'first', next_cursor: null, as_of: '2026-09-08T00:00:00Z' })
      if (path === '/reports/report-1') {
        detailRequests += 1
        return detailRequests === 1
          ? Promise.resolve(reportDetail())
          : Promise.reject(new ApiError('Status endpoint unavailable', status, path))
      }
      return Promise.reject(new Error(`Unexpected API path: ${path}`))
    })
    const view = renderPage()
    await waitForReport(view)

    await act(async () => {
      await queryClient?.refetchQueries({
        queryKey: ['reports', 'detail', 'report-1'],
      })
    })

    await act(async () => {
      await vi.waitFor(() => {
        expect(view.textContent).toContain('Status endpoint unavailable')
      })
    })
    if (status === 503) {
      expect(view.textContent).toContain('Weekly threat landscape')
      expect(view.textContent).toContain('The last loaded report remains visible')
    } else {
      expect(view.textContent).not.toContain('Weekly threat landscape')
      expect(view.textContent).not.toContain('The last loaded report remains visible')
      expect([...view.querySelectorAll('button')].some((entry) => entry.textContent === 'PDF')).toBe(false)
      expect(button(view, 'Retry')).toBeDefined()
    }
  })

  it('reuses the retry idempotency key after an ambiguous failure', async () => {
    const requestHeaders: string[] = []
    let retryCalls = 0
    reportingPageMocks.apiFetch.mockImplementation(
      (path: string, options?: RequestInit) => {
        if (path === '/reports/capabilities') return Promise.resolve(CAPABILITIES)
        if (path === '/reports/templates') return Promise.resolve([])
        if (path.startsWith('/reports/library?')) return Promise.resolve({ items: [], current_cursor: 'first', next_cursor: null, as_of: '2026-09-08T00:00:00Z' })
        if (path === '/reports/report-1') return Promise.resolve(reportDetail('error'))
        if (path === '/reports/report-1/retry') {
          retryCalls += 1
          requestHeaders.push(new Headers(options?.headers).get('Idempotency-Key') ?? '')
          if (retryCalls === 1) {
            return Promise.reject(
              new ApiTransportError(
                'ThreatLens could not reach the API.',
                path,
                'network',
              ),
            )
          }
          return Promise.resolve({
            report_id: 'report-1',
            task_run_id: '33333333-3333-4333-8333-333333333333',
            celery_task_id: null,
            status: 'running',
          })
        }
        return Promise.reject(new Error(`Unexpected API path: ${path}`))
      },
    )
    const view = renderPage()
    await waitForReport(view)

    await act(async () => {
      button(view, 'Retry').click()
      await vi.waitFor(() => {
        expect(view.textContent).toContain('Retry safely with the same request')
      })
    })
    await act(async () => {
      button(view, 'Retry').click()
      await vi.waitFor(() => expect(retryCalls).toBe(2))
    })

    expect(requestHeaders[0]).not.toBe('')
    expect(requestHeaders[1]).toBe(requestHeaders[0])
    expect(view.textContent).toContain('already accepted and generation is in progress')
  })

  it('keeps the retry identity through a remount after an incomplete success response', async () => {
    const requestHeaders: string[] = []
    let retryCalls = 0
    reportingPageMocks.apiFetch.mockImplementation(
      (path: string, options?: RequestInit) => {
        if (path === '/reports/capabilities') return Promise.resolve(CAPABILITIES)
        if (path === '/reports/templates') return Promise.resolve([])
        if (path.startsWith('/reports/library?')) return Promise.resolve({ items: [], current_cursor: 'first', next_cursor: null, as_of: '2026-09-08T00:00:00Z' })
        if (path === '/reports/report-1') return Promise.resolve(reportDetail('error'))
        if (path === '/reports/report-1/retry') {
          retryCalls += 1
          requestHeaders.push(new Headers(options?.headers).get('Idempotency-Key') ?? '')
          return retryCalls === 1
            ? Promise.resolve(undefined)
            : Promise.resolve({
                report_id: 'report-1',
                task_run_id: '33333333-3333-4333-8333-333333333333',
                celery_task_id: null,
                status: 'running',
              })
        }
        return Promise.reject(new Error(`Unexpected API path: ${path}`))
      },
    )
    const firstView = renderPage()
    await waitForReport(firstView)

    await act(async () => {
      button(firstView, 'Retry').click()
      await vi.waitFor(() => expect(firstView.textContent).toContain('incomplete queue confirmation'))
    })
    await act(async () => {
      root?.unmount()
      await Promise.resolve()
    })
    queryClient?.clear()
    queryClient = null
    root = null
    container?.remove()
    container = null

    const secondView = renderPage()
    await waitForReport(secondView)
    await act(async () => {
      button(secondView, 'Retry').click()
      await vi.waitFor(() => expect(retryCalls).toBe(2))
    })

    expect(requestHeaders[0]).not.toBe('')
    expect(requestHeaders[1]).toBe(requestHeaders[0])
    expect(secondView.textContent).toContain('already accepted and generation is in progress')
  })
})

describe('ReportingPage schedule resilience', () => {
  it('renders an actionable schedule query error and retries without a false empty state', async () => {
    reportingPageMocks.routeReportId = undefined
    reportingPageMocks.userRole = 'admin'
    let scheduleRequests = 0
    reportingPageMocks.apiFetch.mockImplementation((path: string) => {
      if (path === '/reports/capabilities') return Promise.resolve(CAPABILITIES)
      if (path === '/reports/templates') return Promise.resolve([REPORT_TEMPLATE])
      if (path.startsWith('/reports/library?')) return Promise.resolve({ items: [], current_cursor: 'first', next_cursor: null, as_of: '2026-09-08T00:00:00Z' })
      if (path === '/reports/schedules') {
        scheduleRequests += 1
        return scheduleRequests === 1
          ? Promise.reject(new Error('Schedule service unavailable'))
          : Promise.resolve([])
      }
      return Promise.reject(new Error(`Unexpected API path: ${path}`))
    })
    const view = renderPage()
    await openReportingTab(view, 'Schedules')

    await act(async () => {
      await vi.waitFor(() => expect(view.textContent).toContain('Schedule service unavailable'))
    })
    expect(view.textContent).not.toContain('No report schedules are configured')

    await act(async () => {
      button(view, 'Retry schedules').click()
      await vi.waitFor(() => expect(view.textContent).toContain('No report schedules are configured'))
    })
    expect(scheduleRequests).toBe(2)
  })

  it('reports an empty run-now response honestly and refetches schedules and reports', async () => {
    reportingPageMocks.routeReportId = undefined
    reportingPageMocks.userRole = 'admin'
    const schedule = reportSchedule('schedule-1', 'Monday landscape')
    let scheduleRequests = 0
    let libraryRequests = 0
    reportingPageMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/reports/capabilities') return Promise.resolve(CAPABILITIES)
      if (path === '/reports/templates') return Promise.resolve([REPORT_TEMPLATE])
      if (path.startsWith('/reports/library?')) {
        libraryRequests += 1
        return Promise.resolve({ items: [], current_cursor: 'first', next_cursor: null, as_of: '2026-09-08T00:00:00Z' })
      }
      if (path === '/reports/schedules') {
        scheduleRequests += 1
        return Promise.resolve([schedule])
      }
      if (path === '/reports/schedules/schedule-1/run' && options?.method === 'POST') return Promise.resolve([])
      return Promise.reject(new Error(`Unexpected API path: ${path}`))
    })
    const view = renderPage()
    await openReportingTab(view, 'Schedules')
    await act(async () => {
      await vi.waitFor(() => expect(view.textContent).toContain('Monday landscape'))
    })

    await act(async () => {
      rowButton(rowByName(view, 'Monday landscape'), 'Run now').click()
      await vi.waitFor(() => expect(view.textContent).toContain('No new report was queued'))
      await vi.waitFor(() => expect(scheduleRequests).toBeGreaterThanOrEqual(2))
      await vi.waitFor(() => expect(libraryRequests).toBeGreaterThanOrEqual(2))
    })
  })

  it('reuses the schedule-run idempotency key after an ambiguous failure', async () => {
    reportingPageMocks.routeReportId = undefined
    reportingPageMocks.userRole = 'admin'
    const schedule = reportSchedule('schedule-1', 'Monday landscape')
    const requestHeaders: string[] = []
    const versionHeaders: string[] = []
    let runRequests = 0
    reportingPageMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/reports/capabilities') return Promise.resolve(CAPABILITIES)
      if (path === '/reports/templates') return Promise.resolve([REPORT_TEMPLATE])
      if (path.startsWith('/reports/library?')) return Promise.resolve({ items: [], current_cursor: 'first', next_cursor: null, as_of: '2026-09-08T00:00:00Z' })
      if (path === '/reports/schedules') return Promise.resolve([schedule])
      if (path === '/reports/schedules/schedule-1/run' && options?.method === 'POST') {
        runRequests += 1
        requestHeaders.push(new Headers(options.headers).get('Idempotency-Key') ?? '')
        versionHeaders.push(new Headers(options.headers).get('If-Match') ?? '')
        if (runRequests === 1) {
          return Promise.reject(new ApiTransportError('The API could not be reached.', path, 'network'))
        }
        return Promise.resolve([{
          report_id: '22222222-2222-4222-8222-222222222222',
          task_run_id: '33333333-3333-4333-8333-333333333333',
          celery_task_id: null,
          status: 'queued',
        }])
      }
      return Promise.reject(new Error(`Unexpected API path: ${path}`))
    })
    const view = renderPage()
    await openReportingTab(view, 'Schedules')
    await act(async () => {
      await vi.waitFor(() => expect(view.textContent).toContain('Monday landscape'))
    })

    await act(async () => {
      rowButton(rowByName(view, 'Monday landscape'), 'Run now').click()
      await vi.waitFor(() => expect(view.textContent).toContain('Retry safely with the same request'))
    })
    await act(async () => {
      rowButton(rowByName(view, 'Monday landscape'), 'Run now').click()
      await vi.waitFor(() => expect(runRequests).toBe(2))
    })

    expect(requestHeaders[0]).not.toBe('')
    expect(requestHeaders[1]).toBe(requestHeaders[0])
    expect(versionHeaders).toEqual([
      '"2026-08-17T08:00:00Z"',
      '"2026-08-17T08:00:00Z"',
    ])
  })

  it('blocks duplicate schedule actions only for the affected row', async () => {
    reportingPageMocks.routeReportId = undefined
    reportingPageMocks.userRole = 'admin'
    const first = reportSchedule('schedule-1', 'Monday landscape')
    const second = reportSchedule('schedule-2', 'Friday landscape')
    let resolveRun: ((value: unknown[]) => void) | undefined
    const resolveScheduleRefreshes: Array<(value: ReportSchedule[]) => void> = []
    let runRequests = 0
    let scheduleRequests = 0
    reportingPageMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/reports/capabilities') return Promise.resolve(CAPABILITIES)
      if (path === '/reports/templates') return Promise.resolve([REPORT_TEMPLATE])
      if (path.startsWith('/reports/library?')) return Promise.resolve({ items: [], current_cursor: 'first', next_cursor: null, as_of: '2026-09-08T00:00:00Z' })
      if (path === '/reports/schedules') {
        scheduleRequests += 1
        return scheduleRequests === 1
          ? Promise.resolve([first, second])
          : new Promise((resolve) => { resolveScheduleRefreshes.push(resolve) })
      }
      if (path === '/reports/schedules/schedule-1/run' && options?.method === 'POST') {
        runRequests += 1
        return new Promise((resolve) => { resolveRun = resolve })
      }
      return Promise.reject(new Error(`Unexpected API path: ${path}`))
    })
    const view = renderPage()
    await openReportingTab(view, 'Schedules')
    await act(async () => {
      await vi.waitFor(() => expect(view.textContent).toContain('Friday landscape'))
    })
    const firstRow = rowByName(view, 'Monday landscape')
    const secondRow = rowByName(view, 'Friday landscape')
    const firstRun = rowButton(firstRow, 'Run now')

    act(() => {
      firstRun.click()
      firstRun.click()
    })
    await act(async () => {
      await vi.waitFor(() => expect(rowButton(firstRow, 'Queueing...').disabled).toBe(true))
      await vi.waitFor(() => expect(runRequests).toBe(1))
    })
    expect(runRequests).toBe(1)
    expect(rowButton(secondRow, 'Run now').disabled).toBe(false)

    await act(async () => {
      resolveRun?.([{
        report_id: '22222222-2222-4222-8222-222222222222',
        task_run_id: '33333333-3333-4333-8333-333333333333',
        celery_task_id: null,
        status: 'queued',
      }])
      await Promise.resolve()
    })
    await vi.waitFor(() => expect(view.textContent).toContain('Scheduled report run queued'))
    await vi.waitFor(() => expect(scheduleRequests).toBe(3))
    expect(rowButton(firstRow, 'Queueing...').disabled).toBe(true)

    await act(async () => {
      for (const resolveScheduleRefresh of resolveScheduleRefreshes) {
        resolveScheduleRefresh([first, second])
      }
      await vi.waitFor(() => expect(rowButton(firstRow, 'Run now').disabled).toBe(false))
    })
  })
})

describe('ReportingPage template pending state', () => {
  it('blocks duplicate clones only for the affected template', async () => {
    reportingPageMocks.routeReportId = undefined
    const secondTemplate = { ...REPORT_TEMPLATE, id: 'template-2', name: 'Executive landscape' }
    let resolveClone: ((value: ReportTemplate) => void) | undefined
    let cloneRequests = 0
    const cloneHeaders: string[] = []
    reportingPageMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/reports/capabilities') return Promise.resolve(CAPABILITIES)
      if (path === '/reports/templates') return Promise.resolve([REPORT_TEMPLATE, secondTemplate])
      if (path.startsWith('/reports/library?')) return Promise.resolve({ items: [], current_cursor: 'first', next_cursor: null, as_of: '2026-09-08T00:00:00Z' })
      if (path === '/reports/templates/template-1/clone' && options?.method === 'POST') {
        cloneRequests += 1
        cloneHeaders.push(new Headers(options.headers).get('Idempotency-Key') ?? '')
        return new Promise((resolve) => { resolveClone = resolve })
      }
      return Promise.reject(new Error(`Unexpected API path: ${path}`))
    })
    const view = renderPage()
    await openReportingTab(view, 'Templates')
    await act(async () => {
      await vi.waitFor(() => expect(view.textContent).toContain('Executive landscape'))
    })
    const firstRow = rowByName(view, 'Threat landscape')
    const secondRow = rowByName(view, 'Executive landscape')
    const firstClone = rowButton(firstRow, 'Clone')

    act(() => {
      firstClone.click()
      firstClone.click()
    })
    await act(async () => {
      await vi.waitFor(() => expect(rowButton(firstRow, 'Cloning...').disabled).toBe(true))
      await vi.waitFor(() => expect(cloneRequests).toBe(1))
    })
    expect(cloneRequests).toBe(1)
    expect(cloneHeaders[0]).not.toBe('')
    expect(rowButton(secondRow, 'Clone').disabled).toBe(false)

    await act(async () => {
      resolveClone?.({
        ...REPORT_TEMPLATE,
        id: '77777777-7777-4777-8777-777777777777',
        name: 'Threat landscape copy',
      })
      await vi.waitFor(() => expect(view.textContent).toContain('Template cloned'))
    })
  })
})

describe('ReportingPage resource version refresh', () => {
  it('persists both editorial review choices and keeps controls locked during each save', async () => {
    reportingPageMocks.routeReportId = undefined
    reportingPageMocks.userRole = 'admin'
    let current = { ...reportSchedule('schedule-1', 'Monday landscape'), review_required: false, resource_version: 'v1' }
    const writes: Array<{ version: string | null; body: Record<string, unknown> }> = []
    let finishSave!: () => void
    reportingPageMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/reports/capabilities') return Promise.resolve(CAPABILITIES)
      if (path === '/reports/templates') return Promise.resolve([REPORT_TEMPLATE])
      if (path.startsWith('/reports/library?')) return Promise.resolve({ items: [], current_cursor: 'first', next_cursor: null, as_of: '2026-09-08T00:00:00Z' })
      if (path === '/reports/schedules') return Promise.resolve([current])
      if (path === '/reports/schedules/schedule-1' && options?.method === 'PUT') {
        const body = JSON.parse(String(options.body))
        writes.push({ version: new Headers(options.headers).get('If-Match'), body })
        return new Promise((resolve) => {
          finishSave = () => {
            current = { ...current, ...body, resource_version: `v${writes.length + 1}` }
            resolve(current)
          }
        })
      }
      return Promise.reject(new Error(`Unexpected API path: ${path}`))
    })
    const view = renderPage()
    await openReportingTab(view, 'Schedules')
    for (const reviewRequired of [true, false]) {
      const row = rowByName(view, 'Monday landscape')
      act(() => rowButton(row, 'Edit').click())
      const label = [...row.querySelectorAll('label')].find((entry) => entry.textContent?.includes('Require editorial review before publication'))!
      const checkbox = label.querySelector('input')!
      expect(checkbox.checked).toBe(!reviewRequired)
      act(() => checkbox.click())
      await act(async () => {
        row.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
        await vi.waitFor(() => expect(checkbox.matches(':disabled')).toBe(true))
      })
      expect(writes.at(-1)?.body.review_required).toBe(reviewRequired)
      await act(async () => {
        finishSave()
        await vi.waitFor(() => expect(row.querySelector('form')).toBeNull())
      })
      expect(current.review_required).toBe(reviewRequired)
    }
    act(() => rowButton(rowByName(view, 'Monday landscape'), 'Edit').click())
    const label = [...view.querySelectorAll('form label')].find((entry) => entry.textContent?.includes('Require editorial review before publication'))!
    expect(label.querySelector('input')!.checked).toBe(false)
    expect(writes.map(({ version }) => version)).toEqual(['"v1"', '"v2"'])
  })

  it('uses the server version from each successful schedule update', async () => {
    reportingPageMocks.routeReportId = undefined
    reportingPageMocks.userRole = 'admin'
    const original = {
      ...reportSchedule('schedule-1', 'Monday landscape'),
      resource_version: 'schedule-v1',
    }
    let current = original
    let scheduleListRequests = 0
    const updateHeaders: string[] = []
    reportingPageMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/reports/capabilities') return Promise.resolve(CAPABILITIES)
      if (path === '/reports/templates') return Promise.resolve([REPORT_TEMPLATE])
      if (path.startsWith('/reports/library?')) return Promise.resolve({ items: [], current_cursor: 'first', next_cursor: null, as_of: '2026-09-08T00:00:00Z' })
      if (path === '/reports/schedules') {
        scheduleListRequests += 1
        return Promise.resolve([current])
      }
      if (path === '/reports/schedules/schedule-1' && options?.method === 'PUT') {
        updateHeaders.push(new Headers(options.headers).get('If-Match') ?? '')
        current = {
          ...current,
          enabled: !current.enabled,
          resource_version: updateHeaders.length === 1
            ? 'schedule-v2'
            : 'schedule-v3',
          updated_at: updateHeaders.length === 1
            ? '2026-08-24T10:00:00Z'
            : '2026-08-24T10:01:00Z',
        }
        return Promise.resolve(current)
      }
      return Promise.reject(new Error(`Unexpected API path: ${path}`))
    })
    const view = renderPage()
    await openReportingTab(view, 'Schedules')
    await act(async () => {
      await vi.waitFor(() => expect(view.textContent).toContain('Monday landscape'))
    })

    await act(async () => {
      rowButton(rowByName(view, 'Monday landscape'), 'Pause').click()
      await vi.waitFor(() => expect(updateHeaders).toHaveLength(1))
      await vi.waitFor(() => expect(scheduleListRequests).toBeGreaterThan(1))
      await vi.waitFor(() => expect(rowButton(rowByName(view, 'Monday landscape'), 'Enable')).toBeTruthy())
    })
    await act(async () => {
      rowButton(rowByName(view, 'Monday landscape'), 'Enable').click()
      await vi.waitFor(() => expect(updateHeaders).toHaveLength(2))
    })

    expect(updateHeaders).toEqual([
      '"schedule-v1"',
      '"schedule-v2"',
    ])
  })

  it('refetches a conflicted schedule before allowing a retry', async () => {
    reportingPageMocks.routeReportId = undefined
    reportingPageMocks.userRole = 'admin'
    let current = reportSchedule('schedule-1', 'Monday landscape')
    let scheduleListRequests = 0
    const updateHeaders: string[] = []
    reportingPageMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/reports/capabilities') return Promise.resolve(CAPABILITIES)
      if (path === '/reports/templates') return Promise.resolve([REPORT_TEMPLATE])
      if (path.startsWith('/reports/library?')) return Promise.resolve({ items: [], current_cursor: 'first', next_cursor: null, as_of: '2026-09-08T00:00:00Z' })
      if (path === '/reports/schedules') {
        scheduleListRequests += 1
        return Promise.resolve([current])
      }
      if (path === '/reports/schedules/schedule-1' && options?.method === 'PUT') {
        updateHeaders.push(new Headers(options.headers).get('If-Match') ?? '')
        if (updateHeaders.length === 1) {
          current = { ...current, updated_at: '2026-08-24T10:30:00Z' }
          return Promise.reject(new ApiError(
            'The report schedule changed after you loaded it.',
            412,
            path,
          ))
        }
        current = {
          ...current,
          enabled: false,
          updated_at: '2026-08-24T10:31:00Z',
        }
        return Promise.resolve(current)
      }
      return Promise.reject(new Error(`Unexpected API path: ${path}`))
    })
    const view = renderPage()
    await openReportingTab(view, 'Schedules')
    await act(async () => {
      await vi.waitFor(() => expect(view.textContent).toContain('Monday landscape'))
      rowButton(rowByName(view, 'Monday landscape'), 'Pause').click()
      await vi.waitFor(() => expect(scheduleListRequests).toBeGreaterThan(1))
      await vi.waitFor(() => expect(view.textContent).toContain('changed after you loaded it'))
    })
    await act(async () => {
      rowButton(rowByName(view, 'Monday landscape'), 'Pause').click()
      await vi.waitFor(() => expect(updateHeaders).toHaveLength(2))
    })

    expect(updateHeaders).toEqual([
      '"2026-08-17T08:00:00Z"',
      '"2026-08-24T10:30:00Z"',
    ])
  })

  it('uses the server version from each successful template update', async () => {
    reportingPageMocks.routeReportId = undefined
    let current = { ...REPORT_TEMPLATE, resource_version: 'template-v1' }
    let templateListRequests = 0
    const updateHeaders: string[] = []
    reportingPageMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/reports/capabilities') return Promise.resolve(CAPABILITIES)
      if (path === '/reports/templates' && options?.method !== 'POST') {
        templateListRequests += 1
        return Promise.resolve([current])
      }
      if (path.startsWith('/reports/library?')) return Promise.resolve({ items: [], current_cursor: 'first', next_cursor: null, as_of: '2026-09-08T00:00:00Z' })
      if (path === '/reports/templates/template-1' && options?.method === 'PUT') {
        updateHeaders.push(new Headers(options.headers).get('If-Match') ?? '')
        current = {
          ...current,
          resource_version: updateHeaders.length === 1
            ? 'template-v2'
            : 'template-v3',
          updated_at: updateHeaders.length === 1
            ? '2026-08-24T11:00:00Z'
            : '2026-08-24T11:01:00Z',
        }
        return Promise.resolve(current)
      }
      return Promise.reject(new Error(`Unexpected API path: ${path}`))
    })
    const view = renderPage()
    await openReportingTab(view, 'Templates')
    await act(async () => {
      await vi.waitFor(() => expect(view.textContent).toContain('Threat landscape'))
      rowButton(rowByName(view, 'Threat landscape'), 'Use template').click()
      await vi.waitFor(() => expect(button(view, 'Update template')).toBeTruthy())
    })

    await act(async () => {
      button(view, 'Update template').click()
      await vi.waitFor(() => expect(button(view, 'Save')).toBeTruthy())
    })
    await act(async () => {
      button(view, 'Save').click()
      await vi.waitFor(() => expect(updateHeaders).toHaveLength(1))
      await vi.waitFor(() => expect(templateListRequests).toBeGreaterThan(1))
      await vi.waitFor(() => expect(button(view, 'Update template')).toBeTruthy())
    })
    await act(async () => {
      button(view, 'Update template').click()
      await vi.waitFor(() => expect(button(view, 'Save')).toBeTruthy())
    })
    await act(async () => {
      button(view, 'Save').click()
      await vi.waitFor(() => expect(updateHeaders).toHaveLength(2))
    })

    expect(updateHeaders).toEqual([
      '"template-v1"',
      '"template-v2"',
    ])
  })

  it('sends the canonical schedule version when deleting', async () => {
    reportingPageMocks.routeReportId = undefined
    reportingPageMocks.userRole = 'admin'
    const schedule = {
      ...reportSchedule('schedule-1', 'Monday landscape'),
      resource_version: 'schedule-delete-v1',
    }
    let deleted = false
    let deleteHeader = ''
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    reportingPageMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/reports/capabilities') return Promise.resolve(CAPABILITIES)
      if (path === '/reports/templates') return Promise.resolve([REPORT_TEMPLATE])
      if (path.startsWith('/reports/library?')) return Promise.resolve({ items: [], current_cursor: 'first', next_cursor: null, as_of: '2026-09-08T00:00:00Z' })
      if (path === '/reports/schedules') return Promise.resolve(deleted ? [] : [schedule])
      if (path === '/reports/schedules/schedule-1' && options?.method === 'DELETE') {
        deleteHeader = new Headers(options.headers).get('If-Match') ?? ''
        deleted = true
        return Promise.resolve(undefined)
      }
      return Promise.reject(new Error(`Unexpected API path: ${path}`))
    })
    const view = renderPage()
    await openReportingTab(view, 'Schedules')
    await act(async () => {
      await vi.waitFor(() => expect(view.textContent).toContain('Monday landscape'))
      rowButton(rowByName(view, 'Monday landscape'), 'Delete').click()
      await vi.waitFor(() => expect(deleteHeader).toBe('"schedule-delete-v1"'))
    })
  })

  it('sends the canonical template version when deleting', async () => {
    reportingPageMocks.routeReportId = undefined
    const template = { ...REPORT_TEMPLATE, resource_version: 'template-delete-v1' }
    let deleted = false
    let deleteHeader = ''
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    reportingPageMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/reports/capabilities') return Promise.resolve(CAPABILITIES)
      if (path === '/reports/templates') return Promise.resolve(deleted ? [] : [template])
      if (path.startsWith('/reports/library?')) return Promise.resolve({ items: [], current_cursor: 'first', next_cursor: null, as_of: '2026-09-08T00:00:00Z' })
      if (path === '/reports/templates/template-1' && options?.method === 'DELETE') {
        deleteHeader = new Headers(options.headers).get('If-Match') ?? ''
        deleted = true
        return Promise.resolve(undefined)
      }
      return Promise.reject(new Error(`Unexpected API path: ${path}`))
    })
    const view = renderPage()
    await openReportingTab(view, 'Templates')
    await act(async () => {
      await vi.waitFor(() => expect(view.textContent).toContain('Threat landscape'))
      rowButton(rowByName(view, 'Threat landscape'), 'Delete').click()
      await vi.waitFor(() => expect(deleteHeader).toBe('"template-delete-v1"'))
    })
  })
})

it.each(['resource_version', 'updated_at'] as const)(
  'keeps a schedule draft with its original %s through a conflict and reloads deliberately',
  async (versionField) => {
    reportingPageMocks.routeReportId = undefined
    reportingPageMocks.userRole = 'admin'
    const original = {
      ...reportSchedule('schedule-1', 'Original schedule'),
      [versionField]: '2026-09-08T09:00:00Z',
      custom_instructions: 'Original shared instructions',
    }
    let current = original
    const writes: Array<{ header: string | null; body: Record<string, unknown> }> = []
    let rejectStale!: () => void
    reportingPageMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/reports/capabilities') return Promise.resolve(CAPABILITIES)
      if (path === '/reports/templates') return Promise.resolve([REPORT_TEMPLATE])
      if (path.startsWith('/reports/library?')) return Promise.resolve({ items: [], current_cursor: 'first', next_cursor: null, as_of: '2026-09-08T00:00:00Z' })
      if (path === '/reports/schedules') return Promise.resolve([current])
      if (path === '/reports/schedules/schedule-1' && options?.method === 'PUT') {
        const written = { header: new Headers(options.headers).get('If-Match'), body: JSON.parse(String(options.body)) }
        writes.push(written)
        if (written.header !== `"${current[versionField]}"`) {
          return new Promise((_resolve, reject) => {
            rejectStale = () => reject(new ApiError('The report schedule changed after you loaded it.', 412, path))
          })
        }
        current = { ...current, ...written.body, [versionField]: '2026-09-08T11:00:00Z' }
        return Promise.resolve(current)
      }
      return Promise.reject(new Error(`Unexpected API path: ${path}`))
    })
    const view = renderPage()
    await openReportingTab(view, 'Schedules')
    act(() => rowButton(rowByName(view, 'Original schedule'), 'Edit').click())
    const name = view.querySelector('form input') as HTMLInputElement
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(name, 'My draft name')
      name.dispatchEvent(new Event('input', { bubbles: true }))
    })
    current = { ...original, [versionField]: '2026-09-08T10:00:00Z', custom_instructions: 'Another administrator updated this' }
    await act(async () => {
      await queryClient!.refetchQueries({ queryKey: ['reports', 'schedules'] })
      await new Promise((resolve) => setTimeout(resolve, 20))
    })
    expect((view.querySelector('form textarea') as HTMLTextAreaElement).value).toBe('Original shared instructions')
    expect(view.textContent).toContain('Your draft keeps its original version')
    await act(async () => {
      view.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
      await vi.waitFor(() => expect(writes).toHaveLength(1))
    })
    expect(writes[0]).toEqual({
      header: '"2026-09-08T09:00:00Z"',
      body: expect.objectContaining({ name: 'My draft name', custom_instructions: 'Original shared instructions' }),
    })
    expect(name.matches(':disabled')).toBe(true)
    await act(async () => {
      rejectStale()
      await vi.waitFor(() => expect(view.textContent).toContain('changed after you loaded it'))
      await new Promise((resolve) => setTimeout(resolve, 20))
    })
    expect(name.isConnected).toBe(true)
    expect(name.matches(':disabled')).toBe(false)
    expect(name.value).toBe('My draft name')
    expect((view.querySelector('form textarea') as HTMLTextAreaElement).value).toBe('Original shared instructions')

    act(() => rowButton(rowByName(view, 'Original schedule'), 'Cancel').click())
    act(() => [...document.querySelectorAll('button')].find((button) => button.textContent === 'Discard changes')!.click())
    act(() => rowButton(rowByName(view, 'Original schedule'), 'Edit').click())
    expect((view.querySelector('form textarea') as HTMLTextAreaElement).value).toBe('Another administrator updated this')
    await act(async () => {
      view.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
      await vi.waitFor(() => expect(writes).toHaveLength(2))
      await new Promise((resolve) => setTimeout(resolve, 20))
    })
    expect(writes[1].header).toBe('"2026-09-08T10:00:00Z"')
    expect(writes[1].body.custom_instructions).toBe('Another administrator updated this')
    expect(view.querySelector('form')).toBeNull()
  },
)


it('retains schedule drafts across reporting tabs and confirms explicit cancellation', async () => {
  reportingPageMocks.routeReportId = undefined
  reportingPageMocks.userRole = 'admin'
  reportingPageMocks.apiFetch.mockImplementation((path: string) => {
    if (path === '/reports/capabilities') return Promise.resolve(CAPABILITIES)
    if (path === '/reports/templates') return Promise.resolve([REPORT_TEMPLATE])
    if (path === '/reports/schedules') return Promise.resolve([reportSchedule('schedule-1', 'Weekly report')])
    if (path.startsWith('/reports/library?')) return Promise.resolve({ items: [], next_cursor: null })
    return Promise.reject(new Error(`Unexpected request: ${path}`))
  })
  const view = renderPage()
  await openReportingTab(view, 'Schedules')
  act(() => rowButton(rowByName(view, 'Weekly report'), 'Edit').click())
  const input = view.querySelector('form input') as HTMLInputElement
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, 'Unsaved schedule')
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await openReportingTab(view, 'Templates')
  expect(input.isConnected).toBe(true)
  expect(input.closest('[hidden]')).not.toBeNull()
  await openReportingTab(view, 'Schedules')
  expect(input.closest('[hidden]')).toBeNull()
  expect(input.value).toBe('Unsaved schedule')
  act(() => rowButton(rowByName(view, 'Weekly report'), 'Cancel').click())
  expect(document.querySelector('[role="alertdialog"]')?.textContent).toContain('Discard unsaved report schedule changes?')
})

describe('reporting metadata independent read authority', () => {
  const privateTemplate = { ...REPORT_TEMPLATE, name: 'Private saved reporting template',
    prompt: { ...REPORT_TEMPLATE.prompt, objective: 'Private saved reporting objective' } }
  const privateSchedule = reportSchedule('schedule-1', 'Private saved reporting schedule')

  function protectReads(target: string) {
    let status = 200
    const rejected: number[] = []
    reportingPageMocks.routeReportId = undefined
    reportingPageMocks.userRole = 'admin'
    reportingPageMocks.apiFetch.mockImplementation((path: string) => {
      if (path === target && status !== 200) {
        rejected.push(status)
        return Promise.reject(new ApiError('Reporting metadata read unavailable', status, path))
      }
      if (path === '/reports/capabilities') return Promise.resolve({ ...CAPABILITIES, context_window_tokens: 892731 })
      if (path === '/reports/templates') return Promise.resolve([privateTemplate])
      if (path === '/reports/schedules') return Promise.resolve([privateSchedule])
      if (path.startsWith('/reports/library?')) return Promise.resolve({ items: [], next_cursor: null })
      return Promise.reject(new Error(`Unexpected reporting metadata request: ${path}`))
    })
    return { rejected, setStatus: (next: number) => { status = next } }
  }

  async function settleRead(key: string, status: number) {
    await act(async () => { await queryClient!.refetchQueries({ queryKey: ['reports', key], exact: true }) })
    await vi.waitFor(() => expect((queryClient!.getQueryState(['reports', key])?.error as ApiError)?.status).toBe(status))
    await act(async () => { await new Promise((done) => setTimeout(done, 15)) })
  }

  it('keeps a copied template objective private while preserving the unsent report title', async () => {
    const reads = protectReads('/reports/templates')
    const view = renderPage()
    await vi.waitFor(() => expect([...view.querySelectorAll('textarea')].map((input) => input.value)).toContain(privateTemplate.prompt.objective))
    const title = view.querySelector<HTMLInputElement>('input[placeholder="Use template and period"]')!
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(title, 'My unsent report title')
      title.dispatchEvent(new Event('input', { bubbles: true }))
    })
    reads.setStatus(403)
    await settleRead('templates', 403)
    const unload = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(unload)
    expect(unload.defaultPrevented).toBe(true)
    expect([...view.querySelectorAll('textarea')].map((input) => input.value)).not.toContain(privateTemplate.prompt.objective)
    reads.setStatus(200)
    await act(async () => { await queryClient!.refetchQueries({ queryKey: ['reports', 'templates'], exact: true }) })
    await vi.waitFor(() => expect(view.querySelector<HTMLInputElement>('input[placeholder="Use template and period"]')?.value).toBe('My unsent report title'))
  })

  it('retains the local save-as-template name and visibility through template access recovery', async () => {
    const reads = protectReads('/reports/templates')
    const view = renderPage()
    const templateForm = () => [...view.querySelectorAll('form')].find((form) => form.textContent?.includes('Template name'))
    await vi.waitFor(() => expect([...view.querySelectorAll('textarea')].map((input) => input.value)).toContain(privateTemplate.prompt.objective))
    act(() => {
      const title = view.querySelector<HTMLInputElement>('input[placeholder="Use template and period"]')!
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(title, 'My retained report title')
      title.dispatchEvent(new Event('input', { bubbles: true }))
      const objective = [...view.querySelectorAll('textarea')].find((input) => input.value === privateTemplate.prompt.objective)!
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(objective, 'My retained local objective')
      objective.dispatchEvent(new Event('input', { bubbles: true }))
    })
    act(() => button(view, 'Save as template').click())
    act(() => {
      const name = templateForm()!.querySelector<HTMLInputElement>('input')!
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(name, 'My unsent template name')
      name.dispatchEvent(new Event('input', { bubbles: true }))
      const visibility = templateForm()!.querySelector<HTMLSelectElement>('select')!
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!.call(visibility, 'shared')
      visibility.dispatchEvent(new Event('change', { bubbles: true }))
    })
    expect(templateForm()?.querySelector<HTMLInputElement>('input')?.value).toBe('My unsent template name')
    expect(templateForm()?.querySelector<HTMLSelectElement>('select')?.value).toBe('shared')
    reads.setStatus(403)
    await settleRead('templates', 403)
    expect(reads.rejected).toContain(403)
    expect(templateForm()).toBeUndefined()
    reads.setStatus(200)
    await act(async () => { await queryClient!.refetchQueries({ queryKey: ['reports', 'templates'], exact: true }) })
    await vi.waitFor(() => expect(view.querySelector<HTMLInputElement>('input[placeholder="Use template and period"]')?.value).toBe('My retained report title'))
    expect([...view.querySelectorAll('textarea')].map((input) => input.value)).toContain('My retained local objective')
    expect(templateForm()?.querySelector<HTMLInputElement>('input')?.value).toBe('My unsent template name')
    expect(templateForm()?.querySelector<HTMLSelectElement>('select')?.value).toBe('shared')
    expect(reportingPageMocks.apiFetch.mock.calls.some(([, options]) => options?.method === 'POST' || options?.method === 'PUT')).toBe(false)
  })

  it('retires untouched template form defaults and protects a solely local edited form draft', async () => {
    const reads = protectReads('/reports/templates')
    const view = renderPage()
    const templateForm = () => [...view.querySelectorAll('form')].find((form) => form.textContent?.includes('Template name'))
    await vi.waitFor(() => expect([...view.querySelectorAll('textarea')].map((input) => input.value)).toContain(privateTemplate.prompt.objective))
    act(() => button(view, 'Save as template').click())
    expect(templateForm()?.querySelector<HTMLInputElement>('input')?.value).toContain(privateTemplate.name)
    reads.setStatus(403)
    await settleRead('templates', 403)
    reads.setStatus(200)
    await act(async () => { await queryClient!.refetchQueries({ queryKey: ['reports', 'templates'], exact: true }) })
    await vi.waitFor(async () => {
      await act(async () => { await new Promise((done) => setTimeout(done, 15)) })
      expect(button(view, 'Save as template').disabled).toBe(false)
    })
    expect(templateForm()).toBeUndefined()
    act(() => button(view, 'Save as template').click())
    act(() => {
      const name = templateForm()!.querySelector<HTMLInputElement>('input')!
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(name, 'Only the local template name changed')
      name.dispatchEvent(new Event('input', { bubbles: true }))
    })
    const unload = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(unload)
    expect(unload.defaultPrevented).toBe(true)
    reads.setStatus(403)
    await settleRead('templates', 403)
    const hiddenUnload = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(hiddenUnload)
    expect(hiddenUnload.defaultPrevented).toBe(true)
    reads.setStatus(200)
    await act(async () => { await queryClient!.refetchQueries({ queryKey: ['reports', 'templates'], exact: true }) })
    await vi.waitFor(() => expect(templateForm()?.querySelector<HTMLInputElement>('input')?.value).toBe('Only the local template name changed'))
  })

  it.each([
    { key: 'templates', tab: 'Templates' as const, value: privateTemplate.name },
    { key: 'schedules', tab: 'Schedules' as const, value: privateSchedule.name },
  ])('keeps denied $key metadata private through a later outage', async ({ key, tab, value }) => {
    const reads = protectReads(`/reports/${key}`)
    const view = renderPage()
    await openReportingTab(view, tab)
    await vi.waitFor(() => expect(view.textContent).toContain(value))
    for (const status of [403, 503]) {
      reads.setStatus(status)
      await settleRead(key, status)
      expect(reads.rejected).toContain(status)
      expect(view.textContent).not.toContain(value)
      expect(view.textContent).toContain('892,731 token context')
    }
  })

  it('keeps denied capabilities metadata private through a later outage', async () => {
    const reads = protectReads('/reports/capabilities')
    const view = renderPage()
    await vi.waitFor(() => expect(view.textContent).toContain('892,731 token context'))
    for (const status of [403, 503]) {
      reads.setStatus(status)
      await settleRead('capabilities', status)
      expect(reads.rejected).toContain(status)
      expect(view.textContent).not.toContain('892,731 token context')
    }
  })

  it('preserves an independently permitted schedule draft when capabilities read is denied', async () => {
    const reads = protectReads('/reports/capabilities')
    const view = renderPage()
    await openReportingTab(view, 'Schedules')
    await vi.waitFor(() => expect(view.textContent).toContain(privateSchedule.name))
    act(() => rowButton(rowByName(view, privateSchedule.name), 'Edit').click())
    const name = view.querySelector<HTMLInputElement>('form input')!
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(name, 'Independently permitted unsent schedule')
      name.dispatchEvent(new Event('input', { bubbles: true }))
    })
    reads.setStatus(403)
    await settleRead('capabilities', 403)
    expect(reads.rejected).toContain(403)
    expect(queryClient!.getQueryState(['reports', 'schedules'])?.status).toBe('success')
    expect(view.textContent).not.toContain('892,731 token context')
    const unload = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(unload)
    expect(unload.defaultPrevented).toBe(true)
    expect(view.querySelector<HTMLInputElement>('form input')?.value).toBe('Independently permitted unsent schedule')
  })

  it('pauses template actions while retaining ordinary outage context', async () => {
    const reads = protectReads('/reports/templates')
    const view = renderPage()
    await openReportingTab(view, 'Templates')
    await vi.waitFor(() => expect(view.textContent).toContain(privateTemplate.name))
    reads.setStatus(503)
    await settleRead('templates', 503)
    expect(view.textContent).toContain(privateTemplate.name)
    for (const label of ['Use template', 'Clone', 'Delete']) {
      const action = rowButton(rowByName(view, privateTemplate.name), label)
      expect(action.disabled).toBe(true)
      act(() => action.click())
    }
    expect(reportingPageMocks.apiFetch.mock.calls.some(([, options]) => options?.method === 'POST' || options?.method === 'DELETE')).toBe(false)
  })

  it('preserves an unsaved schedule draft while pausing an ordinary outage write', async () => {
    const reads = protectReads('/reports/schedules')
    const view = renderPage()
    await openReportingTab(view, 'Schedules')
    await vi.waitFor(() => expect(view.textContent).toContain(privateSchedule.name))
    act(() => rowButton(rowByName(view, privateSchedule.name), 'Edit').click())
    const name = view.querySelector<HTMLInputElement>('form input')!
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(name, 'Unsent local schedule name')
      name.dispatchEvent(new Event('input', { bubbles: true }))
    })
    reads.setStatus(503)
    await settleRead('schedules', 503)
    expect(name.value).toBe('Unsent local schedule name')
    expect(button(view, 'Save schedule').disabled).toBe(true)
    act(() => view.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
    await act(async () => { await new Promise((done) => setTimeout(done, 15)) })
    expect(reportingPageMocks.apiFetch.mock.calls.some(([, options]) => options?.method === 'PUT')).toBe(false)
  })

  it('keeps a denied schedule private without destroying its unsaved local draft', async () => {
    const reads = protectReads('/reports/schedules')
    const view = renderPage()
    await openReportingTab(view, 'Schedules')
    await vi.waitFor(() => expect(view.textContent).toContain(privateSchedule.name))
    act(() => rowButton(rowByName(view, privateSchedule.name), 'Edit').click())
    const name = view.querySelector<HTMLInputElement>('form input')!
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(name, 'Unsent local schedule name')
      name.dispatchEvent(new Event('input', { bubbles: true }))
    })
    reads.setStatus(403)
    await settleRead('schedules', 403)
    const unload = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(unload)
    expect(unload.defaultPrevented).toBe(true)
    expect(view.textContent).not.toContain(privateSchedule.name)
    expect(view.querySelector('form')).toBeNull()
    reads.setStatus(200)
    await act(async () => { await queryClient!.refetchQueries({ queryKey: ['reports', 'schedules'], exact: true }) })
    await vi.waitFor(() => expect(view.querySelector<HTMLInputElement>('form input')?.value).toBe('Unsent local schedule name'))
  })
})
