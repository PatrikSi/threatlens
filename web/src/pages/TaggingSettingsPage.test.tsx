import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'

const taggingPageMocks = vi.hoisted(() => ({
  queryClient: {
    invalidateQueries: vi.fn(),
  },
  currentUser: {
    data: {
      access: {
        permissions: ['write:tagging'],
      },
    },
    isLoading: false,
  },
  useUnsavedChangesWarning: vi.fn(),
}))

function createDiscardMock() {
  return Object.assign(
    vi.fn((onDiscard?: () => void) => {
      onDiscard?.()
      return true
    }),
    { discardDialog: null },
  )
}

taggingPageMocks.useUnsavedChangesWarning.mockImplementation(() => createDiscardMock())

vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => taggingPageMocks.queryClient,
  useQuery: ({ queryKey }: { queryKey: unknown[] }) => {
    const [scope] = queryKey
    const baseResult = {
      isLoading: false,
      isError: false,
      error: null,
    }

    if (scope === 'tagging') {
      return {
        ...baseResult,
        data: {
          settings: {
            id: 'settings-1',
            enabled_categories: ['vulnerability', 'apt_campaign'],
            min_auto_tag_confidence: 0.45,
            secondary_tag_limit: 2,
            created_at: '2026-04-20T10:00:00Z',
            updated_at: '2026-04-21T10:00:00Z',
          },
          rules: [
            {
              id: 'rule-1',
              name: 'VPN disclosures',
              tag_name: 'vpn',
              enabled: true,
              match_type: 'contains',
              pattern: 'vpn',
              case_sensitive: false,
              applies_to: ['title', 'summary'],
              required_categories: [],
              feed_scope: 'all',
              feed_ids: [],
              min_classification_confidence: null,
              created_at: '2026-04-20T10:00:00Z',
              updated_at: '2026-04-21T10:00:00Z',
            },
          ],
        },
      }
    }

    if (scope === 'feeds') {
      return {
        ...baseResult,
        data: [
          {
            id: 'feed-1',
            name: 'Vendor advisories',
            url: 'https://example.com/feed.xml',
            description: null,
            site_url: null,
            language: null,
            enabled: true,
            fetch_mode: 'schedule',
            fetch_interval_seconds: 1800,
            schedule_cron: '0 * * * *',
            etag: null,
            last_modified: null,
            last_fetch_at: null,
            last_success_at: null,
            error_count: 0,
            last_error: null,
            has_unreadable_url: false,
            created_at: '2026-04-20T10:00:00Z',
          },
        ],
      }
    }

    return {
      ...baseResult,
      data: undefined,
    }
  },
  useMutation: () => ({
    mutate: vi.fn(),
    isPending: false,
    isError: false,
    error: null,
  }),
}))

vi.mock('../hooks/useUnsavedChangesWarning', () => ({
  useUnsavedChangesWarning: taggingPageMocks.useUnsavedChangesWarning,
}))

vi.mock('../hooks/useCurrentUser', () => ({
  useCurrentUser: () => taggingPageMocks.currentUser,
}))

import { TaggingSettingsPage } from './TaggingSettingsPage'

describe('TaggingSettingsPage rendered workflow', () => {
  it('renders labeled tagging controls and wires the discard warning', () => {
    const markup = renderToStaticMarkup(createElement(TaggingSettingsPage))

    expect(markup).toContain('Content tagging')
    expect(markup).toContain('Automatic tagging')
    expect(markup).toContain('Retag existing content')
    expect(markup).toContain('Rules')
    expect(markup).toContain('Rule name')
    expect(markup).toContain('Preview rule')
    expect(taggingPageMocks.useUnsavedChangesWarning).toHaveBeenCalledWith(true, 'Discard unsaved tagging changes?')
  })
})
