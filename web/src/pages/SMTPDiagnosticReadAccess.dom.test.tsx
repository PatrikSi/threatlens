// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import type { SMTPTestResponse } from '../types/api'
import { smtpHook, smtpAnalytics } from '../testing/notificationReadFixtures'
import { SMTPIntegrationSettingsPage } from './IntegrationsSettingsPage'
import { intelButton, mountIntel } from './articleIntelligenceTestSupport'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...await original<typeof import('../api/client')>(), apiFetch: vi.fn() }))
vi.mock('../hooks/useCurrentUser', () => ({ useCurrentUser: () => ({
  data: { id: 'operator', role: 'admin', access: { permissions: ['read:integrations', 'write:integrations'] }, features: {} },
  isLoading: false, isError: false, error: null,
}) }))
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
afterEach(() => { view?.close(); view = undefined; vi.resetAllMocks() })

it('retires a saved SMTP test diagnostic copied into a notice after the destination read is denied', async () => {
  let status = 200
  const diagnostic = 'Private SMTP authentication diagnostic for synthetic relay'
  const result: SMTPTestResponse = { success: false, action: 'connection', duration_ms: 10, recipient_email: null,
    error_code: 'authentication_failed', error: diagnostic, server_message: 'Private SMTP server greeting',
    tested_at: '2026-11-15T10:00:00Z', used_unsaved_settings: false }
  vi.mocked(apiFetch).mockImplementation((path, init) => {
    if (init?.method === 'POST') return Promise.resolve(result)
    if (path === '/integrations/smtp/hooks') return status === 200 ? Promise.resolve([smtpHook]) : Promise.reject(new ApiError(`Saved SMTP read ${status}`, status, path))
    if (path === '/integrations/smtp/analytics') return Promise.resolve(smtpAnalytics)
    if (path.includes('/deliveries?')) return Promise.resolve({ deliveries: [], total: 0, page: 1, page_size: 10 })
    if (path.includes('/test-runs?')) return Promise.resolve({ runs: [], total: 0, page: 1, page_size: 10 })
    return Promise.resolve([])
  })
  view = await mountIntel(<SMTPIntegrationSettingsPage />)
  await vi.waitFor(() => expect(view!.host.querySelector<HTMLInputElement>('#smtp-hook-name')?.value).toBe(smtpHook.name))
  act(() => intelButton(view!.host, 'Test connection').click())
  await vi.waitFor(() => expect(view!.host.textContent).toContain(diagnostic))
  for (const next of [403, 503]) {
    status = next
    await act(async () => { await view!.client.invalidateQueries({ queryKey: ['integrations', 'smtp', 'hooks'], exact: true }) })
    await vi.waitFor(() => expect(view!.host.textContent).toContain(`Saved SMTP read ${next}`))
    expect(view!.host.textContent).not.toContain(diagnostic)
    expect(view!.host.textContent).not.toContain('Private SMTP server greeting')
  }
  expect(vi.mocked(apiFetch).mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
})
