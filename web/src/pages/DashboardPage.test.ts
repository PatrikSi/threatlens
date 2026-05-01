import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  getDashboardStorageKeys,
  migrateLegacyDashboardStorage,
} from './dashboardStorage'
import {
  buildSavedViewPreview,
  buildDashboardSavedViewState,
  loadDashboardWindows,
  normalizeDashboardWindows,
  normalizePanelRect,
  parseDashboardSavedView,
  resolveWindowRect,
  resolveSavedViewSelectionChange,
  serializeDashboardWindowLayouts,
  withPanelRectPercentages,
} from './dashboardSavedViews'
import { parseArticleBlocks, sanitizeHref } from './dashboardContent'
import { summarizeGlobalSearchAcrossWindows } from './dashboardState'
import type { SavedView } from '../types/api'
import type { DashboardAlertWindowFilters, DashboardRssWindowFilters } from './dashboardSavedViews'

function createLocalStorageMock() {
  const store = new Map<string, string>()

  return {
    clear() {
      store.clear()
    },
    getItem(key: string) {
      return store.has(key) ? store.get(key)! : null
    },
    setItem(key: string, value: string) {
      store.set(key, value)
    },
    removeItem(key: string) {
      store.delete(key)
    },
  }
}

let localStorageMock = createLocalStorageMock()

