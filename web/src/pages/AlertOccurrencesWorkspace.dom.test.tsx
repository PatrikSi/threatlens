// @vitest-environment jsdom

import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { AlertOccurrence } from '../types/alerts'
;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const domMocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  currentRole: 'admin' as 'admin' | 'analyst' | 'viewer',
  currentUserError: false,
  listMode: 'data' as 'data' | 'loading' | 'error' | 'stale',
  listError: null as unknown,
  listPlaceholder: false,
  occurrences: [] as AlertOccurrence[],
  detail: null as AlertOccurrence | null,
  activity: [] as Array<Record<string, unknown>>,
  listQueryOptions: [] as Array<Record<string, unknown>>,
  mutationError: null as unknown,
  mutationCalls: [] as Array<{ key: string; variables: unknown }>,
  mutationData: {} as Record<string, unknown>,
  mutationErrors: {} as Record<string, unknown>,
  mutationPendingKeys: [] as string[],
  mutationVariables: {} as Record<string, unknown>,
  queryClient: {
    invalidateQueries: vi.fn(() => Promise.resolve()),
    setQueriesData: vi.fn(),
    setQueryData: vi.fn(),
  },
}))

vi.mock('../hooks/useCurrentUser', () => ({
  useCurrentUser: () => ({
    data: {
      id: 'user-1',
      email: 'analyst@example.com',
      role: domMocks.currentRole,
      is_active: true,
      is_approved: true,
      features: {},
    },
    isLoading: false,
    isError: domMocks.currentUserError,
  }),
}))

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...actual,
    apiFetch: (path: string, options?: RequestInit) => domMocks.apiFetch(path, options),
  }
})

vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => domMocks.queryClient,
  useQuery: (options: { queryKey: unknown[]; enabled?: boolean; placeholderData?: unknown }) => {
    const key = options.queryKey
    const scope = key[2]
    const base = {
      isFetching: false,
      isPlaceholderData: false,
      dataUpdatedAt: Date.parse('2026-08-27T12:00:00Z'),
      refetch: vi.fn(() => Promise.resolve()),
    }
    if (scope === 'rules') {
      return {
        ...base,
        data: [{ id: 'rule-1', name: 'Exchange watch', enabled: true }],
        isLoading: false,
        isError: false,
        error: null,
      }
    }
    if (scope === 'list') {
      domMocks.listQueryOptions.push(options as unknown as Record<string, unknown>)
      const path = String(key[3] ?? '')
      const query = new URL(path, 'https://threatlens.local').searchParams
      const page = Number(query.get('page') ?? 1)
      const data =
        domMocks.listMode === 'loading' || domMocks.listMode === 'error'
          ? undefined
          : {
              items: domMocks.occurrences,
              total: Math.max(domMocks.occurrences.length, 60),
              page,
              page_size: Number(query.get('page_size') ?? 25),
            }
      return {
        ...base,
        data,
        isLoading: domMocks.listMode === 'loading',
        isError: domMocks.listMode === 'error' || domMocks.listMode === 'stale',
        error: domMocks.listError,
        isPlaceholderData: domMocks.listPlaceholder,
      }
    }
    if (scope === 'detail') {
      return {
        ...base,
        data: options.enabled === false ? undefined : domMocks.detail,
        isLoading: options.enabled !== false && !domMocks.detail,
        isError: false,
        error: null,
      }
    }
    if (scope === 'activity') {
      return {
        ...base,
        data:
          options.enabled === false
            ? undefined
            : { items: domMocks.activity, total: domMocks.activity.length, page: 1, page_size: 25 },
        isLoading: false,
        isError: false,
        error: null,
      }
    }
    return { ...base, data: undefined, isLoading: false, isError: false, error: null }
  },
  useMutation: (options: {
    mutationKey?: unknown[]
    mutationFn: (variables: unknown) => Promise<unknown>
    onSuccess?: (result: unknown, variables: unknown) => void
    onError?: (error: unknown, variables: unknown) => void
  }) => {
    const key = options.mutationKey?.join(':') ?? 'unknown'
    return {
      mutate: vi.fn((variables: unknown) => {
        domMocks.mutationCalls.push({ key, variables })
        void options
          .mutationFn(variables)
          .then((result) => {
            domMocks.mutationData[key] = result
            delete domMocks.mutationErrors[key]
            options.onSuccess?.(result, variables)
          })
          .catch((error: unknown) => {
            domMocks.mutationErrors[key] = error
            options.onError?.(error, variables)
          })
      }),
      isPending: domMocks.mutationPendingKeys.includes(key),
      variables: domMocks.mutationVariables[key],
      isError: key in domMocks.mutationErrors,
      error: domMocks.mutationErrors[key] ?? null,
      data: domMocks.mutationData[key],
      reset: vi.fn(() => {
        delete domMocks.mutationData[key]
        delete domMocks.mutationErrors[key]
      }),
    }
  },
}))

