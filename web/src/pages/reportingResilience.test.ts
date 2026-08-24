import { describe, expect, it } from 'vitest'

import { ApiError, ApiTransportError } from '../api/client'
import {
  isAmbiguousReportingMutationError,
  reportResourceVersionHeader,
  reportPreviewErrorBlocksCreation,
  reportQueueFeedback,
  requireClonedReportingResource,
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
  const reportId = '11111111-1111-4111-8111-111111111111'
  const otherReportId = '22222222-2222-4222-8222-222222222222'
  const runId = '33333333-3333-4333-8333-333333333333'
  const scheduleId = '44444444-4444-4444-8444-444444444444'
  const otherScheduleId = '55555555-5555-4555-8555-555555555555'
  const templateId = '66666666-6666-4666-8666-666666666666'
  const cloneId = '77777777-7777-4777-8777-777777777777'
  const queueResponse = {
    report_id: reportId,
    task_run_id: runId,
    celery_task_id: null,
    status: 'queued',
    schedule_id: scheduleId,
  }

  it('accepts complete queue and resource confirmations', () => {
    expect(requireReportQueueResponse(queueResponse, '/reports')).toBe(queueResponse)
    expect(requireReportQueueResponseList(
      [queueResponse],
      `/reports/schedules/${scheduleId}/run`,
      scheduleId,
    )).toEqual([queueResponse])
    expect(requireReportQueueResponseList([], '/reports/schedules/1/run')).toEqual([])
    expect(requireReportingResource({ id: templateId }, '/reports/templates', 'template', 201)).toEqual({
      id: templateId,
    })
    expect(reportResourceVersionHeader('2026-08-24T09:30:00Z')).toBe('"2026-08-24T09:30:00Z"')
  })

  it.each([
    undefined,
    null,
    {},
    { ...queueResponse, report_id: '' },
    { ...queueResponse, report_id: 'not-a-uuid' },
    { ...queueResponse, task_run_id: 'not-a-uuid' },
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
    const error = (() => {
      try {
        requireReportingResource(undefined, '/reports/templates/1', 'template update', 200)
      } catch (caught) {
        return caught
      }
      return null
    })()
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status: 200, code: 'invalid_response' })
    expect(() => requireReportingResource(
      { id: 'not-a-uuid' },
      '/reports/templates',
      'template creation',
      201,
    )).toThrow(ApiError)
  })

  it('rejects valid-looking confirmations for a different resource', () => {
    expect(() => requireReportQueueResponse(
      { ...queueResponse, report_id: otherReportId },
      `/reports/${reportId}/retry`,
      reportId,
    )).toThrow('different report')
    expect(() => requireReportingResource(
      { id: otherScheduleId },
      `/reports/schedules/${scheduleId}`,
      'schedule update',
      200,
      scheduleId,
    )).toThrow('different resource')
    expect(() => requireReportQueueResponseList(
      [{ ...queueResponse, schedule_id: otherScheduleId }],
      `/reports/schedules/${scheduleId}/run`,
      scheduleId,
    )).toThrow('different report schedule')
  })

  it('rejects clone responses that identify the source template', () => {
    expect(requireClonedReportingResource(
      { id: cloneId },
      `/reports/templates/${templateId}/clone`,
      'report template clone',
      templateId,
    )).toEqual({ id: cloneId })
    expect(() => requireClonedReportingResource(
      { id: templateId },
      `/reports/templates/${templateId}/clone`,
      'report template clone',
      templateId,
    )).toThrow('source resource')
  })
})
