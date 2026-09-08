// @vitest-environment jsdom

import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const operationsDomMocks = vi.hoisted(() => ({
  overviewAvailable: true,
  overviewError: null as Error | null,
  overviewRefetch: vi.fn(),
  workersError: null as Error | null,
  workersFetching: false,
  workersRefetch: vi.fn(),
  historyError: null as Error | null,
  historyFetching: false,
  historyRefetch: vi.fn(),
  runsError: null as Error | null,
  runsFetching: false,
  runsLoadingForFilteredQuery: false,
  runsTotal: 1,
  runsRefetch: vi.fn(),
  diagnosticsError: null as Error | null,
  diagnosticsRequested: vi.fn(),
  queryOptions: [] as Array<{
    queryKey: unknown[]
    refetchInterval?: number | false
  }>,
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
    {
      key: 'workers',
      label: 'Workers',
      status: 'healthy' as const,
      summary: 'All required queues are covered.',
      checked_at: '2026-08-27T12:00:00Z',
      metrics: {
        worker_count: 2,
        required_queues: ['ingest', 'processing'],
        covered_queues: ['ingest', 'processing'],
        missing_queues: [],
      },
    },
    {
      key: 'scheduler',
      label: 'Scheduler',
      status: 'degraded' as const,
      summary: 'The direct scheduler heartbeat is stale.',
      checked_at: '2026-08-27T12:00:00Z',
      metrics: {
        scheduler_age_seconds: 240,
        scheduler_reason: 'stale',
        worker_round_trip_age_seconds: 2,
        worker_round_trip_reason: 'healthy',
        stale_after_seconds: 180,
      },
    },
    {
      key: 'encrypted_data',
      label: 'Encrypted data',
      status: 'healthy' as const,
      summary: 'All inventoried encrypted fields are readable.',
      checked_at: '2026-08-27T12:00:00Z',
      metrics: {
        total_records: 128,
        unreadable_fields: 0,
        scan_complete: true,
        inventory_scanned_at: '2026-08-27T11:59:58Z',
      },
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
    {
      key: 'application_filesystem',
      label: 'Application filesystem',
      status: 'degraded' as const,
      used_bytes: 917504,
      total_bytes: 1048576,
      available_bytes: 131072,
      percent_used: 87.5,
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

const workerTopology = {
  generated_at: '2026-08-27T12:00:00Z',
  status: 'degraded' as const,
  reason: 'execution_stalled' as const,
  timeout_seconds: 1,
  duration_ms: 42,
  responding_worker_count: 1,
  observed_worker_count: 1,
  worker_inventory_truncated: false,
  total_capacity: 4,
  active_count: 2,
  reserved_count: 1,
  scheduled_count: 0,
  missing_queues: [],
  stale_execution_queues: ['processing'],
  missing_execution_evidence_queues: [],
  canary_dispatch_ok: true,
  canary_dispatch_reason: 'healthy' as const,
  canary_dispatch_heartbeat_at: '2026-08-27T11:59:58Z',
  canary_dispatch_age_seconds: 2,
  probes: [
    {
      probe: 'ping' as const,
      quality: 'complete' as const,
      responder_count: 1,
      observed_responder_count: 1,
      responses_truncated: false,
      missing_responder_count: 0,
      invalid_response_count: 0,
      duration_ms: 8,
    },
  ],
  workers: [
    {
      name: 'worker@runtime',
      queues: ['ingest', 'processing'],
      responded_to: ['ping' as const],
      missing_responses: [],
      ping_ok: true,
      capacity: 4,
      active_count: 2,
      reserved_count: 1,
      scheduled_count: 0,
      processed_total: 431,
      uptime_seconds: 3_600,
      saturated: false,
    },
  ],
  queues: [
    {
      key: 'default',
      label: 'Default tasks',
      service_hint: 'worker',
      required: false,
      status: 'unknown' as const,
      consumers: ['worker@runtime'],
      consumer_count: 1,
      capacity: 4,
      active_count: 2,
      reserved_count: 1,
      scheduled_count: 0,
      saturated: false,
      execution_reason: 'missing' as const,
      execution_heartbeat_at: null,
      execution_age_seconds: null,
      execution_worker: null,
    },
    {
      key: 'ingest',
      label: 'Feed ingestion',
      service_hint: 'worker',
      required: true,
      status: 'healthy' as const,
      consumers: ['worker@runtime'],
      consumer_count: 1,
      capacity: 4,
      active_count: 2,
      reserved_count: 1,
      scheduled_count: 0,
      saturated: false,
      execution_reason: 'fresh' as const,
      execution_heartbeat_at: '2026-08-27T11:59:55Z',
      execution_age_seconds: 5,
      execution_worker: 'worker@runtime',
    },
    {
      key: 'processing',
      label: 'Item processing',
      service_hint: 'worker',
      required: true,
      status: 'degraded' as const,
      consumers: ['worker@runtime'],
      consumer_count: 1,
      capacity: 4,
      active_count: 2,
      reserved_count: 1,
      scheduled_count: 0,
      saturated: false,
      execution_reason: 'stale' as const,
      execution_heartbeat_at: '2026-08-27T11:50:00Z',
      execution_age_seconds: 600,
      execution_worker: 'worker@runtime',
    },
  ],
}

const healthHistory = {
  generated_at: '2026-08-27T12:00:00Z',
  window: '24h' as const,
  sample_interval_seconds: 300,
  effective_resolution_seconds: 300,
  downsampling_strategy: 'none' as const,
  retention_days: 30,
  coverage: {
    requested_start: '2026-08-26T12:00:00Z',
    requested_end: '2026-08-27T12:00:00Z',
    first_sample_at: '2026-08-27T10:55:00Z',
    last_sample_at: '2026-08-27T12:00:00Z',
    expected_sample_count: 289,
    actual_sample_count: 2,
    returned_sample_count: 2,
    missing_sample_count: 287,
    coverage_percent: 0.7,
    gap_count: 2,
    largest_gap_seconds: 82_500,
    collection_stale: false,
    source_anomaly_sample_count: 1,
    returned_anomaly_sample_count: 1,
    source_transition_count: 1,
    returned_transition_count: 1,
    anomaly_evidence_truncated: false,
    transition_evidence_truncated: false,
    gap_intervals: [
      {
        start_at: '2026-08-26T12:00:00Z',
        end_at: '2026-08-27T10:55:00Z',
        duration_seconds: 82_500,
        kind: 'leading' as const,
      },
      {
        start_at: '2026-08-27T10:55:00Z',
        end_at: '2026-08-27T12:00:00Z',
        duration_seconds: 3_900,
        kind: 'internal' as const,
      },
    ],
    returned_gap_interval_count: 2,
    gap_intervals_truncated: false,
  },
  samples: [
    {
      sampled_at: '2026-08-27T10:55:00Z',
      overall_status: 'healthy' as const,
      component_statuses: { 'component:workers': 'healthy' as const },
      worker_status: 'healthy' as const,
      worker_reason: 'healthy' as const,
      responding_worker_count: 1,
      observed_worker_count: 1,
      worker_inventory_truncated: false,
      total_capacity: 4,
      active_count: 1,
      reserved_count: 0,
      scheduled_count: 0,
      missing_queues: [],
      stale_execution_queues: [],
      backlog_pending_count: 0,
      backlog_stale_count: 0,
      critical_issue_count: 0,
      warning_issue_count: 0,
      issue_codes: [],
    },
    {
      sampled_at: '2026-08-27T12:00:00Z',
      overall_status: 'degraded' as const,
      component_statuses: {
        'component:workers': 'degraded' as const,
        'component:database': 'critical' as const,
        'component:scheduler': 'unavailable' as const,
        'storage:application_filesystem': 'degraded' as const,
        'backlog:reports': 'degraded' as const,
      },
      worker_status: 'degraded' as const,
      worker_reason: 'execution_stalled' as const,
      responding_worker_count: 1,
      observed_worker_count: 1,
      worker_inventory_truncated: false,
      total_capacity: 4,
      active_count: 2,
      reserved_count: 1,
      scheduled_count: 0,
      missing_queues: [],
      stale_execution_queues: ['processing'],
      backlog_pending_count: 2,
      backlog_stale_count: 1,
      critical_issue_count: 0,
      warning_issue_count: 1,
      issue_codes: ['reports_stale'],
    },
  ],
}

vi.mock('@tanstack/react-query', () => ({
  useQuery: (options: { queryKey: unknown[]; refetchInterval?: number | false }) => {
    operationsDomMocks.queryOptions.push(options)
    if (options.queryKey[1] === 'overview') {
      return {
        data: operationsDomMocks.overviewAvailable ? overview : undefined,
        isLoading: !operationsDomMocks.overviewAvailable && !operationsDomMocks.overviewError,
        isError: Boolean(operationsDomMocks.overviewError),
        error: operationsDomMocks.overviewError,
        isFetching: false,
        dataUpdatedAt: operationsDomMocks.overviewAvailable
          ? Date.parse('2026-08-27T12:00:01Z')
          : 0,
        refetch: operationsDomMocks.overviewRefetch,
      }
    }
    if (options.queryKey[1] === 'workers') {
      return {
        data: workerTopology,
        isLoading: false,
        isFetching: operationsDomMocks.workersFetching,
        isError: Boolean(operationsDomMocks.workersError),
        error: operationsDomMocks.workersError,
        refetch: operationsDomMocks.workersRefetch,
      }
    }
    if (options.queryKey[1] === 'health-history') {
      return {
        data: healthHistory,
        isLoading: false,
        isFetching: operationsDomMocks.historyFetching,
        isError: Boolean(operationsDomMocks.historyError),
        error: operationsDomMocks.historyError,
        refetch: operationsDomMocks.historyRefetch,
      }
    }
    const page = Number(options.queryKey[2])
    const filteredQuery = Boolean(options.queryKey[3] || options.queryKey[4])
    const loadingFilteredQuery = operationsDomMocks.runsLoadingForFilteredQuery && filteredQuery
    return {
      data: loadingFilteredQuery ? undefined : {
        runs: [run],
        total: operationsDomMocks.runsTotal,
        page,
        page_size: 20,
      },
      isLoading: loadingFilteredQuery,
      isFetching: loadingFilteredQuery || operationsDomMocks.runsFetching,
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
        schema_version: 2,
        generated_at: '2026-08-27T12:00:00Z',
        overview,
        recent_runs: [run],
        recent_runs_truncated: false,
        worker_topology: workerTopology,
        health_history: healthHistory,
      })
    }),
    isPending: false,
  }),
}))

import { OperationsPage } from './OperationsPage'

let root: Root | null = null
let container: HTMLDivElement | null = null

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-08-27T12:00:30Z'))
})

