import { describe, expect, it } from 'vitest'

import { ApiError } from '../api/client'
import type { InvestigationDetail, InvestigationMember } from '../types/investigations'
import {
  buildInvestigationListPath,
  canEditInvestigationNote,
  formatInvestigationActivityAction,
  isAlertOccurrenceUnavailable,
  isFinalInvestigationOwner,
  isInvestigationVersionConflict,
  readInvestigationListFilters,
  readInvestigationTab,
  resolveInvestigationAccess,
  safeInvestigationExternalUrl,
  writeInvestigationListFilters,
} from './investigationPageModel'

const detail = {
  status: 'open',
  current_user_role: 'owner',
} as InvestigationDetail

describe('investigation page model', () => {
  it('normalizes URL filters and builds the repeated-query API contract', () => {
    const filters = readInvestigationListFilters(new URLSearchParams(
      'q=exchange&status=open&status=open&status=invalid&severity=critical&mine=1&archived=1&page=3',
    ))

    expect(filters).toEqual({
      query: 'exchange',
      statuses: ['open'],
      severities: ['critical'],
      assignedToMe: true,
      includeArchived: true,
      page: 3,
    })
    expect(buildInvestigationListPath(filters)).toBe(
      '/investigations?page=3&page_size=25&q=exchange&statuses=open&severities=critical&assigned_to_me=true&include_archived=true',
    )
    expect(writeInvestigationListFilters(filters).toString()).toBe(
      'q=exchange&status=open&severity=critical&mine=1&archived=1&page=3',
    )
  })

  it('defaults malformed pages and unsupported detail tabs safely', () => {
    expect(readInvestigationListFilters(new URLSearchParams('page=-8')).page).toBe(1)
    expect(readInvestigationTab(new URLSearchParams('tab=secrets'))).toBe('overview')
    expect(readInvestigationTab(new URLSearchParams('tab=activity'))).toBe('activity')
  })

  it('keeps the archived status filter independent from the include-archived toggle', () => {
    const filters = readInvestigationListFilters(new URLSearchParams('status=archived'))

    expect(filters.includeArchived).toBe(false)
    expect(filters.statuses).toEqual(['archived'])
    expect(buildInvestigationListPath(filters)).toContain('include_archived=true')
    expect(writeInvestigationListFilters(filters).toString()).toBe('status=archived')
  })

  it('keeps account role, membership, and archive authority separate', () => {
    expect(resolveInvestigationAccess(detail, 'analyst')).toMatchObject({
      canWrite: true,
      canManageMembers: true,
      canArchive: true,
    })
    expect(resolveInvestigationAccess({ ...detail, current_user_role: null }, 'admin')).toMatchObject({
      canWrite: false,
      readOnlyReason: 'This team-visible investigation is read-only until an owner adds you as a member.',
    })
    expect(resolveInvestigationAccess({ ...detail, current_user_role: 'viewer' }, 'analyst').canWrite).toBe(false)
    expect(resolveInvestigationAccess({ ...detail, current_user_role: 'editor' }, 'viewer').canWrite).toBe(false)
    expect(resolveInvestigationAccess({ ...detail, status: 'archived', current_user_role: 'editor' }, 'analyst')).toMatchObject({
      canWrite: false,
      canReopen: true,
    })
  })

  it('prevents final-owner changes and limits note edits to authors or owners', () => {
    const members = [
      { user_id: 'owner-1', role: 'owner' },
      { user_id: 'viewer-1', role: 'viewer' },
    ] as InvestigationMember[]
    expect(isFinalInvestigationOwner(members, 'owner-1')).toBe(true)
    expect(isFinalInvestigationOwner([...members, { user_id: 'owner-2', role: 'owner' } as InvestigationMember], 'owner-1')).toBe(false)
    expect(canEditInvestigationNote('analyst-1', 'analyst-1', 'editor')).toBe(true)
    expect(canEditInvestigationNote('analyst-1', 'analyst-2', 'editor')).toBe(false)
    expect(canEditInvestigationNote('analyst-1', 'analyst-2', 'owner')).toBe(true)
  })

  it('recognizes version conflicts and the explicit alert occurrence capability error', () => {
    expect(isInvestigationVersionConflict(new ApiError('changed', 409, '/investigations/1', null, {
      code: 'investigation_version_conflict',
    }))).toBe(true)
    expect(isInvestigationVersionConflict(new ApiError(
      'The investigation changed after you loaded it.',
      409,
      '/investigations/1',
    ))).toBe(true)
    expect(isInvestigationVersionConflict(new ApiError('already exists', 409, '/investigations/1'))).toBe(false)
    expect(isInvestigationVersionConflict(new ApiError('invalid', 422, '/investigations/1'))).toBe(false)
    expect(isAlertOccurrenceUnavailable(new ApiError(
      'Alert occurrence evidence is unavailable until durable Alerting v2 is enabled.',
      422,
      '/investigations/1/evidence',
    ))).toBe(true)
  })

  it('presents known and future activity actions in human language', () => {
    expect(formatInvestigationActivityAction('investigation.member_added')).toBe('Added an investigation member')
    expect(formatInvestigationActivityAction('investigation.note_removed')).toBe('Removed a note')
    expect(formatInvestigationActivityAction('investigation.custom_review_started')).toBe('Custom review started')
  })

  it('allows only absolute HTTP(S) evidence links', () => {
    expect(safeInvestigationExternalUrl('https://example.com/report?id=1')).toBe('https://example.com/report?id=1')
    expect(safeInvestigationExternalUrl('http://local.example/report')).toBe('http://local.example/report')
    expect(safeInvestigationExternalUrl('javascript:alert(1)')).toBeNull()
    expect(safeInvestigationExternalUrl('/relative/path')).toBeNull()
  })
})
