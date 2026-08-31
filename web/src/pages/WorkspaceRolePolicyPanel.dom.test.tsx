// @vitest-environment jsdom

import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { WorkspaceRolePolicyResponse } from '../types/workspace'
import { TRUSTED_WORKSPACE_MODULES } from '../workspace/moduleRegistry'
import type { WorkspaceSettingsController } from './useWorkspaceSettingsController'
import { WorkspaceRolePolicyPanel } from './WorkspaceRolePolicyPanel'
import { createRolePolicyDraft } from './workspaceSettingsModel'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

let root: Root | null = null
let container: HTMLDivElement | null = null

afterEach(() => {
  act(() => root?.unmount())
  root = null
  container?.remove()
  container = null
  document.body.innerHTML = ''
})

describe('WorkspaceRolePolicyPanel', () => {
  it('renders an inert trusted preview and surfaces future-version warnings', () => {
    const policy = rolePolicy()
    const draft = createRolePolicyDraft(policy)
    const feeds = draft.modules.get('primary.feeds')!
    draft.modules.set('primary.feeds', { ...feeds, visible: false })
    const controller = {
      roles: ['admin', 'analyst', 'viewer'],
      selectedRole: 'analyst',
      selectRole: vi.fn(),
      selectedPolicy: policy,
      roleDraft: draft,
      setRoleDraft: vi.fn(),
      rolePoliciesLoading: false,
      roleError: '',
      roleFeedback: '',
      roleValidation: '',
      selectedPolicyWarnings: ['unknown_policy_module:future.timeline'],
      roleDirty: true,
      resetRolePolicy: { isPending: false, mutate: vi.fn() },
      updateRolePolicy: { isPending: false, mutate: vi.fn() },
      setResetRoleRequested: vi.fn(),
    } as unknown as WorkspaceSettingsController

    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    act(() => root?.render(<WorkspaceRolePolicyPanel controller={controller} />))

    const preview = container.querySelector('[aria-label="Analyst navigation preview"]')
    expect(preview).not.toBeNull()
    expect(preview?.textContent).toContain('Dashboard')
    expect(preview?.textContent).toContain('Mobile navigation')
    expect(preview?.textContent).toContain('Navigation')
    expect(preview?.textContent).not.toContain('Workspace')
    expect(preview?.textContent).not.toContain('Feeds')
    expect(preview?.querySelector('a')).toBeNull()
    expect(preview?.querySelector('button')).toBeNull()
    const roleLabels = [...container.querySelectorAll('[aria-label="Built-in role"] button')]
      .map((button) => button.textContent?.trim())
    expect(roleLabels).toEqual(['Administrator', 'Analyst', 'Viewer'])
    expect(container.textContent).toContain('future.timeline is retained for compatibility')
    expect(container.querySelector('a[href*="future.timeline"]')).toBeNull()
  })

  it('disables editing while mutations are pending and uses compact mobile rows', () => {
    const policy = rolePolicy()
    const controller = {
      roles: ['admin', 'analyst', 'viewer'],
      selectedRole: 'analyst',
      selectRole: vi.fn(),
      selectedPolicy: policy,
      roleDraft: createRolePolicyDraft(policy),
      setRoleDraft: vi.fn(),
      rolePoliciesLoading: false,
      roleError: 'A stale revision was rejected.',
      roleFeedback: '',
      roleValidation: '',
      selectedPolicyWarnings: [],
      roleDirty: true,
      roleMutationPending: true,
      roleRevisionConflict: true,
      discardAndReloadRole: vi.fn(),
      resetRolePolicy: { isPending: true, mutate: vi.fn() },
      updateRolePolicy: { isPending: true, mutate: vi.fn() },
      setResetRoleRequested: vi.fn(),
    } as unknown as WorkspaceSettingsController

    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    act(() => root?.render(<WorkspaceRolePolicyPanel controller={controller} />))

    const controls = [...container.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLButtonElement>(
      'input, select, button',
    )]
    expect(controls.length).toBeGreaterThan(10)
    expect(controls.every((control) => control.disabled)).toBe(true)
    expect(container.textContent).toContain('Discard default changes and reload')
    expect(container.textContent).toContain('Navigation defaults by role')
    expect(container.textContent).toContain('Shown by default')
    expect(container.textContent).toContain('Users can customize')
    expect(container.textContent).toContain('Default start page')
    expect(container.textContent).toContain('Initial dashboard panels')
    expect(container.textContent).toContain('Settings navigation')
    expect(container.textContent).not.toContain('settings.workspace')

    const columnHeaders = [...container.querySelectorAll('thead th')]
    expect(columnHeaders).toHaveLength(5)
    expect(columnHeaders.every((header) => header.getAttribute('scope') === 'col')).toBe(true)

    const table = container.querySelector('table')
    const body = container.querySelector('tbody')
    const row = container.querySelector('tbody tr')
    expect(table?.className).toContain('sm:min-w-[760px]')
    expect(table?.className).not.toMatch(/(^|\s)min-w-\[760px\](\s|$)/)
    expect(body?.className).toContain('block')
    expect(row?.className).toContain('grid')
    expect(row?.className).toContain('sm:table-row')
  })
})

function rolePolicy(): WorkspaceRolePolicyResponse {
  return {
    role: 'analyst', landing_module_id: 'primary.dashboard', revision: 2,
    modules: TRUSTED_WORKSPACE_MODULES.filter((module) => module.policyManaged).map((module) => ({
      module_id: module.id,
      visible: module.defaultVisibleRoles.includes('analyst'),
      optional: module.defaultOptional,
      order: module.defaultOrder,
      mobile_priority: module.defaultMobilePriority,
    })),
    dashboard_panel_ids: ['rss'], updated_by_user_id: null,
    created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    unknown_module_ids: ['future.timeline'], unknown_dashboard_panel_ids: [], warnings: [],
  }
}
