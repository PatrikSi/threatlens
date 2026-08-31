// @vitest-environment jsdom

import { StrictMode, act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const appWorkspaceMocks = vi.hoisted(() => ({
  landingPath: '/',
  settingsNavigation: [] as Array<{
    id: string
    label: string
    route: string
    parentId: string
    landingEligible: boolean
  }>,
}))

vi.mock('./components/WorkspaceProvider', () => ({
  WorkspaceProvider: ({ children }: { children: React.ReactNode }) => children,
}))

vi.mock('./workspace/useWorkspace', () => ({
  useWorkspace: () => ({
    isLoading: false,
    model: {
      landingPath: appWorkspaceMocks.landingPath,
      primaryNavigation: [
        { id: 'primary.dashboard', label: 'Dashboard', route: '/' },
        { id: 'primary.alerts', label: 'Alerts', route: '/alerts' },
        { id: 'primary.feeds', label: 'Feeds', route: '/feeds' },
        { id: 'primary.stats', label: 'Stats', route: '/stats' },
      ],
      mobileNavigation: [
        { id: 'primary.dashboard', label: 'Dashboard', route: '/' },
        { id: 'primary.alerts', label: 'Alerts', route: '/alerts' },
        { id: 'primary.feeds', label: 'Feeds', route: '/feeds' },
        { id: 'primary.stats', label: 'Stats', route: '/stats' },
      ],
      settingsNavigation: appWorkspaceMocks.settingsNavigation,
      mobileSettingsNavigation: appWorkspaceMocks.settingsNavigation,
    },
  }),
}))

vi.mock('./hooks/useCurrentUser', () => ({
  useCurrentUser: () => ({
    data: {
      id: 'user-1',
      email: 'analyst@example.com',
      role: 'analyst',
      is_active: true,
      is_approved: true,
      approved_at: '2026-04-20T10:00:00Z',
      created_at: '2026-04-19T10:00:00Z',
      features: {
        ai_enabled: false,
        ai_configured: false,
        ai_summary_enabled: false,
        ai_relevance_enabled: false,
        ai_daily_brief_enabled: false,
      },
      access: { permissions: ['*:*'] },
    },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
}))

vi.mock('./pages/AlertsPage', async () => {
  const React = await import('react')
  const { useUnsavedChangesWarning } = await import('./hooks/useUnsavedChangesWarning')

  function AlertsPage() {
    const [draftName, setDraftName] = React.useState('')
    const confirmDiscard = useUnsavedChangesWarning(draftName.trim().length > 0, 'Discard test changes?')

    return (
      <section>
        <h2>Alerts Test Page</h2>
        <label htmlFor="alerts-test-draft" className="text-sm font-semibold">
          Draft name
        </label>
        <input
          id="alerts-test-draft"
          value={draftName}
          onChange={(event) => setDraftName(event.target.value)}
          className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2"
        />
        {confirmDiscard.discardDialog}
      </section>
    )
  }

  return { AlertsPage }
})

vi.mock('./pages/StatsPage', () => ({
  StatsPage: () => <div>Stats Test Page</div>,
}))

vi.mock('./pages/DashboardPage', () => ({
  DashboardPage: () => <div>Dashboard Test Page</div>,
}))

vi.mock('./pages/FeedsPage', () => ({
  FeedsPage: () => {
    throw new Error('Lazy feed route exploded')
  },
}))

import App from './App'

let root: Root | null = null
let container: HTMLDivElement | null = null

function setInputValue(input: HTMLInputElement, value: string) {
  const descriptor = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')
  descriptor?.set?.call(input, value)
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

function flushPromises() {
  return new Promise((resolve) => window.setTimeout(resolve, 0))
}

async function renderApp(pathname: string, strict = false) {
  window.history.replaceState({}, '', pathname)
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => {
    root?.render(strict ? <StrictMode><App /></StrictMode> : <App />)
    await flushPromises()
    await flushPromises()
  })
  return container
}

async function waitForSelector<T extends Element>(selector: string) {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    const match = document.querySelector<T>(selector)
    if (match) {
      return match
    }
    await act(async () => {
      await flushPromises()
    })
  }

  return null
}

async function waitForText(text: string) {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    if ((document.body.textContent ?? '').includes(text)) {
      return true
    }
    await act(async () => {
      await flushPromises()
    })
  }

  return false
}

async function waitForPath(pathname: string) {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    if (window.location.pathname === pathname) {
      return true
    }
    await act(async () => {
      await flushPromises()
    })
  }

  return false
}

