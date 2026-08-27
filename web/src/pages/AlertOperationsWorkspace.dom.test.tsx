// @vitest-environment jsdom

import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api/client'
import type { AlertEvaluationRequest } from '../types/alerts'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const operationsMocks = vi.hoisted(() => ({ controller: null as unknown as Record<string, unknown> }))

vi.mock('./useAlertOperationsController', () => ({
  useAlertOperationsController: () => operationsMocks.controller,
}))

import { AlertOperationsWorkspace } from './AlertOperationsWorkspace'

let root: Root | null = null
let container: HTMLDivElement | null = null

function evaluation(overrides: Partial<AlertEvaluationRequest> = {}): AlertEvaluationRequest {
  return {
    id: 'evaluation-1',
    item_id: 'item-1',
    item_content_hash: 'a'.repeat(64),
    state: 'dead_letter',
    source: 'live',
    active_source: 'live',
    notify: true,
    respect_rule_cutover: true,
    attempt_count: 5,
    max_attempts: 5,
    dispatch_attempt_count: 5,
    dispatch_failure_count: 2,
    version: 7,
    accepted_rule_count: 2,
    accepted_match_count: 1,
    degraded_owner_count: 0,
    degraded_owners_json: [],
    evaluated_rule_count: 0,
    occurrence_count: 0,
    backfill_count: 0,
    accepted_at: '2026-08-27T10:00:00Z',
    available_at: '2026-08-27T10:05:00Z',
    dispatch_claimed_at: null,
    last_dispatch_failed_at: '2026-08-27T10:04:00Z',
    claimed_at: null,
    lease_expires_at: null,
    completed_at: '2026-08-27T10:06:00Z',
    last_backfill_at: null,
    last_replayed_at: null,
    last_error_code: 'evaluation_worker_error',
    last_error_message: 'Alert evaluation failed unexpectedly.',
    created_at: '2026-08-27T10:00:00Z',
    updated_at: '2026-08-27T10:06:00Z',
    ...overrides,
  }
}

function createController() {
  const request = evaluation()
  const controller = {
    activityQuery: {
      data: {
        items: [{
          id: 'activity-1',
          request_id: request.id,
          actor_user_id: null,
          action: 'dead_lettered',
          details_json: {},
          created_at: request.updated_at,
        }],
        total: 1,
        page: 1,
        page_size: 50,
      },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    },
    activityPage: 1,
    changeStateFilter: vi.fn(),
    detailQuery: { data: request, isLoading: false, isError: false, error: null, refetch: vi.fn() },
    evaluationsQuery: {
      data: { items: [request], total: 1, page: 1, page_size: 25 },
      isLoading: false,
      isError: false,
      isFetching: false,
      error: null,
      refetch: vi.fn(),
    },
    feedback: null,
    metrics: { total: 14, open: 8, critical: 2, suppressed: 1 },
    metricsQuery: { data: { items: [], truncated: false }, isLoading: false, isError: false, isFetching: false, error: null, refetch: vi.fn() },
    page: 1,
    replay: { mutate: vi.fn(), isPending: false, isError: false, error: null },
    replayTarget: null as AlertEvaluationRequest | null,
    select: vi.fn(),
    selectedId: request.id,
    setFeedback: vi.fn(),
    setActivityPage: vi.fn(),
    setPage: vi.fn(),
    setReplayTarget: vi.fn((target: AlertEvaluationRequest | null) => { controller.replayTarget = target }),
    stateFilter: 'failures',
  }
  return controller
}

function renderWorkspace() {
  if (!container) {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
  }
  act(() => root?.render(<AlertOperationsWorkspace />))
  return container
}

afterEach(() => {
  act(() => root?.unmount())
  root = null
  container?.remove()
  container = null
  document.body.innerHTML = ''
})

describe('AlertOperationsWorkspace', () => {
  it('renders metrics and failure detail and confirms dead-letter replay', () => {
    const controller = createController()
    operationsMocks.controller = controller
    renderWorkspace()

    expect(document.body.textContent).toContain('Your occurrences (30 days)')
    expect(document.body.textContent).toContain('evaluation worker error')
    expect(document.body.textContent).toContain('Alert evaluation failed unexpectedly.')
    const queueButtons = Array.from(
      document.querySelectorAll<HTMLButtonElement>('button[aria-pressed="true"]'),
    )
    expect(queueButtons).toHaveLength(2)
    expect(queueButtons[0].getAttribute('aria-label')).toContain('evaluation-1')
    expect(queueButtons[0].getAttribute('aria-label')).toContain('dead letter')
    expect(queueButtons[0].getAttribute('aria-label')).not.toBe(
      queueButtons[1].getAttribute('aria-label'),
    )
    const detail = document.querySelector<HTMLElement>(
      '[aria-labelledby="alert-evaluation-detail-heading"]',
    )
    expect(detail?.getAttribute('aria-live')).toBe('polite')
    expect(document.activeElement).toBe(detail)

    const replayButton = Array.from(document.querySelectorAll('button')).find((button) => button.textContent === 'Replay')
    act(() => replayButton?.click())
    renderWorkspace()
    expect(document.querySelector('[role="alertdialog"]')).not.toBeNull()

    const confirmButton = Array.from(document.querySelectorAll('button')).find((button) => button.textContent === 'Replay evaluation')
    act(() => confirmButton?.click())
    expect(controller.replay.mutate).toHaveBeenCalledWith(controller.detailQuery.data)
  })

  it('renders a permission-aware load failure with a retry action', () => {
    const controller = createController()
    controller.evaluationsQuery.data = undefined as never
    controller.evaluationsQuery.isError = true
    controller.evaluationsQuery.error = new ApiError(
      'Alert evaluation operations require the administrator role.',
      403,
      '/alerts/occurrences/evaluations',
    ) as never
    operationsMocks.controller = controller
    renderWorkspace()

    expect(document.querySelector('[role="alert"]')?.textContent).toContain('administrator role')
    const retryButton = Array.from(document.querySelectorAll('button')).find((button) => button.textContent === 'Retry')
    act(() => retryButton?.click())
    expect(controller.evaluationsQuery.refetch).toHaveBeenCalled()
  })

  it('uses stacked, full-size pagination controls on narrow viewports', () => {
    const controller = createController()
    controller.evaluationsQuery.data = {
      ...controller.evaluationsQuery.data,
      total: 75,
      page: 2,
    }
    controller.activityQuery.data = {
      ...controller.activityQuery.data,
      total: 120,
      page: 2,
      page_size: 50,
    }
    operationsMocks.controller = controller
    renderWorkspace()

    const previous = Array.from(document.querySelectorAll<HTMLButtonElement>('button')).find(
      (button) => button.textContent === 'Previous',
    )
    const nextActivity = Array.from(document.querySelectorAll<HTMLButtonElement>('button')).find(
      (button) => button.textContent === 'Next activity',
    )
    expect(previous?.className).toContain('min-h-11')
    expect(previous?.className).toContain('sm:min-h-9')
    expect(previous?.parentElement?.className).toContain('grid-cols-2')
    expect(previous?.parentElement?.parentElement?.className).toContain('flex-col')
    expect(nextActivity?.className).toContain('min-h-11')
    expect(nextActivity?.parentElement?.className).toContain('grid-cols-2')
    expect(nextActivity?.parentElement?.parentElement?.className).toContain('sm:flex-row')
  })
})
