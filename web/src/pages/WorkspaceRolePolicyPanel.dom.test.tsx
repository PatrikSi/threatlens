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

const ROLE_TOP_NAVIGATION_IDS = [
  'primary.dashboard',
  'primary.alerts',
  'primary.investigations',
  'primary.feeds',
  'primary.stats',
  'primary.export',
  'primary.reporting',
]

const ROLE_SETTINGS_NAVIGATION_IDS = [
  'settings.account',
  'settings.tokens',
  'settings.ai',
  'settings.tagging',
  'settings.identity',
  'settings.access',
  'settings.users',
  'settings.audit',
  'settings.operations',
  'settings.integrations.webhooks',
  'settings.integrations.smtp',
]

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
      canManagePolicies: true,
      roleMutationPending: false,
      resetRolePolicy: { isPending: false, mutate: vi.fn() },
      updateRolePolicy: { isPending: false, mutate: vi.fn() },
      setResetRoleRequested: vi.fn(),
    } as unknown as WorkspaceSettingsController

    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    act(() => root?.render(<WorkspaceRolePolicyPanel controller={controller} />))

    const preview = container.querySelector('[aria-label="Analyst top navigation preview"]')
    expect(preview).not.toBeNull()
    expect(preview?.textContent).toContain('Dashboard')
    expect(preview?.textContent).toContain('Mobile navigation')
    expect(preview?.querySelector('[data-navigation-preview-module="primary.settings"]')?.textContent)
      .toContain('SettingsFixed container')
    expect(preview?.querySelector('[data-navigation-preview-module^="settings."]')).toBeNull()
    expect(preview?.textContent).not.toContain('Personal settings')
    expect(preview?.textContent).not.toContain('Automation settings')
    expect(preview?.textContent).not.toContain('Integration settings')
    expect(preview?.textContent).not.toContain('Feeds')
    expect(preview?.textContent).not.toContain('AI automation')
    expect(preview?.textContent).not.toContain('Single sign-on')
    expect(preview?.querySelector('a')).toBeNull()
    expect(preview?.querySelector('button')).toBeNull()
    const roleLabels = [...container.querySelectorAll('[aria-label="Built-in role"] button')]
      .map((button) => button.textContent?.trim())
    expect(roleLabels).toEqual(['Administrator', 'Analyst', 'Viewer'])
    expect(container.textContent).toContain('future.timeline is retained for compatibility')
    expect(
      container.querySelector<HTMLInputElement>('[aria-label="Show AI automation for Analyst"]')?.disabled,
    ).toBe(true)
    expect(
      container.querySelector<HTMLInputElement>('[aria-label="Show Single sign-on for Analyst"]')?.disabled,
    ).toBe(true)
    const taggingHandle = container.querySelector<HTMLButtonElement>(
      '[aria-label^="Drag Content tagging."]',
    )
    expect(taggingHandle?.disabled).toBe(true)
    expect(taggingHandle?.draggable).toBe(false)
    expect(taggingHandle?.getAttribute('aria-label')).toContain('Position 1 of 1')
    expect(container.textContent).toContain('Administrator base role required')
    expect(container.querySelector('a[href*="future.timeline"]')).toBeNull()
    expect(navigationItemIds(container, 'data-navigation-reorder-item')).toEqual(
      ROLE_TOP_NAVIGATION_IDS,
    )
    expect(
      sortedNavigationItemIds(container, 'data-settings-navigation-reorder-item'),
    ).toEqual([...ROLE_SETTINGS_NAVIGATION_IDS].sort())
    expect(
      container.querySelector('[data-settings-navigation-reorder-item]')?.closest('details')
        ?.querySelector('summary')?.textContent,
    ).toContain('Settings sidebar')
    expect(
      [...container.querySelectorAll<HTMLOptGroupElement>('select optgroup')].map(
        (group) => group.label,
      ),
    ).toEqual([
      'Main navigation',
      'Personal settings',
      'Integration settings',
    ])
  })

  it.each(['admin', 'analyst', 'viewer'] as const)(
    'keeps the %s role editor scoped to the seven policy-managed top-navigation items',
    (role) => {
      const policy = rolePolicy(role)
      const controller = {
        roles: ['admin', 'analyst', 'viewer'],
        selectedRole: role,
        selectRole: vi.fn(),
        selectedPolicy: policy,
        roleDraft: createRolePolicyDraft(policy),
        setRoleDraft: vi.fn(),
        rolePoliciesLoading: false,
        roleError: '',
        roleFeedback: '',
        roleValidation: '',
        selectedPolicyWarnings: [],
        roleDirty: false,
        canManagePolicies: true,
        roleMutationPending: false,
        roleRevisionConflict: false,
        discardAndReloadRole: vi.fn(),
        resetRolePolicy: { isPending: false, mutate: vi.fn() },
        updateRolePolicy: { isPending: false, mutate: vi.fn() },
        setResetRoleRequested: vi.fn(),
      } as unknown as WorkspaceSettingsController

      container = document.createElement('div')
      document.body.appendChild(container)
      root = createRoot(container)
      act(() => root?.render(<WorkspaceRolePolicyPanel controller={controller} />))

      expect(navigationItemIds(container, 'data-navigation-reorder-item')).toEqual(
        ROLE_TOP_NAVIGATION_IDS,
      )
      expect(container.querySelector('[data-navigation-reorder-item^="settings."]')).toBeNull()
      expect(container.querySelector('[data-navigation-reorder-item="primary.settings"]')).toBeNull()
    },
  )

  it('reorders desktop defaults by drag, handle keyboard controls, and touch buttons', () => {
    const policy = rolePolicy()
    const draft = createRolePolicyDraft(policy)
    const setRoleDraft = vi.fn()
    const controller = {
      roles: ['admin', 'analyst', 'viewer'],
      selectedRole: 'analyst',
      selectRole: vi.fn(),
      selectedPolicy: policy,
      roleDraft: draft,
      setRoleDraft,
      rolePoliciesLoading: false,
      roleError: '',
      roleFeedback: '',
      roleValidation: '',
      selectedPolicyWarnings: [],
      roleDirty: true,
      canManagePolicies: true,
      roleMutationPending: false,
      roleRevisionConflict: false,
      discardAndReloadRole: vi.fn(),
      resetRolePolicy: { isPending: false, mutate: vi.fn() },
      updateRolePolicy: { isPending: false, mutate: vi.fn() },
      setResetRoleRequested: vi.fn(),
    } as unknown as WorkspaceSettingsController

    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    act(() => root?.render(<WorkspaceRolePolicyPanel controller={controller} />))

    const handle = container.querySelector<HTMLButtonElement>('[aria-label^="Drag Feeds."]')
    const target = container.querySelector<HTMLElement>(
      '[data-navigation-reorder-item="primary.dashboard"]',
    )
    const invalidTarget = container.querySelector<HTMLElement>(
      '[data-settings-navigation-reorder-item="settings.tokens"]',
    )
    const earlierButton = container.querySelector<HTMLButtonElement>(
      '[aria-label="Move Feeds earlier for Analyst"]',
    )

    expect(handle).not.toBeNull()
    expect(handle?.draggable).toBe(true)
    expect(handle?.getAttribute('aria-describedby')).toBe(
      'role-navigation-reorder-instructions',
    )
    expect(container.querySelector('[aria-label="Mobile order in primary tier for Feeds"]')).not.toBeNull()
    expect(
      container.querySelector('[aria-label="Mobile order for API tokens uses desktop group order"]')
        ?.tagName,
    ).toBe('SPAN')
    expect(container.querySelector('[data-navigation-reorder-item="primary.settings"]')).toBeNull()
    expect(container.querySelector('[data-navigation-reorder-item="settings.integrations"]')).toBeNull()
    expect(navigationItemIds(container, 'data-navigation-reorder-item')).toEqual(
      ROLE_TOP_NAVIGATION_IDS,
    )
    expect(container.querySelector('[data-navigation-reorder-item^="settings."]')).toBeNull()
    expect(
      container.querySelector<HTMLButtonElement>('[aria-label^="Drag Dashboard."]')
        ?.getAttribute('aria-label'),
    ).toContain('of 7')
    expect(earlierButton).not.toBeNull()

    const transfer = createDataTransfer()
    act(() => dispatchDragEvent(handle!, 'dragstart', transfer))
    let invalidDragOver: Event
    act(() => {
      invalidDragOver = dispatchDragEvent(invalidTarget!, 'dragover', transfer)
    })
    expect(invalidDragOver!.defaultPrevented).toBe(false)
    expect(invalidTarget?.className).not.toContain('bg-cyan/10')
    act(() => dispatchDragEvent(target!, 'dragover', transfer))
    act(() => dispatchDragEvent(target!, 'drop', transfer))
    act(() => dispatchDragEvent(handle!, 'dragend', transfer))

    const dropUpdate = setRoleDraft.mock.calls[0]?.[0] as
      | ((current: typeof draft) => typeof draft)
      | undefined
    expect(dropUpdate).toBeTypeOf('function')
    const droppedDraft = dropUpdate!(draft)
    expect(droppedDraft.modules.get('primary.feeds')?.order).toBeLessThan(
      droppedDraft.modules.get('primary.dashboard')?.order ?? Number.MAX_SAFE_INTEGER,
    )
    expect(droppedDraft.modules.get('primary.feeds')?.mobile_priority).toBe(
      draft.modules.get('primary.feeds')?.mobile_priority,
    )
    expect(container.querySelector('[role="status"]')?.textContent).toContain(
      'Moved Feeds to desktop position 1',
    )

    act(() => earlierButton!.click())
    expect(setRoleDraft).toHaveBeenCalledTimes(2)

    act(() => handle!.click())
    expect(handle?.getAttribute('aria-pressed')).toBe('true')
    act(() => handle!.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true })))
    expect(setRoleDraft).toHaveBeenCalledTimes(3)
    expect(container.querySelector('[role="status"]')?.textContent).toContain(
      'Save Analyst navigation defaults to apply this order.',
    )
    expect(target?.className).toContain('py-2')
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
      canManagePolicies: true,
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
    expect(
      [...container.querySelectorAll<HTMLButtonElement>('[aria-label^="Drag "]')]
        .every((handle) => handle.draggable === false),
    ).toBe(true)
    expect(container.textContent).toContain('Discard default changes and reload')
    expect(container.textContent).toContain('Workspace navigation by role')
    expect(container.textContent).toContain('Shown by default')
    expect(container.textContent).toContain('Users can customize')
    expect(container.textContent).toContain('Desktop order')
    expect(container.textContent).toContain('Mobile order in tier')
    expect(container.textContent).toContain('Default start page')
    expect(container.textContent).toContain('Initial dashboard panels')
    expect(container.textContent).toContain('Organization settings')
    expect(container.textContent).not.toContain('settings.workspace')
    expect(navigationItemIds(container, 'data-navigation-reorder-item')).toEqual(
      ROLE_TOP_NAVIGATION_IDS,
    )

    const table = container.querySelector('table')
    const columnHeaders = [...(table?.querySelectorAll('thead th') ?? [])]
    expect(columnHeaders).toHaveLength(5)
    expect(columnHeaders.every((header) => header.getAttribute('scope') === 'col')).toBe(true)

    const body = table?.querySelector('tbody')
    const row = table?.querySelector('tbody tr')
    expect(table?.className).toContain('sm:min-w-[760px]')
    expect(table?.className).not.toMatch(/(^|\s)min-w-\[760px\](\s|$)/)
    expect(body?.className).toContain('block')
    expect(row?.className).toContain('grid')
    expect(row?.className).toContain('sm:table-row')
  })

  it('lets policy readers inspect every role without exposing organization mutations', () => {
    const policy = rolePolicy()
    const setRoleDraft = vi.fn()
    const controller = {
      roles: ['admin', 'analyst', 'viewer'],
      selectedRole: 'analyst',
      selectRole: vi.fn(),
      selectedPolicy: policy,
      roleDraft: createRolePolicyDraft(policy),
      setRoleDraft,
      rolePoliciesLoading: false,
      roleError: '',
      roleFeedback: '',
      roleValidation: '',
      selectedPolicyWarnings: [],
      roleDirty: false,
      canManagePolicies: false,
      roleMutationPending: false,
      roleRevisionConflict: false,
      discardAndReloadRole: vi.fn(),
      resetRolePolicy: { isPending: false, mutate: vi.fn() },
      updateRolePolicy: { isPending: false, mutate: vi.fn() },
      setResetRoleRequested: vi.fn(),
    } as unknown as WorkspaceSettingsController

    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    act(() => root?.render(<WorkspaceRolePolicyPanel controller={controller} />))

    expect(container.textContent).toContain('Read-only organization policy')
    expect(container.textContent).toContain('Review top-navigation and Settings-sidebar defaults')
    const roleButtons = [...container.querySelectorAll<HTMLButtonElement>('[aria-label="Built-in role"] button')]
    expect(roleButtons).toHaveLength(3)
    expect(roleButtons.every((button) => !button.disabled)).toBe(true)
    act(() => roleButtons[2].click())
    expect(controller.selectRole).toHaveBeenCalledWith('viewer')

    const editingControls = [...container.querySelectorAll<HTMLInputElement | HTMLSelectElement>(
      'input, select',
    )]
    expect(editingControls.length).toBeGreaterThan(10)
    expect(editingControls.every((control) => control.disabled)).toBe(true)
    expect(
      [...container.querySelectorAll<HTMLButtonElement>('[aria-label^="Drag "]')]
        .every((handle) => handle.disabled && !handle.draggable),
    ).toBe(true)
    expect(container.textContent).not.toContain('Save navigation defaults')
    expect(container.textContent).not.toContain('Reset Analyst defaults')
    expect(setRoleDraft).not.toHaveBeenCalled()
  })

  it('resets reorder interaction state when the selected role changes', () => {
    const analystPolicy = rolePolicy()
    const controller = {
      roles: ['admin', 'analyst', 'viewer'],
      selectedRole: 'analyst',
      selectRole: vi.fn(),
      selectedPolicy: analystPolicy,
      roleDraft: createRolePolicyDraft(analystPolicy),
      setRoleDraft: vi.fn(),
      rolePoliciesLoading: false,
      roleError: '',
      roleFeedback: '',
      roleValidation: '',
      selectedPolicyWarnings: [],
      roleDirty: false,
      canManagePolicies: true,
      roleMutationPending: false,
      roleRevisionConflict: false,
      discardAndReloadRole: vi.fn(),
      resetRolePolicy: { isPending: false, mutate: vi.fn() },
      updateRolePolicy: { isPending: false, mutate: vi.fn() },
      setResetRoleRequested: vi.fn(),
    } as unknown as WorkspaceSettingsController

    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    act(() => root?.render(<WorkspaceRolePolicyPanel controller={controller} />))

    const analystHandle = container.querySelector<HTMLButtonElement>(
      '[aria-label^="Drag Feeds."]',
    )!
    act(() => analystHandle.click())
    expect(analystHandle.getAttribute('aria-pressed')).toBe('true')

    const viewerPolicy = { ...analystPolicy, role: 'viewer' as const }
    const viewerController = {
      ...controller,
      selectedRole: 'viewer' as const,
      selectedPolicy: viewerPolicy,
      roleDraft: createRolePolicyDraft(viewerPolicy),
    }
    act(() => root?.render(<WorkspaceRolePolicyPanel controller={viewerController} />))

    const viewerHandle = container.querySelector<HTMLButtonElement>(
      '[aria-label^="Drag Feeds."]',
    )
    expect(viewerHandle?.getAttribute('aria-pressed')).toBe('false')
    expect(container.querySelector('[role="status"]')?.textContent).toContain(
      'save Viewer navigation defaults',
    )
    expect(container.querySelector('[role="status"]')?.textContent).not.toContain('Analyst')
  })
})

