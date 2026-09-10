import type { ProcessingRun, ProcessingWork } from '../types/processing'

export const processingItemId = '10000000-0000-4000-8000-000000000001'
export const processingFeedId = '20000000-0000-4000-8000-000000000001'
export const processingRunId = '30000000-0000-4000-8000-000000000001'

export function processingWorkFixture(patch: Partial<ProcessingWork> = {}): ProcessingWork {
  return {
    item_id: processingItemId, stage: 'tagging', revision: 'work-revision-1', title: 'Gateway advisory',
    feed_id: processingFeedId, feed_name: 'Security advisories', state: 'attention',
    reason: 'invalid_pattern', message: 'Correct the rule before retrying.',
    first_seen_at: '2026-09-10T10:00:00Z', age_seconds: 300, attempts: 2, next_retry_at: null, can_retry: true,
    ...patch,
  }
}

export function processingRunFixture(patch: Partial<ProcessingRun> = {}): ProcessingRun {
  return {
    id: processingRunId, version: 1, status: 'queued', created_at: '2026-09-10T10:05:00Z', updated_at: '2026-09-10T10:05:00Z',
    total_count: 1, completed_count: 0, failed_count: 0, cancelled_count: 0, can_cancel: true,
    items: [{ item_id: processingItemId, stage: 'tagging', title: 'Gateway advisory', feed_name: 'Security advisories', state: 'queued', reason: null, message: null }],
    ...patch,
  }
}
