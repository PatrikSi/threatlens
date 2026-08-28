// @vitest-environment jsdom

import { act } from 'react'
import { createRoot, Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { AlertInterest } from '../types/alerts'
;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const alertsPageDomMocks = vi.hoisted(() => ({
  queryClient: {
    invalidateQueries: vi.fn(),
  },
  saveMutate: vi.fn(),
  updateMutate: vi.fn(),
  deleteMutate: vi.fn(),
  deleteShouldFail: false,
  deleteShouldConflict: false,
  deleteConflictDisablesRule: false,
  saveShouldConflict: false,
  updateShouldFail: false,
  updateShouldConflict: false,
  alertsError: null as Error | null,
  alertsFetching: false,
  alertsRefetch: vi.fn(),
  savePending: false,
  updatePending: false,
  deletePending: false,
  updateVariables: null as { id: string; body: Record<string, unknown> } | null,
  previewItems: [] as unknown[],
  role: 'admin' as 'admin' | 'viewer',
  alerts: [
    {
      id: 'alert-1',
      user_id: 'user-1',
      name: 'VPN advisories',
      category: 'software',
      keywords: ['vpn', 'gateway'],
      enabled: true,
      severity: 'high',
      revision: 7,
      row_version: 11,
      durable_since: '2026-04-20T10:00:00Z',
      suppression_until: null,
      suppression_reason: null,
      created_at: '2026-04-20T10:00:00Z',
      updated_at: '2026-04-21T10:00:00Z',
    },
  ] as AlertInterest[],
}))

const routerMocks = vi.hoisted(() => ({
  useBlocker: vi.fn(() => ({
    state: 'unblocked' as const,
    proceed: vi.fn(),
    reset: vi.fn(),
  })),
}))

function alertMutationResult(
  mutate: ReturnType<typeof vi.fn>,
  pending = false,
  variables: unknown = undefined,
) {
  return {
    mutate,
    reset: vi.fn(),
    isPending: pending,
    isError: false,
    error: null,
    variables,
  }
}

vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => alertsPageDomMocks.queryClient,
  useQuery: ({ queryKey, enabled }: { queryKey: unknown[]; enabled?: boolean }) => {
    const [scope, key] = queryKey
    const baseResult = {
      isLoading: false,
      isError: false,
      error: null,
    }

    if (scope === 'alerts' && key !== 'preview') {
      const includeDisabled = key === true || key === 'delete-conflict-all'
      const data = includeDisabled
        ? alertsPageDomMocks.alerts
        : alertsPageDomMocks.alerts.filter((alert) => alert.enabled)
      return {
        ...baseResult,
        data,
        isError: key === 'delete-conflict-all' ? false : Boolean(alertsPageDomMocks.alertsError),
        error: key === 'delete-conflict-all' ? null : alertsPageDomMocks.alertsError,
        isFetching: key === 'delete-conflict-all' ? false : alertsPageDomMocks.alertsFetching,
        refetch: () => {
          if (key !== 'delete-conflict-all') alertsPageDomMocks.alertsRefetch()
          const refreshed =
            key === 'delete-conflict-all'
              ? alertsPageDomMocks.alerts
              : alertsPageDomMocks.alerts.filter((alert) => key === true || alert.enabled)
          return Promise.resolve({
            data: refreshed,
            error: key === 'delete-conflict-all' ? null : alertsPageDomMocks.alertsError,
          })
        },
      }
    }

    if (scope === 'alerts' && key === 'preview') {
      return {
        ...baseResult,
        data: enabled
          ? {
              items: alertsPageDomMocks.previewItems,
              total: alertsPageDomMocks.previewItems.length,
              page: 1,
              page_size: 5,
            }
          : undefined,
      }
    }

    return {
      ...baseResult,
      data: undefined,
    }
  },
  useMutation: (options: {
    mutationKey?: unknown
    onSuccess?: (result: unknown, variables: unknown) => void
    onError?: (error: unknown, variables: unknown) => void
  }) => {
    const mutationKey = Array.isArray(options?.mutationKey)
      ? options.mutationKey.join(':')
      : String(options?.mutationKey ?? '')
    if (mutationKey === 'alerts:delete') {
      return alertMutationResult(
        vi.fn((alert: { id: string }) => {
          alertsPageDomMocks.deleteMutate(alert)
          if (alertsPageDomMocks.deleteShouldConflict) {
            alertsPageDomMocks.alerts = alertsPageDomMocks.alerts.map((candidate) =>
              candidate.id === alert.id
                ? {
                    ...candidate,
                    name: 'Server revision',
                    enabled: alertsPageDomMocks.deleteConflictDisablesRule
                      ? false
                      : candidate.enabled,
                    row_version: 12,
                  }
                : candidate,
            )
            options.onError?.(
              apiError('Rule changed.', 'alert_revision_conflict', {
                current_revision: 12,
                current_row_version: 12,
                current_rule_revision: 7,
              }),
              alert,
            )
            return
          }
          if (alertsPageDomMocks.deleteShouldFail) {
            options.onError?.(new Error('Alert deletion failed.'), alert)
            return
          }
          options.onSuccess?.(undefined, alert)
        }),
        alertsPageDomMocks.deletePending,
      )
    }
    if (mutationKey === 'alerts:update') {
      return alertMutationResult(
        vi.fn((variables: unknown) => {
          alertsPageDomMocks.updateMutate(variables)
          if (alertsPageDomMocks.updateShouldConflict) {
            options.onError?.(
              apiError('Rule changed.', 'alert_revision_conflict', {
                current_revision: 12,
                current_row_version: 12,
                current_rule_revision: 7,
              }),
              variables,
            )
          } else if (alertsPageDomMocks.updateShouldFail) {
            options.onError?.(
              apiError('Rule update failed.', 'alert_rule_update_failed'),
              variables,
            )
          } else {
            options.onSuccess?.({}, variables)
          }
        }),
        alertsPageDomMocks.updatePending,
        alertsPageDomMocks.updateVariables,
      )
    }
    return alertMutationResult(
      vi.fn((variables: unknown) => {
        alertsPageDomMocks.saveMutate(variables)
        if (alertsPageDomMocks.saveShouldConflict) {
          options.onError?.(
            apiError('The alert rule changed after it was loaded.', 'alert_revision_conflict', {
              current_revision: 12,
              current_row_version: 12,
              current_rule_revision: 7,
            }),
            variables,
          )
        }
      }),
      alertsPageDomMocks.savePending,
    )
  },
}))

