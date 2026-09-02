import { describe, expect, it } from 'vitest'

import type { Article } from '../types/items'
import {
  articleFetchActionLabel,
  articleUnavailableMessage,
} from './articleLifecyclePresentation'

const article: Article = {
  final_url: 'https://example.com/article',
  retrieved_at: '2026-01-01T00:00:00Z',
  http_status: 200,
  content_type: 'text/html',
  title_extracted: null,
  text: null,
  extraction_method: 'retention_purged',
  language: null,
  word_count: null,
  fetch_ms: 120,
  error: null,
  content_purged_at: '2026-09-02T09:00:00Z',
}

describe('lifecycle-purged article presentation', () => {
  it('explains that content was purged while source metadata remains', () => {
    expect(articleUnavailableMessage(article)).toContain(
      'Fetched article content was removed by data lifecycle',
    )
    expect(articleUnavailableMessage(article)).toContain(
      'source metadata remains',
    )
  })

  it('offers an explicit force-refetch action', () => {
    expect(articleFetchActionLabel(article, false)).toBe('Fetch article again')
    expect(articleFetchActionLabel(article, true)).toBe('Queueing...')
  })
})
