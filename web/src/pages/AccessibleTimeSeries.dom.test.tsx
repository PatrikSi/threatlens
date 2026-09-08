// @vitest-environment jsdom

import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it } from 'vitest'

import type { OperationsHealthHistorySample } from '../types/operations'
import { AccessibleTimeSeries } from './AccessibleTimeSeries'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

let root: Root | null = null
let container: HTMLDivElement | null = null

afterEach(() => {
  act(() => root?.unmount())
  root = null
  container?.remove()
  container = null
})

describe('AccessibleTimeSeries', () => {
  it('positions a lone observation within the requested range and renders its marker', () => {
    const sample: OperationsHealthHistorySample = {
      sampled_at: '2026-08-27T11:00:00Z',
      overall_status: 'critical',
      component_statuses: { workers: 'critical' },
      worker_status: 'critical',
      worker_reason: 'execution_stalled',
      responding_worker_count: 1,
      observed_worker_count: 1,
      worker_inventory_truncated: false,
      total_capacity: 4,
      active_count: 4,
      reserved_count: 2,
      scheduled_count: 0,
      missing_queues: [],
      stale_execution_queues: ['processing'],
      backlog_pending_count: 2,
      backlog_stale_count: 1,
      critical_issue_count: 1,
      warning_issue_count: 0,
      issue_codes: ['worker_execution_stalled'],
    }
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)

    act(() => root?.render(
      <AccessibleTimeSeries
        title="Isolated incident"
        description="One observation after a long collection gap"
        samples={[sample]}
        resolutionSeconds={300}
        rangeStart="2026-08-26T12:00:00Z"
        rangeEnd="2026-08-27T12:00:00Z"
        series={[{
          key: 'critical',
          label: 'Critical findings',
          color: '#dc2626',
          value: (point) => point.critical_issue_count,
        }]}
      />,
    ))

    expect(container.querySelector('svg[role="img"]')).not.toBeNull()
    const marker = container.querySelector<SVGCircleElement>('[data-series-point="critical"]')
    expect(marker).not.toBeNull()
    expect(Number(marker?.getAttribute('cx'))).toBeGreaterThan(590)
    expect(container.textContent).not.toContain('At least two numeric observations')
  })
})
