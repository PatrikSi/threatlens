// @vitest-environment jsdom

import { act, type SVGProps } from 'react'
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
    const TestIcon = (props: SVGProps<SVGSVGElement>) => (
      <svg {...props} data-testid="settings-module-icon" />
    )
    const module = (
      id: string,
      route: string,
      label: string,
      parentId = 'primary.settings',
    ) => ({ id, route, label, parentId, icon: TestIcon })
    const account = module('settings.account', '/settings/account', 'Account')
    const tokens = module('settings.tokens', '/settings/tokens', 'API Tokens')
    const workspace = module('settings.workspace', '/settings/workspace', 'Workspace')
    const integrations = module('settings.integrations', '/settings/integrations', 'Integrations')
    const webhooks = module(
      'settings.integrations.webhooks',
      '/settings/integrations/webhooks',
      'Webhooks',
      'settings.integrations',
    )
    const smtp = module(
      'settings.integrations.smtp',
      '/settings/integrations/smtp',
      'SMTP',
      'settings.integrations',
    )
    const organizationModules = [
      module('settings.identity', '/settings/identity', 'Identity'),
      module('settings.access', '/settings/access', 'Access'),
      module('settings.users', '/settings/users', 'Users'),
      module('settings.audit', '/settings/audit-logs', 'Audit Logs'),
    ]
    const ai = module('settings.ai', '/settings/ai', 'AI')
    const tagging = module('settings.tagging', '/settings/tagging', 'Tagging')
    const operations = module('settings.operations', '/settings/operations', 'Operations')
    const children = settingsLayoutMocks.integrationChildrenVisible
      ? settingsLayoutMocks.currentUserError ? [webhooks] : [webhooks, smtp]
      : []
    const policyOrderedModules = settingsLayoutMocks.currentUserError
      ? [integrations]
      : [
          ...organizationModules,
          ai,
          tagging,
          integrations,
          operations,
        ]
    return {
      model: {
        settingsNavigation: [account, tokens, workspace, ...policyOrderedModules, ...children],
        // Deliberately different: the shell must use one stable order at every breakpoint.
        mobileSettingsNavigation: [workspace, tokens, account, ...policyOrderedModules, ...children],
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
  it('keeps the mobile navigation compact and names its current section', () => {
    const view = renderLayout('/settings/account')
    const menuButton = view.querySelector<HTMLButtonElement>(
      '[aria-controls="mobile-settings-navigation"]',
    )

    expect(menuButton).not.toBeNull()
    expect(menuButton?.textContent).toContain('My account')
    expect(menuButton?.getAttribute('aria-label')).toBe(
      'Open settings navigation. Current section: My account',
    )
    expect(view.querySelector('#mobile-settings-navigation')).toBeNull()

    act(() => {
      menuButton!.click()
    })

    expect(menuButton?.getAttribute('aria-expanded')).toBe('true')
    expect(menuButton?.getAttribute('aria-label')).toBe(
      'Close settings navigation. Current section: My account',
    )
    expect(view.querySelector('#mobile-settings-navigation nav')).not.toBeNull()
  })

  it('uses enterprise groups, compact icon-led rows, and presentation-only labels', () => {
    const view = renderLayout('/settings/account')
    const desktopNavigation = view.querySelector('#desktop-settings-navigation')

    expect(navigationGroupHeadings(desktopNavigation)).toEqual([
      'Personal',
      'Organization',
      'Automation',
      'System',
    ])
    expect(groupNavigationLabels(desktopNavigation, 'desktop', 'personal')).toEqual([
      'My account',
      'API tokens',
      'Navigation',
    ])
    expect(groupNavigationLabels(desktopNavigation, 'desktop', 'organization')).toEqual([
      'Single sign-on',
      'Access control',
      'Users',
      'Audit log',
    ])
    expect(groupNavigationLabels(desktopNavigation, 'desktop', 'automation')).toEqual([
      'AI automation',
      'Content tagging',
      'Integrations',
    ])
    expect(groupNavigationLabels(desktopNavigation, 'desktop', 'system')).toEqual([
      'System health',
    ])
    expect(desktopNavigation?.querySelectorAll('[data-testid="settings-module-icon"]')).toHaveLength(11)
    expect(desktopNavigation?.textContent).not.toContain('API Tokens')
    expect(desktopNavigation?.textContent).not.toContain('Audit Logs')
  })

  it('expands Integrations with an accessible control and friendly child names', () => {
    const view = renderLayout('/settings/account')
    const integrationsButton = view.querySelector<HTMLButtonElement>(
      '#desktop-settings-navigation [aria-label="Expand Integrations settings"]',
    )

    expect(integrationsButton).not.toBeNull()
    expect(integrationsButton?.getAttribute('aria-expanded')).toBe('false')
    expect(view.textContent).not.toContain('My webhooks')
    expect(view.textContent).not.toContain('Email delivery')

    act(() => {
      integrationsButton!.click()
    })

    expect(integrationsButton?.getAttribute('aria-expanded')).toBe('true')
    expect(integrationsButton?.getAttribute('aria-label')).toBe(
      'Collapse Integrations settings',
    )
    expect(view.textContent).toContain('My webhooks')
    expect(view.textContent).toContain('Email delivery')
  })

  it('keeps Integrations expanded while an integration route is active', () => {
    const view = renderLayout('/settings/integrations/smtp')
    const integrationsButton = view.querySelector<HTMLButtonElement>(
      '#desktop-settings-navigation [aria-label="Collapse Integrations settings"]',
    )

    expect(integrationsButton).not.toBeNull()
    expect(integrationsButton?.getAttribute('aria-expanded')).toBe('true')
    expect(view.textContent).toContain('My webhooks')
    expect(view.textContent).toContain('Email delivery')
    expect(view.textContent).toContain('SMTP body')
  })

  it('keeps the same grouped policy order on desktop and mobile', () => {
    const view = renderLayout('/settings/account')
    const desktopNavigation = view.querySelector('#desktop-settings-navigation')

    act(() => {
      view.querySelector<HTMLButtonElement>(
        '[aria-controls="mobile-settings-navigation"]',
      )?.click()
    })
    const mobileNavigation = view.querySelector('#mobile-settings-navigation nav')

    expect(navigationGroupHeadings(mobileNavigation)).toEqual(
      navigationGroupHeadings(desktopNavigation),
    )
    for (const group of ['personal', 'organization', 'automation', 'system']) {
      expect(groupNavigationLabels(mobileNavigation, 'mobile', group)).toEqual(
        groupNavigationLabels(desktopNavigation, 'desktop', group),
      )
    }
  })

  it('uses a sticky 248px desktop shell without a duplicate role card', () => {
    const view = renderLayout('/settings/account')
    const desktopSidebar = view.querySelector('#desktop-settings-navigation')?.closest('aside')
    const layout = desktopSidebar?.parentElement

    expect(layout?.className).toContain('lg:grid-cols-[248px_minmax(0,1fr)]')
    expect(desktopSidebar?.className).toContain('sticky')
    expect(desktopSidebar?.className).toContain('overflow-y-auto')
    expect(desktopSidebar?.textContent).toContain('Administrator')
    expect(desktopSidebar?.textContent).not.toContain('Current role')
    expect(desktopSidebar?.querySelector('.tl-surface-muted')).toBeNull()
  })

  it('omits empty settings groups and an Integrations container without children', () => {
    settingsLayoutMocks.integrationChildrenVisible = false
    settingsLayoutMocks.currentUserError = true
    const view = renderLayout('/settings/account')
    const desktopNavigation = view.querySelector('#desktop-settings-navigation')

    expect(navigationGroupHeadings(desktopNavigation)).toEqual(['Personal'])
    expect(desktopNavigation?.querySelector('[aria-label*="Integrations settings"]')).toBeNull()
  })

  it('fails closed and labels the role unavailable when identity refresh fails', () => {
    settingsLayoutMocks.currentUserError = true
    const view = renderLayout('/settings/account')

    expect(view.textContent).toContain('Unavailable')
    expect(view.textContent).not.toContain('Content tagging')
    expect(view.textContent).not.toContain('Single sign-on')
    expect(view.textContent).not.toContain('Users')
    expect(view.textContent).not.toContain('Audit log')
    expect(view.textContent).not.toContain('System health')
    expect(view.textContent).not.toContain('Email delivery')
  })
})

function navigationGroupHeadings(container: Element | null) {
  return Array.from(container?.querySelectorAll('h3') ?? []).map(
    (heading) => heading.textContent?.trim() ?? '',
  )
}

function groupNavigationLabels(
  container: Element | null,
  variant: 'desktop' | 'mobile',
  groupId: string,
) {
  const group = container
    ?.querySelector(`#${variant}-settings-group-${groupId}`)
    ?.closest('section')
  return Array.from(
    group?.querySelectorAll(':scope > div > a, :scope > div > div > button') ?? [],
  ).map((control) => control.textContent?.trim() ?? '')
}
