// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const domMocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  currentUser: {
    id: 'analyst-1',
    email: 'analyst@example.com',
    role: 'analyst' as const,
    access: {
      permissions: [
        'write:investigations',
        'read:items',
        'read:reports',
        'read:alerts',
      ] as string[],
    },
    is_active: true,
    is_approved: true,
    approved_at: '2026-08-01T10:00:00Z',
    created_at: '2026-08-01T10:00:00Z',
    features: {
      ai_enabled: true,
      ai_configured: true,
      ai_summary_enabled: true,
      ai_relevance_enabled: true,
      ai_daily_brief_enabled: true,
    },
  },
}))

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return { ...actual, apiFetch: domMocks.apiFetch }
})

vi.mock('../hooks/useCurrentUser', () => ({
  useCurrentUser: () => ({
    data: domMocks.currentUser,
    isLoading: false,
    isError: false,
    error: null,
  }),
}))

import { ApiError } from '../api/client'
import type {
  InvestigationDetail,
  InvestigationEvidenceCandidate,
  InvestigationEvidenceCandidateListResponse,
} from '../types/investigations'
import { InvestigationsPage } from './InvestigationsPage'

let root: Root | null = null
let container: HTMLDivElement | null = null
let queryClient: QueryClient | null = null

const candidate: InvestigationEvidenceCandidate = {
  source_type: 'ioc',
  source_id: '22222222-2222-4222-8222-222222222222',
  title: 'evil.example',
  description: 'Domain observed in credential-harvesting infrastructure.',
  url: null,
  observed_at: '2026-08-31T12:00:00Z',
  source_label: 'Domain',
  metadata: {
    ioc_type: 'domain',
    value: 'evil.example',
    observation_count: 4,
    related_items: [
      {
        item_id: 'item-1',
        title: 'Credential campaign expands',
        feed_name: 'Vendor research',
        first_seen_at: '2026-08-31T11:00:00Z',
      },
    ],
  },
  already_attached: false,
  match_reason: 'exact_ioc',
}

const alertCandidate: InvestigationEvidenceCandidate = {
  ...candidate,
  source_type: 'alert_occurrence',
  source_id: '33333333-3333-4333-8333-333333333333',
  title: 'Domain keyword alert',
  source_label: 'Keyword',
  metadata: { severity: 'high', lifecycle_state: 'open' },
  match_reason: 'text',
}

const alreadyAttachedCandidate: InvestigationEvidenceCandidate = {
  ...candidate,
  source_type: 'item',
  source_id: '55555555-5555-4555-8555-555555555555',
  title: 'Previously attached article',
  source_label: 'Vendor research',
  metadata: {},
  already_attached: true,
  match_reason: 'related_ioc',
}

const pageTwoCandidate: InvestigationEvidenceCandidate = {
  ...candidate,
  source_type: 'item',
  source_id: '66666666-6666-4666-8666-666666666666',
  title: 'Second-page campaign report',
  source_label: 'Vendor research',
  metadata: {},
  match_reason: 'text',
}

const baseDetail: InvestigationDetail = {
  id: '11111111-1111-4111-8111-111111111111',
  title: 'Exchange exploitation review',
  description: 'Correlate observed indicators and vendor reporting.',
  status: 'open',
  severity: 'high',
  visibility: 'private',
  disposition: null,
  assignee_user_id: 'analyst-1',
  assignee_email: 'analyst@example.com',
  current_user_role: 'owner',
  evidence_count: 0,
  member_count: 1,
  note_count: 0,
  version: 7,
  created_at: '2026-08-20T09:00:00Z',
  updated_at: '2026-08-26T11:30:00Z',
  closed_at: null,
  archived_at: null,
  members: [{
    user_id: 'analyst-1',
    email: 'analyst@example.com',
    role: 'owner',
    created_at: '2026-08-20T09:00:00Z',
  }],
  evidence: [],
  evidence_truncated: false,
  notes: [],
  notes_truncated: false,
}

beforeEach(() => {
  domMocks.apiFetch.mockReset()
  domMocks.currentUser.access.permissions = [
    'write:investigations',
    'read:items',
    'read:reports',
    'read:alerts',
  ]
})

