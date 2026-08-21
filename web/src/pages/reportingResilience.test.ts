import { describe, expect, it } from 'vitest'

import { ApiError, ApiTransportError } from '../api/client'
import {
  reportPreviewErrorBlocksCreation,
  resolveReportCreateBlockedReason,
  shouldRetryReportPreview,
} from './reportingResilience'

describe('report preview resilience', () => {
  it('does not retry or bypass actionable validation errors', () => {
    const error = new ApiError('The configured context is too small.', 422, '/reports/preview')

    expect(shouldRetryReportPreview(0, error)).toBe(false)
    expect(reportPreviewErrorBlocksCreation(error)).toBe(true)
  })

  it('retries one snapshot conflict but keeps the unresolved race blocking', () => {
    const error = new ApiError('Matching articles changed.', 409, '/reports/preview')

    expect(shouldRetryReportPreview(0, error)).toBe(true)
    expect(shouldRetryReportPreview(1, error)).toBe(false)
    expect(reportPreviewErrorBlocksCreation(error)).toBe(true)
  })

  it('allows a server-side planning failure to degrade without disabling generation', () => {
    const error = new ApiError('The API encountered an internal failure.', 503, '/reports/preview', null, {
      retryable: true,
    })

    expect(shouldRetryReportPreview(0, error)).toBe(true)
    expect(reportPreviewErrorBlocksCreation(error)).toBe(false)
  })

  it('does not duplicate expensive planning after a transport timeout', () => {
    const error = new ApiTransportError('The request timed out.', '/reports/preview', 'timeout')

    expect(shouldRetryReportPreview(0, error)).toBe(false)
    expect(shouldRetryReportPreview(1, error)).toBe(false)
    expect(reportPreviewErrorBlocksCreation(error)).toBe(false)
  })

  it('retries one connection failure because preview planning is side-effect free', () => {
    const error = new ApiTransportError('The API could not be reached.', '/reports/preview', 'network')

    expect(shouldRetryReportPreview(0, error)).toBe(true)
    expect(shouldRetryReportPreview(1, error)).toBe(false)
  })

  it('does not let a failed diagnostic estimate become a hard generation dependency', () => {
    const error = new ApiTransportError('The request timed out.', '/reports/preview', 'timeout')

    expect(resolveReportCreateBlockedReason({
      canAuthor: true,
      reportingEnabled: true,
      aiConfigured: true,
      validationError: undefined,
      previewIsCurrent: true,
      previewIsFetching: false,
      previewError: error,
      selectedSourceCount: undefined,
    })).toBeNull()
  })

  it('keeps a rejected context configuration blocking with a specific next step', () => {
    const error = new ApiError('The configured context is too small.', 422, '/reports/preview')

    expect(resolveReportCreateBlockedReason({
      canAuthor: true,
      reportingEnabled: true,
      aiConfigured: true,
      validationError: undefined,
      previewIsCurrent: true,
      previewIsFetching: false,
      previewError: error,
      selectedSourceCount: undefined,
    })).toBe('Resolve the context estimate error before generating.')
  })
})
