// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it } from 'vitest'
import { markdownReport } from '../../browser/report-markdown-fixture'
import type { ReportDetail } from '../types/api'
import { ReportDetailView } from './ReportDetailView'
import type { ReportingController } from './useReportingController'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
let root: Root | undefined
let host: HTMLDivElement
function render(report: ReportDetail = markdownReport()) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  const controller = { canAuthor: false, currentUser: { data: { id: 'reader' } }, detailActionPending: false } as ReportingController
  act(() => root!.render(<ReportDetailView report={report} controller={controller} />))
  return host.querySelector('article')!
}
afterEach(() => { act(() => root?.unmount()); host?.remove() })

describe('report Markdown presentation', () => {
  it('discloses incomplete evidence and the limits of structural citation checks', () => {
    const report = markdownReport()
    report.coverage.grounding = { version: 1, status: 'insufficient_evidence', validated_findings: 0, cited_claim_blocks: 0 }
    render(report)
    expect(host.textContent).toContain('Evidence synthesis is incomplete.')
    expect(host.textContent).toContain('0 findings include quotations matched to supplied excerpts')
    expect(host.textContent).toContain('not whether every claim follows from its evidence')
  })

  it('renders report hierarchy, nested lists, tables, emphasis, quotes and literal code', () => {
    const article = render()
    expect(article.querySelector('h2')?.textContent).toBe('Assessment')
    expect(article.querySelector('h3')?.textContent).toBe('Operational assessment')
    expect(article.querySelector('h4')?.textContent).toBe('Priorities')
    expect(article.querySelector('ol > li > ul')?.children).toHaveLength(2)
    expect(article.querySelectorAll('table thead th[scope="col"]')).toHaveLength(3)
    expect(article.querySelectorAll('table tbody tr')).toHaveLength(3)
    expect(article.querySelector('strong')?.textContent).toBe('Critical')
    expect(article.querySelector('em')?.textContent).toBe('careful validation')
    expect(article.querySelector('del')?.textContent).toBe('Dismiss')
    expect(article.querySelector('blockquote')?.textContent).toContain('Verify the evidence')
    expect(article.querySelector('pre code')?.textContent).toContain('[S1] <script>example</script>')
    expect(article.querySelector('code a')).toBeNull()
  })

  it('links only included known citations and moves keyboard focus to source evidence', () => {
    const report = markdownReport()
    report.sources.push({ ...report.sources[0], citation_key: 'S99', included: false })
    const article = render(report)
    const citations = article.querySelectorAll<HTMLAnchorElement>('a[aria-label="Source S1"]')
    expect(citations).toHaveLength(6)
    expect(article.querySelector('td a[aria-label="Source S1"]')).not.toBeNull()
    expect(article.querySelector('a[aria-label="Source S99"]')).toBeNull()
    const target = host.querySelector<HTMLElement>('#report-markdown-report-source-S1')!
    act(() => citations[0].dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true })))
    expect(document.activeElement).toBe(target)
    expect(target.textContent).toContain('Verified advisory')
    expect(target.getAttribute('href')).toBe('https://evidence.example.test/advisory')
  })

  it('omits active HTML and automatic images while rejecting unsafe link schemes', () => {
    const report = markdownReport()
    report.sources[0].url = 'javascript:alert(1)'
    report.sections[0].body_markdown += '\n\n[data](data:text/html,example) [file](file:///etc/passwd) [relative](/api/v1/auth/logout)'
    const article = render(report)
    expect(article.querySelector('script, img, iframe, object, embed, style, form')).toBeNull()
    expect(article.querySelector('[onerror]')).toBeNull()
    expect(article.textContent).toContain('[Image omitted: Untrusted chart]')
    const external = article.querySelector('a[href="https://advisory.example.test/details"]')!
    expect(external.getAttribute('rel')).toBe('noopener noreferrer')
    expect(external.getAttribute('target')).toBe('_blank')
    for (const anchor of host.querySelectorAll('a[href]')) {
      expect(anchor.getAttribute('href')).toMatch(/^(https:\/\/|#report-)/)
    }
    expect(host.querySelector('#report-markdown-report-source-S1')?.hasAttribute('href')).toBe(false)
  })

  it('does not corrupt citation-looking link labels, code fences or inline code', () => {
    const report = markdownReport()
    report.sections[0].body_markdown = '[Reference [S1]](https://example.test) and `[S1]`.\n\n```\n[S1]\n```\n\nPlain [S1].'
    const article = render(report)
    expect(article.querySelector('a a')).toBeNull()
    expect(article.querySelectorAll('a[aria-label="Source S1"]')).toHaveLength(1)
    expect(article.querySelectorAll('code')).toHaveLength(2)
  })
})
