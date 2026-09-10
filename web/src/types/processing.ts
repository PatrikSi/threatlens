export type ProcessingStage = 'article' | 'classification' | 'ioc' | 'tagging'
export type ProcessingWorkState = 'pending' | 'queued' | 'running' | 'retry_wait' | 'attention'
export type ProcessingRunStatus = 'queued' | 'running' | 'succeeded' | 'partial' | 'cancelled' | 'failed'

export interface ProcessingWorkIdentity {
  item_id: string
  stage: ProcessingStage
  revision: string
}

export interface ProcessingWork extends ProcessingWorkIdentity {
  title: string
  feed_id: string
  feed_name: string
  state: ProcessingWorkState
  reason: string | null
  message: string | null
  first_seen_at: string
  age_seconds: number
  attempts: number
  next_retry_at: string | null
  can_retry: boolean
}

export interface ProcessingPage<T> {
  items: T[]
  next_cursor: string | null
  has_more: boolean
}

export interface ProcessingRecoveryRequest {
  idempotency_key: string
  items: ProcessingWorkIdentity[]
}

export interface ProcessingRunItem {
  item_id: string
  stage: ProcessingStage
  state: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'
  title: string | null
  feed_name: string | null
  reason: string | null
  message: string | null
}

export interface ProcessingRun {
  id: string
  version: number
  status: ProcessingRunStatus
  created_at: string
  updated_at: string
  total_count: number
  completed_count: number
  failed_count: number
  cancelled_count: number
  can_cancel: boolean
  access_limited?: boolean
  items: ProcessingRunItem[]
}
