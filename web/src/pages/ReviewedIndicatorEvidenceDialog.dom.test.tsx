// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import type { ReviewedIndicator } from '../types/indicatorPublications'
import type { IndicatorPage } from '../types/indicators'
import { ReviewedIndicatorEvidenceDialog } from './ReviewedIndicatorEvidenceDialog'
import { intelButton, mountIntel, settle } from './articleIntelligenceTestSupport'

vi.mock('../api/client', async (original) => ({ ...await original<typeof import('../api/client')>(), apiFetch: vi.fn() }))
;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
afterEach(() => { view?.close(); view = undefined; vi.resetAllMocks() })
const reviewed: ReviewedIndicator = {
  assessment_id: 'assessment', item_id: 'article', ioc_id: 'indicator', type: 'domain', value: 'evil.net',
  title: 'Reviewed source', assessment_version: 4, source_revision: 2, extraction_revision: 3,
  expires_at: null, evidence_count: 1,
}
const page: IndicatorPage = {
  items: [{ id: 'indicator', type: 'domain', value: 'evil.net', raw: 'evil[.]net', extraction_confidence: 1,
    evidence: [{ source: 'article', raw: 'evil[.]net', quote: 'A retained reviewed passage.', start: 10, end: 20, transformations: ['defanged'] }],
    ai: null, ai_current: false, excluded: false, exclusion_reasons: [], suppressed: false,
    assessment: { version: 4, verdict: 'malicious', reason: 'Analyst verified campaign infrastructure.',
      source_revision: 2, extraction_revision: 3, expires_at: null, expired: false, current: true, updated_at: '2026-09-27T00:00:00Z' } }],
  total: 1, page: 1, page_size: 1, source_revision: 2, extraction_revision: 3, extraction_current: true,
  can_review: true, can_manage_suppressions: false,
}
async function open() {
  view = await mountIntel(<ReviewedIndicatorEvidenceDialog teamId="team" previewFingerprint="preview"
    reviewed={reviewed} onClose={vi.fn()} onRefresh={vi.fn()} />)
}

it('fetches only the selected observation and displays matching evidence', async () => {
  vi.mocked(apiFetch).mockResolvedValue(page)
  await open()
  expect(apiFetch).toHaveBeenCalledTimes(1)
  const url = new URL(vi.mocked(apiFetch).mock.calls[0][0], 'https://threatlens.local')
  expect(url.pathname).toBe('/items/article/indicators')
  expect(Object.fromEntries(url.searchParams)).toEqual({ team_id: 'team', ioc_id: 'indicator', page_size: '1' })
  expect(document.querySelector('[role="dialog"]')?.textContent).toContain('A retained reviewed passage.')
  expect(document.querySelector('[role="dialog"]')?.textContent).toContain('Analyst verified campaign infrastructure.')
})

it('shows publication passages without including unpinned supplemental AI evidence', async () => {
  const current = structuredClone(page)
  current.items[0].ai = {
    role: 'malicious_infrastructure', assertion: 'inferred', maliciousness_confidence: null,
    evidence: [{ source: 'article', quote: 'A newer AI supporting passage outside this publication.' }],
  }
  current.items[0].ai_current = true
  vi.mocked(apiFetch).mockResolvedValue(current)
  await open()
  const text = document.querySelector('[role="dialog"]')?.textContent
  expect(text).toContain('A retained reviewed passage.')
  expect(text).toContain('Analyst verified campaign infrastructure.')
  expect(text).toContain('Supplemental AI assessments are outside this publication.')
  expect(text).not.toContain('A newer AI supporting passage outside this publication.')
  expect(text).not.toContain('AI role:')
})

it.each(['source', 'extraction', 'assessment', 'verdict', 'expired', 'stale', 'suppressed', 'excluded', 'missing'])(
  'withholds changed %s evidence and requests a new preview', async (change) => {
    const updated = structuredClone(page)
    if (change === 'source') updated.source_revision += 1
    if (change === 'extraction') updated.extraction_revision += 1
    if (change === 'assessment') updated.items[0].assessment!.version += 1
    if (change === 'verdict') updated.items[0].assessment!.verdict = 'reference'
    if (change === 'expired') updated.items[0].assessment!.expired = true
    if (change === 'stale') updated.extraction_current = false
    if (change === 'suppressed') updated.items[0].suppressed = true
    if (change === 'excluded') updated.items[0].excluded = true
    if (change === 'missing') updated.items = []
    vi.mocked(apiFetch).mockResolvedValue(updated)
    await open()
    const dialog = document.querySelector('[role="dialog"]')!
    expect(dialog.textContent).not.toContain('A retained reviewed passage.')
    expect(dialog.textContent).not.toContain('Analyst verified campaign infrastructure.')
    expect(intelButton(dialog, 'Refresh publication preview')).toBeTruthy()
  },
)

it('removes retained evidence after an explicit access denial and allows recovery', async () => {
  vi.mocked(apiFetch).mockResolvedValueOnce(page)
  await open()
  expect(document.body.textContent).toContain('A retained reviewed passage.')
  vi.mocked(apiFetch).mockRejectedValueOnce(new ApiError('Team access removed', 403, '/items/article/indicators'))
  await act(async () => { await view!.client.invalidateQueries({ queryKey: ['reviewed-indicator-evidence'] }) })
  await settle()
  expect(document.body.textContent).not.toContain('A retained reviewed passage.')
  expect(document.body.textContent).toContain('Team access removed')
  vi.mocked(apiFetch).mockResolvedValueOnce(page)
  act(() => intelButton(document, 'Retry evidence').click())
  await settle()
  expect(document.body.textContent).toContain('A retained reviewed passage.')
})
