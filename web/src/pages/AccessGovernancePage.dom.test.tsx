// @vitest-environment jsdom

import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { DataPolicyOverview } from '../types/api'
import type { IAMCatalog } from './accessGovernanceApi'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const governancePageDomMocks = vi.hoisted(() => ({
  permissions: [] as string[],
  durablePermissions: null as string[] | null,
  elevationIds: [] as string[],
  sensitiveActionsReady: true,
  approvalItems: [] as Array<Record<string, unknown>>,
  queryOptions: [] as Array<{ queryKey: unknown[]; enabled?: boolean }>,
  queryResult: vi.fn(),
  refetch: vi.fn(() => Promise.resolve()),
  invalidateQueries: vi.fn(() => Promise.resolve()),
  removeQueries: vi.fn(),
}))

vi.mock('../hooks/useCurrentUser', () => ({
  useCurrentUser: () => ({
    data: {
      id: 'admin-1',
      access: {
        permissions: governancePageDomMocks.permissions,
        durable_permissions:
          governancePageDomMocks.durablePermissions ??
          governancePageDomMocks.permissions,
        elevation_ids: governancePageDomMocks.elevationIds,
      },
      authentication: {
        credential_kind: 'opaque_session',
        session_auth_method: 'local',
        mfa_method: null,
        recently_authenticated: true,
        recent_authentication_expires_at: '2026-08-20T01:00:00Z',
        identity_provider_mfa_asserted: false,
        reauthentication_endpoint: '/auth/security/reauthenticate',
        sensitive_actions_ready: governancePageDomMocks.sensitiveActionsReady,
      },
    },
    isLoading: false,
    isFetching: false,
    error: null,
  }),
}))

vi.mock('../hooks/useUnsavedChangesWarning', () => ({
  useUnsavedChangesWarning: () =>
    Object.assign((action?: () => void) => {
      action?.()
      return true
    }, { discardDialog: null, discardDialogOpen: false }),
}))

vi.mock('@tanstack/react-query', () => ({
  useQuery: (options: { queryKey: unknown[]; enabled?: boolean }) => {
    governancePageDomMocks.queryOptions.push(options)
    const data = governancePageDomMocks.queryResult(options.queryKey)
    return {
      data,
      isLoading: false,
      isError: false,
      isSuccess: true,
      isFetching: false,
      error: null,
      refetch: governancePageDomMocks.refetch,
    }
  },
  useMutation: () => ({
    mutate: vi.fn(),
    isPending: false,
    error: null,
    reset: vi.fn(),
  }),
  useQueryClient: () => ({
    invalidateQueries: governancePageDomMocks.invalidateQueries,
    removeQueries: governancePageDomMocks.removeQueries,
  }),
}))

import { AccessGovernancePage } from './AccessGovernancePage'

let root: Root | null = null
let container: HTMLDivElement | null = null

const catalog: IAMCatalog = {
  permissions: [
    {
      id: 'read:iam',
      group: 'IAM',
      label: 'Read IAM',
      description: 'Inspect access policy',
      risk: 'standard',
      delegable: true,
    },
  ],
  roles: [
    {
      id: 'role-1',
      key: 'incident-responder',
      name: 'Incident responder',
      description: 'Respond to incidents',
      permissions: ['read:iam'],
      is_system: false,
      revision: 2,
      assignment_count: 1,
      group_count: 1,
      created_at: '2026-08-01T00:00:00Z',
      updated_at: '2026-08-20T00:00:00Z',
    },
    {
      id: 'role-admin',
      key: 'admin',
      name: 'Administrator',
      description: 'Sealed administrator authority',
      permissions: ['*:*'],
      is_system: true,
      revision: 1,
      assignment_count: 2,
      group_count: 0,
      created_at: '2026-08-01T00:00:00Z',
      updated_at: '2026-08-20T00:00:00Z',
    },
  ],
  groups: [
    {
      id: 'group-system',
      key: 'all-users',
      name: 'All users',
      description: 'Every approved user',
      source: 'local',
      external_key: null,
      is_system: true,
      revision: 1,
      member_count: 10,
      role_ids: [],
      created_at: '2026-08-01T00:00:00Z',
      updated_at: '2026-08-20T00:00:00Z',
    },
    {
      id: 'group-oidc',
      key: 'idp-responders',
      name: 'IdP responders',
      description: 'Synchronized responders',
      source: 'oidc',
      external_key: 'responders',
      is_system: false,
      revision: 3,
      member_count: 4,
      role_ids: ['role-1', 'role-admin'],
      created_at: '2026-08-01T00:00:00Z',
      updated_at: '2026-08-20T00:00:00Z',
    },
    {
      id: 'group-local',
      key: 'local-response',
      name: 'Local response',
      description: 'Locally managed responders',
      source: 'local',
      external_key: null,
      is_system: false,
      revision: 5,
      member_count: 1,
      role_ids: [],
      created_at: '2026-08-01T00:00:00Z',
      updated_at: '2026-08-20T00:00:00Z',
    },
  ],
}