import { ApiError } from '../api/client'
import { AlertOccurrencesWorkspace } from './AlertOccurrencesWorkspace'

let root: Root | null = null
let container: HTMLDivElement | null = null

function occurrence(overrides: Partial<AlertOccurrence> = {}): AlertOccurrence {
  return {
    id: 'occurrence-1',
    alert_interest_id: 'rule-1',
    rule_id_snapshot: 'rule-1',
    owner_user_id: 'user-1',
    item_id: 'item-1',
    item_id_snapshot: 'item-1',
    integration_event_id: 'event-1',
    rule_revision: 2,
    item_content_hash: 'a'.repeat(64),
    alert_name_snapshot: 'Exchange watch',
    alert_category_snapshot: 'vulnerability',
    alert_keywords_snapshot: ['exchange', 'cve'],
    matched_keywords: ['exchange'],
    source_snapshot_json: {
      item: {
        title: 'Exchange vulnerability report',
        summary: 'Security update available.',
        url: 'https://example.com/advisory',
        first_seen_at: '2026-08-27T10:00:00Z',
      },
      feed: { name: 'Vendor advisories' },
      classification: { primary_category: 'vulnerability' },
    },
    severity_snapshot: 'high',
    lifecycle_state: 'new',
    is_suppressed: false,
    suppressed_at: null,
    suppression_reason: null,
    is_snoozed: false,
    snoozed_until: null,
    snooze_reason: null,
    closure_disposition: null,
    acknowledged_at: null,
    acknowledged_by_user_id: null,
    investigating_at: null,
    investigating_by_user_id: null,
    closed_at: null,
    closed_by_user_id: null,
    version: 1,
    created_at: '2026-08-27T10:00:00Z',
    updated_at: '2026-08-27T10:00:00Z',
    ...overrides,
  }
}

function renderWorkspace() {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => root?.render(<AlertOccurrencesWorkspace />))
  return container
}

function rerenderWorkspace() {
  act(() => root?.render(<AlertOccurrencesWorkspace />))
}

function resetRenderedWorkspace() {
  act(() => root?.unmount())
  root = null
  container?.remove()
  container = null
  document.body.innerHTML = ''
}

function findButton(label: string) {
  return Array.from(document.querySelectorAll('button')).find(
    (button) => button.textContent?.trim() === label,
  )
}

async function click(element: HTMLElement | null | undefined) {
  expect(element).not.toBeNull()
  await act(async () => {
    element?.click()
    await Promise.resolve()
    await Promise.resolve()
  })
}

function setSelectValue(select: HTMLSelectElement, value: string) {
  const descriptor = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value')
  descriptor?.set?.call(select, value)
  select.dispatchEvent(new Event('change', { bubbles: true }))
}