beforeEach(() => {
  localStorageMock = createLocalStorageMock()
  vi.stubGlobal('window', { localStorage: localStorageMock } as Window)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('getDashboardStorageKeys', () => {
  it('scopes persisted dashboard state by user id', () => {
    const aliceKeys = getDashboardStorageKeys('alice')
    const bobKeys = getDashboardStorageKeys('bob')

    expect(aliceKeys.windows).toBe('threatlens.dashboard.windows.v2:alice')
    expect(aliceKeys.windowSeenAt).toBe('threatlens.dashboard.window-seen.v1:alice')
    expect(aliceKeys.lastOpenedAt).toBe('threatlens.user-last-open.v1:alice')

    expect(bobKeys.windows).toBe('threatlens.dashboard.windows.v2:bob')
    expect(bobKeys.windowSeenAt).toBe('threatlens.dashboard.window-seen.v1:bob')
    expect(bobKeys.lastOpenedAt).toBe('threatlens.user-last-open.v1:bob')
  })
})

describe('migrateLegacyDashboardStorage', () => {
  it('copies legacy shared dashboard state into scoped per-user keys', () => {
    const keys = getDashboardStorageKeys('alice')
    localStorageMock.clear()
    localStorageMock.setItem('threatlens.dashboard.windows.v2', JSON.stringify([{ id: 'window-1' }]))
    localStorageMock.setItem('threatlens.dashboard.window-seen.v1', JSON.stringify({ 'window-1': '2026-04-11T00:00:00.000Z' }))
    localStorageMock.setItem('threatlens.user-last-open.v1', '2026-04-11T01:00:00.000Z')

    expect(migrateLegacyDashboardStorage('alice')).toBe(true)
    expect(localStorageMock.getItem(keys.windows)).toBe(JSON.stringify([{ id: 'window-1' }]))
    expect(localStorageMock.getItem(keys.windowSeenAt)).toBe(JSON.stringify({ 'window-1': '2026-04-11T00:00:00.000Z' }))
    expect(localStorageMock.getItem(keys.lastOpenedAt)).toBe('2026-04-11T01:00:00.000Z')
  })

  it('does not overwrite already scoped dashboard state', () => {
    const keys = getDashboardStorageKeys('alice')
    localStorageMock.clear()
    localStorageMock.setItem(keys.windows, JSON.stringify([{ id: 'scoped-window' }]))
    localStorageMock.setItem(keys.windowSeenAt, JSON.stringify({ 'scoped-window': '2026-04-11T02:00:00.000Z' }))
    localStorageMock.setItem(keys.lastOpenedAt, '2026-04-11T03:00:00.000Z')
    localStorageMock.setItem('threatlens.dashboard.windows.v2', JSON.stringify([{ id: 'legacy-window' }]))
    localStorageMock.setItem('threatlens.dashboard.window-seen.v1', JSON.stringify({ 'legacy-window': '2026-04-11T04:00:00.000Z' }))
    localStorageMock.setItem('threatlens.user-last-open.v1', '2026-04-11T05:00:00.000Z')

    expect(migrateLegacyDashboardStorage('alice')).toBe(false)
    expect(localStorageMock.getItem(keys.windows)).toBe(JSON.stringify([{ id: 'scoped-window' }]))
    expect(localStorageMock.getItem(keys.windowSeenAt)).toBe(JSON.stringify({ 'scoped-window': '2026-04-11T02:00:00.000Z' }))
    expect(localStorageMock.getItem(keys.lastOpenedAt)).toBe('2026-04-11T03:00:00.000Z')
  })
})

describe('summarizeGlobalSearchAcrossWindows', () => {
  it('returns a shared query when every searchable panel is aligned', () => {
    expect(
      summarizeGlobalSearchAcrossWindows([
        {
          type: 'rss',
          rss_filters: { q: 'cve' },
          alert_filters: null,
        },
        {
          type: 'alerts',
          rss_filters: null,
          alert_filters: { q: 'cve' },
        },
      ]),
    ).toEqual({
      value: 'cve',
      isMixed: false,
    })
  })

  it('marks the toolbar as mixed when panels diverge', () => {
    expect(
      summarizeGlobalSearchAcrossWindows([
        {
          type: 'rss',
          rss_filters: { q: 'cve' },
          alert_filters: null,
        },
        {
          type: 'alerts',
          rss_filters: null,
          alert_filters: { q: 'ransomware' },
        },
      ]),
    ).toEqual({
      value: '',
      isMixed: true,
    })
  })
})

describe('saved view payloads', () => {
  it('builds a versioned payload that round-trips through the parser', () => {
    const built = buildDashboardSavedViewState(
      [
        {
          id: 'rss-1',
          type: 'rss',
          title: 'RSS Panel 1',
          snap: 'full',
          rect: { x: 0, y: 0, width: 1200, height: 720 },
          controls_collapsed: false,
          scratch_note: '',
          time_override: null,
          rss_filters: {
            selected_feed_ids: ['feed-1'],
            selected_tags: ['vendor:microsoft'],
            q: 'exchange',
            read_status: 'unread',
            star_status: 'all',
            view_mode: 'compact',
            page: 3,
            page_size: 50,
            sort: 'published_at_desc',
            show_advanced_filters: true,
          },
          alert_filters: null,
          selected_daily_brief_id: null,
        },
        {
          id: 'notes-1',
          type: 'notes',
          title: 'Notes Panel 1',
          snap: 'free',
          rect: { x: 24, y: 24, width: 480, height: 360 },
          controls_collapsed: false,
          scratch_note: 'Track exposed assets.',
          time_override: null,
          rss_filters: null,
          alert_filters: null,
          selected_daily_brief_id: null,
        },
      ],
      {
        time_range: '7d',
        custom_since_date: '',
        custom_until_date: '',
        rolling_days: '7',
      },
      { width: 1380, height: 760 },
    )

    expect(built.schema_version).toBe(1)
    expect(built.windows[0].rss_filters?.page).toBe(1)

    const parsed = parseDashboardSavedView(built as unknown as Record<string, unknown>, 1380, 760)

    expect(parsed.schema_version).toBe(1)
    expect(parsed.rss_filters.q).toBe('exchange')
    expect(parsed.windows[0].rss_filters?.page).toBe(1)
    expect(parsed.windows[1].scratch_note).toBe('Track exposed assets.')
    expect(parsed.windows[1].rect.xPct).toBeCloseTo(24 / 1380)
    expect(parsed.windows[1].rect.widthPct).toBeCloseTo(480 / 1380)
  })

  it('serializes per-window filters when persisting local window layouts', () => {
    const serialized = serializeDashboardWindowLayouts([
      {
        id: 'rss-1',
        type: 'rss',
        title: 'RSS Panel 1',
        snap: 'full',
        rect: { x: 0, y: 0, width: 1200, height: 720 },
        controls_collapsed: true,
        scratch_note: '',
        time_override: {
          time_range: 'days',
          custom_since_date: '',
          custom_until_date: '',
          rolling_days: '14',
        },
        rss_filters: {
          selected_feed_ids: ['feed-1'],
          selected_tags: ['vendor:microsoft'],
          q: 'exchange',
          read_status: 'unread',
          star_status: 'starred',
          view_mode: 'expanded',
          page: 4,
          page_size: 50,
          sort: 'first_seen_desc',
          show_advanced_filters: true,
        },
        alert_filters: null,
        selected_daily_brief_id: null,
      },
      {
        id: 'alerts-1',
        type: 'alerts',
        title: 'Alerts Panel 1',
        snap: 'right',
        rect: { x: 600, y: 0, width: 600, height: 720 },
        controls_collapsed: false,
        scratch_note: '',
        time_override: null,
        rss_filters: null,
        alert_filters: {
          selected_alert_ids: ['alert-1'],
          selected_categories: ['vulnerability'],
          q: 'fortinet',
          view_mode: 'compact',
          page: 2,
          page_size: 10,
          sort: 'published_at_asc',
        },
        selected_daily_brief_id: null,
      },
    ])

    expect(serialized[0].rss_filters).toEqual({
      selected_feed_ids: ['feed-1'],
      selected_tags: ['vendor:microsoft'],
      q: 'exchange',
      read_status: 'unread',
      star_status: 'starred',
      view_mode: 'expanded',
      page: 4,
      page_size: 50,
      sort: 'first_seen_desc',
      show_advanced_filters: true,
    })
    expect(serialized[1].alert_filters).toEqual({
      selected_alert_ids: ['alert-1'],
      selected_categories: ['vulnerability'],
      q: 'fortinet',
      view_mode: 'compact',
      page: 2,
      page_size: 10,
      sort: 'published_at_asc',
    })

    localStorageMock.setItem('dashboard-windows', JSON.stringify(serialized))
    const roundTripped = loadDashboardWindows('dashboard-windows', 1200, 720)
    expect(roundTripped[0].rss_filters?.q).toBe('exchange')
    expect(roundTripped[0].rss_filters?.page).toBe(4)
    expect(roundTripped[1].alert_filters?.q).toBe('fortinet')
    expect(roundTripped[1].alert_filters?.page).toBe(2)
  })

  it('normalizes fractional panel geometry before saving dashboard layouts', () => {
    const normalized = normalizePanelRect({ x: 10.4, y: 20.6, width: 640.2, height: 420.9 }, 1200.8, 720.2)
    expect(normalized).toEqual({ x: 10, y: 21, width: 640, height: 421 })

    const fractionalWindow = {
      id: 'rss-1',
      type: 'rss',
      title: 'RSS Panel 1',
      snap: 'free',
      rect: { x: 10.4, y: 20.6, width: 640.2, height: 420.9 },
      controls_collapsed: false,
      scratch_note: '',
      time_override: null,
      rss_filters: {
        selected_feed_ids: [],
        selected_tags: [],
        q: '',
        read_status: 'all',
        star_status: 'all',
        view_mode: 'compact',
        page: 1,
        page_size: 25,
        sort: 'published_at_desc',
        show_advanced_filters: false,
      },
      alert_filters: null,
      selected_daily_brief_id: null,
    } satisfies Parameters<typeof serializeDashboardWindowLayouts>[0][number]

    const serialized = serializeDashboardWindowLayouts([fractionalWindow])
    expect(serialized[0].rect).toEqual({ x: 10, y: 21, width: 640, height: 421 })

    const serializedWithContainer = serializeDashboardWindowLayouts([fractionalWindow], { width: 1200, height: 720 })
    expect(serializedWithContainer[0].rect).toMatchObject({ x: 10, y: 21, width: 640, height: 421 })
    expect(serializedWithContainer[0].rect.xPct).toBeCloseTo(10 / 1200)
    expect(serializedWithContainer[0].rect.yPct).toBeCloseTo(21 / 720)
    expect(serializedWithContainer[0].rect.widthPct).toBeCloseTo(640 / 1200)
    expect(serializedWithContainer[0].rect.heightPct).toBeCloseTo(421 / 720)

    const savedState = buildDashboardSavedViewState(
      [fractionalWindow],
      {
        time_range: 'all',
        custom_since_date: '',
        custom_until_date: '',
        rolling_days: '7',
      },
      { width: 1200, height: 720 },
    )
    expect(savedState.windows[0].rect).toMatchObject({ x: 10, y: 21, width: 640, height: 421 })
    expect(savedState.windows[0].rect.widthPct).toBeCloseTo(640 / 1200)
  })

  it('scales floating percentage geometry without changing fixed snap layouts', () => {
    const floatingWindow = {
      id: 'notes-1',
      type: 'notes',
      title: 'Notes Panel 1',
      snap: 'free',
      rect: withPanelRectPercentages({ x: 120, y: 72, width: 600, height: 360 }, 1200, 720),
      controls_collapsed: false,
      scratch_note: '',
      time_override: null,
      rss_filters: null,
      alert_filters: null,
      selected_daily_brief_id: null,
    } satisfies Parameters<typeof normalizeDashboardWindows>[0][number]
    const fixedWindow = {
      id: 'rss-1',
      type: 'rss',
      title: 'RSS Panel 1',
      snap: 'left',
      rect: {
        x: 120,
        y: 72,
        width: 600,
        height: 360,
        xPct: 0.1,
        yPct: 0.1,
        widthPct: 0.5,
        heightPct: 0.5,
      },
      controls_collapsed: false,
      scratch_note: '',
      time_override: null,
      rss_filters: {
        selected_feed_ids: [],
        selected_tags: [],
        q: '',
        read_status: 'all',
        star_status: 'all',
        view_mode: 'compact',
        page: 1,
        page_size: 25,
        sort: 'published_at_desc',
        show_advanced_filters: false,
      },
      alert_filters: null,
      selected_daily_brief_id: null,
    } satisfies Parameters<typeof normalizeDashboardWindows>[0][number]

    const [scaledFloating, scaledFixed] = normalizeDashboardWindows([floatingWindow, fixedWindow], 1600, 900)

    expect(resolveWindowRect(scaledFloating, 1600, 900)).toMatchObject({
      x: 160,
      y: 90,
      width: 800,
      height: 450,
    })
    expect(scaledFloating.rect.xPct).toBeCloseTo(0.1)
    expect(scaledFloating.rect.widthPct).toBeCloseTo(0.5)
    expect(scaledFixed.rect).toEqual({ x: 0, y: 0, width: 800, height: 900 })

    const serializedFixed = serializeDashboardWindowLayouts([scaledFixed], { width: 1600, height: 900 })
    expect(serializedFixed[0].rect).toEqual({ x: 0, y: 0, width: 800, height: 900 })
  })

  it('migrates legacy saved views into the current schema', () => {
    const parsed = parseDashboardSavedView(
      {
        filters: {
          selected_feed_ids: ['feed-2'],
          q: 'legacy-search',
          read_status: 'read',
        },
        panel_rect: {
          x: 40,
          y: 30,
          width: 640,
          height: 420,
        },
      },
      1380,
      760,
    )

    expect(parsed.schema_version).toBe(1)
    expect(parsed.rss_filters.selected_feed_ids).toEqual(['feed-2'])
    expect(parsed.rss_filters.q).toBe('legacy-search')
    expect(parsed.windows).toHaveLength(1)
    expect(parsed.windows[0].type).toBe('rss')
    expect(parsed.windows[0].rect.width).toBeGreaterThan(0)
  })

  it('drops non-search window time overrides while preserving valid daily-brief selection', () => {
    const parsed = parseDashboardSavedView(
      {
        schema_version: 1,
        version: 6,
        rss_filters: {},
        alert_filters: {},
        windows: [
          {
            id: 'notes-1',
            type: 'notes',
            title: 'Notes Panel 1',
            snap: 'full',
            rect: { x: 0, y: 0, width: 900, height: 500 },
            controls_collapsed: false,
            scratch_note: 'Track pivots',
            time_override: {
              time_range: '7d',
              custom_since_date: '',
              custom_until_date: '',
              rolling_days: '7',
            },
            rss_filters: null,
            alert_filters: null,
            selected_daily_brief_id: null,
          },
          {
            id: 'brief-1',
            type: 'daily_brief',
            title: 'Daily Brief Panel 1',
            snap: 'right',
            rect: { x: 0, y: 0, width: 900, height: 500 },
            controls_collapsed: false,
            scratch_note: '',
            time_override: {
              time_range: '30d',
              custom_since_date: '',
              custom_until_date: '',
              rolling_days: '30',
            },
            rss_filters: null,
            alert_filters: null,
            selected_daily_brief_id: 'brief-snapshot-1',
          },
        ],
        ui: { show_advanced_filters: false },
      },
      1380,
      760,
    )

    expect(parsed.windows[0].type).toBe('notes')
    expect(parsed.windows[0].time_override).toBeNull()
    expect(parsed.windows[1].type).toBe('daily_brief')
    expect(parsed.windows[1].time_override).toBeNull()
    expect(parsed.windows[1].selected_daily_brief_id).toBe('brief-snapshot-1')
  })

  it('strips dashboard time fields from per-panel filters before saving', () => {
    const built = buildDashboardSavedViewState(
      [
        {
          id: 'rss-1',
          type: 'rss',
          title: 'RSS Panel 1',
          snap: 'left',
          rect: { x: 0, y: 0, width: 690, height: 760 },
          controls_collapsed: false,
          scratch_note: '',
          time_override: null,
          rss_filters: {
            selected_feed_ids: ['feed-1'],
            selected_tags: [],
            q: 'edge',
            read_status: 'all',
            star_status: 'all',
            view_mode: 'compact',
            page: 4,
            page_size: 25,
            sort: 'published_at_desc',
            show_advanced_filters: false,
            time_range: 'all',
            custom_since_date: '',
            custom_until_date: '',
            rolling_days: '7',
          } as unknown as DashboardRssWindowFilters,
          alert_filters: null,
          selected_daily_brief_id: null,
        },
        {
          id: 'alerts-1',
          type: 'alerts',
          title: 'Alerts Panel 1',
          snap: 'right',
          rect: { x: 690, y: 0, width: 690, height: 760 },
          controls_collapsed: false,
          scratch_note: '',
          time_override: null,
          rss_filters: null,
          alert_filters: {
            selected_alert_ids: ['alert-1'],
            selected_categories: [],
            q: 'credential',
            view_mode: 'expanded',
            page: 2,
            page_size: 50,
            sort: 'first_seen_desc',
            time_range: '7d',
            custom_since_date: '',
            custom_until_date: '',
            rolling_days: '7',
          } as unknown as DashboardAlertWindowFilters,
          selected_daily_brief_id: null,
        },
      ],
      {
        time_range: '30d',
        custom_since_date: '',
        custom_until_date: '',
        rolling_days: '30',
      },
    )

    const rssFilters = built.windows[0].rss_filters as unknown as Record<string, unknown>
    const alertFilters = built.windows[1].alert_filters as unknown as Record<string, unknown>

    expect(rssFilters.page).toBe(1)
    expect(alertFilters.page).toBe(1)
    expect(rssFilters).not.toHaveProperty('time_range')
    expect(rssFilters).not.toHaveProperty('custom_since_date')
    expect(rssFilters).not.toHaveProperty('custom_until_date')
    expect(rssFilters).not.toHaveProperty('rolling_days')
    expect(alertFilters).not.toHaveProperty('time_range')
    expect(alertFilters).not.toHaveProperty('custom_since_date')
    expect(alertFilters).not.toHaveProperty('custom_until_date')
    expect(alertFilters).not.toHaveProperty('rolling_days')
  })

  it('loads persisted dashboard windows through the shared window contract', () => {
    localStorageMock.setItem(
      'threatlens.dashboard.windows.v2:alice',
      JSON.stringify([
        {
          id: 'notes-1',
          type: 'notes',
          title: 'Notes Panel 1',
          snap: 'full',
          rect: { x: 0, y: 0, width: 920, height: 520 },
          controls_collapsed: false,
          scratch_note: 'Keep investigation notes here.',
          time_override: {
            time_range: '24h',
            custom_since_date: '',
            custom_until_date: '',
            rolling_days: '1',
          },
          rss_filters: { q: 'should-be-ignored' },
          alert_filters: null,
          selected_daily_brief_id: 'should-be-cleared',
        },
      ]),
    )

    expect(loadDashboardWindows('threatlens.dashboard.windows.v2:alice', 1380, 760)).toEqual([
      {
        id: 'notes-1',
        type: 'notes',
        title: 'Notes Panel 1',
        snap: 'full',
        rect: { x: 0, y: 0, width: 1380, height: 760 },
        controls_collapsed: false,
        scratch_note: 'Keep investigation notes here.',
        time_override: null,
        rss_filters: null,
        alert_filters: null,
        selected_daily_brief_id: null,
      },
    ])
  })

  it('builds saved-view previews from the parsed contract shape', () => {
    const queryJson = buildDashboardSavedViewState(
      [
        {
          id: 'rss-1',
          type: 'rss',
          title: 'RSS Panel 1',
          snap: 'left',
          rect: { x: 0, y: 0, width: 690, height: 760 },
          controls_collapsed: false,
          scratch_note: '',
          time_override: null,
          rss_filters: {
            selected_feed_ids: [],
            selected_tags: [],
            q: '',
            read_status: 'all',
            star_status: 'all',
            view_mode: 'compact',
            page: 1,
            page_size: 25,
            sort: 'published_at_desc',
            show_advanced_filters: false,
          },
          alert_filters: null,
          selected_daily_brief_id: null,
        },
        {
          id: 'notes-1',
          type: 'notes',
          title: 'Notes Panel 1',
          snap: 'right',
          rect: { x: 690, y: 0, width: 690, height: 760 },
          controls_collapsed: false,
          scratch_note: 'Pivot questions',
          time_override: null,
          rss_filters: null,
          alert_filters: null,
          selected_daily_brief_id: null,
        },
      ],
      {
        time_range: 'all',
        custom_since_date: '',
        custom_until_date: '',
        rolling_days: '7',
      },
    )

    const preview = buildSavedViewPreview(
      {
        id: 'view-1',
        user_id: 'alice',
        name: 'Analyst view',
        created_at: '2026-04-22T10:00:00.000Z',
        query_json: queryJson,
      } satisfies SavedView,
      1380,
      760,
    )

    expect(preview.window_type_counts).toEqual({
      rss: 1,
      alerts: 0,
      notes: 1,
      daily_brief: 0,
    })
    expect(preview.windows).toHaveLength(2)
  })
})

describe('resolveSavedViewSelectionChange', () => {
  it('requires confirmation before loading another view during a protected edit session', () => {
    expect(
      resolveSavedViewSelectionChange({
        currentActiveSavedViewId: 'view-1',
        nextValue: 'view-2',
        hasProtectedEditSession: true,
      }),
    ).toEqual({
      kind: 'confirm_load',
      viewId: 'view-2',
    })
  })

  it('allows clearing the active view without discarding the edit snapshot', () => {
    expect(
      resolveSavedViewSelectionChange({
        currentActiveSavedViewId: 'view-1',
        nextValue: '',
        hasProtectedEditSession: true,
      }),
    ).toEqual({
      kind: 'clear',
    })
  })
})

describe('parseArticleBlocks', () => {
  it('preserves headings, lists, quotes, and paragraphs from analyst-facing article text', () => {
    expect(
      parseArticleBlocks(`# Summary

Threat actors targeted edge systems.
- Reset credentials
- Review VPN exposure
1. Validate logs
2. Rotate tokens
> Capture affected hosts first`),
    ).toEqual([
      { kind: 'heading', text: 'Summary' },
      { kind: 'paragraph', text: 'Threat actors targeted edge systems.' },
      { kind: 'bullet-list', items: ['Reset credentials', 'Review VPN exposure'] },
      { kind: 'numbered-list', items: ['Validate logs', 'Rotate tokens'] },
      { kind: 'quote', text: 'Capture affected hosts first' },
    ])
  })
})

describe('sanitizeHref', () => {
  it('allows only http and https links for rendered article HTML', () => {
    expect(sanitizeHref('https://example.com/report')).toBe('https://example.com/report')
    expect(sanitizeHref('http://example.com/report')).toBe('http://example.com/report')
    expect(sanitizeHref('mailto:ops@example.com')).toBeNull()
    expect(sanitizeHref('javascript:alert(1)')).toBeNull()
  })
})
