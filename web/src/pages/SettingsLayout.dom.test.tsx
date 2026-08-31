// @vitest-environment jsdom

import { act } from 'react'
import { createRoot, Root } from 'react-dom/client'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const settingsLayoutMocks = vi.hoisted(() => ({
  currentUserError: false,
  integrationChildrenVisible: true,
}))

vi.mock('../hooks/useCurrentUser', () => ({
  useCurrentUser: () => ({
    data: {
      id: 'admin-1',
      email: 'admin@example.com',
      role: 'admin',
      is_active: true,
      is_approved: true,
      approved_at: '2026-04-21T10:00:00Z',
      created_at: '2026-04-20T10:00:00Z',
      features: {
        ai_enabled: true,
        ai_configured: true,
        ai_summary_enabled: true,
        ai_relevance_enabled: true,
        ai_daily_brief_enabled: true,
      },
    },
    isLoading: false,
    isError: settingsLayoutMocks.currentUserError,
    error: null,
  }),
}))

vi.mock('../workspace/useWorkspace', () => ({
  useWorkspace: () => {
    const account = { id: 'settings.account', route: '/settings/account', label: 'Account', parentId: 'primary.settings' }
    const tokens = { id: 'settings.tokens', route: '/settings/tokens', label: 'API Tokens', parentId: 'primary.settings' }
    const workspace = { id: 'settings.workspace', route: '/settings/workspace', label: 'Workspace', parentId: 'primary.settings' }
    const integrations = { id: 'settings.integrations', route: '/settings/integrations', label: 'Integrations', parentId: 'primary.settings' }
    const webhooks = { id: 'settings.integrations.webhooks', route: '/settings/integrations/webhooks', label: 'Webhooks', parentId: 'settings.integrations' }
    const smtp = { id: 'settings.integrations.smtp', route: '/settings/integrations/smtp', label: 'SMTP', parentId: 'settings.integrations' }
    const adminModules = [
      { id: 'settings.ai', route: '/settings/ai', label: 'AI', parentId: 'primary.settings' },
      { id: 'settings.tagging', route: '/settings/tagging', label: 'Tagging', parentId: 'primary.settings' },
      { id: 'settings.identity', route: '/settings/identity', label: 'Identity', parentId: 'primary.settings' },
      { id: 'settings.users', route: '/settings/users', label: 'Users', parentId: 'primary.settings' },
      { id: 'settings.audit', route: '/settings/audit-logs', label: 'Audit Logs', parentId: 'primary.settings' },
      { id: 'settings.operations', route: '/settings/operations', label: 'Operations', parentId: 'primary.settings' },
    ]
    const children = settingsLayoutMocks.integrationChildrenVisible
      ? settingsLayoutMocks.currentUserError ? [webhooks] : [webhooks, smtp]
      : []
    const common = settingsLayoutMocks.currentUserError ? [] : adminModules
    return {
      model: {
        settingsNavigation: [account, integrations, tokens, workspace, ...common, ...children],
        mobileSettingsNavigation: [workspace, tokens, integrations, account, ...common, ...children],
      },
    }
  },
}))

import { SettingsLayout } from './SettingsLayout'

let root: Root | null = null
let container: HTMLDivElement | null = null

function renderLayout(path: string) {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => {
    root?.render(
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/settings" element={<SettingsLayout />}>
            <Route path="account" element={<div>Account body</div>} />
            <Route path="integrations">
              <Route path="webhooks" element={<div>Webhooks body</div>} />
              <Route path="smtp" element={<div>SMTP body</div>} />
            </Route>
          </Route>
        </Routes>
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
  settingsLayoutMocks.currentUserError = false
  settingsLayoutMocks.integrationChildrenVisible = true
})

describe('SettingsLayout navigation', () => {
  it('keeps the mobile settings navigation compact until requested', () => {
    const view = renderLayout('/settings/account')
    const menuButton = view.querySelector<HTMLButtonElement>('[aria-controls="mobile-settings-navigation"]')

    expect(menuButton).not.toBeNull()
    expect(menuButton?.textContent).toContain('Account')
    expect(view.querySelector('#mobile-settings-navigation')).toBeNull()

    act(() => {
      menuButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    const mobileNavigation = view.querySelector('#mobile-settings-navigation nav')
    expect(mobileNavigation).not.toBeNull()
    expect(mobileNavigation?.className).toContain('divide-y')
    expect(mobileNavigation?.className).not.toContain('grid-cols-2')
  })

  it('expands the integrations section when clicked', () => {
    renderLayout('/settings/account')

    const integrationsButton = Array.from(document.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === 'Integrations',
    )
    expect(integrationsButton).not.toBeNull()
    expect(integrationsButton?.getAttribute('aria-expanded')).toBe('false')
    expect(document.body.textContent ?? '').not.toContain('Notifications')
    expect(document.body.textContent ?? '').not.toContain('Webhooks')
    expect(document.body.textContent ?? '').not.toContain('SMTP')

    act(() => {
      integrationsButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(integrationsButton?.getAttribute('aria-expanded')).toBe('true')
    expect(document.body.textContent ?? '').toContain('Webhooks')
    expect(document.body.textContent ?? '').toContain('SMTP')
  })

  it('keeps integrations expanded while an integration route is active', () => {
    renderLayout('/settings/integrations/smtp')

    const integrationsButton = Array.from(document.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === 'Integrations',
    )
    expect(integrationsButton).not.toBeNull()
    expect(integrationsButton?.getAttribute('aria-expanded')).toBe('true')
    expect(document.body.textContent ?? '').toContain('Webhooks')
    expect(document.body.textContent ?? '').toContain('SMTP')
    expect(document.body.textContent ?? '').toContain('SMTP body')
  })

  it('places Integrations by desktop order and honors a separate mobile priority order', () => {
    const view = renderLayout('/settings/account')
    const desktopItems = topLevelNavigationLabels(
      view.querySelector('#desktop-settings-navigation > div'),
    )
    expect(desktopItems.slice(0, 4)).toEqual(['Account', 'Integrations', 'API Tokens', 'Workspace'])

    act(() => {
      view.querySelector<HTMLButtonElement>('[aria-controls="mobile-settings-navigation"]')?.click()
    })
    const mobileItems = topLevelNavigationLabels(
      view.querySelector('#mobile-settings-navigation nav'),
    )
    expect(mobileItems.slice(0, 4)).toEqual(['Workspace', 'API Tokens', 'Integrations', 'Account'])
  })

  it('does not render an empty Integrations container when no child route is available', () => {
    settingsLayoutMocks.integrationChildrenVisible = false
    const view = renderLayout('/settings/account')

    expect(
      Array.from(view.querySelectorAll('button')).some((button) => button.textContent?.trim() === 'Integrations'),
    ).toBe(false)
  })

  it('fails closed and labels the role unavailable when identity refresh fails', () => {
    settingsLayoutMocks.currentUserError = true
    const view = renderLayout('/settings/account')

    expect(view.textContent).toContain('Current role')
    expect(view.textContent).toContain('unavailable')
    expect(view.textContent).not.toContain('Tagging')
    expect(view.textContent).not.toContain('Identity')
    expect(view.textContent).not.toContain('Users')
    expect(view.textContent).not.toContain('Audit Logs')
    expect(view.textContent).not.toContain('Operations')
    expect(view.textContent).not.toContain('SMTP')
  })
})

function topLevelNavigationLabels(container: Element | null) {
  return Array.from(container?.children ?? []).map((element) => {
    const control = element.matches('a, button')
      ? element
      : element.querySelector(':scope > a, :scope > button')
    return control?.textContent?.trim() ?? ''
  })
}