afterEach(() => {
  act(() => root?.unmount())
  root = null
  container?.remove()
  container = null
  queryClient?.clear()
  queryClient = null
  document.body.innerHTML = ''
})

describe('Investigation evidence finder', () => {
  it('searches human-readable evidence, uses server IOC analysis, and posts the hidden stable ID', async () => {
    let attached = false
    domMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => {
      if (isCandidateSearch(path)) {
        const query = candidateSearchBody(options).q
        return Promise.resolve(candidateResponse(
          attached
            ? [{ ...candidate, already_attached: true }, alreadyAttachedCandidate]
            : [candidate, alreadyAttachedCandidate],
          {
            kind: query?.startsWith('https://') ? 'url' : query === 'evil.example' ? 'ioc' : 'empty',
            normalized_value: query ?? null,
            detected_ioc_type: query ? 'domain' : null,
          },
        ))
      }
      if (path.endsWith('/evidence') && options?.method === 'POST') {
        attached = true
        return Promise.resolve({ ...baseDetail, evidence_count: 1, version: 8 })
      }
      if (path.includes('/evidence?page=')) {
        return Promise.resolve({
          evidence: [],
          total: attached ? 1 : 0,
          page: 1,
          page_size: 25,
        })
      }
      return Promise.reject(new Error(`Unexpected request: ${path}`))
    })
    await renderDetail()

    act(() => findButton('Add evidence')?.click())
    await flushRequests(2)
    const initialCandidateRequest = domMocks.apiFetch.mock.calls.find(
      ([path, options]) => isCandidateSearch(path) && candidateSearchBody(options).q === null,
    )
    expect(candidateSearchBody(initialCandidateRequest?.[1]).source_types).toEqual([
      'item',
      'report',
      'alert_occurrence',
    ])
    expect(pageText()).toContain('Recent suggestions omit indicators for faster browsing')
    const search = document.querySelector<HTMLInputElement>('#investigation-evidence-search')!
    expect(search.maxLength).toBe(255)
    act(() => setInputValue(search, 'evil.example'))
    expect(pageText()).toContain('Searching the current scope')
    await flushRequests(3, 340)

    expect(pageText()).toContain('Detected: Domain')
    const indicatorSearchRequest = [...domMocks.apiFetch.mock.calls].reverse().find(
      ([path, options]) =>
        isCandidateSearch(path) && candidateSearchBody(options).q === 'evil.example',
    )
    expect(candidateSearchBody(indicatorSearchRequest?.[1]).source_types).toContain('ioc')
    act(() => setInputValue(search, 'https://evil.example/path'))
    await flushRequests(3, 340)
    expect(pageText()).toContain('Detected: URL · Domain host')
    act(() => setInputValue(search, 'evil.example'))
    await flushRequests(3, 340)
    expect(pageText()).toContain('Already attached')
    expect(document.querySelector<HTMLInputElement>(
      `input[value="item:${alreadyAttachedCandidate.source_id}"]`,
    )?.disabled).toBe(true)
    const radio = document.querySelector<HTMLInputElement>(
      'input[name="investigation-evidence-candidate"]',
    )!
    act(() => radio.click())
    expect(pageText()).toContain('Evidence preview')
    expect(pageText()).toContain('Credential campaign expands')
    expect(document.querySelector<HTMLInputElement>('input[name="source_id"]')?.value).toBe(
      candidate.source_id,
    )

    const note = document.querySelector<HTMLTextAreaElement>('#investigation-evidence-note')!
    act(() => setTextAreaValue(note, 'Correlates with the initial victim report.'))
    act(() => findButton('Attach evidence')?.click())
    await flushRequests(4)

    const post = domMocks.apiFetch.mock.calls.find(
      ([path, options]) => path.endsWith('/evidence') && options?.method === 'POST',
    )
    expect(JSON.parse(post?.[1].body as string)).toEqual({
      source_type: 'ioc',
      source_id: candidate.source_id,
      note: 'Correlates with the initial victim report.',
      expected_version: 7,
    })
    expect(pageText()).toContain('Evidence added.')
    expect(document.querySelector('#investigation-evidence-note')).toBeNull()
  })

  it('keeps saved evidence visible when discovery fails and never queries forbidden sources', async () => {
    domMocks.currentUser.access.permissions = ['write:investigations', 'read:items']
    const savedEvidence = {
      id: 'evidence-1',
      source_type: 'item' as const,
      source_id: '44444444-4444-4444-8444-444444444444',
      title_snapshot: 'Saved incident analysis',
      description_snapshot: 'A point-in-time article snapshot.',
      url_snapshot: null,
      metadata_snapshot: { feed_name: 'Internal research' },
      note: null,
      added_by_user_id: 'analyst-1',
      created_at: '2026-08-30T10:00:00Z',
    }
    const detail = { ...baseDetail, evidence: [savedEvidence], evidence_count: 1 }
    domMocks.apiFetch.mockRejectedValue(new Error('search index unavailable'))
    await renderDetail(detail)

    act(() => findButton('Add evidence')?.click())
    await flushRequests(2)

    expect(pageText()).toContain('Evidence search could not be loaded')
    expect(pageText()).toContain('Saved incident analysis')
    expect(findButton('Retry search')).not.toBeNull()
    const candidateRequest = domMocks.apiFetch.mock.calls.find(([path]) =>
      isCandidateSearch(path),
    )
    expect(candidateSearchBody(candidateRequest?.[1]).source_types).toEqual(['item'])
    expect(optionFor('Reports')?.disabled).toBe(true)
    expect(optionFor('My alerts')?.disabled).toBe(true)

    const technicalDetails = Array.from(document.querySelectorAll('details')).find((details) =>
      details.querySelector('summary')?.textContent?.includes('Technical details'),
    )
    expect(technicalDetails?.textContent).toContain(savedEvidence.source_id)
    expect(technicalDetails?.textContent).toContain('Copy')
  })

  it('holds underspecified text locally instead of issuing expensive fuzzy searches', async () => {
    domMocks.apiFetch.mockImplementation((path: string) => {
      if (isCandidateSearch(path)) {
        return Promise.resolve(candidateResponse([candidate]))
      }
      return Promise.reject(new Error(`Unexpected request: ${path}`))
    })
    await renderDetail()

    act(() => findButton('Add evidence')?.click())
    await flushRequests(2)
    const search = document.querySelector<HTMLInputElement>('#investigation-evidence-search')!
    act(() => setInputValue(search, 'ab'))
    await flushRequests(2, 340)

    expect(pageText()).toContain('Enter at least 3 characters')
    expect(domMocks.apiFetch.mock.calls.filter(([path]) =>
      isCandidateSearch(path),
    )).toHaveLength(1)

    act(() => setInputValue(search, 'abc'))
    await flushRequests(3, 340)
    expect(domMocks.apiFetch.mock.calls.filter(([path]) =>
      isCandidateSearch(path),
    )).toHaveLength(2)
  })

  it('keeps explicit indicator browsing available without a search term', async () => {
    domMocks.apiFetch.mockImplementation((path: string) => {
      if (isCandidateSearch(path)) return Promise.resolve(candidateResponse([candidate]))
      return Promise.reject(new Error(`Unexpected request: ${path}`))
    })
    await renderDetail()

    act(() => findButton('Add evidence')?.click())
    await flushRequests(2)
    expect(pageText()).not.toContain(candidate.title)

    const source = document.querySelector<HTMLSelectElement>(
      '#investigation-evidence-source-filter',
    )!
    act(() => setSelectValue(source, 'ioc'))
    await flushRequests(2)

    const explicitRequest = [...domMocks.apiFetch.mock.calls].reverse().find(
      ([path, options]) =>
        isCandidateSearch(path) && candidateSearchBody(options).source_types.length === 1,
    )
    expect(candidateSearchBody(explicitRequest?.[1]).source_types).toEqual(['ioc'])
    expect(pageText()).toContain(candidate.title)
    expect(pageText()).not.toContain('Recent suggestions omit indicators')
  })

  it('labels bounded totals as lower bounds and always shows refinement guidance', async () => {
    domMocks.apiFetch.mockImplementation((path: string) => {
      if (isCandidateSearch(path)) {
        return Promise.resolve({
          ...candidateResponse([pageTwoCandidate]),
          total: 400,
          total_truncated: true,
        })
      }
      return Promise.reject(new Error(`Unexpected request: ${path}`))
    })
    await renderDetail()

    act(() => findButton('Add evidence')?.click())
    await flushRequests(2)

    expect(pageText()).toContain('1-1 of at least 400 · Page 1 of 20')
    expect(pageText()).toContain(
      'Only the first 400 matches are available in this view. Refine the search or scope',
    )
  })

  it('rebases a preserved evidence draft before retrying a version conflict', async () => {
    const refreshed = { ...baseDetail, version: 8, updated_at: '2026-08-31T13:00:00Z' }
    const saved = { ...refreshed, version: 9, evidence_count: 1 }
    domMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => {
      if (isCandidateSearch(path)) {
        return Promise.resolve(candidateResponse([candidate]))
      }
      if (path.endsWith('/evidence') && options?.method === 'POST') {
        const expectedVersion = JSON.parse(options.body as string).expected_version
        return expectedVersion === 7
          ? Promise.reject(new ApiError(
            'The investigation changed after you loaded it.',
            409,
            path,
          ))
          : Promise.resolve(saved)
      }
      if (path.includes('/evidence?page=')) {
        return Promise.resolve({ evidence: [], total: 0, page: 1, page_size: 25 })
      }
      if (path === `/investigations/${baseDetail.id}`) return Promise.resolve(refreshed)
      return Promise.reject(new Error(`Unexpected request: ${path}`))
    })
    await renderDetail()

    act(() => findButton('Add evidence')?.click())
    await flushRequests(2)
    act(() => setSelectValue(
      document.querySelector<HTMLSelectElement>('#investigation-evidence-source-filter')!,
      'ioc',
    ))
    await flushRequests(2)
    act(() => document.querySelector<HTMLInputElement>(
      'input[name="investigation-evidence-candidate"]',
    )?.click())
    const note = document.querySelector<HTMLTextAreaElement>('#investigation-evidence-note')!
    act(() => setTextAreaValue(note, 'Preserve this conflict context.'))
    act(() => findButton('Attach evidence')?.click())
    await flushRequests(4)

    expect(pageText()).toContain('rebase your preserved draft before retrying')
    expect(document.querySelector<HTMLTextAreaElement>('#investigation-evidence-note')?.value).toBe(
      'Preserve this conflict context.',
    )
    act(() => findButton('Review and rebase draft')?.click())
    await flushRequests(2)
    expect(pageText()).toContain('Latest version loaded')

    act(() => findButton('Attach evidence')?.click())
    await flushRequests(4)
    const writes = domMocks.apiFetch.mock.calls.filter(
      ([path, options]) => path.endsWith('/evidence') && options?.method === 'POST',
    )
    expect(writes.map(([, options]) => JSON.parse(options.body as string).expected_version)).toEqual(
      [7, 8],
    )
    expect(pageText()).toContain('Evidence added.')
  })

  it('preserves a failed alert attachment draft and locks the rejected capability', async () => {
    domMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => {
      if (isCandidateSearch(path)) {
        return Promise.resolve(candidateResponse([alertCandidate]))
      }
      if (path.endsWith('/evidence') && options?.method === 'POST') {
        return Promise.reject(new ApiError(
          'Alert occurrence evidence is unavailable until durable Alerting v2 is enabled.',
          422,
          path,
        ))
      }
      return Promise.reject(new Error(`Unexpected request: ${path}`))
    })
    await renderDetail()

    act(() => findButton('Add evidence')?.click())
    await flushRequests(2)
    const radio = document.querySelector<HTMLInputElement>(
      'input[name="investigation-evidence-candidate"]',
    )!
    act(() => radio.click())
    const note = document.querySelector<HTMLTextAreaElement>('#investigation-evidence-note')!
    act(() => setTextAreaValue(note, 'Preserve this analyst context.'))
    act(() => findButton('Attach evidence')?.click())
    await flushRequests(4)

    expect(document.querySelector<HTMLTextAreaElement>('#investigation-evidence-note')?.value).toBe(
      'Preserve this analyst context.',
    )
    expect(document.querySelector<HTMLInputElement>('input[name="source_id"]')?.value).toBe(
      alertCandidate.source_id,
    )
    expect(pageText()).toContain(
      'Alert occurrence evidence is unavailable until durable Alerting v2 is enabled.',
    )
    expect(findButton('Attach evidence')?.disabled).toBe(true)
    act(() => document.querySelector<HTMLAnchorElement>('a[href="/investigations"]')?.click())
    await flushRequests()
    expect(document.querySelector('[role="alertdialog"]')?.textContent).toContain(
      'Discard unsaved investigation changes?',
    )
  })

  it('moves a failed attachment error to the workspace when another result is selected', async () => {
    domMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => {
      if (isCandidateSearch(path)) {
        return Promise.resolve(candidateResponse([candidate, pageTwoCandidate]))
      }
      if (path.endsWith('/evidence') && options?.method === 'POST') {
        return Promise.reject(new ApiError('Attachment service unavailable.', 503, path))
      }
      return Promise.reject(new Error(`Unexpected request: ${path}`))
    })
    await renderDetail()

    act(() => findButton('Add evidence')?.click())
    await flushRequests(2)
    act(() => setInputValue(
      document.querySelector<HTMLInputElement>('#investigation-evidence-search')!,
      'campaign',
    ))
    await flushRequests(3, 340)
    act(() => document.querySelector<HTMLInputElement>(
      `input[value="ioc:${candidate.source_id}"]`,
    )?.click())
    act(() => findButton('Attach evidence')?.click())
    await flushRequests(4)

    const contextualError = document.querySelector<HTMLElement>('[role="alert"]')
    expect(contextualError?.textContent).toContain('Attachment service unavailable.')
    act(() => document.querySelector<HTMLInputElement>(
      `input[value="item:${pageTwoCandidate.source_id}"]`,
    )?.click())

    expect(pageText()).toContain(pageTwoCandidate.title)
    const workspaceError = document.querySelector<HTMLElement>('[role="alert"]')
    expect(workspaceError?.textContent).toContain('Attachment service unavailable.')
  })

  it('clears a selected record when moving to another candidate page', async () => {
    domMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => {
      if (isCandidateSearch(path)) {
        const page = candidateSearchBody(options).page === 2
          ? 2
          : 1
        return Promise.resolve({
          ...candidateResponse(page === 1 ? [candidate] : [pageTwoCandidate]),
          total: 2,
          page,
          page_size: 1,
        })
      }
      return Promise.reject(new Error(`Unexpected request: ${path}`))
    })
    await renderDetail()

    act(() => findButton('Add evidence')?.click())
    await flushRequests(2)
    act(() => setInputValue(
      document.querySelector<HTMLInputElement>('#investigation-evidence-search')!,
      'campaign',
    ))
    await flushRequests(3, 340)
    act(() => document.querySelector<HTMLInputElement>(
      'input[name="investigation-evidence-candidate"]',
    )?.click())
    const note = document.querySelector<HTMLTextAreaElement>('#investigation-evidence-note')!
    act(() => setTextAreaValue(note, 'Context that should survive pagination.'))
    expect(document.querySelector<HTMLInputElement>('input[name="source_id"]')?.value).toBe(
      candidate.source_id,
    )

    act(() => findButton('Next')?.click())
    expect(document.querySelector<HTMLInputElement>('input[name="source_id"]')?.value).not.toBe(
      candidate.source_id,
    )
    expect(findButton('Attach evidence')?.disabled ?? true).toBe(true)
    await flushRequests(2)

    expect(pageText()).toContain('Second-page campaign report')
    expect(pageText()).not.toContain('Credential campaign expands')
    const pageTwoRequest = domMocks.apiFetch.mock.calls.find(([path, options]) =>
      isCandidateSearch(path) && candidateSearchBody(options).page === 2,
    )
    expect(candidateSearchBody(pageTwoRequest?.[1]).as_of).toBe(
      '2026-09-01T00:00:00Z',
    )
    act(() => document.querySelector<HTMLInputElement>(
      'input[name="investigation-evidence-candidate"]',
    )?.click())
    expect(document.querySelector<HTMLTextAreaElement>('#investigation-evidence-note')?.value).toBe(
      'Context that should survive pagination.',
    )
  })

  it('does not expose evidence discovery to read-only investigation members', async () => {
    await renderDetail({ ...baseDetail, current_user_role: 'viewer' })

    expect(findButton('Add evidence')).toBeNull()
    expect(document.querySelector('#investigation-evidence-search')).toBeNull()
    expect(domMocks.apiFetch.mock.calls.some(([path]) =>
      isCandidateSearch(path),
    )).toBe(false)
  })
})

