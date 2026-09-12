// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it } from 'vitest'
import corpus from '../../../tests/fixtures/report-citation-corpus.json'
import { ReportMarkdownText } from './ReportMarkdownText'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
it.each(corpus)('renders the shared citation contract: $name', (entry) => {
  const host = document.createElement('div')
  const root = createRoot(host)
  try {
    act(() => root.render(<ReportMarkdownText value={entry.body} citationTargets={new Map([['S1', 'source-S1']])} />))
    expect(host.querySelectorAll('a[href="#source-S1"]')).toHaveLength(entry.source_links)
    expect(host.querySelector('a a')).toBeNull()
    if (entry.name === 'ordinary autolink and separate citation') {
      expect(host.querySelector('a[href="https://example.test/advisory"]')).not.toBeNull()
    }
    if (entry.name === 'citation repair does not decode following text twice') {
      expect(host.textContent).toContain('&#91;S99&#93;')
      expect(host.textContent).not.toContain('[S99]')
    }
    if (entry.name.startsWith('GFM URL')) {
      expect(host.textContent).toBe(entry.body.replaceAll('&#91;', '[').replaceAll('&#93;', ']'))
      expect(host.querySelector('a[href*="%5BS1"]')).toBeNull()
    }
  } finally { act(() => root.unmount()) }
})