beforeEach(() => {
  const first = occurrence()
  domMocks.currentRole = 'admin'
  domMocks.currentUserError = false
  domMocks.listMode = 'data'
  domMocks.listError = null
  domMocks.listPlaceholder = false
  domMocks.occurrences = [first]
  domMocks.detail = first
  domMocks.activity = [
    {
      id: 'activity-1',
      occurrence_id: first.id,
      actor_user_id: null,
      action: 'created',
      details_json: { matched_keyword_count: 1 },
      created_at: '2026-08-27T10:00:00Z',
    },
  ]
  domMocks.listQueryOptions = []
  domMocks.mutationError = null
  domMocks.mutationCalls = []
  domMocks.mutationData = {}
  domMocks.mutationErrors = {}
  domMocks.mutationPendingKeys = []
  domMocks.mutationVariables = {}
  domMocks.apiFetch.mockReset()
  domMocks.queryClient.invalidateQueries.mockClear()
  domMocks.queryClient.setQueriesData.mockClear()
  domMocks.queryClient.setQueryData.mockClear()
  domMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => {
    if (domMocks.mutationError) return Promise.reject(domMocks.mutationError)
    const body = options?.body ? (JSON.parse(String(options.body)) as Record<string, unknown>) : {}
    if (path.endsWith('/lifecycle')) {
      return Promise.resolve({
        ...domMocks.detail,
        lifecycle_state: body.state,
        closure_disposition: body.disposition ?? null,
        version: 2,
      })
    }
    if (path.includes('/bulk/')) {
      return Promise.resolve({ items: domMocks.occurrences, updated: domMocks.occurrences.length })
    }
    if (path.endsWith('/snooze')) {
      return Promise.resolve({
        ...domMocks.detail,
        is_snoozed: true,
        snoozed_until: body.snoozed_until,
        version: 2,
      })
    }
    return Promise.resolve({})
  })
})

afterEach(() => {
  resetRenderedWorkspace()
})