function candidateResponse(
  candidates: InvestigationEvidenceCandidate[],
  queryAnalysis: InvestigationEvidenceCandidateListResponse['query_analysis'] = {
    kind: 'empty',
    normalized_value: null,
    detected_ioc_type: null,
  },
): InvestigationEvidenceCandidateListResponse {
  return {
    candidates,
    total: candidates.length,
    total_truncated: false,
    page: 1,
    page_size: 20,
    query_analysis: queryAnalysis,
    source_capabilities: [
      { source_type: 'item', available: true, unavailable_reason: null, required_permissions: ['read:items'] },
      { source_type: 'ioc', available: true, unavailable_reason: null, required_permissions: ['read:items'] },
      { source_type: 'report', available: true, unavailable_reason: null, required_permissions: ['read:reports'] },
      { source_type: 'alert_occurrence', available: true, unavailable_reason: null, required_permissions: ['read:alerts', 'read:items'] },
    ],
    effective_range: '7d',
    effective_since: '2026-08-25T00:00:00Z',
    effective_until: '2026-09-01T00:00:00Z',
  }
}

async function renderDetail(detail = baseDetail) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false },
      mutations: { retry: false },
    },
  })
  queryClient = client
  client.setQueryData(['investigations', 'detail', detail.id], detail)
  client.setQueryData(['investigations', 'evidence', detail.id, 1], {
    evidence: detail.evidence,
    total: detail.evidence_count,
    page: 1,
    page_size: 25,
  })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  const router = createMemoryRouter(
    [
      { path: '/investigations', element: <div>Investigation list</div> },
      { path: '/investigations/:investigationId', element: <InvestigationsPage /> },
    ],
    { initialEntries: [`/investigations/${detail.id}?tab=evidence`] },
  )
  await act(async () => {
    root?.render(
      <QueryClientProvider client={client}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    )
  })
  await flushRequests()
}