const policyOverview: DataPolicyOverview = {
  state: {
    mode: 'disabled',
    revision: 12,
    coverage_version: 7,
    required_coverage_version: 8,
    enforced_at: null,
    enforced_by_user_id: null,
    updated_by_user_id: 'admin-1',
    updated_at: '2026-08-20T00:00:00Z',
  },
  labels: [
    {
      id: 'label-1',
      key: 'restricted',
      name: 'Restricted',
      description: 'Restricted intelligence',
      color: '#B91C1C',
      is_unrestricted: false,
      is_system: false,
      is_active: true,
      revision: 2,
      role_ids: ['role-1'],
      assigned_feed_count: 3,
      created_at: '2026-08-01T00:00:00Z',
      updated_at: '2026-08-20T00:00:00Z',
    },
  ],
  preflight: {
    ready_for_audit: true,
    ready_for_enforcement: false,
    current_coverage_version: 7,
    required_coverage_version: 8,
    evaluated_policy_revision: 12,
    full: true,
    checked_at: '2026-08-20T12:34:56Z',
    route_manifest: {
      installed: true,
      valid: true,
      version: 3,
      digest: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
      declared_operation_count: 268,
      validated_operation_count: 268,
      request_context_operation_count: 108,
      governance_class_counts: {
        captured_async: 5,
        control_plane: 143,
        dynamic_target: 7,
        egress_fenced: 1,
        public: 11,
        request_context: 101,
      },
    },
    blocker_counts: { missing_route_coverage: 1 },
    blockers: [
      {
        code: 'missing_route_coverage',
        detail: 'One retained-data route has no policy declaration.',
        count: 1,
      },
    ],
  },
}

function queryData(queryKey: unknown[]) {
  const area = queryKey[1]
  if (area === 'iam-catalog') return catalog
  if (area === 'data-policy') return policyOverview
  if (area === 'service-accounts') {
    return { items: [], total: 0, page: 1, page_size: 100 }
  }
  if (area === 'access-reviews') {
    return { campaigns: [], total: 0, page: 1, page_size: 100 }
  }
  if (area === 'elevations') {
    return { elevations: [], total: 0, page: 1, page_size: 100 }
  }
  if (area === 'action-approvals') {
    return {
      approvals: governancePageDomMocks.approvalItems,
      total: governancePageDomMocks.approvalItems.length,
      page: 1,
      page_size: 100,
    }
  }
  if (area === 'iam-groups') return []
  if (area === 'iam-member-candidates') {
    return { users: [], total: 0, limit: 20, offset: 0, has_more: false }
  }
  return undefined
}

function renderPage(permissions: string[]) {
  governancePageDomMocks.permissions = permissions
  governancePageDomMocks.queryResult.mockImplementation(queryData)
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => {
    root?.render(
      <MemoryRouter>
        <AccessGovernancePage />
      </MemoryRouter>,
    )
  })
  return container
}

