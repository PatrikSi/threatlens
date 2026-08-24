import { describe, expect, it } from 'vitest'

import { ApiError, ApiTransportError } from '../api/client'
import {
  isAmbiguousReportingMutationError,
  reportPreviewErrorBlocksCreation,
  reportQueueFeedback,
  requireReportQueueResponse,
  requireReportQueueResponseList,
  requireReportingResource,
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

describe('report queue feedback', () => {
  it('reports queued create and retry operations precisely', () => {
    expect(reportQueueFeedback('create', 'queued')).toEqual({
      kind: 'success',
      message: 'Report queued. Progress and provider history are now available.',
    })
    expect(reportQueueFeedback('retry', 'queued')).toEqual({
      kind: 'success',
      message: 'Report retry queued.',
    })
  })

  it('does not describe idempotent running or failed responses as newly queued', () => {
    expect(reportQueueFeedback('create', 'running')).toMatchObject({
      kind: 'info',
      message: expect.stringContaining('already accepted'),
    })
    expect(reportQueueFeedback('retry', 'error')).toMatchObject({
      kind: 'error',
      message: expect.stringContaining('generation failed'),
    })
  })
})

describe('report mutation response validation', () => {
  const queueResponse = {
    report_id: 'report-1',
    task_run_id: 'run-1',
    celery_task_id: null,
    status: 'queued',
  }

  it('accepts complete queue and resource confirmations', () => {
    expect(requireReportQueueResponse(queueResponse, '/reports')).toBe(queueResponse)
    expect(requireReportQueueResponseList([queueResponse], '/reports/schedules/1/run')).toEqual([queueResponse])
    expect(requireReportQueueResponseList([], '/reports/schedules/1/run')).toEqual([])
    expect(requireReportingResource({ id: 'template-1' }, '/reports/templates', 'template')).toEqual({
      id: 'template-1',
    })
  })

  it.each([
    undefined,
    null,
    {},
    { ...queueResponse, report_id: '' },
    { ...queueResponse, celery_task_id: 42 },
  ])('rejects incomplete successful queue responses as ambiguous (%j)', (value) => {
    const error = (() => {
      try {
        requireReportQueueResponse(value, '/reports')
      } catch (caught) {
        return caught
      }
      return null
    })()

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ code: 'invalid_response', retryable: true })
    expect(isAmbiguousReportingMutationError(error)).toBe(true)
  })

  it('rejects malformed schedule arrays and resource confirmations', () => {
    expect(() => requireReportQueueResponseList([{}], '/reports/schedules/1/run')).toThrow(ApiError)
    expect(() => requireReportingResource(undefined, '/reports/templates', 'template')).toThrow(ApiError)
  })
})
