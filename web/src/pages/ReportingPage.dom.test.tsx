// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ReportDetail } from '../types/api'


;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const reportingPageMocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  apiDownload: vi.fn(),
  navigate: vi.fn(),
  routeReportId: 'report-1' as string | undefined,
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
      role: 'analyst',
      is_active: true,
    },
  }),
}))

vi.mock('react-router-dom', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-router-dom')>()),
  useNavigate: () => reportingPageMocks.navigate,
  useParams: () => ({ reportId: reportingPageMocks.routeReportId }),
}))

import { ApiTransportError } from '../api/client'
import { ReportingPage } from './ReportingPage'


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

function reportDetail(status: ReportDetail['status'] = 'ready'): ReportDetail {
  return {
    id: 'report-1',
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

async function waitForReport(view: HTMLDivElement) {
  await act(async () => {
    await vi.waitFor(() => {
      expect(view.textContent).toContain('Weekly threat landscape')
    })
  })
}

function button(view: HTMLDivElement, label: string): HTMLButtonElement {
  const match = Array.from(view.querySelectorAll('button')).find(
    (entry) => entry.textContent?.trim() === label,
  )
  if (!match) throw new Error(`Button not found: ${label}`)
  return match
}

beforeEach(() => {
  reportingPageMocks.routeReportId = 'report-1'
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
})

describe('ReportingPage detail actions', () => {
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
      { timeoutMs: 60_000 },
    )
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
            task_run_id: 'run-1',
            celery_task_id: null,
            status: 'queued',
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
    expect(view.textContent).toContain('Report retry queued')
  })
})
