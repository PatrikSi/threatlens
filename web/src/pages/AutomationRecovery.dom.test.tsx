// @vitest-environment jsdom
import { act, useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import { AuthProvider } from '../components/AuthContext'
import type {
  WebhookConditionGroup,
  WebhookCredentialProfile,
} from '../types/webhookAutomation'
import { ArticleIndicatorsPanel } from './ArticleIndicatorsPanel'
import { WebhookConditionBuilder } from './WebhookConditionBuilder'
import { WebhookCredentialProfiles } from './WebhookCredentialProfiles'
import { newCondition } from './webhookConditionModel'
import {
  automationField,
  editAutomation,
  indicatorFixture,
  indicatorPageFixture,
} from './indicatorAutomationTestSupport'
import {
  intelButton,
  mountIntel,
  settle,
} from './articleIntelligenceTestSupport'
;(
  globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({
  ...(await original<object>()),
  apiFetch: vi.fn(),
}))
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
afterEach(() => {
  view?.close()
  view = undefined
  vi.clearAllMocks()
  localStorage.clear()
  sessionStorage.clear()
})

function Conditions({ initial }: { initial: WebhookConditionGroup }) {
  const [value, setValue] = useState<WebhookConditionGroup | null>(initial)
  return (
    <WebhookConditionBuilder
      value={value}
      onChange={setValue}
      disabled={false}
    />
  )
}

describe('condition editor bounds and keyboard recovery', () => {
  it('prevents NOT wrapping that would push a nested leaf beyond four levels', async () => {
    const initial: WebhookConditionGroup = {
      op: 'all',
      conditions: [
        {
          op: 'all',
          conditions: [{ op: 'all', conditions: [newCondition()] }],
        },
        newCondition(),
      ],
    }
    view = await mountIntel(<Conditions initial={initial} />)
    const match = automationField(view.host, 'Match') as HTMLSelectElement
    expect(
      match.querySelector<HTMLOptionElement>('option[value="not"]')?.disabled,
    ).toBe(true)
    // The handler also guards synthetic/programmatic selection of a disabled option.
    editAutomation(view.host, 'Match', 'not')
    expect(match.value).toBe('all')
    expect(view.host.querySelector('[role="alert"]')).toBeNull()
  })

  it('allows NOT at the node limit when switching does not add another wrapper', async () => {
    const initial: WebhookConditionGroup = {
      op: 'all',
      conditions: [
        { op: 'all', conditions: Array.from({ length: 30 }, newCondition) },
      ],
    }
    view = await mountIntel(<Conditions initial={initial} />)
    const match = automationField(view.host, 'Match') as HTMLSelectElement
    expect(
      match.querySelector<HTMLOptionElement>('option[value="not"]')?.disabled,
    ).toBe(false)
    editAutomation(view.host, 'Match', 'not')
    expect(match.value).toBe('not')
    expect(view.host.querySelector('[role="alert"]')).toBeNull()
  })

  it('keeps a cleared threshold empty and invalid instead of silently accepting zero', async () => {
    view = await mountIntel(
      <Conditions
        initial={{
          op: 'all',
          conditions: [
            { field: 'extraction_confidence', operator: 'gte', value: 0.8 },
          ],
        }}
      />,
    )
    editAutomation(view.host, 'Threshold', '')
    expect(automationField(view.host, 'Threshold').value).toBe('')
    expect(view.host.querySelector('[role="alert"]')?.textContent).toContain(
      'Enter a non-negative numeric condition.',
    )
    editAutomation(view.host, 'Threshold', '0.9')
    expect(automationField(view.host, 'Threshold').value).toBe('0.9')
    expect(view.host.querySelector('[role="alert"]')).toBeNull()
  })

  it('returns keyboard focus to the group selector after removing a condition', async () => {
    view = await mountIntel(
      <Conditions
        initial={{ op: 'all', conditions: [newCondition(), newCondition()] }}
      />,
    )
    const remove = view.host.querySelector<HTMLButtonElement>(
      '[aria-label="Remove condition 2"]',
    )!
    act(() => {
      remove.focus()
      remove.click()
    })
    await settle()
    await settle()
    expect(document.activeElement).toBe(automationField(view.host, 'Match'))
    expect(
      view.host.querySelectorAll('[aria-label^="Remove condition"]'),
    ).toHaveLength(1)
  })
})

describe('credential creation and authorization recovery', () => {
  const saved: WebhookCredentialProfile = {
    id: 'created-profile',
    name: 'New SIEM profile',
    enabled: true,
    auth_type: 'bearer',
    header_name: null,
    auth_configured: true,
    signing_configured: false,
    revision: 1,
  }

  it('announces successful creation after remounting the selected profile and clears the secret', async () => {
    let profiles: WebhookCredentialProfile[] = []
    vi.mocked(apiFetch).mockImplementation((_, options) => {
      if (options?.method === 'POST') {
        profiles = [saved]
        return Promise.resolve(saved) as ReturnType<typeof apiFetch>
      }
      return Promise.resolve(profiles) as ReturnType<typeof apiFetch>
    })
    const close = vi.fn()
    const dirty = vi.fn()
    view = await mountIntel(
      <WebhookCredentialProfiles
        writable
        onClose={close}
        onDirtyChange={dirty}
      />,
    )
    editAutomation(document, 'Profile name', saved.name)
    editAutomation(document, 'Authentication', 'bearer')
    editAutomation(document, 'Authentication secret', 'synthetic-secret')
    act(() => intelButton(document, 'Save credential profile').click())
    await settle()
    expect(automationField(document, 'Saved profile').value).toBe(saved.id)
    expect(
      automationField(document, 'Replacement authentication secret').value,
    ).toBe('')
    expect(
      [...document.querySelectorAll('[role="status"]')].some((entry) =>
        entry.textContent?.includes(
          'Credential profile saved. Secrets have been cleared from this form.',
        ),
      ),
    ).toBe(true)
    expect(dirty).toHaveBeenLastCalledWith(false)
    act(() => intelButton(document, 'Close profiles').click())
    expect(close).toHaveBeenCalledOnce()
    expect(document.querySelector('[role="alertdialog"]')).toBeNull()
  })

  it('removes secret drafts and phantom dirty protection when profile read access is denied', async () => {
    let denied = false
    vi.mocked(apiFetch).mockImplementation((url) =>
      denied
        ? Promise.reject(new ApiError('Profile access removed', 403, url))
        : Promise.resolve([]),
    )
    const close = vi.fn()
    const dirty = vi.fn()
    view = await mountIntel(
      <WebhookCredentialProfiles
        writable
        onClose={close}
        onDirtyChange={dirty}
      />,
    )
    editAutomation(document, 'Profile name', 'Unsaved profile')
    editAutomation(document, 'Authentication', 'bearer')
    editAutomation(document, 'Authentication secret', 'private-draft-secret')
    await settle()
    expect(dirty).toHaveBeenLastCalledWith(true)
    denied = true
    await act(async () => {
      await view!.client.invalidateQueries({
        queryKey: ['notifications', 'credential-profiles'],
      })
    })
    await settle()
    expect(document.querySelector('input[type="password"]')).toBeNull()
    expect(document.body.textContent).toContain('Profile access removed')
    expect(dirty).toHaveBeenLastCalledWith(false)
    act(() => intelButton(document, 'Close profiles').click())
    expect(close).toHaveBeenCalledOnce()
    expect(document.querySelector('[role="alertdialog"]')).toBeNull()
  })

  it('preserves a dirty credential draft during a transient read failure', async () => {
    let unavailable = false
    vi.mocked(apiFetch).mockImplementation((url) =>
      unavailable
        ? Promise.reject(
            new ApiError('Profiles temporarily unavailable', 503, url),
          )
        : Promise.resolve([]),
    )
    const close = vi.fn()
    view = await mountIntel(
      <WebhookCredentialProfiles
        writable
        onClose={close}
        onDirtyChange={vi.fn()}
      />,
    )
    editAutomation(document, 'Profile name', 'Keep my draft')
    editAutomation(document, 'Authentication', 'bearer')
    editAutomation(
      document,
      'Authentication secret',
      'synthetic-unsaved-secret',
    )
    unavailable = true
    await act(async () => {
      await view!.client.invalidateQueries({
        queryKey: ['notifications', 'credential-profiles'],
      })
    })
    await settle()
    expect(automationField(document, 'Profile name').value).toBe(
      'Keep my draft',
    )
    expect(automationField(document, 'Authentication secret').value).toBe(
      'synthetic-unsaved-secret',
    )
    expect(
      automationField(document, 'Authentication secret').matches(':disabled'),
    ).toBe(true)
    act(() => intelButton(document, 'Close profiles').click())
    await settle()
    expect(
      document.querySelector('[role="alertdialog"]')?.textContent,
    ).toContain('Discard credential changes?')
    expect(close).not.toHaveBeenCalled()
  })
})

describe('indicator page failure recovery', () => {
  it('drops cached team dispositions when current permissions lose team access', async () => {
    let permissions = ['read:items', 'read:teams']
    vi.mocked(apiFetch).mockImplementation((url) => {
      if (url === '/auth/me')
        return Promise.resolve({
          id: 'team-recovery-user',
          access: { permissions },
        }) as ReturnType<typeof apiFetch>
      if (url.startsWith('/teams?'))
        return Promise.resolve({
          items: [{ id: 'team-1', name: 'Review team' }],
          total: 1,
          page: 1,
          page_size: 50,
        }) as ReturnType<typeof apiFetch>
      return Promise.resolve({
        ...indicatorPageFixture,
        items: [
          { ...indicatorFixture, suppressed: url.includes('team_id=team-1') },
        ],
      }) as ReturnType<typeof apiFetch>
    })
    view = await mountIntel(
      <AuthProvider>
        <ArticleIndicatorsPanel itemId="item-1" />
      </AuthProvider>,
    )
    const details = view.host.querySelector('details')!
    act(() => {
      details.open = true
      details.dispatchEvent(new Event('toggle'))
    })
    await settle()
    editAutomation(view.host, 'Indicator assessment team', 'team-1')
    await settle()
    expect(view.host.textContent).toContain('Suppressed for the selected team.')
    expect(intelButton(view.host, 'Review indicator')).toBeDefined()
    permissions = ['read:items']
    await act(async () => {
      await view!.client.invalidateQueries({ queryKey: ['auth', 'me'] })
    })
    await settle()
    expect(view.host.textContent).not.toContain(
      'Suppressed for the selected team.',
    )
    expect(view.host.textContent).not.toContain('Review indicator')
    expect(view.host.textContent).not.toContain('Indicator assessment team')
    expect(view.host.textContent).toContain(indicatorFixture.value)
  })

  it('offers a previous page after a failed later page and can retry that later page', async () => {
    let secondPageAvailable = false
    vi.mocked(apiFetch).mockImplementation((url) => {
      if (url === '/auth/me')
        return Promise.resolve({
          id: 'recovery-user',
          access: { permissions: ['read:items'] },
        }) as ReturnType<typeof apiFetch>
      if (url.includes('page=2'))
        return secondPageAvailable
          ? (Promise.resolve({
              ...indicatorPageFixture,
              page: 2,
              total: 21,
              items: [
                {
                  ...indicatorFixture,
                  id: 'last-indicator',
                  value: 'last-page.example',
                },
              ],
            }) as ReturnType<typeof apiFetch>)
          : Promise.reject(
              new ApiError('Temporary second-page failure', 503, url),
            )
      return Promise.resolve({
        ...indicatorPageFixture,
        page: 1,
        total: 21,
        items: [{ ...indicatorFixture, value: 'first-page.example' }],
      }) as ReturnType<typeof apiFetch>
    })
    view = await mountIntel(
      <AuthProvider>
        <ArticleIndicatorsPanel itemId="item-1" />
      </AuthProvider>,
    )
    const details = view.host.querySelector('details')!
    act(() => {
      details.open = true
      details.dispatchEvent(new Event('toggle'))
    })
    await settle()
    act(() => intelButton(view!.host, 'Next indicators').click())
    await settle()
    expect(view.host.textContent).toContain('Temporary second-page failure')
    expect(intelButton(view.host, 'Previous indicator page').disabled).toBe(
      false,
    )
    act(() => intelButton(view!.host, 'Previous indicator page').click())
    await settle()
    expect(view.host.textContent).toContain('first-page.example')
    act(() => intelButton(view!.host, 'Next indicators').click())
    await settle()
    secondPageAvailable = true
    act(() => intelButton(view!.host, 'Retry indicators').click())
    await settle()
    expect(view.host.textContent).toContain('last-page.example')
    expect(view.host.textContent).toContain('Page 2 of 2')
  })
})
