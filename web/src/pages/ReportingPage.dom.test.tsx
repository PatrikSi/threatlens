// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ReportDetail, ReportSchedule, ReportTemplate } from '../types/api'


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
        <ReportingPage />
      </QueryClientProvider>,
    )
  })
  return container
}

function rerenderPage() {
  act(() => {
    root?.render(
      <QueryClientProvider client={queryClient!}>
        <ReportingPage />
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
    if (path === '/reports?limit=100') return Promise.resolve([])
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
      if (path === '/reports?limit=100') return Promise.resolve([])
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
      if (path === '/reports?limit=100') return Promise.resolve([])
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
      if (path === '/reports?limit=100') return Promise.resolve([])
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
      if (path === '/reports?limit=100') return Promise.resolve([])
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
      if (path === '/reports?limit=100') return Promise.resolve([])
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

  it('retains the loaded report when a status refresh fails', async () => {
    let detailRequests = 0
    reportingPageMocks.apiFetch.mockImplementation((path: string) => {
      if (path === '/reports/capabilities') return Promise.resolve(CAPABILITIES)
      if (path === '/reports/templates') return Promise.resolve([])
      if (path === '/reports?limit=100') return Promise.resolve([])
      if (path === '/reports/report-1') {
        detailRequests += 1
        return detailRequests === 1
          ? Promise.resolve(reportDetail())
          : Promise.reject(new Error('Status endpoint unavailable'))
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
    expect(view.textContent).toContain('Weekly threat landscape')
    expect(view.textContent).toContain('The last loaded report remains visible')
  })

  it('reuses the retry idempotency key after an ambiguous failure', async () => {
    const requestHeaders: string[] = []
    let retryCalls = 0
    reportingPageMocks.apiFetch.mockImplementation(
      (path: string, options?: RequestInit) => {
        if (path === '/reports/capabilities') return Promise.resolve(CAPABILITIES)
        if (path === '/reports/templates') return Promise.resolve([])
        if (path === '/reports?limit=100') return Promise.resolve([])
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
        if (path === '/reports?limit=100') return Promise.resolve([])
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
      if (path === '/reports?limit=100') return Promise.resolve([])
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
      if (path === '/reports?limit=100') {
        libraryRequests += 1
        return Promise.resolve([])
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
      if (path === '/reports?limit=100') return Promise.resolve([])
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
      if (path === '/reports?limit=100') return Promise.resolve([])
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
      if (path === '/reports?limit=100') return Promise.resolve([])
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
      if (path === '/reports?limit=100') return Promise.resolve([])
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
      if (path === '/reports?limit=100') return Promise.resolve([])
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
      if (path === '/reports?limit=100') return Promise.resolve([])
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
      if (path === '/reports?limit=100') return Promise.resolve([])
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
      if (path === '/reports?limit=100') return Promise.resolve([])
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