function apiError(message: string, code: string, detail: unknown = null) {
  const error = Object.assign(new Error(message), {
    name: 'ApiError',
    status: code === 'alert_revision_conflict' ? 409 : 503,
    code,
    detail,
    path: '/alerts/alert-1',
    retryable: false,
  })
  return error
}

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return {
    ...actual,
    useBlocker: routerMocks.useBlocker,
  }
})

vi.mock('./AlertOccurrencesWorkspace', () => ({
  AlertOccurrencesWorkspace: () => <div>Occurrence workspace marker</div>,
}))

vi.mock('./AlertOperationsWorkspace', () => ({
  AlertOperationsWorkspace: () => <div>Operations workspace marker</div>,
}))

vi.mock('../hooks/useCurrentUser', () => ({
  useCurrentUser: () => ({
    data: { id: 'user-1', role: alertsPageDomMocks.role },
    isLoading: false,
    isError: false,
  }),
}))

import { AlertsPage } from './AlertsPage'

let root: Root | null = null
let container: HTMLDivElement | null = null

function renderPage() {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => {
    root?.render(<AlertsPage />)
  })
  return container
}

function rerenderPage() {
  act(() => {
    root?.render(<AlertsPage />)
  })
}

function pageText() {
  return document.body.textContent ?? ''
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

function setSelectValue(input: HTMLSelectElement, value: string) {
  const descriptor = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value')
  descriptor?.set?.call(input, value)
  input.dispatchEvent(new Event('change', { bubbles: true }))
}

afterEach(() => {
  act(() => {
    root?.unmount()
  })
  root = null
  container?.remove()
  container = null
  document.body.innerHTML = ''
  alertsPageDomMocks.saveMutate.mockReset()
  alertsPageDomMocks.updateMutate.mockReset()
  alertsPageDomMocks.deleteMutate.mockReset()
  alertsPageDomMocks.deleteShouldFail = false
  alertsPageDomMocks.deleteShouldConflict = false
  alertsPageDomMocks.deleteConflictDisablesRule = false
  alertsPageDomMocks.saveShouldConflict = false
  alertsPageDomMocks.updateShouldFail = false
  alertsPageDomMocks.updateShouldConflict = false
  alertsPageDomMocks.alertsError = null
  alertsPageDomMocks.alertsFetching = false
  alertsPageDomMocks.alertsRefetch.mockReset()
  alertsPageDomMocks.savePending = false
  alertsPageDomMocks.updatePending = false
  alertsPageDomMocks.deletePending = false
  alertsPageDomMocks.updateVariables = null
  alertsPageDomMocks.previewItems = []
  alertsPageDomMocks.role = 'admin'
  alertsPageDomMocks.alerts = [
    {
      id: 'alert-1',
      user_id: 'user-1',
      name: 'VPN advisories',
      category: 'software',
      keywords: ['vpn', 'gateway'],
      enabled: true,
      severity: 'high',
      revision: 7,
      row_version: 11,
      durable_since: '2026-04-20T10:00:00Z',
      suppression_until: null,
      suppression_reason: null,
      created_at: '2026-04-20T10:00:00Z',
      updated_at: '2026-04-21T10:00:00Z',
    },
  ]
})

describe('AlertsPage DOM workflows', () => {
  it('preserves the rule draft while switching to occurrence triage and back', () => {
    const view = renderPage()
    const nameInput = view.querySelector<HTMLInputElement>('#alert-interest-name')

    act(() => setInputValue(nameInput!, 'Unsaved rule draft'))
    const occurrenceTab = view.querySelector<HTMLButtonElement>('#alert-occurrences-tab')
    act(() => occurrenceTab?.click())

    expect(pageText()).toContain('Occurrence workspace marker')
    expect(view.querySelector('#alert-rules-panel')?.hasAttribute('hidden')).toBe(true)

    const rulesTab = view.querySelector<HTMLButtonElement>('#alert-rules-tab')
    act(() => rulesTab?.click())
    expect(view.querySelector<HTMLInputElement>('#alert-interest-name')?.value).toBe(
      'Unsaved rule draft',
    )
  })

  it('implements roving keyboard tabs and keeps controlled panels addressable', () => {
    const view = renderPage()
    const rulesTab = view.querySelector<HTMLButtonElement>('#alert-rules-tab')!
    const occurrenceTab = view.querySelector<HTMLButtonElement>('#alert-occurrences-tab')!
    const operationsTab = view.querySelector<HTMLButtonElement>('#alert-operations-tab')!

    expect(view.querySelector('#alert-occurrences-panel')).not.toBeNull()
    expect(rulesTab.tabIndex).toBe(0)
    expect(occurrenceTab.tabIndex).toBe(-1)

    act(() =>
      rulesTab.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true })),
    )
    expect(document.activeElement).toBe(occurrenceTab)
    expect(occurrenceTab.getAttribute('aria-selected')).toBe('true')
    expect(occurrenceTab.tabIndex).toBe(0)

    act(() =>
      occurrenceTab.dispatchEvent(new KeyboardEvent('keydown', { key: 'End', bubbles: true })),
    )
    expect(document.activeElement).toBe(operationsTab)
    expect(pageText()).toContain('Operations workspace marker')

    act(() =>
      operationsTab.dispatchEvent(new KeyboardEvent('keydown', { key: 'Home', bubbles: true })),
    )
    expect(document.activeElement).toBe(rulesTab)
    expect(rulesTab.getAttribute('aria-selected')).toBe('true')
  })

  it('keeps primary alert controls at mobile touch size without changing desktop sizing', () => {
    const view = renderPage()
    const rulesTab = view.querySelector<HTMLButtonElement>('#alert-rules-tab')!
    const nameInput = view.querySelector<HTMLInputElement>('#alert-interest-name')!
    const addButton = Array.from(view.querySelectorAll<HTMLButtonElement>('button')).find(
      (button) => button.textContent === 'Add Interest',
    )!

    expect(rulesTab.className).toContain('min-h-11')
    expect(rulesTab.className).toContain('sm:min-h-10')
    expect(nameInput.className).toContain('min-h-11')
    expect(nameInput.className).toContain('sm:min-h-0')
    expect(addButton.className).toContain('min-h-11')
    expect(addButton.className).toContain('sm:min-h-0')
  })

  it('labels and announces pending alert save and state changes', () => {
    const view = renderPage()
    act(() => {
      setInputValue(view.querySelector<HTMLInputElement>('#alert-interest-name')!, 'New rule')
      setTextAreaValue(
        view.querySelector<HTMLTextAreaElement>('#alert-interest-keywords')!,
        'ransomware',
      )
    })

    alertsPageDomMocks.savePending = true
    rerenderPage()
    const saving = Array.from(view.querySelectorAll<HTMLButtonElement>('button')).find(
      (button) => button.textContent === 'Adding alert interest...',
    )
    expect(saving?.disabled).toBe(true)
    expect(
      Array.from(view.querySelectorAll('[role="status"]')).some(
        (status) => status.textContent === 'Adding alert interest...',
      ),
    ).toBe(true)

    alertsPageDomMocks.savePending = false
    alertsPageDomMocks.updatePending = true
    alertsPageDomMocks.updateVariables = { id: 'alert-1', body: { enabled: false } }
    rerenderPage()
    const disabling = Array.from(view.querySelectorAll<HTMLButtonElement>('button')).find(
      (button) => button.textContent === 'Disabling...',
    )
    expect(disabling?.disabled).toBe(true)
    expect(pageText()).toContain('Disabling alert rule VPN advisories...')
  })

  it('uses a specific live pending label while deleting an alert rule', () => {
    const view = renderPage()
    act(() =>
      view
        .querySelector<HTMLButtonElement>('button[aria-label="Delete alert rule VPN advisories"]')
        ?.click(),
    )

    alertsPageDomMocks.deletePending = true
    rerenderPage()

    const dialog = document.body.querySelector('[role="alertdialog"]')
    expect(dialog?.textContent).toContain('Deleting alert...')
    expect(dialog?.querySelector('[role="status"]')?.textContent).toContain(
      'Deleting alert rule VPN advisories...',
    )
  })

  it('does not expose alert operations to non-administrators', () => {
    alertsPageDomMocks.role = 'viewer'
    const view = renderPage()
    expect(view.querySelector('#alert-operations-tab')).toBeNull()
    expect(view.querySelector('#alert-operations-panel')).toBeNull()
  })

  it('keeps the delete target open and renders destructive mutation failures', () => {
    alertsPageDomMocks.deleteShouldFail = true
    renderPage()
    const deleteButton = Array.from(document.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === 'Delete',
    )

    act(() => {
      deleteButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    const confirmDeleteButton = Array.from(document.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === 'Delete alert',
    )
    act(() => {
      confirmDeleteButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(document.body.textContent).toContain('Delete alert interest?')
    expect(document.body.textContent).toContain('VPN advisories')
    expect(document.querySelector('[role="alert"]')?.textContent).toContain(
      'Alert deletion failed.',
    )
  })

  it('refreshes a stale delete target and requires explicit reconfirmation', async () => {
    alertsPageDomMocks.deleteShouldConflict = true
    renderPage()
    const deleteButton = Array.from(document.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === 'Delete',
    )
    act(() => deleteButton?.click())
    const confirmDeleteButton = Array.from(document.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === 'Delete alert',
    )!

    await act(async () => {
      confirmDeleteButton.click()
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(alertsPageDomMocks.deleteMutate).toHaveBeenCalledTimes(1)
    expect(document.body.textContent).toContain('Server revision')
    expect(document.body.textContent).toContain('Review them, then confirm deletion again')
    expect(confirmDeleteButton.disabled).toBe(false)

    alertsPageDomMocks.deleteShouldConflict = false
    act(() => confirmDeleteButton.click())
    expect(alertsPageDomMocks.deleteMutate).toHaveBeenLastCalledWith(
      expect.objectContaining({ id: 'alert-1', row_version: 12 }),
    )
  })

  it('refreshes a stale delete target from all rules when another client disables it', async () => {
    alertsPageDomMocks.deleteShouldConflict = true
    alertsPageDomMocks.deleteConflictDisablesRule = true
    renderPage()
    const deleteButton = document.querySelector<HTMLButtonElement>(
      'button[aria-label="Delete alert rule VPN advisories"]',
    )
    act(() => deleteButton?.click())
    const confirmDeleteButton = Array.from(document.querySelectorAll<HTMLButtonElement>('button')).find(
      (button) => button.textContent?.trim() === 'Delete alert',
    )!

    await act(async () => {
      confirmDeleteButton.click()
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(pageText()).toContain('Server revision')
    expect(pageText()).toContain('Disabled')
    expect(pageText()).toContain('Review them, then confirm deletion again')
    expect(pageText()).not.toContain('This alert rule no longer exists')
    expect(confirmDeleteButton.disabled).toBe(false)
  })

  it('renders a retryable load error without also rendering the empty state', () => {
    alertsPageDomMocks.alerts = []
    alertsPageDomMocks.alertsError = new Error('alert store unavailable')
    const view = renderPage()

    const error = view.querySelector('[role="alert"]')
    expect(error?.textContent).toContain('Alert interests could not be loaded')
    expect(error?.textContent).toContain('alert store unavailable')
    expect(pageText()).not.toContain('No alert interests configured yet')
    const retry = Array.from(view.querySelectorAll<HTMLButtonElement>('button')).find(
      (button) => button.textContent === 'Retry alert rules',
    )
    act(() => retry?.click())
    expect(alertsPageDomMocks.alertsRefetch).toHaveBeenCalledTimes(1)
  })

  it('disables empty alert submissions and renders preview summaries as text', () => {
    alertsPageDomMocks.previewItems = [
      {
        id: 'item-1',
        title: 'Ransomware report',
        feed_name: 'Security Feed',
        first_seen_at: '2026-04-21T10:00:00Z',
        summary:
          '<p><em><strong>Introduction</strong></em></p>&#xd;<a href="https://example.com">Read more</a>',
        matches: [{ category: 'malware', matched_keywords: ['ransomware'] }],
      },
    ]
    const view = renderPage()
    const addButton = Array.from(view.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Add Interest'),
    )
    const nameInput = view.querySelector<HTMLInputElement>('#alert-interest-name')
    const keywordsInput = view.querySelector<HTMLTextAreaElement>('#alert-interest-keywords')

    expect(addButton).not.toBeNull()
    expect(addButton?.hasAttribute('disabled')).toBe(true)
    expect(pageText()).not.toContain('Enter an interest name.')

    act(() => {
      setInputValue(nameInput!, 'Agent2 Preview Only')
    })

    expect(pageText()).toContain('Enter at least one keyword.')

    act(() => {
      setTextAreaValue(keywordsInput!, 'ransomware')
    })

    expect(pageText()).toContain('Introduction Read more')
    expect(pageText()).not.toContain('<p>')
    expect(pageText()).not.toContain('&#xd;')
  })

  it('loads alert edits, protects unsaved changes, toggles alert state, and confirms deletion', () => {
    const view = renderPage()

    const editButton = Array.from(view.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Edit'),
    )
    const disableButton = Array.from(view.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Disable'),
    )
    const deleteButton = Array.from(view.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Delete'),
    )

    expect(editButton).not.toBeNull()
    expect(disableButton).not.toBeNull()
    expect(deleteButton).not.toBeNull()
    expect(editButton?.getAttribute('aria-label')).toBe('Edit alert rule VPN advisories')
    expect(disableButton?.getAttribute('aria-label')).toBe('Disable alert rule VPN advisories')
    expect(deleteButton?.getAttribute('aria-label')).toBe('Delete alert rule VPN advisories')
    expect(editButton?.className).toContain('min-h-11')
    expect(editButton?.className).toContain('sm:min-h-0')

    act(() => {
      editButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(view.querySelector<HTMLInputElement>('#alert-interest-name')?.value).toBe(
      'VPN advisories',
    )
    expect(view.querySelector<HTMLTextAreaElement>('#alert-interest-keywords')?.value).toBe(
      'vpn, gateway',
    )

    act(() => {
      disableButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(alertsPageDomMocks.updateMutate).toHaveBeenCalledWith({
      id: 'alert-1',
      body: { enabled: false, expected_row_version: 11 },
    })

    act(() => {
      deleteButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(pageText()).toContain('Delete alert interest?')
    expect(pageText()).toContain('VPN advisories')

    const confirmDeleteButton = Array.from(document.querySelectorAll('button'))
      .filter((button) => button.textContent?.includes('Delete alert'))
      .at(-1)
    expect(confirmDeleteButton).not.toBeNull()

    act(() => {
      confirmDeleteButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(alertsPageDomMocks.deleteMutate).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'alert-1', row_version: 11 }),
    )
    expect(view.querySelector<HTMLInputElement>('#alert-interest-name')?.value).toBe('')
  })

  it('edits severity and validates a bounded notification suppression window', () => {
    const view = renderPage()
    const editButton = Array.from(view.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === 'Edit',
    )
    act(() => editButton?.click())

    expect(view.querySelector<HTMLSelectElement>('#alert-interest-severity')?.value).toBe('high')
    act(() => {
      setSelectValue(view.querySelector<HTMLSelectElement>('#alert-interest-severity')!, 'critical')
      view.querySelector<HTMLInputElement>('input[type="checkbox"][class="accent-cyan"]')?.click()
    })
    expect(pageText()).toContain('Choose a future suppression end time.')

    const until = '2030-01-02T09:30'
    act(() => {
      setInputValue(
        view.querySelector<HTMLInputElement>('#alert-interest-suppression-until')!,
        until,
      )
      setInputValue(
        view.querySelector<HTMLInputElement>('#alert-interest-suppression-reason')!,
        'Planned maintenance window',
      )
    })
    const saveButton = Array.from(view.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === 'Save changes',
    )
    expect(saveButton?.hasAttribute('disabled')).toBe(false)
    act(() => saveButton?.click())

    expect(alertsPageDomMocks.saveMutate).toHaveBeenCalledWith({
      id: 'alert-1',
      expectedRevision: 7,
      expectedRowVersion: 11,
      name: 'VPN advisories',
      category: 'software',
      keywords: ['vpn', 'gateway'],
      severity: 'critical',
      suppressionUntil: new Date(until).toISOString(),
      suppressionReason: 'Planned maintenance window',
    })
  })

  it('preserves a stale suppression draft and requires an explicit reload after a conflict', async () => {
    alertsPageDomMocks.saveShouldConflict = true
    const view = renderPage()
    const editButton = Array.from(view.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === 'Edit',
    )
    act(() => editButton?.click())

    alertsPageDomMocks.alerts = [
      {
        ...alertsPageDomMocks.alerts[0],
        name: 'Server revision',
        severity: 'critical',
        revision: 7,
        row_version: 12,
        suppression_until: '2031-02-01T10:00:00Z',
        suppression_reason: 'Server maintenance',
      },
    ]
    act(() => {
      view.querySelector<HTMLInputElement>('input[type="checkbox"][class="accent-cyan"]')?.click()
      setInputValue(
        view.querySelector<HTMLInputElement>('#alert-interest-suppression-until')!,
        '2031-01-01T10:00',
      )
      setInputValue(
        view.querySelector<HTMLInputElement>('#alert-interest-suppression-reason')!,
        'My stale maintenance',
      )
    })
    const saveButton = Array.from(view.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === 'Save changes',
    )
    act(() => saveButton?.click())

    expect(alertsPageDomMocks.saveMutate).toHaveBeenCalledWith(
      expect.objectContaining({
        id: 'alert-1',
        expectedRevision: 7,
        expectedRowVersion: 11,
        suppressionReason: 'My stale maintenance',
      }),
    )
    expect(pageText()).toContain('This rule changed on the server.')
    expect(pageText()).toContain('the current version is 12')
    expect(view.querySelector<HTMLInputElement>('#alert-interest-suppression-reason')?.value).toBe(
      'My stale maintenance',
    )
    expect(saveButton?.hasAttribute('disabled')).toBe(true)

    const reloadButton = Array.from(view.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === 'Reload latest rule',
    )
    await act(async () => {
      reloadButton?.click()
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(view.querySelector<HTMLInputElement>('#alert-interest-name')?.value).toBe(
      'Server revision',
    )
    expect(view.querySelector<HTMLSelectElement>('#alert-interest-severity')?.value).toBe(
      'critical',
    )
    expect(view.querySelector<HTMLInputElement>('#alert-interest-suppression-reason')?.value).toBe(
      'Server maintenance',
    )
    expect(pageText()).not.toContain('This rule changed on the server.')
  })

  it('renders enable and disable failures instead of silently reverting state', () => {
    alertsPageDomMocks.updateShouldFail = true
    const view = renderPage()
    const disableButton = Array.from(view.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === 'Disable',
    )
    act(() => disableButton?.click())

    expect(alertsPageDomMocks.updateMutate).toHaveBeenCalledWith({
      id: 'alert-1',
      body: { enabled: false, expected_row_version: 11 },
    })
    expect(view.querySelector('[role="alert"]')?.textContent).toContain(
      'The alert rule state could not be updated',
    )
    expect(pageText()).toContain('Rule update failed.')
  })

  it('renders and recovers from a stale disable conflict', () => {
    alertsPageDomMocks.updateShouldConflict = true
    const view = renderPage()
    const disableButton = Array.from(view.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === 'Disable',
    )
    act(() => disableButton?.click())

    expect(alertsPageDomMocks.updateMutate).toHaveBeenCalledWith({
      id: 'alert-1',
      body: { enabled: false, expected_row_version: 11 },
    })
    expect(pageText()).toContain('current version 12')
    expect(alertsPageDomMocks.queryClient.invalidateQueries).toHaveBeenCalledWith({
      queryKey: ['alerts'],
    })
  })

  it('discards unsaved alert edits before opening the delete confirmation', () => {
    const view = renderPage()

    const editButton = Array.from(view.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Edit'),
    )
    expect(editButton).not.toBeNull()

    act(() => {
      editButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    const nameInput = view.querySelector<HTMLInputElement>('#alert-interest-name')
    expect(nameInput).not.toBeNull()

    act(() => {
      setInputValue(nameInput!, 'Changed alert name')
    })

    const deleteButton = Array.from(view.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Delete'),
    )
    expect(deleteButton).not.toBeNull()

    act(() => {
      deleteButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(pageText()).toContain('Discard unsaved changes?')
    expect(pageText()).toContain('Discard unsaved alert changes?')
    expect(pageText()).not.toContain('Delete alert interest?')

    const discardChangesButton = Array.from(document.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Discard changes'),
    )
    expect(discardChangesButton).not.toBeNull()

    act(() => {
      discardChangesButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(pageText()).toContain('Delete alert interest?')
    expect(pageText()).toContain('VPN advisories')

    const confirmDeleteButton = Array.from(document.querySelectorAll('button'))
      .filter((button) => button.textContent?.includes('Delete alert'))
      .at(-1)
    expect(confirmDeleteButton).not.toBeNull()

    act(() => {
      confirmDeleteButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(alertsPageDomMocks.deleteMutate).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'alert-1', row_version: 11 }),
    )
  })
})