afterEach(async () => {
  await act(async () => {
    root?.unmount()
    await flushPromises()
  })
  root = null
  container?.remove()
  container = null
  document.body.innerHTML = ''
  window.history.replaceState({}, '', '/')
  window.sessionStorage.clear()
  appWorkspaceMocks.landingPath = '/'
  appWorkspaceMocks.settingsNavigation = []
})

describe('App router integration', () => {
  it('keeps the authenticated root as the dashboard regardless of the configured landing', async () => {
    appWorkspaceMocks.landingPath = '/stats'

    await renderApp('/')

    expect(await waitForText('Dashboard Test Page')).toBe(true)
    expect(window.location.pathname).toBe('/')
  })

  it('restores an explicit OIDC deep link before applying the workspace landing', async () => {
    appWorkspaceMocks.landingPath = '/stats'
    window.sessionStorage.setItem(
      'threatlens.auth.oidc-return.v1',
      JSON.stringify({ destination: '/alerts', createdAt: Date.now() }),
    )

    await renderApp('/start', true)

    expect(await waitForText('Alerts Test Page')).toBe(true)
    expect(window.location.pathname).toBe('/alerts')
    expect(window.sessionStorage.getItem('threatlens.auth.oidc-return.v1')).toBeNull()
  })

  it('redirects an Integrations container to its first visible child instead of Webhooks unconditionally', async () => {
    appWorkspaceMocks.settingsNavigation = [
      {
        id: 'settings.integrations',
        label: 'Integrations',
        route: '/settings/integrations',
        parentId: 'primary.settings',
        landingEligible: false,
      },
      {
        id: 'settings.integrations.smtp',
        label: 'SMTP',
        route: '/settings/integrations/smtp',
        parentId: 'settings.integrations',
        landingEligible: true,
      },
    ]

    await renderApp('/settings/integrations')
    expect(await waitForPath('/settings/integrations/smtp')).toBe(true)

    expect(window.location.pathname).not.toBe('/settings/integrations/webhooks')
  })

  it('leaves an empty Integrations container through a non-container Settings fallback', async () => {
    appWorkspaceMocks.settingsNavigation = [
      {
        id: 'settings.account',
        label: 'Account',
        route: '/settings/account',
        parentId: 'primary.settings',
        landingEligible: true,
      },
      {
        id: 'settings.integrations',
        label: 'Integrations',
        route: '/settings/integrations',
        parentId: 'primary.settings',
        landingEligible: false,
      },
    ]

    await renderApp('/settings/integrations')

    expect(await waitForPath('/settings/account')).toBe(true)
  })


  it('blocks in-app navigation when a dirty page uses useUnsavedChangesWarning', async () => {
    await renderApp('/alerts')
    const draftInput = await waitForSelector<HTMLInputElement>('#alerts-test-draft')
    expect(draftInput).not.toBeNull()

    act(() => {
      setInputValue(draftInput!, 'Unsaved draft')
    })

    const statsLink = Array.from(document.querySelectorAll('a')).find((link) => link.textContent?.trim() === 'Stats')
    expect(statsLink).not.toBeNull()

    await act(async () => {
      statsLink!.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }))
      await flushPromises()
    })

    expect(document.body.textContent ?? '').toContain('Discard unsaved changes?')
    expect(document.body.textContent ?? '').toContain('Discard test changes?')

    const discardButton = Array.from(document.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Discard changes'),
    )
    expect(discardButton).not.toBeNull()

    await act(async () => {
      discardButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      await flushPromises()
      await flushPromises()
    })

    expect(await waitForText('Stats Test Page')).toBe(true)
  })

  it('renders a route error boundary when a lazy page fails during render', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})

    try {
      await renderApp('/feeds')

      expect(await waitForText('Page failed to load')).toBe(true)
      expect(document.body.textContent ?? '').toContain('Lazy feed route exploded')
      expect(document.querySelector('[role="alert"]')).not.toBeNull()
    } finally {
      consoleError.mockRestore()
    }
  })
})
