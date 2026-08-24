import { ApiError, ApiTransportError } from '../api/client'
import type { ReportQueueResponse } from '../types/api'

export const REPORT_PREVIEW_TIMEOUT_MS = 60_000
export const REPORT_CREATE_TIMEOUT_MS = 120_000

export type ReportQueueAction = 'create' | 'retry'
export type ReportQueueFeedback = {
  kind: 'error' | 'info' | 'success'
  message: string
}

const RETRYABLE_CLIENT_STATUSES = new Set([408, 409, 425, 429])
const NON_BLOCKING_CLIENT_STATUSES = new Set([408, 425, 429])

export function isAmbiguousReportingMutationError(error: unknown): boolean {
  return error instanceof ApiTransportError
    || (error instanceof ApiError && (error.status >= 500 || error.code === 'invalid_response'))
}

export function requireReportQueueResponse(
  value: unknown,
  path: string,
  expectedReportId?: string,
): ReportQueueResponse {
  if (!isReportQueueResponse(value)) {
    throw invalidReportingResponse(
      path,
      value,
      'The API accepted the report request but returned an incomplete queue confirmation.',
      202,
    )
  }
  if (expectedReportId && value.report_id !== expectedReportId) {
    throw invalidReportingResponse(
      path,
      value,
      'The API returned a queue confirmation for a different report.',
      202,
    )
  }
  return value
}

export function requireReportQueueResponseList(
  value: unknown,
  path: string,
): ReportQueueResponse[] {
  if (!Array.isArray(value) || !value.every(isReportQueueResponse)) {
    throw invalidReportingResponse(
      path,
      value,
      'The API accepted the schedule run but returned an incomplete queue confirmation.',
      202,
    )
  }
  return value
}

export function requireReportingResource<T extends { id: string }>(
  value: unknown,
  path: string,
  resourceLabel: string,
  responseStatus: 200 | 201,
  expectedResourceId?: string,
): T {
  if (!isRecord(value) || !isNonEmptyString(value.id)) {
    throw invalidReportingResponse(
      path,
      value,
      `The API accepted the ${resourceLabel} request but did not identify the saved resource.`,
      responseStatus,
    )
  }
  if (expectedResourceId && value.id !== expectedResourceId) {
    throw invalidReportingResponse(
      path,
      value,
      `The API returned a different resource after the ${resourceLabel} request.`,
      responseStatus,
    )
  }
  return value as T
}

export function shouldRetryReportPreview(failureCount: number, error: unknown): boolean {
  if (failureCount >= 1) return false
  if (error instanceof ApiTransportError) return error.kind === 'network'
  if (!(error instanceof ApiError)) return false
  return error.retryable || RETRYABLE_CLIENT_STATUSES.has(error.status)
}

export function reportPreviewErrorBlocksCreation(error: unknown): boolean {
  if (!(error instanceof ApiError)) return false
  return error.status >= 400 && error.status < 500 && !NON_BLOCKING_CLIENT_STATUSES.has(error.status)
}

export function reportQueueFeedback(
  action: ReportQueueAction,
  status: string,
): ReportQueueFeedback {
  const normalizedStatus = status.trim().toLowerCase()
  if (normalizedStatus === 'queued') {
    return {
      kind: 'success',
      message: action === 'retry'
        ? 'Report retry queued.'
        : 'Report queued. Progress and provider history are now available.',
    }
  }
  if (normalizedStatus === 'running') {
    return {
      kind: 'info',
      message: 'This report request was already accepted and generation is in progress. Opening its current status.',
    }
  }
  if (normalizedStatus === 'success' || normalizedStatus === 'ready') {
    return {
      kind: 'success',
      message: 'This report request has already completed. Opening the generated report.',
    }
  }
  if (normalizedStatus === 'error') {
    return {
      kind: 'error',
      message: 'This report request was already accepted, but generation failed. Opening the report for troubleshooting.',
    }
  }
  if (normalizedStatus === 'skipped' || normalizedStatus === 'canceled') {
    return {
      kind: 'info',
      message: `This report request was already ${normalizedStatus}. Opening the report details.`,
    }
  }
  return {
    kind: 'info',
    message: normalizedStatus
      ? `The server returned report task status "${normalizedStatus}". Opening the report details.`
      : 'The server accepted the report request. Opening the report details.',
  }
}

export function resolveReportCreateBlockedReason({
  canAuthor,
  reportingEnabled,
  aiConfigured,
  validationError,
  previewIsCurrent,
  previewIsFetching,
  previewError,
  selectedSourceCount,
}: {
  canAuthor: boolean
  reportingEnabled: boolean
  aiConfigured: boolean
  validationError: string | undefined
  previewIsCurrent: boolean
  previewIsFetching: boolean
  previewError: unknown
  selectedSourceCount: number | undefined
}): string | null {
  if (!canAuthor) return 'The analyst or administrator role is required to generate reports.'
  if (!reportingEnabled) return 'AI reporting is disabled in AI settings.'
  if (!aiConfigured) return 'Configure and test the AI provider before generating reports.'
  if (validationError) return validationError
  if (!previewIsCurrent || previewIsFetching) return 'Wait for the source and context estimate to update.'
  if (previewError && reportPreviewErrorBlocksCreation(previewError)) {
    return 'Resolve the context estimate error before generating.'
  }
  if (!previewError && !selectedSourceCount) {
    return 'No matching articles fit the current source and context guardrails.'
  }
  return null
}

function isReportQueueResponse(value: unknown): value is ReportQueueResponse {
  return isRecord(value)
    && isNonEmptyString(value.report_id)
    && isNonEmptyString(value.task_run_id)
    && isNonEmptyString(value.status)
    && (value.celery_task_id === null || typeof value.celery_task_id === 'string')
}

function invalidReportingResponse(
  path: string,
  value: unknown,
  message: string,
  status: number,
): ApiError {
  return new ApiError(message, status, path, value, {
    responseBody: value,
    code: 'invalid_response',
    retryable: true,
  })
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === 'string' && Boolean(value.trim())
}
