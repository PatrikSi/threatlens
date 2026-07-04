// @vitest-environment jsdom

import { act } from 'react'
import { createRoot, Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const integrationsPageDomMocks = vi.hoisted(() => ({
  queryClient: {
    invalidateQueries: vi.fn(),
    setQueryData: vi.fn(),
  },
  saveMutate: vi.fn(),
  testMutate: vi.fn(),
  smtpSettings: {
    id: 'smtp-1',
    name: 'SMTP',
    integration_type: 'smtp',
    direction: 'destination',
    enabled: true,
    configured: true,
    schema_version: 1,
    host: 'smtp.example.com',
    port: 587,
    security: 'starttls',
    username: 'relay-user',
    password_configured: true,
    has_unreadable_secret: false,
    from_email: 'threatlens@example.com',
    from_name: 'ThreatLens',
    timeout_seconds: 10,
    health_status: 'healthy',
    last_test_at: '2026-07-04T10:00:00Z',
    last_success_at: '2026-07-04T10:00:00Z',
    last_error_at: null,
    last_error: null,
    last_test_duration_ms: 42,
    created_at: '2026-07-04T09:00:00Z',
    updated_at: '2026-07-04T09:30:00Z',
  },
}))

const routerMocks = vi.hoisted(() => ({
  useBlocker: vi.fn(() => ({
    state: 'unblocked' as const,
    proceed: vi.fn(),
    reset: vi.fn(),
  })),
}))

function mutationResult(mutate: ReturnType<typeof vi.fn>) {
  return {
    mutate,
    isPending: false,
    isError: false,
    error: null,
  }
}

vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => integrationsPageDomMocks.queryClient,
  useQuery: ({ queryKey }: { queryKey: unknown[] }) => {
    const key = queryKey.join(':')
    const baseResult = {
      isLoading: false,
      isError: false,
      error: null,
    }
    if (key === 'integrations:connectors') {
      return {
        ...baseResult,
        data: [
          {
            integration_type: 'smtp',
            direction: 'destination',
            display_name: 'SMTP',
            description: 'Send operational emails through an SMTP server.',
            config_schema_version: 1,
            supports_test: true,
            capabilities: ['destination', 'email'],
          },
        ],
      }
    }
    if (key === 'integrations:smtp:settings') {
      return {
        ...baseResult,
        data: integrationsPageDomMocks.smtpSettings,
      }
    }
    return { ...baseResult, data: undefined }
  },
  useMutation: (options: { mutationKey?: unknown }) => {
    const mutationKey = Array.isArray(options?.mutationKey) ? options.mutationKey.join(':') : String(options?.mutationKey ?? '')
    if (mutationKey === 'integrations:smtp:test') {
      return mutationResult(integrationsPageDomMocks.testMutate)
    }
    return mutationResult(integrationsPageDomMocks.saveMutate)
  },
}))

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return {
    ...actual,
    useBlocker: routerMocks.useBlocker,
  }
})

import { IntegrationsSettingsPage } from './IntegrationsSettingsPage'

let root: Root | null = null
let container: HTMLDivElement | null = null

function renderPage() {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => {
    root?.render(<IntegrationsSettingsPage />)
  })
  return container
}

function setInputValue(input: HTMLInputElement, value: string) {
  const descriptor = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')
  descriptor?.set?.call(input, value)
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

function getButton(text: string) {
  return Array.from(document.querySelectorAll('button')).find((button) => button.textContent?.includes(text)) ?? null
}

afterEach(() => {
  act(() => {
    root?.unmount()
  })
  root = null
  container?.remove()
  container = null
  document.body.innerHTML = ''
  integrationsPageDomMocks.queryClient.invalidateQueries.mockReset()
  integrationsPageDomMocks.queryClient.setQueryData.mockReset()
  integrationsPageDomMocks.saveMutate.mockReset()
  integrationsPageDomMocks.testMutate.mockReset()
  routerMocks.useBlocker.mockReset()
  routerMocks.useBlocker.mockImplementation(() => ({
    state: 'unblocked' as const,
    proceed: vi.fn(),
    reset: vi.fn(),
  }))
})

describe('IntegrationsSettingsPage DOM workflows', () => {
  it('renders SMTP settings without exposing the saved password', () => {
    const view = renderPage()

    expect(view.textContent).toContain('Integrations')
    expect(view.textContent).toContain('SMTP')
    expect(view.textContent).toContain('Healthy')
    const passwordInput = view.querySelector<HTMLInputElement>('#smtp-password')
    expect(passwordInput).not.toBeNull()
    expect(passwordInput?.value).toBe('')
    expect(passwordInput?.placeholder).toBe('Saved password configured')
  })

  it('tests current unsaved form values and saves typed password replacements', () => {
    const view = renderPage()

    act(() => {
      setInputValue(view.querySelector<HTMLInputElement>('#smtp-host')!, 'draft.example.com')
      setInputValue(view.querySelector<HTMLInputElement>('#smtp-test-recipient')!, 'analyst@example.com')
    })

    act(() => {
      getButton('Test SMTP')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(integrationsPageDomMocks.testMutate).toHaveBeenCalledWith(
      expect.objectContaining({
        recipient_email: 'analyst@example.com',
        settings: expect.objectContaining({
          host: 'draft.example.com',
        }),
      }),
    )

    act(() => {
      setInputValue(view.querySelector<HTMLInputElement>('#smtp-password')!, 'new-secret')
    })
    act(() => {
      getButton('Save SMTP')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(integrationsPageDomMocks.saveMutate).toHaveBeenCalledWith(
      expect.objectContaining({
        host: 'draft.example.com',
        password: 'new-secret',
      }),
    )
  })
})
