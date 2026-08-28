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
    id: 'user-1',
    email: 'analyst@example.com',
    role: 'analyst' as 'admin' | 'analyst' | 'viewer',
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
import type { InvestigationDetail, InvestigationListResponse } from '../types/investigations'
import { InvestigationsPage } from './InvestigationsPage'

let root: Root | null = null
let container: HTMLDivElement | null = null
let queryClient: QueryClient | null = null

const baseDetail: InvestigationDetail = {
  id: '11111111-1111-4111-8111-111111111111',
  title: 'Exchange exploitation review',
  description: 'Correlate observed indicators and vendor reporting.',
  status: 'open',
  severity: 'high',
  visibility: 'private',
  disposition: null,
  assignee_user_id: 'user-1',
  assignee_email: 'analyst@example.com',
  current_user_role: 'owner',
  evidence_count: 0,
  member_count: 1,
  note_count: 1,
  version: 7,
  created_at: '2026-08-20T09:00:00Z',
  updated_at: '2026-08-26T11:30:00Z',
  closed_at: null,
  archived_at: null,
  members: [
    {
      user_id: 'user-1',
      email: 'analyst@example.com',
      role: 'owner',
      created_at: '2026-08-20T09:00:00Z',
    },
  ],
  evidence: [],
  evidence_truncated: false,
  notes: [
    {
      id: 'note-1',
      author_user_id: 'user-1',
      author_email: 'analyst@example.com',
      body: 'Initial working theory',
      version: 3,
      created_at: '2026-08-20T09:30:00Z',
      updated_at: '2026-08-20T09:30:00Z',
    },
  ],
  notes_truncated: false,
}

const listResponse: InvestigationListResponse = {
  investigations: [baseDetail],
  total: 1,
  page: 1,
  page_size: 25,
}

beforeEach(() => {
  domMocks.apiFetch.mockReset()
  domMocks.currentUser.role = 'analyst'
})

afterEach(() => {
  act(() => root?.unmount())
  root = null
  container?.remove()
  container = null
  document.body.innerHTML = ''
  queryClient?.clear()
  queryClient = null
})