afterEach(() => {
  act(() => {
    root?.unmount()
  })
  root = null
  container?.remove()
  container = null
  document.body.innerHTML = ''
  governancePageDomMocks.permissions = []
  governancePageDomMocks.durablePermissions = null
  governancePageDomMocks.elevationIds = []
  governancePageDomMocks.sensitiveActionsReady = true
  governancePageDomMocks.approvalItems = []
  governancePageDomMocks.queryOptions = []
  governancePageDomMocks.queryResult.mockReset()
  governancePageDomMocks.refetch.mockClear()
  governancePageDomMocks.invalidateQueries.mockClear()
  governancePageDomMocks.removeQueries.mockClear()
})

describe('AccessGovernancePage permission and policy workflows', () => {
  it('does not expose data-policy controls or approval data for lookalike permissions', () => {
    const view = renderPage(['read:iam', 'read:action_approvals'])

    expect(view.querySelectorAll('h1')).toHaveLength(1)
    expect(view.querySelector('h1')?.textContent).toBe('Access control')
    expect(findButton(view, 'Data handling')).toBeUndefined()
    expect(queryEnabled('data-policy')).toBe(false)
    expect(queryEnabled('action-approvals')).toBe(false)
    expect(view.textContent).toContain('Data-policy state is not available to this role.')
    const attentionColumnHeaders = [...view.querySelectorAll('th')]
    expect(attentionColumnHeaders).toHaveLength(4)
    expect(attentionColumnHeaders.every((heading) => heading.getAttribute('scope') === 'col')).toBe(true)
  })

  it('uses read:approvals to enable the approval queue and read:data_policies for its tab', () => {
    const view = renderPage([
      'read:iam',
      'read:approvals',
      'read:data_policies',
    ])

    expect(findButton(view, 'Data handling')).toBeDefined()
    expect(queryEnabled('data-policy')).toBe(true)
    expect(queryEnabled('action-approvals')).toBe(true)
  })

  it('keeps system and OIDC groups visibly immutable even with IAM write access', () => {
    const view = renderPage(['read:iam', 'write:iam', 'read:users'])

    click(findButton(view, 'Groups'))

    expect(view.querySelectorAll('[aria-label="Externally managed group"]')).toHaveLength(2)
    expect(view.textContent).toContain(
      'System group membership is derived automatically and cannot be edited.',
    )
    expect(view.querySelector('fieldset')?.hasAttribute('disabled')).toBe(true)
    expect(findButton(view, 'Delete group')).toBeUndefined()
    expect(findButton(view, 'New group')).toBeDefined()

    click(findButtonContaining(view, 'IdP responders'))

    expect(view.textContent).toContain(
      'This group is controlled by its identity-provider mapping.',
    )
    expect(view.querySelector('fieldset')?.hasAttribute('disabled')).toBe(true)
    expect(findButton(view, 'Delete group')).toBeUndefined()
  })

  it('surfaces activation preflight evidence and blocks enforcement review', () => {
    const view = renderPage([
      'read:iam',
      'read:data_policies',
      'write:data_policies',
    ])

    click(findButton(view, 'Data handling'))

    expect(view.textContent).toContain('Activation preflight')
    expect(view.textContent).toContain('Coverage 7 / 8')
    expect(view.textContent).toContain('Full scan · policy revision 12')
    expect(view.textContent).toContain(
      'Valid · v3 · 268 / 268 operations · 108 request-context',
    )
    expect(view.textContent).toContain('dynamic target 7')
    expect(view.textContent).toContain(
      'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
    )
    expect(view.textContent).toContain('missing route coverage')
    expect(view.textContent).toContain(
      'One retained-data route has no policy declaration.',
    )

    const modeSelect = view.querySelector<HTMLSelectElement>(
      'section[aria-labelledby="policy-mode-heading"] select',
    )
    const reason = view.querySelector<HTMLTextAreaElement>(
      'section[aria-labelledby="policy-mode-heading"] textarea',
    )
    expect(modeSelect).not.toBeNull()
    expect(reason).not.toBeNull()

    act(() => {
      setSelectValue(modeSelect!, 'enforced')
      setTextAreaValue(reason!, 'Coverage review is complete')
    })

    const reviewButton = findButton(view, 'Review mode change')
    expect(view.textContent).toContain(
      'Resolve the preflight blockers before selecting this mode.',
    )
    expect(reviewButton?.hasAttribute('disabled')).toBe(true)
    expect(view.textContent).toContain('Reassign the 3 feed(s)')
    expect(findButton(view, 'Archive label')?.hasAttribute('disabled')).toBe(true)
    expect(findButton(view, 'Save role boundary')).toBeDefined()
    expect(findButton(view, 'Save metadata')).toBeDefined()
  })

  it('renders sealed wildcard authority explicitly', () => {
    const view = renderPage(['read:iam'])
    click(findButton(view, 'Access roles'))
    click(findButtonContaining(view, 'Administrator'))

    expect(view.textContent).toContain(
      'All current and future permissions (wildcard)',
    )
    expect(view.textContent).toContain('2 principal assignments')
  })

  it('does not convert temporary IAM elevation into persistent write authority', () => {
    governancePageDomMocks.durablePermissions = ['read:iam']
    governancePageDomMocks.elevationIds = ['elevation-1']
    const view = renderPage(['read:iam', 'write:iam'])

    click(findButton(view, 'Access roles'))

    expect(view.textContent).toContain(
      'Temporary elevation can inspect access policy',
    )
    expect(findButton(view, 'New access role')).toBeUndefined()
  })

  it('hides cached optional governance data as soon as permission is revoked', () => {
    governancePageDomMocks.approvalItems = [
      {
        id: 'approval-1',
        action_type: 'export.delete',
        action_label: 'Delete classified export',
        target_type: 'export',
        target_id: 'export-1',
        requested_by_email: 'operator@example.com',
        request_reason: 'Remove stale artifact',
        expires_at: '2026-08-21T00:00:00Z',
        status: 'pending',
        revision: 1,
        created_at: '2026-08-20T00:00:00Z',
        updated_at: '2026-08-20T00:00:00Z',
      },
    ]
    const view = renderPage(['read:iam', 'read:approvals'])
    expect(view.textContent).toContain('Delete classified export')

    governancePageDomMocks.permissions = ['read:iam']
    act(() => {
      root?.render(
        <MemoryRouter>
          <AccessGovernancePage />
        </MemoryRouter>,
      )
    })

    expect(view.textContent).not.toContain('Delete classified export')
    expect(governancePageDomMocks.removeQueries).toHaveBeenCalledWith({
      queryKey: ['governance', 'action-approvals'],
      exact: true,
    })
  })
})