function renderPage(initialEntry = '/settings/operations') {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => {
    root?.render(
      <MemoryRouter initialEntries={[initialEntry]}>
        <OperationsPage />
      </MemoryRouter>,
    )
  })
  return container
}

afterEach(() => {
  act(() => root?.unmount())
  root = null
  container?.remove()
  container = null
  document.body.innerHTML = ''
  operationsDomMocks.overviewAvailable = true
  operationsDomMocks.overviewError = null
  operationsDomMocks.overviewRefetch.mockReset()
  operationsDomMocks.workersError = null
  operationsDomMocks.workersFetching = false
  operationsDomMocks.workersRefetch.mockReset()
  operationsDomMocks.historyError = null
  operationsDomMocks.historyFetching = false
  operationsDomMocks.historyRefetch.mockReset()
  operationsDomMocks.runsError = null
  operationsDomMocks.runsFetching = false
  operationsDomMocks.runsLoadingForFilteredQuery = false
  operationsDomMocks.runsTotal = 1
  operationsDomMocks.runsRefetch.mockReset()
  operationsDomMocks.diagnosticsError = null
  operationsDomMocks.diagnosticsRequested.mockReset()
  operationsDomMocks.queryOptions.length = 0
  Object.assign(workerTopology, {
    status: 'degraded' as const,
    reason: 'execution_stalled' as const,
    responding_worker_count: 1,
    observed_worker_count: 1,
    worker_inventory_truncated: false,
    canary_dispatch_ok: true,
    canary_dispatch_reason: 'healthy' as const,
    canary_dispatch_heartbeat_at: '2026-08-27T11:59:58Z',
    canary_dispatch_age_seconds: 2,
    probes: [{
      probe: 'ping' as const,
      quality: 'complete' as const,
      responder_count: 1,
      observed_responder_count: 1,
      responses_truncated: false,
      missing_responder_count: 0,
      invalid_response_count: 0,
      duration_ms: 8,
    }],
  })
  vi.restoreAllMocks()
  vi.useRealTimers()
})

