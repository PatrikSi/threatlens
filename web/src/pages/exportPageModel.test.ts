import { describe, expect, it } from 'vitest'

import type { ArticleExportPreview } from '../types/api'
import {
  applyDatePreset,
  changeExportFormat,
  createDefaultExportFilterDraft,
  createDefaultExportOptions,
  defaultExportFilename,
  exportBlockingReason,
  formatByteSize,
  validateExportFilterDraft,
} from './exportPageModel'

const AVAILABLE_PREVIEW: ArticleExportPreview = {
  total_matches: 10,
  articles_with_text: 8,
  items_with_iocs: 4,
  preview_limit: 25,
  exceeds_export_limit: false,
  exceeds_pdf_limit: false,
  items: [],
}

describe('export page model', () => {
  it('defaults to an inclusive 30-day local date window', () => {
    const draft = createDefaultExportFilterDraft(new Date(2026, 7, 13, 14, 30))

    expect(draft.datePreset).toBe('30')
    expect(draft.sinceDate).toBe('2026-07-15')
    expect(draft.untilDate).toBe('2026-08-13')
  })

  it('applies date presets without overwriting a custom range', () => {
    const initial = createDefaultExportFilterDraft(new Date(2026, 7, 13))
    const sevenDays = applyDatePreset(initial, '7', new Date(2026, 7, 13))
    const custom = applyDatePreset({ ...sevenDays, sinceDate: '2026-01-02' }, 'custom')
    const allTime = applyDatePreset(custom, 'all')

    expect(sevenDays.sinceDate).toBe('2026-08-07')
    expect(custom.sinceDate).toBe('2026-01-02')
    expect(allTime).toMatchObject({ sinceDate: '', untilDate: '', datePreset: 'all' })
  })

  it('converts valid form values into API filters', () => {
    const draft = {
      ...createDefaultExportFilterDraft(new Date(2026, 7, 13)),
      q: '  ransomware  ',
      scoreMin: '0.25',
      scoreMax: '0.9',
      isRead: 'false' as const,
      isStarred: 'true' as const,
    }

    const result = validateExportFilterDraft(draft)

    expect(result.errors).toEqual([])
    expect(result.filters).toMatchObject({
      q: 'ransomware',
      ai_score_min: 0.25,
      ai_score_max: 0.9,
      is_read: false,
      is_starred: true,
      since: expect.stringMatching(/^2026-07-15T/),
      until: expect.stringMatching(/^2026-08-13T/),
    })
  })

  it('rejects invalid score and date ranges before previewing', () => {
    const draft = {
      ...createDefaultExportFilterDraft(new Date(2026, 7, 13)),
      scoreMin: '1.1',
      scoreMax: '-0.1',
      sinceDate: '2026-08-14',
      untilDate: '2026-08-13',
    }

    const result = validateExportFilterDraft(draft)

    expect(result.filters).toBeNull()
    expect(result.errors).toEqual([
      'Minimum AI score must be a number from 0 to 1.',
      'Maximum AI score must be a number from 0 to 1.',
      'Start date cannot be later than end date.',
    ])
  })

  it('loads format-specific defaults while preserving the filename prefix', () => {
    const csv = { ...createDefaultExportOptions('csv'), filename_prefix: 'research-weekly' }
    const jsonl = changeExportFormat('jsonl', csv)
    const pdf = changeExportFormat('pdf_bundle', jsonl)

    expect(jsonl).toMatchObject({ include_article_text: true, include_ioc_csv: false, filename_prefix: 'research-weekly' })
    expect(pdf).toMatchObject({ pdf_include_article_text: true, filename_prefix: 'research-weekly' })
  })

  it('blocks empty and oversized exports using the selected format limit', () => {
    expect(exportBlockingReason({ ...AVAILABLE_PREVIEW, total_matches: 0 }, 'csv', [])).toContain('No articles')
    expect(exportBlockingReason({ ...AVAILABLE_PREVIEW, exceeds_export_limit: true }, 'jsonl', [])).toContain('article limit')
    expect(exportBlockingReason({ ...AVAILABLE_PREVIEW, exceeds_pdf_limit: true }, 'pdf_bundle', [])).toContain('PDF bundle')
    expect(exportBlockingReason({ ...AVAILABLE_PREVIEW, exceeds_pdf_limit: true }, 'csv', [])).toBeNull()
  })

  it('builds safe fallback filenames and readable byte sizes', () => {
    expect(defaultExportFilename('stix', ' August / research ')).toBe('August-research.stix.json')
    expect(defaultExportFilename('pdf_bundle', null)).toBe('threatlens-export.pdf.zip')
    expect(formatByteSize(250 * 1024 * 1024)).toBe('250 MiB')
  })
})
