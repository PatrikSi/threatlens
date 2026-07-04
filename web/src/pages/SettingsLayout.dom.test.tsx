// @vitest-environment jsdom

import { act } from 'react'
import { createRoot, Root } from 'react-dom/client'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

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
    isError: false,
    error: null,
  }),
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
})

describe('SettingsLayout navigation', () => {
  it('expands the integrations section when clicked', () => {
    renderLayout('/settings/account')

    const integrationsButton = Array.from(document.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Integrations'),
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

    const integrationsButton = Array.from(document.querySelectorAll('button')).find((button) =>
      button.textContent?.includes('Integrations'),
    )
    expect(integrationsButton).not.toBeNull()
    expect(integrationsButton?.getAttribute('aria-expanded')).toBe('true')
    expect(document.body.textContent ?? '').toContain('Webhooks')
    expect(document.body.textContent ?? '').toContain('SMTP')
    expect(document.body.textContent ?? '').toContain('SMTP body')
  })
})