async function flushRequests(rounds = 1, delay = 0) {
  for (let index = 0; index < rounds; index += 1) {
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, delay))
    })
  }
}

function pageText() {
  return document.body.textContent ?? ''
}

function findButton(label: string) {
  return Array.from(document.querySelectorAll<HTMLButtonElement>('button')).find(
    (button) => button.textContent?.trim() === label,
  ) ?? null
}

function optionFor(label: string) {
  return Array.from(document.querySelectorAll<HTMLOptionElement>('option')).find(
    (option) => option.textContent?.startsWith(label),
  ) ?? null
}

function setInputValue(input: HTMLInputElement, value: string) {
  const descriptor = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')
  descriptor?.set?.call(input, value)
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

function setTextAreaValue(input: HTMLTextAreaElement, value: string) {
  const descriptor = Object.getOwnPropertyDescriptor(
    window.HTMLTextAreaElement.prototype,
    'value',
  )
  descriptor?.set?.call(input, value)
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

function setSelectValue(select: HTMLSelectElement, value: string) {
  const descriptor = Object.getOwnPropertyDescriptor(
    window.HTMLSelectElement.prototype,
    'value',
  )
  descriptor?.set?.call(select, value)
  select.dispatchEvent(new Event('change', { bubbles: true }))
}

function isCandidateSearch(path: string): boolean {
  return path.endsWith('/evidence-candidates')
}

function candidateSearchBody(options?: RequestInit): {
  q: string | null
  page: number
  as_of: string | null
  source_types: string[]
} {
  if (typeof options?.body !== 'string') {
    return { q: null, page: 1, as_of: null, source_types: [] }
  }
  return JSON.parse(options.body) as {
    q: string | null
    page: number
    as_of: string | null
    source_types: string[]
  }
}
