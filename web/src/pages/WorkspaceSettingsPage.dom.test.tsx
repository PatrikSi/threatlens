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
      'Choose the navigation items, start page, and initial dashboard panels',
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

  it('uses personal scope and omits organization defaults for non-policy managers', () => {
    pageMocks.controller = controller({ canManagePolicies: false })

    renderPage()

    const header = document.body.querySelector('header')
    expect(header?.querySelector('h1')?.textContent).toBe('Navigation')
    expect(header?.querySelector('span')?.textContent).toBe('Personal')
    expect(document.body.textContent).toContain('Personal panel')
    expect(document.body.textContent).not.toContain('Role panel')
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

function controller(overrides: Partial<WorkspaceSettingsController>): WorkspaceSettingsController {
  return {
    workspace: {
      model: { warnings: [] },
      preferences: null,
      isResettingPreferences: false,
    },
    workspaceError: '',
    canManagePolicies: true,
    isRefreshing: false,
    personalMutationPending: false,
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
