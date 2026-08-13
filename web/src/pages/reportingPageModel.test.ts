import { describe, expect, it } from 'vitest'

import {
  DEFAULT_REPORT_PROMPT,
  DEFAULT_REPORT_SECTIONS,
  createDefaultExportFilterDraftForReports,
  parseListInput,
  reportPeriodFromFilters,
  validateReportBuilder,
} from './reportingPageModel'

describe('reportingPageModel', () => {
  it('uses an actual seven-day source window by default', () => {
    const draft = createDefaultExportFilterDraftForReports(new Date(2026, 7, 13, 12))

    expect(draft.datePreset).toBe('7')
    expect(draft.sinceDate).toBe('2026-08-07')
    expect(draft.untilDate).toBe('2026-08-13')
  })

  it('deduplicates prompted topic lists while preserving order', () => {
    expect(parseListInput('identity, edge\nidentity, malware')).toEqual([
      'identity',
      'edge',
      'malware',
    ])
  })

  it('blocks empty objectives and reports with no enabled sections', () => {
    const validation = validateReportBuilder(
      createDefaultExportFilterDraftForReports(new Date(2026, 7, 13, 12)),
      { ...DEFAULT_REPORT_PROMPT, objective: ' ' },
      DEFAULT_REPORT_SECTIONS.map((section) => ({ ...section, enabled: false })),
    )

    expect(validation.errors).toContain('Report objective is required.')
    expect(validation.errors).toContain('Enable at least one report section.')
  })

  it('derives a stable report period from validated filters', () => {
    expect(reportPeriodFromFilters({
      q: null,
      feed_ids: [],
      tag_ids: [],
      tags_mode: 'any',
      classifications: [],
      ai_relevance_labels: [],
      ai_score_min: null,
      ai_score_max: null,
      is_read: null,
      is_starred: null,
      has_article_text: null,
      since: '2026-08-01T00:00:00.000Z',
      until: '2026-08-08T00:00:00.000Z',
      date_basis: 'published_at_or_first_seen_at',
      sort: 'published_at_desc',
    })).toEqual({
      period_start: '2026-08-01T00:00:00.000Z',
      period_end: '2026-08-08T00:00:00.000Z',
    })
  })
})
