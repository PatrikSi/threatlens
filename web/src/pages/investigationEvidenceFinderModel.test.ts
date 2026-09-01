import { describe, expect, it } from 'vitest'

import type { InvestigationEvidenceCandidateListResponse } from '../types/investigations'
import {
  availableEvidenceSourceTypes,
  buildEvidenceCandidateRequest,
  candidateEvidenceSourceTypes,
  candidatePageCount,
  evidenceSourceAvailability,
  formatEvidenceCandidateMatchReason,
} from './investigationEvidenceFinderModel'

describe('investigation evidence finder model', () => {
  it('builds the scoped candidate contract with repeated stable source types', () => {
    expect(buildEvidenceCandidateRequest({
      investigationId: 'investigation/id',
      query: '  example.com  ',
      sourceTypes: ['item', 'ioc', 'alert_occurrence'],
      range: '7d',
      page: 2,
    })).toEqual({
      path: '/investigations/investigation%2Fid/evidence-candidates',
      body: {
        q: 'example.com',
        source_types: ['item', 'ioc', 'alert_occurrence'],
        range: '7d',
        page: 2,
        page_size: 20,
        as_of: null,
      },
    })
  })

  it('omits a blank query while preserving the default recent-evidence scope', () => {
    const request = buildEvidenceCandidateRequest({
      investigationId: 'investigation-1',
      query: '   ',
      sourceTypes: ['report'],
      range: '7d',
      page: 1,
    })

    expect(request.body).toMatchObject({
      q: null,
      range: '7d',
      source_types: ['report'],
    })
  })

  it('anchors later candidate pages to the first response timestamp', () => {
    const request = buildEvidenceCandidateRequest({
      investigationId: 'investigation-1',
      query: 'example.com',
      sourceTypes: ['item'],
      range: '24h',
      page: 2,
      asOf: '2026-09-01T12:00:00Z',
    })

    expect(request.body.as_of).toBe('2026-09-01T12:00:00Z')
  })

  it('filters sources with effective permission semantics before querying', () => {
    const writeOnly = evidenceSourceAvailability(
      ['write:items', 'write:reports', 'read:alerts'],
      undefined,
      false,
    )

    expect(availableEvidenceSourceTypes(writeOnly, 'all')).toEqual([
      'item',
      'ioc',
      'report',
      'alert_occurrence',
    ])
    expect(availableEvidenceSourceTypes(writeOnly, 'report')).toEqual(['report'])
    expect(availableEvidenceSourceTypes(writeOnly, 'item')).toEqual(['item'])
  })

  it('omits indicators only from automatic all-source suggestions', () => {
    const availability = evidenceSourceAvailability(['*:*'], undefined, false)

    expect(candidateEvidenceSourceTypes(availability, 'all', '')).toEqual([
      'item',
      'report',
      'alert_occurrence',
    ])
    expect(candidateEvidenceSourceTypes(availability, 'all', 'evil.example')).toEqual([
      'item',
      'ioc',
      'report',
      'alert_occurrence',
    ])
    expect(candidateEvidenceSourceTypes(availability, 'ioc', '')).toEqual(['ioc'])
  })

  it('locks unavailable sources without leaking them into candidate requests', () => {
    const availability = evidenceSourceAvailability(
      ['read:items'],
      [
        {
          source_type: 'ioc',
          available: false,
          unavailable_reason: 'Indicator search is rebuilding.',
          required_permissions: ['read:items'],
        },
      ],
      false,
    )

    expect(availableEvidenceSourceTypes(availability, 'all')).toEqual(['item'])
    expect(availability.find((entry) => entry.sourceType === 'ioc')).toMatchObject({
      available: false,
      reason: 'This evidence source is unavailable.',
    })
    expect(availability.find((entry) => entry.sourceType === 'report')?.reason).toContain(
      'View reports',
    )
    expect(availability.find((entry) => entry.sourceType === 'alert_occurrence')?.reason).toContain(
      'View alerts',
    )
  })

  it('requires both alert permissions and honors deployment capability failures', () => {
    expect(availableEvidenceSourceTypes(
      evidenceSourceAvailability(['read:alerts'], undefined, false),
      'alert_occurrence',
    )).toEqual([])
    expect(availableEvidenceSourceTypes(
      evidenceSourceAvailability(['*:*'], undefined, false),
      'alert_occurrence',
    )).toEqual(['alert_occurrence'])
    expect(availableEvidenceSourceTypes(
      evidenceSourceAvailability(['*:*'], undefined, true),
      'alert_occurrence',
    )).toEqual([])
  })

  it('formats candidate reasons and bounds pagination', () => {
    expect(formatEvidenceCandidateMatchReason('exact_ioc')).toBe('Exact indicator match')
    expect(formatEvidenceCandidateMatchReason('prefix')).toBe('Prefix match')
    expect(formatEvidenceCandidateMatchReason('future_reason')).toBe('Future reason')
    expect(formatEvidenceCandidateMatchReason(null)).toBeNull()
    expect(candidatePageCount({ total: 41, page: 1, page_size: 20 } as InvestigationEvidenceCandidateListResponse)).toBe(3)
    expect(candidatePageCount({ total: 2_000, page: 1, page_size: 20 } as InvestigationEvidenceCandidateListResponse)).toBe(20)
    expect(candidatePageCount(undefined)).toBe(1)
  })
})