describe('OperationsPage DOM workflows', () => {
  it('turns an active finding into evidence and worker troubleshooting', () => {
    const view = renderPage()

    expect(view.textContent).toContain('ThreatLens 1.7.0')
    expect(view.textContent).toContain('PostgreSQL')
    expect(view.textContent).toContain('Report generation')
    expect(view.textContent).toContain('Scheduled reporting may be delayed.')
    expect(view.textContent).toContain('Check the AI report worker queue.')
    expect(view.querySelector('[aria-label="Health dimensions"]')).not.toBeNull()
    expect(view.querySelector('h1')?.textContent).toBe('System health')
    expect(view.firstElementChild?.className).toContain('space-y-3')
    const workers = Array.from(view.querySelectorAll<HTMLButtonElement>('button')).find(
      (button) => button.textContent?.includes('Workers') && button.closest('li'),
    )
    expect(workers?.getAttribute('role')).toBeNull()
    act(() => workers?.click())

    expect(view.textContent).toContain('One or more queue execution paths are stale')
    expect(view.textContent).toContain('Item processing')
    expect(view.textContent).toContain('1/2 required healthy')
    expect(view.textContent).toContain('Optional')
    expect(view.textContent).toContain('10m ago on worker@runtime')
    expect(view.textContent).toContain('Narrow down the failure')
    expect(view.textContent).toContain('docker compose logs --since 15m --tail 200 worker')
    expect(view.textContent).toContain('Responding worker nodes')
    const healthyStatus = view.querySelector('[data-status="healthy"]')
    expect(healthyStatus?.className).toContain('tl-chip-success')
    expect(healthyStatus?.querySelector('svg[aria-hidden="true"]')).not.toBeNull()
    expect(Array.from(view.querySelectorAll('th')).every((heading) => heading.getAttribute('scope') === 'col')).toBe(true)
  })

  it('keeps the last successful snapshot visible when refresh fails', () => {
    operationsDomMocks.overviewError = new Error('probe timed out')
    const view = renderPage()

    const alert = view.querySelector('[role="alert"]')
    expect(alert?.textContent).toContain('This is the last successful snapshot')
    expect(view.textContent).toContain('PostgreSQL')
    expect(view.textContent).toContain('Last known · Degraded')
  })

  it('distinguishes a stopped canary dispatcher from a worker-side stall', () => {
    Object.assign(workerTopology, {
      reason: 'canary_dispatch_unavailable' as const,
      canary_dispatch_ok: false,
      canary_dispatch_reason: 'stale' as const,
      canary_dispatch_heartbeat_at: '2026-08-27T11:50:00Z',
      canary_dispatch_age_seconds: 600,
    })
    const view = renderPage()
    const workers = Array.from(view.querySelectorAll<HTMLButtonElement>('button')).find(
      (button) => button.textContent?.includes('Workers') && button.closest('li'),
    )
    act(() => workers?.click())

    expect(view.textContent).toContain('Queue execution canaries are not being dispatched')
    expect(view.textContent).toContain('Stale · 10m ago')
    expect(view.textContent).toContain('docker compose ps beat')
    expect(view.textContent).not.toContain('One or more queue execution paths are stale')
  })

  it('labels bounded worker inventory and aggregate evidence as partial', () => {
    Object.assign(workerTopology, {
      responding_worker_count: 64,
      observed_worker_count: 65,
      worker_inventory_truncated: true,
      probes: [{
        ...workerTopology.probes[0],
        quality: 'partial' as const,
        responder_count: 64,
        observed_responder_count: 65,
        responses_truncated: true,
      }],
    })
    const view = renderPage()
    const workers = Array.from(view.querySelectorAll<HTMLButtonElement>('button')).find(
      (button) => button.textContent?.includes('Workers') && button.closest('li'),
    )
    act(() => workers?.click())

    expect(view.textContent).toContain('Worker inventory limited')
    expect(view.textContent).toContain('64/65')
    expect(view.textContent).toContain('Capacity, load, and queue coverage are partial')
    expect(view.textContent).toContain('64 shown of 65 observed')
  })

  it('offers an explicit retry when no operations snapshot could be loaded', () => {
    operationsDomMocks.overviewAvailable = false
    operationsDomMocks.overviewError = new Error('probe timed out')
    const view = renderPage()

    expect(view.textContent).toContain('Deployment health is unavailable.')
    const retry = Array.from(view.querySelectorAll<HTMLButtonElement>('button')).find(
      (button) => button.textContent === 'Retry system health',
    )
    expect(retry).not.toBeNull()
    act(() => retry?.click())
    expect(operationsDomMocks.overviewRefetch).toHaveBeenCalledOnce()
  })

  it('does not describe the initial operations load as a retry', () => {
    operationsDomMocks.overviewAvailable = false
    const view = renderPage()

    expect(view.textContent).toContain('Loading deployment health...')
    expect(view.textContent).toContain('Loading...')
    expect(view.textContent).not.toContain('Retrying...')
  })

  it('labels retained operation rows while updating selected history', () => {
    operationsDomMocks.runsFetching = true
    operationsDomMocks.runsError = new Error('history query timed out')
    const view = renderPage('/settings/operations?view=recovery')

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

  it('hides rows from the previous selection while changed filters load', () => {
    operationsDomMocks.runsLoadingForFilteredQuery = true
    const view = renderPage('/settings/operations?view=recovery')
    expect(view.textContent).toContain('Host cli')

    const typeFilter = view.querySelector<HTMLSelectElement>('select')
    act(() => {
      if (!typeFilter) return
      typeFilter.value = 'backup'
      typeFilter.dispatchEvent(new Event('change', { bubbles: true }))
    })

    expect(view.textContent).toContain('Loading operation history...')
    expect(view.textContent).not.toContain('Host cli')
  })

  it('offers retry while retaining the last loaded history after failure', () => {
    operationsDomMocks.runsError = new Error('history query timed out')
    const view = renderPage('/settings/operations?view=recovery')

    const retry = Array.from(
      view.querySelectorAll<HTMLButtonElement>('button'),
    ).find((button) => button.textContent === 'Retry history')
    expect(retry?.disabled).toBe(false)
    act(() => retry?.click())
    expect(operationsDomMocks.runsRefetch).toHaveBeenCalled()
    expect(view.textContent).toContain('Restore drill')
    expect(view.textContent).toContain('Operation history could not be loaded')
    expect(view.textContent).not.toContain('Recovery history could not be loaded')
  })

  it('keeps operation history available when the live overview fails', () => {
    operationsDomMocks.overviewAvailable = false
    operationsDomMocks.overviewError = new Error('live topology probe timed out')
    const view = renderPage('/settings/operations?view=recovery')

    expect(view.textContent).toContain('Activity')
    expect(view.textContent).not.toContain('Recovery evidence')
    expect(view.textContent).toContain('live topology probe timed out')
    expect(view.textContent).toContain('Operation history')
    expect(view.textContent).toContain('Restore drill')
  })

  it('clamps operation-history pagination when the refreshed total shrinks', () => {
    operationsDomMocks.runsTotal = 41
    const view = renderPage('/settings/operations?view=recovery')

    act(() => findButton(view, 'Next')?.click())
    expect(view.textContent).toContain('Page 2 of 3')

    operationsDomMocks.runsTotal = 20
    act(() => findButton(view, 'Next')?.click())
    expect(view.textContent).toContain('Page 1 of 1')
    expect([...operationsDomMocks.queryOptions].reverse().find(
      (options) => options.queryKey[1] === 'runs',
    )?.queryKey[2]).toBe(1)
  })

  it('shows retained health transitions, coverage gaps, and actionable historical findings', () => {
    const view = renderPage('/settings/operations?view=trends&range=24h')

    expect(view.textContent).toContain('Observed health history')
    expect(view.textContent).toContain('0.7%')
    expect(view.textContent).toContain('Status transitions')
    expect(view.textContent).toContain('Worker capacity and load')
    expect(view.textContent).toContain('Historical findings')
    expect(view.textContent).toContain('Database: Critical')
    expect(view.textContent).toContain('Scheduler: Unavailable')
    expect(view.textContent).toContain('Application filesystem storage: Degraded')
    expect(view.textContent).toContain('Reports backlog: Degraded')
    expect(view.textContent).toContain('Findings: Reports stale')
    expect(view.textContent).toContain('Worker exceptions')
    expect(view.textContent).toContain('Stale execution: processing')
    expect(view.querySelectorAll('svg[role="img"]').length).toBeGreaterThan(0)
    const timeline = view.querySelector('[aria-label^="Observed status timeline"]')
    const segments = timeline?.querySelectorAll<HTMLElement>('[data-observed-status]') ?? []
    expect(segments).toHaveLength(2)
    expect(Number.parseFloat(segments[0].style.left)).toBeGreaterThan(90)
    expect(Number.parseFloat(segments[0].style.width)).toBeLessThan(1)
    expect(
      Number.parseFloat(segments[1].style.left) -
      Number.parseFloat(segments[0].style.left) -
      Number.parseFloat(segments[0].style.width),
    ).toBeGreaterThan(1)
    expect(view.querySelectorAll('[data-series-segment]').length).toBe(0)
    expect(view.textContent).toContain('Unobserved')
  })

  it('refreshes and auto-refreshes the dataset for the active operations view', () => {
    const view = renderPage('/settings/operations?view=trends&range=24h')
    const historyOptions = operationsDomMocks.queryOptions.find(
      (options) => options.queryKey[1] === 'health-history',
    )
    expect(historyOptions?.refetchInterval).toBe(300_000)
    expect(operationsDomMocks.queryOptions.find(
      (options) => options.queryKey[1] === 'overview',
    )?.refetchInterval).toBe(false)

    act(() => findButton(view, 'Refresh')?.click())
    expect(operationsDomMocks.overviewRefetch).toHaveBeenCalledOnce()
    expect(operationsDomMocks.historyRefetch).toHaveBeenCalledOnce()

    act(() => findButton(view, 'Activity')?.click())
    const runsOptions = [...operationsDomMocks.queryOptions].reverse().find(
      (options) => options.queryKey[1] === 'runs',
    )
    expect(runsOptions?.refetchInterval).toBe(30_000)
  })

  it('keeps retained trends available when the live overview cannot load', () => {
    operationsDomMocks.overviewAvailable = false
    operationsDomMocks.overviewError = new Error('live probe unavailable')

    const view = renderPage('/settings/operations?view=trends&range=24h')

    expect(view.textContent).toContain('Deployment health is unavailable.')
    expect(view.textContent).toContain('Observed health history')
    expect(view.textContent).toContain('Status transitions')
    expect(view.textContent).not.toContain('Retry system health')
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

function findButton(view: HTMLElement, label: string) {
  return Array.from(view.querySelectorAll<HTMLButtonElement>('button')).find(
    (button) => button.textContent?.trim() === label,
  ) ?? null
}