describe('InvestigationsPage DOM workflows', () => {
  it('renders compact desktop and mobile list alternatives with clear result counts', async () => {
    domMocks.apiFetch.mockResolvedValue(listResponse)
    await renderAt('/investigations')

    expect(document.querySelector('table[aria-label="Investigations"]')).not.toBeNull()
    expect(document.querySelector('[data-layout="mobile-cards"]')).not.toBeNull()
    expect(pageText()).toContain('1-1 of 1')
    expect(pageText()).toContain('Exchange exploitation review')
    expect(pageText()).toContain('0 evidence')
    expect(findButton('Create investigation')).not.toBeNull()
  })

  it('hides investigation creation for global viewers', async () => {
    domMocks.currentUser.role = 'viewer'
    domMocks.apiFetch.mockResolvedValue(listResponse)
    await renderAt('/investigations')

    expect(findButton('Create investigation')).toBeNull()
  })

  it('renders loading, empty, and initial-error states with recovery', async () => {
    domMocks.apiFetch.mockImplementation(() => new Promise(() => {}))
    await renderAt('/investigations', false)
    expect(document.querySelector('[role="status"]')?.textContent).toContain('Loading investigations')
    cleanupRender()

    domMocks.apiFetch.mockResolvedValue({ ...listResponse, investigations: [], total: 0 })
    await renderAt('/investigations')
    expect(pageText()).toContain('No matching investigations')
    expect(pageText()).toContain('Create the first investigation')
    cleanupRender()

    domMocks.apiFetch.mockRejectedValue(new Error('database unavailable'))
    await renderAt('/investigations')
    expect(document.querySelector('[role="alert"]')?.textContent).toContain('Investigations could not be loaded')
    expect(findButton('Retry')).not.toBeNull()
  })

  it('keeps stale list data visible when refresh fails', async () => {
    const client = createQueryClient()
    client.setQueryData(
      ['investigations', 'list', defaultFilters()],
      listResponse,
      { updatedAt: 1 },
    )
    domMocks.apiFetch.mockRejectedValue(new Error('refresh failed'))
    await renderAt('/investigations', true, client)

    expect(pageText()).toContain('The last loaded results remain visible.')
    expect(pageText()).toContain('Exchange exploitation review')
  })

  it('offers recovery when a saved list page is now beyond the available results', async () => {
    domMocks.apiFetch.mockResolvedValue({ ...listResponse, investigations: [], total: 0, page: 3 })
    await renderAt('/investigations?page=3')

    expect(pageText()).toContain('No matching investigations')
    expect(findButton('Return to first page')).not.toBeNull()
  })

  it('creates an investigation with the analyst draft and navigates to its cached workspace', async () => {
    domMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/investigations' && options?.method === 'POST') return Promise.resolve(baseDetail)
      return Promise.resolve(listResponse)
    })
    await renderAt('/investigations')

    act(() => findButton('Create investigation')?.click())
    const title = document.querySelector<HTMLInputElement>('#investigation-create-title')
    const description = document.querySelector<HTMLTextAreaElement>('#investigation-create-description')
    act(() => {
      setInputValue(title!, '  Exchange exploitation review  ')
      setTextAreaValue(description!, '  Correlate observed indicators and vendor reporting.  ')
    })
    act(() => findButton('Create')?.click())
    await flushRequests(2)

    const createCall = domMocks.apiFetch.mock.calls.find((call) => call[0] === '/investigations' && call[1]?.method === 'POST')
    expect(JSON.parse(createCall?.[1].body as string)).toEqual({
      title: 'Exchange exploitation review',
      description: 'Correlate observed indicators and vendor reporting.',
      severity: 'medium',
      visibility: 'private',
      assignee_user_id: 'user-1',
    })
    expect(pageText()).toContain('Version 7')
  })

  it('preserves creation input after a retriable failure', async () => {
    domMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/investigations' && options?.method === 'POST') return Promise.reject(new Error('write timed out'))
      return Promise.resolve(listResponse)
    })
    await renderAt('/investigations')
    act(() => findButton('Create investigation')?.click())
    const title = document.querySelector<HTMLInputElement>('#investigation-create-title')!
    act(() => setInputValue(title, 'Preserved draft'))
    act(() => findButton('Create')?.click())
    await flushRequests()

    expect(document.querySelector<HTMLInputElement>('#investigation-create-title')?.value).toBe('Preserved draft')
    expect(document.querySelector('[role="alert"]')?.textContent).toContain('Your draft has been preserved')
  })

  it('guards navigation from a dismissed creation draft and restores it for editing', async () => {
    domMocks.apiFetch.mockResolvedValue(listResponse)
    await renderAt('/investigations')

    act(() => findButton('Create investigation')?.click())
    const title = document.querySelector<HTMLInputElement>('#investigation-create-title')!
    act(() => setInputValue(title, 'Unfinished investigation'))
    act(() => findButton('Cancel')?.click())

    const investigationLink = document.querySelector<HTMLAnchorElement>(
      `a[href="/investigations/${baseDetail.id}"]`,
    )
    act(() => investigationLink?.click())
    await flushRequests()

    expect(document.querySelector('[role="alertdialog"]')?.textContent).toContain(
      'You have an unfinished investigation draft. Leave without creating it?',
    )
    expect(pageText()).not.toContain('Version 7')

    act(() => findButton('Cancel')?.click())
    act(() => findButton('Create investigation')?.click())
    expect(document.querySelector<HTMLInputElement>('#investigation-create-title')?.value).toBe(
      'Unfinished investigation',
    )
  })

  it('keeps team-visible nonmembers and global viewers read-only and renders notes as text', async () => {
    const readOnlyDetail = {
      ...baseDetail,
      visibility: 'team' as const,
      current_user_role: null,
      notes: [{ ...baseDetail.notes[0], body: '<img src=x onerror=alert(1)>Plain analyst text' }],
    }
    await renderDetail(readOnlyDetail, '?tab=notes')

    expect(pageText()).toContain('read-only until an owner adds you as a member')
    expect(document.querySelector('#investigation-new-note')).toBeNull()
    expect(pageText()).toContain('<img src=x onerror=alert(1)>Plain analyst text')
    expect(document.querySelector('img')).toBeNull()

    cleanupRender()
    domMocks.currentUser.role = 'viewer'
    await renderDetail({ ...baseDetail, current_user_role: 'editor' }, '?tab=notes')
    expect(pageText()).toContain('account role has read-only access')
    expect(document.querySelector('#investigation-new-note')).toBeNull()
  })

  it('recovers from a stale note write without losing input, then retries with the refreshed version', async () => {
    const refreshed = { ...baseDetail, version: 8, updated_at: '2026-08-27T12:00:00Z' }
    const saved = { ...refreshed, version: 9, note_count: 2, notes: [{ ...baseDetail.notes[0] }, { ...baseDetail.notes[0], id: 'note-2', body: 'Unsent conflict note' }] }
    let noteWrites = 0
    domMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => {
      if (path.endsWith('/notes') && options?.method === 'POST') {
        noteWrites += 1
        return noteWrites === 1
          ? Promise.reject(new ApiError('The investigation changed after you loaded it.', 409, path))
          : Promise.resolve(saved)
      }
      if (path.includes(`/investigations/${baseDetail.id}/notes?page=`)) {
        const source = noteWrites > 1 ? saved : refreshed
        return Promise.resolve({
          notes: source.notes,
          total: source.note_count,
          page: 1,
          page_size: 25,
        })
      }
      if (path === `/investigations/${baseDetail.id}`) return Promise.resolve(refreshed)
      return Promise.reject(new Error(`Unexpected request: ${path}`))
    })
    await renderDetail(baseDetail, '?tab=notes')
    const note = document.querySelector<HTMLTextAreaElement>('#investigation-new-note')!
    act(() => setTextAreaValue(note, 'Unsent conflict note'))
    act(() => findButton('Add note')?.click())
    await flushRequests(3)

    expect(document.querySelector<HTMLTextAreaElement>('#investigation-new-note')?.value).toBe('Unsent conflict note')
    expect(pageText()).toContain('Refresh and review the latest version before retrying')
    expect(pageText()).toContain('Version 8')

    act(() => findButton('Add note')?.click())
    await flushRequests(2)
    const writes = domMocks.apiFetch.mock.calls.filter((call) => call[0].endsWith('/notes') && call[1]?.method === 'POST')
    expect(JSON.parse(writes[0][1].body as string).expected_version).toBe(7)
    expect(JSON.parse(writes[1][1].body as string).expected_version).toBe(8)
    expect(document.querySelector<HTMLTextAreaElement>('#investigation-new-note')?.value).toBe('')
    expect(pageText()).toContain('Note added.')
    expect(pageText()).toContain('Version 9')
  })

  it('requires confirmation and sends both versions when removing a note', async () => {
    const withoutNote = { ...baseDetail, version: 8, note_count: 0, notes: [] }
    let removed = false
    domMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => {
      if (path.includes(`/investigations/${baseDetail.id}/notes?page=`)) {
        return Promise.resolve({
          notes: removed ? [] : baseDetail.notes,
          total: removed ? 0 : 1,
          page: 1,
          page_size: 25,
        })
      }
      if (path.includes('/notes/note-1') && options?.method === 'DELETE') {
        removed = true
        return Promise.resolve(withoutNote)
      }
      return Promise.reject(new Error(`Unexpected request: ${path}`))
    })
    await renderDetail(baseDetail, '?tab=notes')

    act(() => findButton('Remove')?.click())
    const dialog = document.querySelector('[role="alertdialog"]')
    expect(dialog?.textContent).toContain('Remove analyst note?')
    expect(domMocks.apiFetch).not.toHaveBeenCalled()

    const confirm = Array.from(dialog?.querySelectorAll<HTMLButtonElement>('button') ?? [])
      .find((button) => button.textContent === 'Remove note')
    act(() => confirm?.click())
    await flushRequests()

    expect(domMocks.apiFetch).toHaveBeenCalledWith(
      `/investigations/${baseDetail.id}/notes/note-1?expected_note_version=3&expected_investigation_version=7`,
      { method: 'DELETE' },
    )
    expect(pageText()).toContain('Note removed.')
    expect(pageText()).toContain('No analyst notes have been recorded.')
  })

  it('keeps a failed destructive action open and shows its actionable error in the dialog', async () => {
    domMocks.apiFetch.mockImplementation((path: string) => {
      if (path.includes(`/investigations/${baseDetail.id}/notes?page=`)) {
        return Promise.resolve({
          notes: baseDetail.notes,
          total: 1,
          page: 1,
          page_size: 25,
        })
      }
      return Promise.reject(new ApiError(
        'The note changed after you loaded it. Refresh and review the latest version.',
        409,
        `/investigations/${baseDetail.id}/notes/note-1`,
        null,
        { code: 'investigation_note_version_conflict' },
      ))
    })
    await renderDetail(baseDetail, '?tab=notes')

    act(() => findButton('Remove')?.click())
    const confirm = Array.from(document.querySelectorAll<HTMLButtonElement>('[role="alertdialog"] button'))
      .find((button) => button.textContent === 'Remove note')
    act(() => confirm?.click())
    await flushRequests()

    const dialog = document.querySelector('[role="alertdialog"]')
    expect(dialog).not.toBeNull()
    expect(dialog?.textContent).toContain('The note changed after you loaded it')
  })

  it('fails closed instead of displaying cached investigation data after access is revoked', async () => {
    domMocks.apiFetch.mockRejectedValue(new ApiError(
      'Investigation not found.',
      404,
      `/investigations/${baseDetail.id}`,
      null,
      { code: 'investigation_not_found' },
    ))
    const client = createQueryClient()
    client.setQueryData(['investigations', 'detail', baseDetail.id], baseDetail, { updatedAt: 1 })
    await renderAt(`/investigations/${baseDetail.id}`, true, client)

    expect(pageText()).toContain('Investigation not found.')
    expect(pageText()).not.toContain('Exchange exploitation review')
    expect(findButton('Retry')).not.toBeNull()
  })

  it('keeps the discard dialog available after a terminal refresh hides a dirty workspace', async () => {
    const { router } = await renderDetail(baseDetail)
    const description = document.querySelector<HTMLTextAreaElement>('#investigation-description')!
    act(() => setTextAreaValue(description, 'Unsaved scope after access loss'))
    domMocks.apiFetch.mockRejectedValue(
      new ApiError(
        'Investigation access is no longer available.',
        403,
        `/investigations/${baseDetail.id}`,
      ),
    )

    act(() => findButton('Refresh')?.click())
    await flushRequests(2)
    expect(pageText()).toContain('Investigation access is no longer available.')
    expect(pageText()).not.toContain('Unsaved scope after access loss')

    act(() => {
      void router.navigate('/investigations')
    })
    await flushRequests()

    expect(document.querySelector('[role="alertdialog"]')?.textContent).toContain(
      'Discard unsaved investigation changes?',
    )
    act(() => findButton('Cancel')?.click())
    expect(pageText()).toContain('Investigation access is no longer available.')
  })

  it('does not discard an unsaved overview draft after an unrelated lifecycle mutation', async () => {
    domMocks.apiFetch.mockResolvedValue({ ...baseDetail, status: 'monitoring', version: 8 })
    await renderDetail(baseDetail)
    const description = document.querySelector<HTMLTextAreaElement>('#investigation-description')!
    act(() => setTextAreaValue(description, 'Unsaved analyst scope refinement'))
    act(() => findButton('Start monitoring')?.click())
    await flushRequests()

    expect(description.value).toBe('Unsaved analyst scope refinement')
    expect(pageText()).toContain('Monitoring')
    expect(pageText()).toContain('Version 8')
  })

  it('preserves drafts across tabs and confirms before leaving the workspace', async () => {
    await renderDetail(baseDetail)
    const description = document.querySelector<HTMLTextAreaElement>(
      '#investigation-description',
    )!
    act(() => setTextAreaValue(description, 'Unsaved cross-tab context'))

    act(() => findButton('Notes')?.click())
    await flushRequests()
    expect(document.querySelector('[role="alertdialog"]')).toBeNull()
    act(() => findButton('Overview')?.click())
    await flushRequests()
    expect(
      document.querySelector<HTMLTextAreaElement>(
        '#investigation-description',
      )?.value,
    ).toBe('Unsaved cross-tab context')

    const backLink = document.querySelector<HTMLAnchorElement>(
      'a[href="/investigations"]',
    )
    act(() => backLink?.click())
    await flushRequests()
    expect(document.querySelector('[role="alertdialog"]')?.textContent).toContain(
      'Discard unsaved investigation changes?',
    )
    expect(pageText()).toContain('Exchange exploitation review')

    act(() => findButton('Cancel')?.click())
    expect(document.querySelector('[role="alertdialog"]')).toBeNull()
  })

  it('uses the narrow candidate directory, filters current members, and protects the final owner', async () => {
    const updated = {
      ...baseDetail,
      version: 8,
      member_count: 2,
      members: [...baseDetail.members, { user_id: 'user-2', email: 'responder@example.com', role: 'editor' as const, created_at: '2026-08-27T12:00:00Z' }],
    }
    domMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => {
      if (path.startsWith('/investigations/member-candidates')) {
        return Promise.resolve({
          users: [
            { id: 'user-1', email: 'analyst@example.com', account_role: 'analyst' },
            { id: 'user-2', email: 'responder@example.com', account_role: 'analyst' },
          ],
          total: 2,
          page: 1,
          page_size: 20,
        })
      }
      if (path.endsWith('/members') && options?.method === 'POST') return Promise.resolve(updated)
      return Promise.reject(new Error(`Unexpected request: ${path}`))
    })
    await renderDetail(baseDetail, '?tab=members')
    await flushRequests(2)

    expect(document.querySelector('input[name="investigation-member-candidate"][value="user-1"]')).toBeNull()
    expect(pageText()).toContain('responder@example.com')
    const finalOwnerButton = findButton('Final owner')
    expect(finalOwnerButton?.disabled).toBe(true)
    expect(pageText()).toContain(
      'This is the final owner. Add another owner before changing or removing this member.',
    )
    const candidate = document.querySelector<HTMLInputElement>('input[value="user-2"]')
    act(() => candidate?.click())
    act(() => findButton('Add member')?.click())
    await flushRequests()

    const addCall = domMocks.apiFetch.mock.calls.find((call) => call[0].endsWith('/members') && call[1]?.method === 'POST')
    expect(JSON.parse(addCall?.[1].body as string)).toEqual({ user_id: 'user-2', role: 'viewer', expected_version: 7 })
    expect(pageText()).toContain('Member added.')
  })

  it('requires explicit confirmation before reducing an investigation member role', async () => {
    const detail = {
      ...baseDetail,
      member_count: 3,
      members: [
        ...baseDetail.members,
        {
          user_id: 'user-2',
          email: 'owner-two@example.com',
          role: 'owner' as const,
          created_at: '2026-08-21T09:00:00Z',
        },
        {
          user_id: 'user-3',
          email: 'editor@example.com',
          role: 'editor' as const,
          created_at: '2026-08-22T09:00:00Z',
        },
      ],
    }
    const updated = {
      ...detail,
      version: 8,
      members: detail.members.map((member) =>
        member.user_id === 'user-3' ? { ...member, role: 'viewer' as const } : member,
      ),
    }
    domMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => {
      if (path.startsWith('/investigations/member-candidates')) {
        return Promise.resolve({ users: [], total: 0, page: 1, page_size: 20 })
      }
      if (path.endsWith('/members/user-3') && options?.method === 'PATCH') {
        return Promise.resolve(updated)
      }
      return Promise.reject(new Error(`Unexpected request: ${path}`))
    })
    await renderDetail(detail, '?tab=members')
    await flushRequests(2)

    const role = document.querySelector<HTMLSelectElement>('#investigation-member-role-user-3')!
    act(() => setSelectValue(role, 'viewer'))

    const dialog = document.querySelector('[role="alertdialog"]')
    expect(dialog?.textContent).toContain('Reduce investigation access?')
    expect(dialog?.textContent).toContain('Editor to Viewer')
    expect(
      domMocks.apiFetch.mock.calls.some(
        ([path, options]) => path.endsWith('/members/user-3') && options?.method === 'PATCH',
      ),
    ).toBe(false)

    const confirm = Array.from(dialog?.querySelectorAll<HTMLButtonElement>('button') ?? []).find(
      (button) => button.textContent === 'Change member role',
    )
    act(() => confirm?.click())
    await flushRequests()

    const roleChange = domMocks.apiFetch.mock.calls.find(
      ([path, options]) => path.endsWith('/members/user-3') && options?.method === 'PATCH',
    )
    expect(JSON.parse(roleChange?.[1].body as string)).toEqual({
      role: 'viewer',
      expected_version: 7,
    })
    expect(pageText()).toContain('Member role updated.')
  })

  it('uses the paginated evidence API with mobile-safe page controls', async () => {
    const paginatedDetail = {
      ...baseDetail,
      evidence_count: 201,
      evidence_truncated: true,
      evidence: [],
    }
    domMocks.apiFetch.mockImplementation((path: string) => {
      if (path.includes('/evidence?page=1&page_size=25')) {
        return Promise.resolve({
          evidence: Array.from({ length: 25 }, (_, index) =>
            evidenceRecord(index + 1, `Evidence page one ${index + 1}`),
          ),
          total: 201,
          page: 1,
          page_size: 25,
        })
      }
      if (path.includes('/evidence?page=2&page_size=25')) {
        return Promise.resolve({
          evidence: Array.from({ length: 25 }, (_, index) =>
            evidenceRecord(index + 26, `Evidence page two ${index + 1}`),
          ),
          total: 201,
          page: 2,
          page_size: 25,
        })
      }
      return Promise.reject(new Error(`Unexpected request: ${path}`))
    })
    await renderDetailWithCollectionFetch(paginatedDetail, '?tab=evidence')
    await flushRequests(2)

    expect(domMocks.apiFetch).toHaveBeenCalledWith(
      `/investigations/${baseDetail.id}/evidence?page=1&page_size=25`,
    )
    expect(pageText()).toContain('Evidence page one 1')
    expect(pageText()).toContain('1-25 of 201 · Page 1 of 9')
    const next = document.querySelector<HTMLButtonElement>(
      'button[aria-label="Next page of evidence"]',
    )
    expect(next?.className).toContain('min-h-11')
    expect(next?.className).toContain('sm:min-h-0')
    expect(next?.parentElement?.className).toContain('grid-cols-2')

    act(() => next?.click())
    await flushRequests(2)

    expect(domMocks.apiFetch).toHaveBeenCalledWith(
      `/investigations/${baseDetail.id}/evidence?page=2&page_size=25`,
    )
    expect(pageText()).toContain('Evidence page two 1')
    expect(pageText()).not.toContain('Evidence page one 1')
    expect(pageText()).toContain('26-50 of 201 · Page 2 of 9')
  })

  it('describes a truncated collection while its complete first page is loading', async () => {
    domMocks.apiFetch.mockImplementation(() => new Promise(() => {}))
    await renderDetailWithCollectionFetch(
      {
        ...baseDetail,
        evidence_count: 201,
        evidence_truncated: true,
      },
      '?tab=evidence',
    )

    expect(document.querySelector('[role="status"]')?.textContent).toContain(
      'Loading the first page of 201 evidence',
    )
  })

  it('offers retry after an initial notes failure without showing a false empty state', async () => {
    let reads = 0
    domMocks.apiFetch.mockImplementation((path: string) => {
      if (!path.includes('/notes?page=1&page_size=25')) {
        return Promise.reject(new Error(`Unexpected request: ${path}`))
      }
      reads += 1
      return reads === 1
        ? Promise.reject(new Error('notes storage timed out'))
        : Promise.resolve({ notes: [], total: 0, page: 1, page_size: 25 })
    })
    await renderDetailWithCollectionFetch(
      { ...baseDetail, note_count: 0, notes: [] },
      '?tab=notes',
    )
    await flushRequests(2)

    expect(document.querySelector('[role="alert"]')?.textContent).toContain(
      'notes storage timed out',
    )
    expect(pageText()).not.toContain('No analyst notes have been recorded')
    act(() => findButton('Retry analyst notes')?.click())
    await flushRequests(2)

    expect(reads).toBe(2)
    expect(pageText()).toContain('No analyst notes have been recorded')
    expect(pageText()).not.toContain('The last loaded page remains visible')
  })

  it('keeps a stale notes page visible after a refresh failure', async () => {
    const staleNote = { ...baseDetail.notes[0], body: 'Last loaded analyst context' }
    const client = createQueryClient()
    client.setQueryData(['investigations', 'detail', baseDetail.id], baseDetail)
    client.setQueryData(
      ['investigations', 'notes', baseDetail.id, 1],
      { notes: [staleNote], total: 1, page: 1, page_size: 25 },
      { updatedAt: 1 },
    )
    domMocks.apiFetch.mockRejectedValue(new Error('notes refresh unavailable'))
    await renderAt(`/investigations/${baseDetail.id}?tab=notes`, true, client)
    await flushRequests(2)

    expect(pageText()).toContain('Last loaded analyst context')
    expect(document.querySelector('[role="alert"]')?.textContent).toContain(
      'notes refresh unavailable',
    )
    expect(document.querySelector('[role="alert"]')?.textContent).toContain(
      'The last loaded page remains visible',
    )
  })

  it('fails closed instead of showing a cached notes page after collection access is denied', async () => {
    const client = createQueryClient()
    client.setQueryData(['investigations', 'detail', baseDetail.id], baseDetail)
    client.setQueryData(
      ['investigations', 'notes', baseDetail.id, 1],
      {
        notes: [{ ...baseDetail.notes[0], body: 'Sensitive cached note' }],
        total: 1,
        page: 1,
        page_size: 25,
      },
      { updatedAt: 1 },
    )
    domMocks.apiFetch.mockRejectedValue(
      new ApiError('Investigation access is no longer available.', 403, '/investigations/notes'),
    )
    await renderAt(`/investigations/${baseDetail.id}?tab=notes`, true, client)
    await flushRequests(2)

    expect(pageText()).not.toContain('Sensitive cached note')
    expect(document.querySelector('#investigation-new-note')).toBeNull()
    expect(document.querySelector('[role="alert"]')?.textContent).toContain(
      'Investigation access is no longer available',
    )
  })

  it('edits a paginated note with its page version and refreshes that page after saving', async () => {
    const olderNote = {
      ...baseDetail.notes[0],
      id: 'older-note',
      body: 'Historical working theory',
      version: 11,
    }
    const updatedDetail = {
      ...baseDetail,
      version: 8,
      note_count: 201,
      notes_truncated: true,
      notes: [],
    }
    let saved = false
    let noteReads = 0
    domMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => {
      if (path.includes('/notes?page=1&page_size=25')) {
        noteReads += 1
        return Promise.resolve({
          notes: [
            saved
              ? { ...olderNote, body: 'Revised historical theory', version: 12 }
              : olderNote,
          ],
          total: 201,
          page: 1,
          page_size: 25,
        })
      }
      if (path.endsWith('/notes/older-note') && options?.method === 'PATCH') {
        saved = true
        return Promise.resolve(updatedDetail)
      }
      return Promise.reject(new Error(`Unexpected request: ${path}`))
    })
    await renderDetailWithCollectionFetch(updatedDetail, '?tab=notes')
    await flushRequests(2)

    act(() => findButton('Edit')?.click())
    const editor = document.querySelector<HTMLTextAreaElement>(
      '#investigation-note-edit-older-note',
    )
    act(() => setTextAreaValue(editor!, 'Revised historical theory'))
    act(() => findButton('Save note')?.click())
    await flushRequests(3)

    const update = domMocks.apiFetch.mock.calls.find(
      ([path, options]) => path.endsWith('/notes/older-note') && options?.method === 'PATCH',
    )
    expect(JSON.parse(update?.[1].body as string)).toEqual({
      body: 'Revised historical theory',
      expected_note_version: 11,
      expected_investigation_version: 8,
    })
    expect(noteReads).toBeGreaterThanOrEqual(2)
    expect(pageText()).toContain('Revised historical theory')
    expect(pageText()).toContain('Note updated.')
  })

  it('preserves alert occurrence evidence input and marks the capability unavailable after the explicit API response', async () => {
    domMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => {
      if (path.endsWith('/evidence') && options?.method === 'POST') {
        return Promise.reject(new ApiError(
          'Alert occurrence evidence is unavailable until durable Alerting v2 is enabled.',
          422,
          path,
        ))
      }
      return Promise.reject(new Error(`Unexpected request: ${path}`))
    })
    await renderDetail(baseDetail, '?tab=evidence')
    const type = document.querySelector<HTMLSelectElement>('#investigation-evidence-type')!
    const sourceId = document.querySelector<HTMLInputElement>('#investigation-evidence-source-id')!
    act(() => {
      setSelectValue(type, 'alert_occurrence')
      setInputValue(sourceId, '22222222-2222-4222-8222-222222222222')
    })
    act(() => findButton('Add evidence')?.click())
    await flushRequests()

    expect(pageText()).toContain('Alert occurrence evidence is unavailable until durable Alerting v2 is enabled.')
    expect(document.querySelector<HTMLInputElement>('#investigation-evidence-source-id')?.value).toBe('22222222-2222-4222-8222-222222222222')
    expect(findButton('Add evidence')?.disabled).toBe(true)
  })

  it('requires explicit confirmation before reopening a closed investigation', async () => {
    const closed = { ...baseDetail, status: 'closed' as const, closed_at: '2026-08-26T12:00:00Z', disposition: 'resolved' }
    const reopened = { ...closed, status: 'open' as const, closed_at: null, disposition: null, version: 8 }
    domMocks.apiFetch.mockResolvedValue(reopened)
    await renderDetail(closed)

    act(() => findButton('Reopen investigation')?.click())
    expect(document.querySelector('[role="alertdialog"]')?.textContent).toContain('Reopen investigation?')
    expect(domMocks.apiFetch).not.toHaveBeenCalled()
    const confirm = Array.from(document.querySelectorAll<HTMLButtonElement>('[role="alertdialog"] button')).find((button) => button.textContent === 'Reopen investigation')
    act(() => confirm?.click())
    await flushRequests()

    const body = JSON.parse(domMocks.apiFetch.mock.calls[0][1].body as string)
    expect(body).toEqual({ status: 'open', disposition: null, expected_version: 7 })
  })

  it('renders paginated activity with human-readable actions and the raw timestamp', async () => {
    domMocks.apiFetch.mockResolvedValue({
      activities: [{
        id: 'activity-1',
        actor_user_id: 'user-1',
        actor_email: 'analyst@example.com',
        action: 'investigation.evidence_added',
        entity_type: 'evidence',
        entity_id: 'evidence-1',
        details: { source_type: 'ioc' },
        created_at: '2026-08-27T12:34:56.123456Z',
      }],
      total: 1,
      page: 1,
      page_size: 25,
    })
    await renderDetail(baseDetail, '?tab=activity')
    await flushRequests()

    expect(pageText()).toContain('Added evidence')
    expect(pageText()).toContain('2026-08-27T12:34:56.123456Z')
    expect(pageText()).toContain('investigation.evidence_added')
  })
})