function queryEnabled(area: string): boolean | undefined {
  return governancePageDomMocks.queryOptions
    .filter((options) => options.queryKey[1] === area)
    .at(-1)?.enabled
}

function findButton(view: ParentNode, label: string): HTMLButtonElement | undefined {
  return Array.from(view.querySelectorAll('button')).find(
    (button) => button.textContent?.trim() === label,
  )
}

function findButtonContaining(
  view: ParentNode,
  label: string,
): HTMLButtonElement | undefined {
  return Array.from(view.querySelectorAll('button')).find(
    (button) => button.textContent?.includes(label),
  )
}

function click(button: HTMLButtonElement | undefined) {
  expect(button).toBeDefined()
  act(() => {
    button?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  })
}

function setSelectValue(select: HTMLSelectElement, value: string) {
  const descriptor = Object.getOwnPropertyDescriptor(
    window.HTMLSelectElement.prototype,
    'value',
  )
  descriptor?.set?.call(select, value)
  select.dispatchEvent(new Event('change', { bubbles: true }))
}

function setTextAreaValue(textarea: HTMLTextAreaElement, value: string) {
  const descriptor = Object.getOwnPropertyDescriptor(
    window.HTMLTextAreaElement.prototype,
    'value',
  )
  descriptor?.set?.call(textarea, value)
  textarea.dispatchEvent(new Event('input', { bubbles: true }))
}
