// @vitest-environment jsdom

import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { WorkspaceSettingsController } from './useWorkspaceSettingsController'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const pageMocks = vi.hoisted(() => ({
  controller: null as unknown,
}))

vi.mock('./useWorkspaceSettingsController', () => ({
  useWorkspaceSettingsController: () => pageMocks.controller,
}))

vi.mock('./WorkspacePersonalizationPanel', () => ({
  WorkspacePersonalizationPanel: () => <div>Personal panel</div>,
}))

vi.mock('./WorkspaceRolePolicyPanel', () => ({
  WorkspaceRolePolicyPanel: () => <div>Role panel</div>,
}))

vi.mock('./WorkspaceCompatibilityWarnings', () => ({
  WorkspaceCompatibilityWarnings: () => null,
}))

import { WorkspaceSettingsPage } from './WorkspaceSettingsPage'

let root: Root | null = null
let container: HTMLDivElement | null = null

afterEach(() => {
  act(() => root?.unmount())
  root = null
  container?.remove()
  container = null
  document.body.innerHTML = ''
})

describe('WorkspaceSettingsPage', () => {
  it('requires confirmation before a dirty refresh discards both drafts', () => {
    const setDiscardReloadRequested = vi.fn()
    const discardAndReload = vi.fn()
    pageMocks.controller = controller({
      hasUnsavedChanges: true,
      discardReloadRequested: false,
      setDiscardReloadRequested,
      discardAndReload,
    })
    renderPage()

    expect(document.body.textContent).toContain('Personal and organization')
    expect(document.body.textContent).toContain('Navigation')
    expect(document.body.textContent).toContain(
      'Configure the top navigation, Settings sidebar, start page, and initial dashboard panels',
    )

    clickButton('Discard and reload')
    expect(setDiscardReloadRequested).toHaveBeenCalledWith(true)
    expect(discardAndReload).not.toHaveBeenCalled()

    pageMocks.controller = controller({
      hasUnsavedChanges: true,
      discardReloadRequested: true,
      setDiscardReloadRequested,
      discardAndReload,
    })
    rerenderPage()
    expect(document.body.textContent).toContain('All unsaved personal preferences and role defaults')

    const dialog = document.body.querySelector('[role="alertdialog"]')
    const confirm = [...(dialog?.querySelectorAll('button') ?? [])].find(
      (button) => button.textContent === 'Discard and reload',
    )
    expect(confirm).toBeDefined()
    act(() => confirm?.dispatchEvent(new MouseEvent('click', { bubbles: true })))
    expect(discardAndReload).toHaveBeenCalledTimes(1)
  })

  it('uses personal scope and omits organization defaults without workspace read access', () => {
    pageMocks.controller = controller({ canReadPolicies: false, canManagePolicies: false })

    renderPage()

    const header = document.body.querySelector('header')
    expect(header?.querySelector('h1')?.textContent).toBe('Navigation')
    expect(header?.querySelector('span')?.textContent).toBe('Personal')
    expect(document.body.textContent).toContain('Personal panel')
    expect(document.body.textContent).not.toContain('Role panel')
    expect(document.body.querySelector('[role="tablist"]')).toBeNull()
  })

  it('exposes organization defaults for inspection without management access', () => {
    pageMocks.controller = controller({ canReadPolicies: true, canManagePolicies: false })

    renderPage()

    const header = document.body.querySelector('header')
    expect(header?.querySelector('span')?.textContent).toBe('Personal and organization')
    expect(document.body.querySelector('[role="tablist"]')).not.toBeNull()
    expect(document.body.textContent).toContain('Personal panel')

    act(() => findTab('Role defaults').click())
    expect(findTabPanel('role-defaults-navigation-panel').hidden).toBe(false)
    expect(document.body.textContent).toContain('Role panel')
    expect(document.body.textContent).toContain(
      'Review organization top-navigation and Settings-sidebar defaults',
    )
  })

  it('shows one admin editor at a time with an arrow-key accessible tab pattern', () => {
    pageMocks.controller = controller({})

    renderPage()

    const personalTab = findTab('Personal')
    const roleDefaultsTab = findTab('Role defaults')
    const personalPanel = findTabPanel('personal-navigation-panel')
    const roleDefaultsPanel = findTabPanel('role-defaults-navigation-panel')

    expect(document.body.querySelector('[role="tablist"]')?.getAttribute('aria-label')).toBe(
      'Navigation settings scope',
    )
    expect(personalTab.getAttribute('aria-selected')).toBe('true')
    expect(personalTab.tabIndex).toBe(0)
    expect(roleDefaultsTab.getAttribute('aria-selected')).toBe('false')
    expect(roleDefaultsTab.tabIndex).toBe(-1)
    expect(personalPanel.hidden).toBe(false)
    expect(roleDefaultsPanel.hidden).toBe(true)
    expect(document.body.textContent).toContain('Customize your top navigation, Settings sidebar')

    act(() => {
      personalTab.focus()
      personalTab.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }))
    })

    expect(roleDefaultsTab.getAttribute('aria-selected')).toBe('true')
    expect(roleDefaultsTab.tabIndex).toBe(0)
    expect(roleDefaultsTab).toBe(document.activeElement)
    expect(personalPanel.hidden).toBe(true)
    expect(roleDefaultsPanel.hidden).toBe(false)
    expect(document.body.textContent).toContain(
      'Set organization top-navigation and Settings-sidebar defaults',
    )

    act(() => {
      roleDefaultsTab.dispatchEvent(new KeyboardEvent('keydown', { key: 'Home', bubbles: true }))
    })

    expect(personalTab.getAttribute('aria-selected')).toBe('true')
    expect(personalTab).toBe(document.activeElement)
    expect(personalPanel.hidden).toBe(false)
    expect(roleDefaultsPanel.hidden).toBe(true)
  })

  it('keeps inactive drafts and issues discoverable without unmounting either editor', () => {
    pageMocks.controller = controller({
      personalDirty: true,
      personalError: 'Personal settings could not be saved.',
      roleDirty: true,
      roleValidation: 'Choose a visible start page.',
      selectedPolicyWarnings: ['unknown_policy_module:legacy'],
    })

    renderPage()

    const personalTab = findTab('Personal')
    const roleDefaultsTab = findTab('Role defaults')
    const personalPanel = findTabPanel('personal-navigation-panel')
    const roleDefaultsPanel = findTabPanel('role-defaults-navigation-panel')

    expect(personalTab.textContent).toContain('Unsaved')
    expect(personalTab.textContent).toContain('Needs attention')
    expect(roleDefaultsTab.textContent).toContain('Unsaved')
    expect(roleDefaultsTab.textContent).toContain('Needs attention')
    expect(personalPanel.textContent).toContain('Personal panel')
    expect(roleDefaultsPanel.textContent).toContain('Role panel')
    expect(roleDefaultsPanel.hidden).toBe(true)

    act(() => roleDefaultsTab.dispatchEvent(new MouseEvent('click', { bubbles: true })))

    expect(personalPanel.hidden).toBe(true)
    expect(roleDefaultsPanel.hidden).toBe(false)
    expect(personalTab.textContent).toContain('Unsaved')
    expect(personalTab.textContent).toContain('Needs attention')
  })
})

