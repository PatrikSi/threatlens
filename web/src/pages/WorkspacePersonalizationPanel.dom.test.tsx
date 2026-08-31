// @vitest-environment jsdom

import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { WorkspaceEffectiveResponse, WorkspaceUserPreferenceResponse } from '../types/workspace'
import { TRUSTED_DASHBOARD_PANELS, TRUSTED_WORKSPACE_MODULES } from '../workspace/moduleRegistry'
import type { WorkspaceSettingsController } from './useWorkspaceSettingsController'
import { WorkspacePersonalizationPanel } from './WorkspacePersonalizationPanel'
import { createPersonalWorkspaceDraft } from './workspaceSettingsModel'

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

describe('WorkspacePersonalizationPanel', () => {
  it('disables every editable control while a personal mutation is pending', () => {
    const effective = effectiveWorkspace()
    const userPreferences = preferences()
    const controller = {
      workspace: {
        effective,
        preferences: userPreferences,
        error: null,
        isSavingPreferences: true,
        isResettingPreferences: false,
        userContext: {
          role: 'admin',
          permissions: ['*:*'],
          features: {
            ai_enabled: true,
            ai_configured: true,
            ai_summary_enabled: true,
            ai_relevance_enabled: true,
            ai_daily_brief_enabled: true,
          },
          accountEligible: true,
        },
      },
      personalDraft: createPersonalWorkspaceDraft(effective, userPreferences),
      personalDirty: true,
      personalError: 'A stale revision was rejected.',
      personalFeedback: '',
      personalMutationPending: true,
      personalRevisionConflict: true,
      discardAndReloadPersonal: vi.fn(),
      resetPersonal: vi.fn(),
      savePersonal: vi.fn(),
      setPersonalDraft: vi.fn(),
      setResetPersonalRequested: vi.fn(),
    } as unknown as WorkspaceSettingsController

    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    act(() => root?.render(<WorkspacePersonalizationPanel controller={controller} />))

    const controls = [...container.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLButtonElement>(
      'input, select, button',
    )]
    expect(controls.length).toBeGreaterThan(5)
    expect(controls.every((control) => control.disabled)).toBe(true)
    expect(container.textContent).toContain('Discard personal changes and reload')
  })

  it('preserves an unknown future landing when an unrelated trusted module is toggled', () => {
    const effective = effectiveWorkspace()
    const userPreferences = { ...preferences(), landing_module_id: 'future.timeline' }
    const draft = createPersonalWorkspaceDraft(effective, userPreferences)
    const setPersonalDraft = vi.fn()
    const controller = personalController(effective, userPreferences, draft, setPersonalDraft)

    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    act(() => root?.render(<WorkspacePersonalizationPanel controller={controller} />))

    const toggle = container.querySelector<HTMLInputElement>('input[type="checkbox"]')!
    act(() => toggle.dispatchEvent(new MouseEvent('click', { bubbles: true })))
    const update = setPersonalDraft.mock.calls[0][0] as (current: typeof draft) => typeof draft

    expect(update(draft).landingModuleId).toBe('future.timeline')
    expect(container.textContent).toContain('Unavailable in this version (kept)')
    expect(container.textContent).toContain('My account')
    expect(container.textContent).toContain('API tokens')
    expect(container.textContent).toContain('Access control')
    expect(container.textContent).not.toContain('settings.account')
    expect(container.textContent).not.toContain('settings.workspace')
  })

  it('supports pointer drag reordering with named keyboard and touch fallbacks', () => {
    const effective = effectiveWorkspace()
    const userPreferences = preferences()
    const draft = createPersonalWorkspaceDraft(effective, userPreferences)
    const setPersonalDraft = vi.fn()
    const controller = personalController(effective, userPreferences, draft, setPersonalDraft)

    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    act(() => root?.render(<WorkspacePersonalizationPanel controller={controller} />))

    const handle = container.querySelector<HTMLButtonElement>('[aria-label^="Drag Feeds."]')
    const target = container.querySelector<HTMLElement>(
      '[data-navigation-reorder-item="primary.alerts"]',
    )
    const invalidTarget = container.querySelector<HTMLElement>(
      '[data-navigation-reorder-item="settings.tokens"]',
    )
    const earlierButton = container.querySelector<HTMLButtonElement>(
      '[aria-label="Move Feeds earlier"]',
    )

    expect(handle).not.toBeNull()
    expect(handle?.draggable).toBe(true)
    expect(handle?.getAttribute('aria-describedby')).toBe(
      'personal-navigation-reorder-instructions',
    )
    expect(container.textContent).toContain(
      'Use the earlier and later buttons for keyboard or touch.',
    )
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

    const dropUpdate = setPersonalDraft.mock.calls[0]?.[0] as
      | ((current: typeof draft) => typeof draft)
      | undefined
    expect(dropUpdate).toBeTypeOf('function')
    const droppedDraft = dropUpdate!(draft)
    expect(droppedDraft.modules.get('primary.feeds')?.order).toBeLessThan(
      droppedDraft.modules.get('primary.alerts')?.order ?? Number.MAX_SAFE_INTEGER,
    )
    expect(container.querySelector('[role="status"]')?.textContent).toContain(
      'Moved Feeds to position 1',
    )

    act(() => earlierButton!.click())
    expect(setPersonalDraft).toHaveBeenCalledTimes(2)
    expect(container.querySelector('[role="status"]')?.textContent).toContain(
      'Save navigation preferences to apply this order.',
    )
    expect(target?.className).toContain('py-2')
  })

  it('does not allow the final first-use dashboard panel to be removed', () => {
    const effective = effectiveWorkspace()
    const userPreferences = preferences()
    const draft = createPersonalWorkspaceDraft(effective, userPreferences)
    const controller = personalController(effective, userPreferences, draft, vi.fn())

    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    act(() => root?.render(<WorkspacePersonalizationPanel controller={controller} />))

    const rssLabel = [...container.querySelectorAll('label')].find((label) =>
      label.textContent?.includes('RSS intelligence'),
    )
    expect(rssLabel?.querySelector<HTMLInputElement>('input')?.disabled).toBe(true)
    expect(container.textContent).toContain('Initial dashboard panels')
    expect(container.textContent).toContain('Start page')
    expect(container.textContent).toContain('Existing saved layouts are not replaced')
  })
})

