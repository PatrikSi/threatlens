// @vitest-environment jsdom
import { act, useState } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { ArticleExtractionContinuation } from './ArticleExtractionContinuation'
import { apiFetch } from '../api/client'
import type { ExtractionCoverage } from '../types/articleIntelligence'
import { deferred, intelButton, mountIntel, settle } from './articleIntelligenceTestSupport'
vi.mock('../api/client', async (original) => ({ ...await original<typeof import('../api/client')>(), apiFetch: vi.fn() }))
;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
afterEach(() => { view?.close(); view = undefined; vi.resetAllMocks() })
const coverage: ExtractionCoverage = { planner_version: 1, source_hash: 'a'.repeat(64), progress_revision: 'b'.repeat(64),
  normalized_text_chars: 100000, processed_chars: 64000, uncovered_chars: 36000, reserved_tokens: 50000,
  token_budget: 64000, call_limit: 8, output_limited: false, sections: [] }

it('late acceptance does not disable continuation of a newer source checkpoint', async () => {
  const pending = deferred<unknown>()
  vi.mocked(apiFetch).mockReturnValueOnce(pending.promise).mockResolvedValue({ run_id: 'new-run' })
  let update!: (value: ExtractionCoverage) => void
  function Workspace() {
    const [value, setValue] = useState(coverage)
    update = setValue
    return <ArticleExtractionContinuation itemId="article-1" coverage={value} disabled={false} />
  }
  view = await mountIntel(<Workspace />)
  act(() => intelButton(view!.host, 'Authorize additional article sections').click())
  await settle()
  act(() => update({ ...coverage, progress_revision: 'c'.repeat(64), call_limit: 16, token_budget: 128000 }))
  await act(async () => pending.resolve({ run_id: 'old-run' }))
  await settle()
  expect(intelButton(view.host, 'Authorize additional article sections').disabled).toBe(false)
  expect(view.host.textContent).not.toContain('Additional processing was queued')
  act(() => intelButton(view!.host, 'Authorize additional article sections').click())
  await settle()
  const bodies = vi.mocked(apiFetch).mock.calls.map((call) => JSON.parse(String(call[1]?.body)))
  expect(bodies).toHaveLength(2)
  expect(bodies[0].progress_revision).toBe(coverage.progress_revision)
  expect(bodies[1].progress_revision).toBe('c'.repeat(64))
  expect(bodies[0].request_id).not.toBe(bodies[1].request_id)
})

it('does not submit another authorization once the cumulative ceiling is reached', async () => {
  view = await mountIntel(<ArticleExtractionContinuation itemId="article-1" coverage={{ ...coverage, call_limit: 32, token_budget: 256000 }} disabled={false} />)
  expect(intelButton(view.host, 'Authorize additional article sections').disabled).toBe(true)
  expect(view.host.textContent).toContain('Review the remaining source manually')
  expect(apiFetch).not.toHaveBeenCalled()
})