async function renderDetail(detail: InvestigationDetail, suffix = '') {
  const client = createQueryClient()
  client.setQueryData(['investigations', 'detail', detail.id], detail)
  client.setQueryData(['investigations', 'evidence', detail.id, 1], {
    evidence: detail.evidence.slice(0, 25),
    total: detail.evidence_count,
    page: 1,
    page_size: 25,
  })
  client.setQueryData(['investigations', 'notes', detail.id, 1], {
    notes: detail.notes.slice(0, 25),
    total: detail.note_count,
    page: 1,
    page_size: 25,
  })
  return renderAt(`/investigations/${detail.id}${suffix}`, true, client)
}

async function renderDetailWithCollectionFetch(detail: InvestigationDetail, suffix: string) {
  const client = createQueryClient()
  client.setQueryData(['investigations', 'detail', detail.id], detail)
  return renderAt(`/investigations/${detail.id}${suffix}`, true, client)
}

async function renderAt(path: string, settle = true, client = createQueryClient()) {
  queryClient = client
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  const router = createMemoryRouter(
    [
      { path: '/investigations', element: <InvestigationsPage /> },
      {
        path: '/investigations/:investigationId',
        element: <InvestigationsPage />,
      },
    ],
    { initialEntries: [path] },
  )
  await act(async () => {
    root?.render(
      <QueryClientProvider client={client}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    )
  })
  if (settle) await flushRequests()
  return { container, router }
}

