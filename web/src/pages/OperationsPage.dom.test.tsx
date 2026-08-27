// @vitest-environment jsdom

import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const operationsDomMocks = vi.hoisted(() => ({
  overviewError: null as Error | null,
  runsError: null as Error | null,
  runsFetching: false,
  runsRefetch: vi.fn(),
  diagnosticsError: null as Error | null,
  diagnosticsRequested: vi.fn(),
}))

const overview = {
  generated_at: '2026-08-27T12:00:00Z',
  overall_status: 'degraded' as const,
  application: {
    version: '1.7.0',
    schema_revision: '0057_system_operations',
    expected_schema_revision: '0057_system_operations',
    schema_current: true,
  },
  components: [
    {
      key: 'database',
      label: 'PostgreSQL',
      status: 'healthy' as const,
      summary: 'Database queries are responding.',
      checked_at: '2026-08-27T12:00:00Z',
      metrics: {},
    },
  ],
  storage: [
    {
      key: 'database',
      label: 'Database size',
      status: 'healthy' as const,
      used_bytes: 1048576,
      total_bytes: null,
      available_bytes: null,
      percent_used: null,
    },
  ],
  backlogs: [
    {
      key: 'reports',
      label: 'Report generation',
      status: 'degraded' as const,
      pending_count: 2,
      active_count: 1,
      stale_count: 1,
      failed_count: 0,
      oldest_pending_age_seconds: 420,
      degraded_after_seconds: 300,
    },
  ],
  recovery: {
    latest_backup: null,
    latest_verify: null,
    latest_restore_drill: null,
    latest_restore: null,
  },
  issues: [
    {
      code: 'reports_stale',
      severity: 'warning' as const,
      component: 'reports',
      summary: 'A report has waited too long.',
      effect: 'Scheduled reporting may be delayed.',
      recommended_action: 'Check the AI report worker queue.',
    },
  ],
}

const run = {
  id: '55c704f3-7034-4b32-8598-a757ed769c85',
  operation_type: 'restore_drill' as const,
  status: 'succeeded' as const,
  initiated_by: 'operator',
  source: 'host_cli',
  started_at: '2026-08-27T11:00:00Z',
  finished_at: '2026-08-27T11:01:30Z',
  created_at: '2026-08-27T11:00:00Z',
  updated_at: '2026-08-27T11:01:30Z',
  metadata: {},
  error_code: null,
  error_message: null,
}

vi.mock('@tanstack/react-query', () => ({
  useQuery: (options: { queryKey: unknown[] }) => {
    if (options.queryKey[1] === 'overview') {
      return {
        data: overview,
        isLoading: false,
        isError: Boolean(operationsDomMocks.overviewError),
        error: operationsDomMocks.overviewError,
        isFetching: false,
        dataUpdatedAt: Date.parse('2026-08-27T12:00:01Z'),
        refetch: vi.fn(),
      }
    }
    return {
      data: { runs: [run], total: 1, page: 1, page_size: 20 },
      isLoading: false,
      isFetching: operationsDomMocks.runsFetching,
      isError: Boolean(operationsDomMocks.runsError),
      error: operationsDomMocks.runsError,
      refetch: operationsDomMocks.runsRefetch,
    }
  },
  useMutation: (options: { onSuccess?: (payload: unknown) => void; onError?: (error: Error) => void }) => ({
    mutate: vi.fn(() => {
      operationsDomMocks.diagnosticsRequested()
      if (operationsDomMocks.diagnosticsError) {
        options.onError?.(operationsDomMocks.diagnosticsError)
        return
      }
      options.onSuccess?.({
        schema_version: 1,
        generated_at: '2026-08-27T12:00:00Z',
        overview,
        recent_runs: [run],
        recent_runs_truncated: false,
      })
    }),
    isPending: false,
  }),
}))

import { OperationsPage } from './OperationsPage'

let root: Root | null = null
let container: HTMLDivElement | null = null

function renderPage() {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => {
    root?.render(<OperationsPage />)
  })
  return container
}

afterEach(() => {
  act(() => root?.unmount())
  root = null
  container?.remove()
  container = null
  document.body.innerHTML = ''
  operationsDomMocks.overviewError = null
  operationsDomMocks.runsError = null
  operationsDomMocks.runsFetching = false
  operationsDomMocks.runsRefetch.mockReset()
  operationsDomMocks.diagnosticsError = null
  operationsDomMocks.diagnosticsRequested.mockReset()
  vi.restoreAllMocks()
})

describe('OperationsPage DOM workflows', () => {
  it('renders actionable component, queue, recovery, and issue state', () => {
    const view = renderPage()

    expect(view.textContent).toContain('ThreatLens 1.7.0')
    expect(view.textContent).toContain('PostgreSQL')
    expect(view.textContent).toContain('Report generation')
    expect(view.textContent).toContain('Scheduled reporting may be delayed.')
    expect(view.textContent).toContain('Check the AI report worker queue.')
    expect(view.textContent).toContain('Latest restore drill')
    expect(view.textContent).toContain('Restore drill')
    expect(view.querySelector('table')).not.toBeNull()
  })

  it('keeps the last successful snapshot visible when refresh fails', () => {
    operationsDomMocks.overviewError = new Error('probe timed out')
    const view = renderPage()

    const alert = view.querySelector('[role="alert"]')
    expect(alert?.textContent).toContain('Displaying the last successful snapshot')
    expect(view.textContent).toContain('PostgreSQL')
  })

  it('labels retained operation rows while updating selected history', () => {
    operationsDomMocks.runsFetching = true
    operationsDomMocks.runsError = new Error('history query timed out')
    const view = renderPage()

    expect(view.textContent).toContain(
      'Updating operation history for the selected filters...',
    )
    expect(view.textContent).toContain(
      'The last loaded operation history remains visible.',
    )
    expect(view.textContent).toContain('Restore drill')
    expect(view.querySelector('[aria-busy="true"]')).not.toBeNull()

    operationsDomMocks.runsFetching = false
    const retry = Array.from(
      view.querySelectorAll<HTMLButtonElement>('button'),
    ).find((button) => button.textContent === 'Retrying...')
    expect(retry?.disabled).toBe(true)
  })

  it('offers retry while retaining the last loaded history after failure', () => {
    operationsDomMocks.runsError = new Error('history query timed out')
    const view = renderPage()

    const retry = Array.from(
      view.querySelectorAll<HTMLButtonElement>('button'),
    ).find((button) => button.textContent === 'Retry history')
    expect(retry?.disabled).toBe(false)
    act(() => retry?.click())
    expect(operationsDomMocks.runsRefetch).toHaveBeenCalled()
    expect(view.textContent).toContain('Restore drill')
  })

  it('downloads the bounded diagnostics snapshot and announces completion', () => {
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: vi.fn(() => 'blob:operations-diagnostics'),
    })
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      value: vi.fn(),
    })
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const view = renderPage()
    const button = Array.from(view.querySelectorAll('button')).find((candidate) =>
      candidate.textContent?.includes('Download diagnostics'),
    )

    act(() => button?.click())

    expect(operationsDomMocks.diagnosticsRequested).toHaveBeenCalledOnce()
    expect(view.querySelector('[role="status"]')?.textContent).toContain('Diagnostic snapshot downloaded.')
  })
})
