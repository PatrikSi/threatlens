import { apiFetch } from '../api/client'
import type {
  LifecycleOverviewResponse,
  LifecyclePolicy,
  LifecyclePolicyUpdateRequest,
  LifecyclePreview,
  LifecyclePreviewRequest,
  LifecycleRun,
  LifecycleRunCancelRequest,
  LifecycleRunListResponse,
  LifecycleRunRequest,
  LifecycleRunStatus,
  LifecycleRunTrigger,
} from '../types/lifecycle'
import { observeLifecyclePreview } from './lifecyclePreviewClock'

const JSON_HEADERS = { 'Content-Type': 'application/json' }

export async function loadLifecycleOverview(signal?: AbortSignal) {
  const requestStartedAt = Date.now()
  const overview = await apiFetch<LifecycleOverviewResponse>('/operations/lifecycle', {
    cache: 'no-store',
    signal,
  })
  for (const target of overview.targets) {
    if (target.latest_preview) {
      observeLifecyclePreview(target.latest_preview, requestStartedAt)
    }
  }
  return overview
}

export async function previewLifecyclePolicy(
  request: LifecyclePreviewRequest,
  signal?: AbortSignal,
) {
  const requestStartedAt = Date.now()
  const preview = await apiFetch<LifecyclePreview>('/operations/lifecycle/preview', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(request),
    cache: 'no-store',
    signal,
  })
  return observeLifecyclePreview(preview, requestStartedAt)
}

export function updateLifecyclePolicy(
  targetKey: string,
  request: LifecyclePolicyUpdateRequest,
  idempotencyKey = newLifecycleRequestKey('policy'),
) {
  return apiFetch<LifecyclePolicy>(
    `/operations/lifecycle/policies/${encodeURIComponent(targetKey)}`,
    {
      method: 'PUT',
      headers: {
        ...JSON_HEADERS,
        'Idempotency-Key': idempotencyKey,
      },
      body: JSON.stringify(request),
      cache: 'no-store',
    },
  )
}

export function loadLifecycleRuns({
  page,
  pageSize,
  targetKey,
  status,
  trigger,
  signal,
}: {
  page: number
  pageSize: number
  targetKey?: string
  status?: LifecycleRunStatus
  trigger?: LifecycleRunTrigger
  signal?: AbortSignal
}) {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
  })
  if (targetKey) params.set('target_key', targetKey)
  if (status) params.set('status', status)
  if (trigger) params.set('trigger_source', trigger)
  return apiFetch<LifecycleRunListResponse>(
    `/operations/lifecycle/runs?${params.toString()}`,
    { cache: 'no-store', signal },
  )
}

export function startLifecycleRun(
  request: LifecycleRunRequest,
  idempotencyKey = newLifecycleRequestKey('run'),
) {
  return apiFetch<LifecycleRun>('/operations/lifecycle/runs', {
    method: 'POST',
    headers: {
      ...JSON_HEADERS,
      'Idempotency-Key': idempotencyKey,
    },
    body: JSON.stringify(request),
    cache: 'no-store',
  })
}

export function cancelLifecycleRun(
  runId: string,
  request: LifecycleRunCancelRequest,
  idempotencyKey = newLifecycleRequestKey('cancel'),
) {
  return apiFetch<LifecycleRun>(
    `/operations/lifecycle/runs/${encodeURIComponent(runId)}/cancel`,
    {
      method: 'POST',
      headers: {
        ...JSON_HEADERS,
        'Idempotency-Key': idempotencyKey,
      },
      body: JSON.stringify(request),
      cache: 'no-store',
    },
  )
}

export function newLifecycleRequestKey(action: string): string {
  const random = typeof globalThis.crypto?.randomUUID === 'function'
    ? globalThis.crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`
  return `lifecycle-${action}-${random}`
}