function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false },
      mutations: { retry: false },
    },
  })
}

function defaultFilters() {
  return {
    query: '',
    statuses: [],
    severities: [],
    assignedToMe: false,
    includeArchived: false,
    page: 1,
  }
}

async function flushRequests(rounds = 1) {
  for (let index = 0; index < rounds; index += 1) {
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 0))
    })
  }
}

function cleanupRender() {
  act(() => root?.unmount())
  root = null
  container?.remove()
  container = null
  queryClient?.clear()
  queryClient = null
  document.body.innerHTML = ''
}

function pageText() {
  return document.body.textContent ?? ''
}

function findButton(label: string) {
  return Array.from(document.querySelectorAll<HTMLButtonElement>('button')).find((button) => button.textContent?.trim() === label) ?? null
}

function setInputValue(input: HTMLInputElement, value: string) {
  const descriptor = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')
  descriptor?.set?.call(input, value)
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

function setTextAreaValue(input: HTMLTextAreaElement, value: string) {
  const descriptor = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')
  descriptor?.set?.call(input, value)
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

function setSelectValue(select: HTMLSelectElement, value: string) {
  const descriptor = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value')
  descriptor?.set?.call(select, value)
  select.dispatchEvent(new Event('change', { bubbles: true }))
}

function evidenceRecord(index: number, title: string) {
  return {
    id: `evidence-${index}`,
    source_type: 'ioc' as const,
    source_id: `source-${index}`,
    title_snapshot: title,
    description_snapshot: null,
    url_snapshot: null,
    metadata_snapshot: {},
    note: null,
    added_by_user_id: 'user-1',
    created_at: '2026-08-27T12:00:00Z',
  }
}
