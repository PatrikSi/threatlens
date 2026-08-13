import { describe, expect, it } from 'vitest'

import { parseRssWindowFiltersCandidate, type DashboardRssWindowFilters } from './dashboardSavedViews'

const fallback: DashboardRssWindowFilters = {
  selected_feed_ids: ['fallback-feed'],
  selected_tags: ['fallback-tag'],
  q: 'fallback query',
  read_status: 'read',
  star_status: 'starred',
  ai_relevance: 'medium',
  view_mode: 'expanded',
  page: 7,
  page_size: 100,
  sort: 'first_seen_asc',
  show_advanced_filters: true,
}

describe('parseRssWindowFiltersCandidate', () => {
  it('preserves fallback precedence for absent and legacy neutral candidate values', () => {
    expect(
      parseRssWindowFiltersCandidate(
        {
          read_status: 'all',
          star_status: 'all',
          view_mode: 'compact',
        },
        fallback,
      ),
    ).toEqual(fallback)
  })

  it('normalizes valid candidates without changing established coercion rules', () => {
    expect(
      parseRssWindowFiltersCandidate(
        {
          selected_feed_ids: ['feed-1', 42, 'feed-2'],
          selected_tags: ['vendor:microsoft', 'content_fetched', null, 'priority'],
          q: '',
          read_status: 'unread',
          star_status: 'unstarred',
          ai_relevance: 'high',
          view_mode: 'expanded',
          page: 4.9,
          page_size: 50,
          sort: 'published_at_asc',
          show_advanced_filters: false,
        },
        fallback,
      ),
    ).toEqual({
      selected_feed_ids: ['feed-1', 'feed-2'],
      selected_tags: ['vendor:microsoft'],
      q: '',
      read_status: 'unread',
      star_status: 'unstarred',
      ai_relevance: 'high',
      view_mode: 'expanded',
      page: 4,
      page_size: 50,
      sort: 'published_at_asc',
      show_advanced_filters: false,
    })
  })

  it('uses defaults for a non-object candidate and nullish fallback properties', () => {
    const nullishFallback = {
      selected_feed_ids: undefined,
      q: undefined,
      show_advanced_filters: undefined,
    } satisfies Partial<DashboardRssWindowFilters>

    expect(parseRssWindowFiltersCandidate('invalid', nullishFallback, true)).toEqual({
      selected_feed_ids: [],
      selected_tags: [],
      q: '',
      read_status: 'all',
      star_status: 'all',
      ai_relevance: 'all',
      view_mode: 'compact',
      page: 1,
      page_size: 25,
      sort: 'published_at_desc',
      show_advanced_filters: true,
    })
  })
})