describe('AlertOccurrencesWorkspace operator workflows', () => {
  it('renders dense responsive triage controls with mobile-accessible labels and submits a versioned transition', async () => {
    const view = renderWorkspace()

    expect(view.textContent).toContain('Alert occurrence triage')
    expect(view.textContent).toContain('Server-filtered total')
    expect(view.querySelector('table')).not.toBeNull()
    const mobileList = view.querySelector('article')?.parentElement
    expect(mobileList?.className).toContain('md:hidden')
    expect(
      view.querySelectorAll('[aria-label="Inspect occurrence from Exchange watch"]'),
    ).toHaveLength(2)
    expect(
      view.querySelectorAll('[aria-label="Select occurrence from Exchange watch"]'),
    ).toHaveLength(2)
    const inspectControls = view.querySelectorAll<HTMLButtonElement>(
      '[aria-label="Inspect occurrence from Exchange watch"]',
    )
    const selectControls = view.querySelectorAll<HTMLInputElement>(
      '[aria-label="Select occurrence from Exchange watch"]',
    )
    expect(inspectControls[0]?.className).toContain('min-h-9')
    expect(inspectControls[1]?.className).toContain('min-h-11')
    expect(selectControls[1]?.parentElement?.className).toContain('h-11')
    expect(selectControls[1]?.parentElement?.className).toContain('w-11')

    await click(
      view.querySelector<HTMLButtonElement>(
        'button[aria-label="Inspect occurrence from Exchange watch"]',
      ),
    )
    expect(document.body.textContent).toContain('Evidence snapshot')
    expect(document.body.textContent).toContain('Occurrence created')

    await click(findButton('Acknowledge'))
    expect(domMocks.apiFetch).toHaveBeenCalledWith(
      '/alerts/occurrences/occurrence-1/lifecycle',
      expect.objectContaining({ method: 'PATCH' }),
    )
    const request = domMocks.apiFetch.mock.calls.at(-1)
    expect(JSON.parse(String(request?.[1]?.body))).toEqual({
      expected_version: 1,
      state: 'acknowledged',
    })
    expect(document.body.textContent).toContain('Occurrence moved to acknowledged.')
  })

  it('uses operation-specific pending labels and a polite live announcement', async () => {
    const view = renderWorkspace()
    await click(
      view.querySelector<HTMLButtonElement>(
        'button[aria-label="Inspect occurrence from Exchange watch"]',
      ),
    )
    domMocks.mutationPendingKeys = ['alerts:occurrences:lifecycle']
    domMocks.mutationVariables['alerts:occurrences:lifecycle'] = {
      occurrence: domMocks.detail,
      state: 'acknowledged',
    }
    rerenderWorkspace()

    const pendingButton = findButton('Acknowledging...')
    expect(pendingButton?.hasAttribute('disabled')).toBe(true)
    expect(
      Array.from(view.querySelectorAll('[role="status"]')).some(
        (status) => status.textContent === 'Acknowledging alert occurrence...',
      ),
    ).toBe(true)
    expect(findButton('Close occurrence')?.hasAttribute('disabled')).toBe(true)
  })

  it('restores focus to the Inspect control that opened occurrence details', async () => {
    const view = renderWorkspace()
    const inspect = view.querySelector<HTMLButtonElement>(
      'button[aria-label="Inspect occurrence from Exchange watch"]',
    )

    await click(inspect)
    expect(document.activeElement?.getAttribute('aria-labelledby')).toBe(
      'alert-occurrence-detail-heading',
    )
    await click(
      view.querySelector<HTMLButtonElement>('button[aria-label="Close occurrence details"]'),
    )
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 0))
    })

    expect(document.activeElement).toBe(inspect)
  })

  it('returns focus to Refresh when the originating row disappears during a refetch', async () => {
    const view = renderWorkspace()
    const inspect = view.querySelector<HTMLButtonElement>(
      'button[aria-label="Inspect occurrence from Exchange watch"]',
    )
    await click(inspect)

    domMocks.occurrences = []
    rerenderWorkspace()
    expect(inspect?.isConnected).toBe(false)
    await click(
      view.querySelector<HTMLButtonElement>('button[aria-label="Close occurrence details"]'),
    )
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 0))
    })

    expect(document.activeElement).toBe(view.querySelector('#alert-occurrences-refresh'))
  })

  it('keeps server filters and pagination in the query key and uses the supported bulk endpoint', async () => {
    const second = occurrence({ id: 'occurrence-2', item_id: 'item-2', version: 4 })
    domMocks.occurrences = [occurrence(), second]
    const view = renderWorkspace()

    await click(
      view.querySelector<HTMLInputElement>(
        'input[aria-label="Select all occurrences on the loaded page"]',
      ),
    )
    expect(document.body.textContent).toContain('2 selected')
    await click(findButton('Acknowledge selected'))

    expect(domMocks.apiFetch).toHaveBeenCalledWith(
      '/alerts/occurrences/bulk/acknowledge',
      expect.objectContaining({ method: 'POST' }),
    )
    const body = JSON.parse(String(domMocks.apiFetch.mock.calls.at(-1)?.[1]?.body))
    expect(body.items).toEqual([
      { occurrence_id: 'occurrence-1', expected_version: 1 },
      { occurrence_id: 'occurrence-2', expected_version: 4 },
    ])

    act(() => {
      const rule = view.querySelector<HTMLSelectElement>('#alert-occurrence-rule')
      setSelectValue(rule!, 'rule-1')
    })
    await click(
      Array.from(view.querySelectorAll('label'))
        .find((label) => label.textContent?.trim() === 'Critical')
        ?.querySelector('input'),
    )
    await click(findButton('Next'))

    const latest = domMocks.listQueryOptions.at(-1)
    const path = String((latest?.queryKey as unknown[])[3])
    const params = new URL(path, 'https://threatlens.local').searchParams
    expect(params.get('alert_interest_id')).toBe('rule-1')
    expect(params.getAll('severities')).toEqual(['critical'])
    expect(params.get('page')).toBe('2')
    expect(typeof latest?.placeholderData).toBe('function')
  })

  it('recovers from an optimistic concurrency conflict and does not present a generic retry as safe', async () => {
    domMocks.mutationError = new ApiError(
      'Alert occurrence changed since it was loaded: expected version 1, current version is 2.',
      409,
      '/alerts/occurrences/occurrence-1/lifecycle',
      null,
      { code: 'alert_occurrence_version_conflict' },
    )
    const view = renderWorkspace()
    await click(
      view.querySelector<HTMLButtonElement>(
        'button[aria-label="Inspect occurrence from Exchange watch"]',
      ),
    )
    await click(findButton('Acknowledge'))

    expect(document.body.textContent).toContain('The latest state has been requested')
    expect(document.body.textContent).toContain('review it before retrying')
    expect(domMocks.queryClient.invalidateQueries).toHaveBeenCalledWith({
      queryKey: ['alerts', 'occurrences'],
    })
  })

  it('corrects a closure disposition without implying that closed occurrences can reopen', async () => {
    const closed = occurrence({
      lifecycle_state: 'closed',
      closure_disposition: 'false_positive',
      version: 7,
    })
    domMocks.occurrences = [closed]
    domMocks.detail = closed
    const view = renderWorkspace()

    await click(
      view.querySelector<HTMLButtonElement>(
        'button[aria-label="Inspect occurrence from Exchange watch"]',
      ),
    )
    expect(document.body.textContent).toContain('cannot be reopened')
    expect(document.body.textContent).not.toContain('Reopen occurrence')
    await click(findButton('Change closure disposition'))
    expect(document.body.textContent).toContain('Change closure disposition?')
    expect(findButton('Update disposition')?.hasAttribute('disabled')).toBe(true)

    act(() => {
      setSelectValue(
        document.querySelector<HTMLSelectElement>('#alert-close-disposition')!,
        'benign',
      )
    })
    await click(findButton('Update disposition'))

    const request = domMocks.apiFetch.mock.calls.at(-1)
    expect(request?.[0]).toBe('/alerts/occurrences/occurrence-1/lifecycle')
    expect(JSON.parse(String(request?.[1]?.body))).toEqual({
      expected_version: 7,
      state: 'closed',
      disposition: 'benign',
    })
    expect(document.body.textContent).toContain('Closure disposition updated.')
  })

  it('locks closure inputs while their update is in flight', async () => {
    const closed = occurrence({
      lifecycle_state: 'closed',
      closure_disposition: 'false_positive',
      version: 7,
    })
    domMocks.occurrences = [closed]
    domMocks.detail = closed
    const view = renderWorkspace()
    await click(
      view.querySelector<HTMLButtonElement>(
        'button[aria-label="Inspect occurrence from Exchange watch"]',
      ),
    )
    await click(findButton('Change closure disposition'))
    act(() => {
      setSelectValue(
        document.querySelector<HTMLSelectElement>('#alert-close-disposition')!,
        'benign',
      )
    })
    domMocks.mutationPendingKeys = ['alerts:occurrences:lifecycle']
    domMocks.mutationVariables['alerts:occurrences:lifecycle'] = {
      occurrence: closed,
      state: 'closed',
      disposition: 'benign',
    }
    rerenderWorkspace()

    expect(
      document.querySelector<HTMLSelectElement>('#alert-close-disposition')?.disabled,
    ).toBe(true)
    expect(findButton('Updating disposition...')?.hasAttribute('disabled')).toBe(true)
    expect(findButton('Cancel')?.hasAttribute('disabled')).toBe(true)
  })

  it('fails into an explicit read-only state after a permission denial', async () => {
    domMocks.mutationError = new ApiError(
      'The API denied this operation.',
      403,
      '/alerts/occurrences/occurrence-1/lifecycle',
    )
    const view = renderWorkspace()
    await click(
      view.querySelector<HTMLButtonElement>(
        'button[aria-label="Inspect occurrence from Exchange watch"]',
      ),
    )
    await click(findButton('Acknowledge'))

    expect(document.body.textContent).toContain('cannot update them')
    expect(document.body.textContent).toContain(
      'Verify it has permission to manage alerts; API tokens also need write:alerts',
    )
    expect(findButton('Acknowledge')?.hasAttribute('disabled')).toBe(true)
  })

  it('keeps stale data visible, handles loading and empty states, and exposes backfill only to administrators', async () => {
    domMocks.listMode = 'stale'
    domMocks.listError = new ApiError('Worker timeout.', 503, '/alerts/occurrences')
    renderWorkspace()
    expect(document.body.textContent).toContain('The last loaded data remains visible')
    expect(findButton('Backfill history')).not.toBeUndefined()
    await click(findButton('Backfill history'))
    expect(document.body.textContent).toContain(
      'Backfill never sends SMTP or webhook notifications',
    )
    expect(document.body.textContent).toContain('Preview backfill')

    resetRenderedWorkspace()
    domMocks.currentRole = 'analyst'
    domMocks.listMode = 'loading'
    const loadingView = renderWorkspace()
    expect(loadingView.textContent).toContain('Loading alert occurrences...')
    expect(findButton('Backfill history')).toBeUndefined()

    resetRenderedWorkspace()
    domMocks.listMode = 'data'
    domMocks.occurrences = []
    const emptyView = renderWorkspace()
    expect(emptyView.textContent).toContain('No matching alert occurrences')
  })

  it('fails closed when the current administrator role cannot be refreshed', () => {
    domMocks.currentUserError = true
    const view = renderWorkspace()
    expect(view.textContent).not.toContain('Backfill history')
  })

  it('applies an immutable preview token and continues with the returned cursor', async () => {
    let previewCall = 0
    domMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => {
      const body = options?.body
        ? (JSON.parse(String(options.body)) as Record<string, unknown>)
        : {}
      if (path.endsWith('/reconciliation/preview')) {
        previewCall += 1
        return Promise.resolve({
          preview_token: `preview-${previewCall}`,
          expires_at: '2026-08-27T12:15:00Z',
          candidates: [
            {
              item_id: `item-${previewCall}`,
              content_hash: 'a'.repeat(64),
              title: `Preview item ${previewCall}`,
              first_seen_at: '2026-08-27T10:00:00Z',
            },
          ],
          matched_count: 2,
          returned_count: 1,
          truncated: previewCall === 1,
          has_more: previewCall === 1,
          next_cursor_first_seen_at: previewCall === 1 ? '2026-08-27T10:00:00Z' : null,
          next_cursor_item_id: previewCall === 1 ? '00000000-0000-0000-0000-000000000001' : null,
          notifications_enabled: false,
        })
      }
      if (path.endsWith('/reconciliation/apply')) {
        expect(body).toEqual({ preview_token: 'preview-1' })
        return Promise.resolve({
          accepted: 1,
          existing: 0,
          skipped: 0,
          enqueue_failed: false,
          has_more: true,
          next_cursor_first_seen_at: '2026-08-27T10:00:00Z',
          next_cursor_item_id: '00000000-0000-0000-0000-000000000001',
          notifications_enabled: false,
        })
      }
      return Promise.resolve({})
    })
    renderWorkspace()
    await click(findButton('Backfill history'))
    await click(findButton('Preview backfill'))
    rerenderWorkspace()
    expect(document.body.textContent).toContain('More articles remain after this reviewed page')

    await click(findButton('Apply reviewed page'))
    rerenderWorkspace()
    expect(document.body.textContent).toContain('More articles remain. Preview the next page')
    await click(findButton('Preview next page'))

    const finalPreview = domMocks.apiFetch.mock.calls
      .filter(([path]) => String(path).endsWith('/reconciliation/preview'))
      .at(-1)
    expect(JSON.parse(String(finalPreview?.[1]?.body))).toEqual(
      expect.objectContaining({
        cursor_first_seen_at: '2026-08-27T10:00:00Z',
        cursor_item_id: '00000000-0000-0000-0000-000000000001',
      }),
    )
  })
})
