// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { ArticleEvidenceView } from './ArticleEvidenceView'
import { extractionFixture, mountIntel } from './articleIntelligenceTestSupport'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
afterEach(() => { view?.close(); view = undefined })
describe('article evidence presentation', () => {
  it('separates reported references and AI inference, discloses gaps and renders quotes as text', async () => {
    view = await mountIntel(<ArticleEvidenceView extraction={extractionFixture} stale />)
    expect(view.host.textContent).toContain('Reported by source · reference')
    expect(view.host.textContent).toContain('AI inference')
    expect(view.host.textContent).toContain('earlier article revision')
    expect(view.host.textContent).toContain('Only part of the article')
    expect(view.host.textContent).toContain('<script>not executable</script>')
    expect(view.host.querySelector('script')).toBeNull()
    expect(view.host.querySelectorAll('a')).toHaveLength(0)
    expect(view.host.textContent).toContain('No independent validation')
  })
})

it('shows bounded section progress, uncovered evidence and output caps', async () => {
  view = await mountIntel(<ArticleEvidenceView extraction={{ ...extractionFixture, coverage: {
    planner_version: 1, source_hash: 'a'.repeat(64), normalized_text_chars: 16000,
    processed_chars: 8000, uncovered_chars: 8000, reserved_tokens: 12000,
    token_budget: 64000, call_limit: 8, output_limited: true,
    sections: [
      { index: 0, start: 0, end: 8000, status: 'completed' },
      { index: 1, start: 8000, end: 16000, status: 'started' },
    ],
  } }} stale={false} />)
  expect(view.host.textContent).toContain('1 of 2 planned sections')
  expect(view.host.textContent).toContain('8,000 characters remain uncovered')
  expect(view.host.textContent).toContain('awaiting a durable result')
  expect(view.host.textContent).toContain('reached its entity or relationship limit')
  expect(view.host.textContent).toContain('Summary and relevance use the first section')
})