function rolePolicy(
  role: WorkspaceRolePolicyResponse['role'] = 'analyst',
): WorkspaceRolePolicyResponse {
  return {
    role, landing_module_id: 'primary.dashboard', revision: 2,
    modules: TRUSTED_WORKSPACE_MODULES.filter(
      (module) => module.policyManaged || module.isContainer,
    ).map((module) => ({
      module_id: module.id,
      visible: module.defaultVisibleRoles.includes(role),
      optional: module.defaultOptional,
      order: module.defaultOrder,
      mobile_priority: module.defaultMobilePriority,
    })),
    dashboard_panel_ids: ['rss'], updated_by_user_id: null,
    created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    unknown_module_ids: ['future.timeline'], unknown_dashboard_panel_ids: [], warnings: [],
  }
}

function createDataTransfer(): DataTransfer {
  let value = ''
  return {
    dropEffect: 'none',
    effectAllowed: 'uninitialized',
    getData: () => value,
    setData: (_format: string, nextValue: string) => {
      value = nextValue
    },
  } as unknown as DataTransfer
}

function dispatchDragEvent(target: Element, type: string, dataTransfer: DataTransfer) {
  const event = new Event(type, { bubbles: true, cancelable: true })
  Object.defineProperty(event, 'dataTransfer', { value: dataTransfer })
  target.dispatchEvent(event)
  return event
}

function navigationItemIds(
  view: ParentNode,
  attribute: 'data-navigation-reorder-item' | 'data-settings-navigation-reorder-item',
) {
  return [...view.querySelectorAll(`[${attribute}]`)].map((item) => item.getAttribute(attribute))
}

function sortedNavigationItemIds(
  view: ParentNode,
  attribute: 'data-navigation-reorder-item' | 'data-settings-navigation-reorder-item',
) {
  return navigationItemIds(view, attribute).sort()
}
