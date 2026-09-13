import { ApiError } from '../api/client'
import type { ProcessingStage, ProcessingWorkIdentity, ProcessingWorkState } from '../types/processing'

export const PROCESSING_STAGES: Array<{ value: ProcessingStage; label: string }> = [
  { value: 'article', label: 'Article retrieval' },
  { value: 'classification', label: 'Classification' },
  { value: 'ioc', label: 'Indicator extraction' },
  { value: 'tagging', label: 'Tagging' },
]
export const PROCESSING_STATES: Array<{ value: ProcessingWorkState; label: string }> = [
  { value: 'pending', label: 'Pending' },
  { value: 'queued', label: 'Queued' },
  { value: 'running', label: 'Running' },
  { value: 'retry_wait', label: 'Awaiting retry' },
  { value: 'attention', label: 'Needs attention' },
]
export const PROCESSING_PAGE_SIZE = 50
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

export function processingScope(params: URLSearchParams) {
  const stage = params.get('work_stage') ?? ''
  const state = params.get('work_state') ?? ''
  return {
    stage: PROCESSING_STAGES.some((entry) => entry.value === stage) ? stage : '',
    state: PROCESSING_STATES.some((entry) => entry.value === state) ? state : '',
    feed: validId(params.get('work_feed')),
    cursor: validCursor(params.get('work_cursor')),
    run: validId(params.get('work_run')),
    runCursor: validCursor(params.get('work_runs_cursor')),
  }
}

function validId(value: string | null) { return value && UUID.test(value) ? value : '' }
function validCursor(value: string | null) { return value && value.length <= 4096 ? value : '' }

export function processingWorkPath(scope: ReturnType<typeof processingScope>) {
  const params = new URLSearchParams({ limit: String(PROCESSING_PAGE_SIZE) })
  if (scope.stage) params.set('stage', scope.stage)
  if (scope.state) params.set('state', scope.state)
  if (scope.feed) params.set('feed_id', scope.feed)
  if (scope.cursor) params.set('cursor', scope.cursor)
  return `/processing/work?${params}`
}

export function processingWorkKey(work: Pick<ProcessingWorkIdentity, 'item_id' | 'stage'>) {
  return `${work.item_id}:${work.stage}`
}

export function processingStageLabel(stage: ProcessingStage) {
  return PROCESSING_STAGES.find((entry) => entry.value === stage)?.label ?? stage
}

export function processingStateLabel(state: string) {
  return PROCESSING_STATES.find((entry) => entry.value === state)?.label
    ?? ({ succeeded: 'Succeeded', partial: 'Partially completed', failed: 'Failed', cancelled: 'Cancelled' }[state])
    ?? state
}

export function processingAccessError(error: unknown) {
  return error instanceof ApiError && [401, 403, 404].includes(error.status)
}

export function processingConflict(error: unknown) {
  return error instanceof ApiError && [409, 412].includes(error.status)
}
