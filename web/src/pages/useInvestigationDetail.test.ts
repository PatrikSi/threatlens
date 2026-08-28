import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiFetchMock = vi.hoisted(() => vi.fn())

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return { ...actual, apiFetch: apiFetchMock }
})

import type { InvestigationDetail } from '../types/investigations'
import { executeInvestigationMutation, type InvestigationMutationOperation } from './useInvestigationDetail'

const detail = {
  id: 'investigation-1',
  version: 7,
  notes: [{ id: 'note-1', version: 3 }],
} as InvestigationDetail

describe('investigation mutation requests', () => {
  beforeEach(() => {
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
      operation: { kind: 'update', changes: { severity: 'critical' }, expectedVersion: 7 },
      path: '/investigations/investigation-1',
      method: 'PATCH',
      body: { severity: 'critical', expected_version: 7 },
    },
    {
      operation: { kind: 'add-member', userId: 'user-2', role: 'editor', expectedVersion: 7 },
      path: '/investigations/investigation-1/members',
      method: 'POST',
      body: { user_id: 'user-2', role: 'editor', expected_version: 7 },
    },
    {
      operation: { kind: 'update-member', userId: 'user-2', role: 'viewer', expectedVersion: 7 },
      path: '/investigations/investigation-1/members/user-2',
      method: 'PATCH',
      body: { role: 'viewer', expected_version: 7 },
    },
    {
      operation: { kind: 'remove-member', userId: 'user-2', expectedVersion: 7 },
      path: '/investigations/investigation-1/members/user-2?expected_version=7',
      method: 'DELETE',
    },
    {
      operation: { kind: 'add-evidence', sourceType: 'ioc', sourceId: 'ioc-1', note: 'Correlated indicator', expectedVersion: 7 },
      path: '/investigations/investigation-1/evidence',
      method: 'POST',
      body: { source_type: 'ioc', source_id: 'ioc-1', note: 'Correlated indicator', expected_version: 7 },
    },
    {
      operation: { kind: 'remove-evidence', evidenceId: 'evidence-1', expectedVersion: 7 },
      path: '/investigations/investigation-1/evidence/evidence-1?expected_version=7',
      method: 'DELETE',
    },
    {
      operation: { kind: 'add-note', body: 'Working theory', expectedVersion: 7 },
      path: '/investigations/investigation-1/notes',
      method: 'POST',
      body: { body: 'Working theory', expected_version: 7 },
    },
    {
      operation: { kind: 'update-note', noteId: 'note-1', noteVersion: 3, body: 'Revised theory', expectedVersion: 7 },
      path: '/investigations/investigation-1/notes/note-1',
      method: 'PATCH',
      body: { body: 'Revised theory', expected_note_version: 3, expected_investigation_version: 7 },
    },
    {
      operation: { kind: 'remove-note', noteId: 'note-1', noteVersion: 3, expectedVersion: 7 },
      path: '/investigations/investigation-1/notes/note-1?expected_note_version=3&expected_investigation_version=7',
      method: 'DELETE',
    },
  ])('adds the current preconditions for $operation.kind', async ({ operation, path, method, body }) => {
    await executeInvestigationMutation('investigation-1', operation)

    expect(apiFetchMock).toHaveBeenCalledOnce()
    const [actualPath, options] = apiFetchMock.mock.calls[0]
    expect(actualPath).toBe(path)
    expect(options.method).toBe(method)
    expect(options.body ? JSON.parse(options.body) : undefined).toEqual(body)
  })

  it('retains the version captured with the user intent', async () => {
    await executeInvestigationMutation('investigation-1', {
      kind: 'add-note',
      body: 'Uses draft baseline',
      expectedVersion: 7,
    })

    expect(JSON.parse(apiFetchMock.mock.calls[0][1].body)).toMatchObject({ expected_version: 7 })
  })

  it('refuses to write when the captured detail version is invalid', async () => {
    await expect(executeInvestigationMutation('investigation-1', {
      kind: 'add-note',
      body: 'Unsafe write',
      expectedVersion: 0,
    })).rejects.toThrow('draft investigation version is unavailable')
    expect(apiFetchMock).not.toHaveBeenCalled()
  })

  it('uses the paginated note version when the note is absent from the detail snapshot', async () => {
    await executeInvestigationMutation('investigation-1', {
      kind: 'update-note',
      noteId: 'older-note',
      noteVersion: 11,
      body: 'Updated historical context',
      expectedVersion: 7,
    })

    expect(JSON.parse(apiFetchMock.mock.calls[0][1].body)).toMatchObject({
      expected_note_version: 11,
      expected_investigation_version: 7,
    })
  })

  it('rejects a note write when the displayed page has no valid note version', async () => {
    await expect(
      executeInvestigationMutation('investigation-1', {
        kind: 'remove-note',
        noteId: 'note-1',
        noteVersion: 0,
        expectedVersion: 7,
      }),
    ).rejects.toThrow('displayed note version is unavailable')
    expect(apiFetchMock).not.toHaveBeenCalled()
  })
})