function renderPage() {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  rerenderPage()
}

function rerenderPage() {
  act(() => root?.render(<WorkspaceSettingsPage />))
}

function clickButton(label: string) {
  const button = [...(container?.querySelectorAll('button') ?? [])].find(
    (candidate) => candidate.textContent?.trim() === label,
  )
  expect(button).toBeDefined()
  act(() => button?.dispatchEvent(new MouseEvent('click', { bubbles: true })))
}

function findTab(label: string): HTMLButtonElement {
  const tab = [...document.body.querySelectorAll<HTMLButtonElement>('[role="tab"]')].find(
    (candidate) => candidate.textContent?.includes(label),
  )
  expect(tab).toBeDefined()
  return tab!
}

function findTabPanel(id: string): HTMLDivElement {
  const panel = document.getElementById(id)
  expect(panel?.getAttribute('role')).toBe('tabpanel')
  return panel as HTMLDivElement
}

function controller(overrides: Partial<WorkspaceSettingsController>): WorkspaceSettingsController {
  return {
    workspace: {
      model: { warnings: [] },
      preferences: null,
      isResettingPreferences: false,
    },
    workspaceError: '',
    canReadPolicies: true,
    canManagePolicies: true,
    personalDirty: false,
    personalError: '',
    isRefreshing: false,
    personalMutationPending: false,
    roleDirty: false,
    roleError: '',
    roleValidation: '',
    selectedPolicyWarnings: [],
    roleMutationPending: false,
    hasUnsavedChanges: false,
    refresh: vi.fn(),
    requestedRole: null,
    selectedRole: 'analyst',
    cancelRoleSelection: vi.fn(),
    confirmRoleSelection: vi.fn(),
    resetPersonalRequested: false,
    setResetPersonalRequested: vi.fn(),
    resetPersonal: vi.fn(),
    resetRoleRequested: false,
    setResetRoleRequested: vi.fn(),
    resetRolePolicy: { isPending: false, mutate: vi.fn() },
    discardReloadRequested: false,
    setDiscardReloadRequested: vi.fn(),
    discardAndReload: vi.fn(),
    ...overrides,
  } as unknown as WorkspaceSettingsController
}
