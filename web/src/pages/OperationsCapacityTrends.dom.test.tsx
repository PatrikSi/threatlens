// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it } from 'vitest'
import { operationsHistoryFixture, operationsSampleFixture } from '../testing/operationsHistoryFixtures'
import type { OperationsHealthHistoryResponse } from '../types/operations'
import { OperationsCapacityTrends } from './OperationsCapacityTrends'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
let root: Root
function render(history: OperationsHealthHistoryResponse) {
  const container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  act(() => root.render(<OperationsCapacityTrends history={history} />))
  return container
}
function select(value: string) {
  const select = document.querySelector('select')!
  act(() => { select.value = value; select.dispatchEvent(new Event('change', { bubbles: true })) })
}
afterEach(() => { act(() => root?.unmount()); document.body.replaceChildren() })

describe('historical freshness and runtime capacity', () => {
  it('keeps uninstrumented legacy observations unavailable instead of showing zero pressure', () => {
    const view = render(operationsHistoryFixture([operationsSampleFixture()]))
    expect(view.textContent).toContain('Numeric observations are unavailable')
    expect(view.textContent).toContain('Older observations may predate this instrumentation')
    select('memory')
    expect(view.textContent).toContain('not total deployment or worker memory')
    expect(view.querySelectorAll('[data-series-point]')).toHaveLength(0)
  })

  it('distinguishes a known empty backlog from unavailable stages and presents its threshold', () => {
    const view = render(operationsHistoryFixture([operationsSampleFixture({ backlogs: [{
      key: 'classification', label: 'Classification', status: 'healthy', pending_count: 0, active_count: 0,
      stale_count: 0, failed_count: 0, oldest_pending_age_seconds: null, degraded_after_seconds: 300,
    }] })]))
    expect(view.querySelectorAll('[data-series-point="classification"]')).toHaveLength(1)
    expect(view.querySelectorAll('[data-series-point="tagging"]')).toHaveLength(0)
    expect(view.textContent).toContain('No pending work')
    expect(view.textContent).toContain('Warning threshold')
    expect(view.textContent).toContain('5m')
  })

  it('changes comparable-unit views and does not bridge unknown collection gaps', () => {
    const history = operationsHistoryFixture([
      operationsSampleFixture({ runtime_metrics: { database_connections: 4, database_lock_waiters: 0, container_memory_percent: 70 } }),
      operationsSampleFixture({ sampled_at: '2026-09-10T10:05:00Z', runtime_metrics: { database_connections: 5, database_lock_waiters: 1, container_memory_percent: null } }),
      operationsSampleFixture({ sampled_at: '2026-09-10T10:10:00Z', runtime_metrics: { database_connections: 6, database_lock_waiters: 2, container_memory_percent: 85 } }),
    ])
    history.coverage.gap_intervals_truncated = true
    const view = render(history)
    select('database-counts')
    expect(view.textContent).toContain('does not cover other database roles')
    expect(view.querySelectorAll('[data-series-point="database_connections"]')).toHaveLength(3)
    expect(view.querySelectorAll('[data-series-segment]')).toHaveLength(0)
    select('memory')
    expect(view.querySelectorAll('[data-series-point="container_memory_percent"]')).toHaveLength(2)
    expect(view.textContent).toContain('85.0%')
    expect(view.textContent).toContain('Unavailable')
  })

  it('keeps incomplete counter groups unknown and explains overlapping bucket counts', () => {
    const view = render(operationsHistoryFixture([operationsSampleFixture({ runtime_metrics: {
      database_lock_timeout_last_15m: 1, database_statement_timeout_last_15m: null,
      outbound_deadline_last_15m: 2, export_transfer_deadline_last_15m: 3, export_generation_deadline_last_15m: 4,
    } })]))
    select('deadlines')
    expect(view.textContent).toContain('Overlapping samples must not be summed')
    expect(view.querySelectorAll('[data-series-point="database_events"]')).toHaveLength(0)
    const cells = [...view.querySelectorAll('details td')].map((cell) => cell.textContent)
    expect(cells.slice(1)).toEqual(['Unavailable', '2', '7'])
  })
})
