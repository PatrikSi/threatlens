import { ApiError, ApiTransportError } from '../api/client'

export const REPORT_PREVIEW_TIMEOUT_MS = 60_000
export const REPORT_CREATE_TIMEOUT_MS = 120_000

const RETRYABLE_CLIENT_STATUSES = new Set([408, 409, 425, 429])
const NON_BLOCKING_CLIENT_STATUSES = new Set([408, 425, 429])

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
