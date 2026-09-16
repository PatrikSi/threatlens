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
