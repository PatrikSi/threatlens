// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'

import type { ReportDetail } from '../types/api'
import { ReportDetailView } from './ReportDetailView'
import type { ReportingController } from './useReportingController'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

vi.mock('./ReportSourceEvidenceDialog', () => ({
  ReportSourceEvidenceDialog: ({ reportId }: { reportId: string }) => (
    <div role="dialog" aria-label="Retained evidence">{reportId}</div>
  ),
}))

let root: Root | undefined
let container: HTMLDivElement
let client: QueryClient

afterEach(() => {
  act(() => root?.unmount())
  client?.clear()
  container?.remove()
  vi.restoreAllMocks()
})

it('keeps one editor across polling and resets editor/evidence state when the report changes', () => {
  const errors = vi.spyOn(console, 'error').mockImplementation(() => undefined)
  const controller = {
    canAuthor: true,
    isAdmin: false,
    currentUser: { data: { id: 'owner' } },
    detailActionPending: false,
    setEditorialDirty: vi.fn(),
    builderDraft: { confirmDiscard: vi.fn() },
    reportDetailQuery: { refetch: vi.fn() },
  } as unknown as ReportingController
  const original: ReportDetail = {
    id: 'report-one', owner_user_id: 'owner', title: 'First report', status: 'ready',
    template_id: null, schedule_id: null, report_type: 'custom', trigger_source: 'manual',
    generation_stage: 'ready', provider: 'local', model: 'test-model',
    error_code: null, error: null, created_at: '2026-09-01T00:00:00Z',
    publication_status: 'draft', editorial_version: 1, review_required: true,
    period_start: '2026-09-01T00:00:00Z', period_end: '2026-09-02T00:00:00Z',
    generated_at: '2026-09-02T00:00:00Z', source_count: 1, included_source_count: 1,
    model_calls: 1, generation_batches: 1, estimated_input_tokens: 100,
    prompt_tokens: 100, completion_tokens: 50, total_tokens: 150,
    context_window_tokens: 8192, delivery_requested: false, delivery_mode: 'summary',
    filters: {
      q: null, feed_ids: [], tag_ids: [], tags_mode: 'any', classifications: [],
      ai_relevance_labels: [], ai_score_min: null, ai_score_max: null,
      is_read: null, is_starred: null, has_article_text: null, since: null, until: null,
      date_basis: 'published_at_or_first_seen_at', sort: 'published_at_desc',
    },
    prompt: {
      audience: 'security_team', objective: 'Review source evidence.', tone: 'analytical',
      detail_level: 'standard', use_company_context: true, custom_instructions: null,
      focus_topics: [], excluded_topics: [],
    },
    sections_config: [{ key: 'summary', title: 'Summary', enabled: true }],
    metrics: {}, coverage: { warnings: [] }, summary_text: 'Summary.',
    sections: [{ key: 'summary', title: 'Summary', body_markdown: 'Claim [S1].',
      position: 0, status: 'ready', key_points: [], citations: ['S1'], error: null }],
    sources: [{ citation_key: 'S1', included: true, title: 'Retained source',
      url: 'https://source.example.com/article', feed_name: 'Source feed',
      item_id: 'item-one', rank: 1, exclusion_reason: null, classification: null,
      relevance_score: null, relevance_label: null, published_at: null,
      first_seen_at: '2026-09-01T00:00:00Z', tags: [], iocs: [], estimated_tokens: 100 }],
  }
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  function render(report: ReportDetail) {
    act(() => root!.render(
      <QueryClientProvider client={client}>
        <ReportDetailView controller={controller} report={report} />
      </QueryClientProvider>,
    ))
  }
  function click(label: string) {
    const button = Array.from(container.querySelectorAll('button'))
      .find((entry) => entry.textContent?.trim() === label)
    expect(button).toBeTruthy()
    act(() => button!.click())
  }
  function assertSingleReport() {
    expect(container.querySelectorAll('[aria-label="Report review and publication"]')).toHaveLength(1)
    expect(container.querySelectorAll('article')).toHaveLength(1)
  }

  render(original)
  assertSingleReport()
  click('Edit draft')
  const title = container.querySelector('input')!
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(title, 'Unsaved local title')
    title.dispatchEvent(new Event('input', { bubbles: true }))
  })
  click('Read retained evidence [S1]')
  expect(container.querySelector('[role="dialog"]')?.textContent).toBe('report-one')
  for (let version = 2; version <= 4; version += 1) {
    render({ ...original, editorial_version: version, title: 'Updated server title' })
    assertSingleReport()
    expect(container.querySelector('input')?.value).toBe('Unsaved local title')
    expect(container.querySelector('[role="dialog"]')?.textContent).toBe('report-one')
  }
  render({ ...original, id: 'report-two', title: 'Second report' })
  assertSingleReport()
  expect(container.querySelector('input')).toBeNull()
  expect(container.querySelector('[role="dialog"]')).toBeNull()
  click('Edit draft')
  expect(container.querySelector('input')?.value).toBe('Second report')
  expect(errors.mock.calls.filter((call) => String(call[0]).includes('same key'))).toEqual([])
})