function personalController(
  effective: WorkspaceEffectiveResponse,
  userPreferences: WorkspaceUserPreferenceResponse,
  personalDraft: ReturnType<typeof createPersonalWorkspaceDraft>,
  setPersonalDraft: ReturnType<typeof vi.fn>,
): WorkspaceSettingsController {
  return {
    workspace: {
      effective,
      preferences: userPreferences,
      error: null,
      isSavingPreferences: false,
      isResettingPreferences: false,
      userContext: {
        role: 'admin',
        permissions: ['*:*'],
        features: {
          ai_enabled: true,
          ai_configured: true,
          ai_summary_enabled: true,
          ai_relevance_enabled: true,
          ai_daily_brief_enabled: true,
        },
        accountEligible: true,
      },
    },
    personalDraft,
    personalDirty: true,
    personalError: '',
    personalFeedback: '',
    personalMutationPending: false,
    personalRevisionConflict: false,
    discardAndReloadPersonal: vi.fn(),
    resetPersonal: vi.fn(),
    savePersonal: vi.fn(),
    setPersonalDraft,
    setResetPersonalRequested: vi.fn(),
  } as unknown as WorkspaceSettingsController
}

function preferences(): WorkspaceUserPreferenceResponse {
  return {
    user_id: 'user-1',
    role: 'admin',
    landing_module_id: null,
    modules: [],
    dashboard_panel_ids: ['rss'],
    revision: 2,
    updated_by_user_id: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    unknown_module_ids: [],
    unknown_dashboard_panel_ids: [],
    warnings: [],
  }
}

function effectiveWorkspace(): WorkspaceEffectiveResponse {
  return {
    role: 'admin',
    policy_revision: 1,
    preference_revision: 2,
    landing_module_id: 'primary.dashboard',
    dashboard_panel_ids: ['rss'],
    dashboard_panels: TRUSTED_DASHBOARD_PANELS.map((panel) => ({
      id: panel.id,
      visible: true,
      permission_allowed: true,
      feature_available: true,
      missing_permissions: [],
      reasons: [],
    })),
    modules: TRUSTED_WORKSPACE_MODULES.filter((module) => module.policyManaged).map((module) => ({
      id: module.id,
      label: module.label,
      route: module.route,
      section: module.section,
      parent_id: module.parentId,
      visible: true,
      optional: module.defaultOptional,
      order: module.defaultOrder,
      mobile_priority: module.defaultMobilePriority,
      mobile_behavior: module.mobileBehavior,
      permission_allowed: true,
      missing_permissions: [],
      feature_available: true,
      policy_visible: true,
      preference_visible: true,
      reasons: [],
    })),
    warnings: [],
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
