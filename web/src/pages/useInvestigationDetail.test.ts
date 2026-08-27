import { QueryClient } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiFetchMock = vi.hoisted(() => vi.fn())

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return { ...actual, apiFetch: apiFetchMock }
})

import type { InvestigationDetail } from '../types/investigations'
import { executeInvestigationMutation, type InvestigationMutationOperation } from './useInvestigationDetail'

const detailKey = ['investigations', 'detail', 'investigation-1'] as const
const detail = {
  id: 'investigation-1',
  version: 7,
  notes: [{ id: 'note-1', version: 3 }],
} as InvestigationDetail

describe('investigation mutation requests', () => {
  let queryClient: QueryClient

  beforeEach(() => {
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    queryClient.setQueryData(detailKey, detail)
    apiFetchMock.mockReset()
    apiFetchMock.mockResolvedValue(detail)
  })

  it.each<{
    operation: InvestigationMutationOperation
    path: string
    method: string
    body?: Record<string, unknown>
  }>([
    {
      operation: { kind: 'update', changes: { severity: 'critical' } },
      path: '/investigations/investigation-1',
      method: 'PATCH',
      body: { severity: 'critical', expected_version: 7 },
    },
    {
      operation: { kind: 'add-member', userId: 'user-2', role: 'editor' },
      path: '/investigations/investigation-1/members',
      method: 'POST',
      body: { user_id: 'user-2', role: 'editor', expected_version: 7 },
    },
    {
      operation: { kind: 'update-member', userId: 'user-2', role: 'viewer' },
      path: '/investigations/investigation-1/members/user-2',
      method: 'PATCH',
      body: { role: 'viewer', expected_version: 7 },
    },
    {
      operation: { kind: 'remove-member', userId: 'user-2' },
      path: '/investigations/investigation-1/members/user-2?expected_version=7',
      method: 'DELETE',
    },
    {
      operation: { kind: 'add-evidence', sourceType: 'ioc', sourceId: 'ioc-1', note: 'Correlated indicator' },
      path: '/investigations/investigation-1/evidence',
      method: 'POST',
      body: { source_type: 'ioc', source_id: 'ioc-1', note: 'Correlated indicator', expected_version: 7 },
    },
    {
      operation: { kind: 'remove-evidence', evidenceId: 'evidence-1' },
      path: '/investigations/investigation-1/evidence/evidence-1?expected_version=7',
      method: 'DELETE',
    },
    {
      operation: { kind: 'add-note', body: 'Working theory' },
      path: '/investigations/investigation-1/notes',
      method: 'POST',
      body: { body: 'Working theory', expected_version: 7 },
    },
    {
      operation: { kind: 'update-note', noteId: 'note-1', body: 'Revised theory' },
      path: '/investigations/investigation-1/notes/note-1',
      method: 'PATCH',
      body: { body: 'Revised theory', expected_note_version: 3, expected_investigation_version: 7 },
    },
    {
      operation: { kind: 'remove-note', noteId: 'note-1' },
      path: '/investigations/investigation-1/notes/note-1?expected_note_version=3&expected_investigation_version=7',
      method: 'DELETE',
    },
  ])('adds the current preconditions for $operation.kind', async ({ operation, path, method, body }) => {
    await executeInvestigationMutation(queryClient, detailKey, 'investigation-1', operation)

    expect(apiFetchMock).toHaveBeenCalledOnce()
    const [actualPath, options] = apiFetchMock.mock.calls[0]
    expect(actualPath).toBe(path)
    expect(options.method).toBe(method)
    expect(options.body ? JSON.parse(options.body) : undefined).toEqual(body)
  })

  it('reads a newly cached version at dispatch time instead of retaining a render-time version', async () => {
    queryClient.setQueryData(detailKey, { ...detail, version: 12 })

    await executeInvestigationMutation(queryClient, detailKey, 'investigation-1', {
      kind: 'add-note',
      body: 'Uses latest version',
    })

    expect(JSON.parse(apiFetchMock.mock.calls[0][1].body)).toMatchObject({ expected_version: 12 })
  })

  it('refuses to write when no current detail snapshot is available', async () => {
    queryClient.removeQueries({ queryKey: detailKey })

    await expect(executeInvestigationMutation(queryClient, detailKey, 'investigation-1', {
      kind: 'add-note',
      body: 'Unsafe write',
    })).rejects.toThrow('latest investigation version is unavailable')
    expect(apiFetchMock).not.toHaveBeenCalled()
  })
})
