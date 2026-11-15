// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import { AuditLogsPage } from './AuditLogsPage'
import { deferred, intelButton, mountIntel, settle } from './articleIntelligenceTestSupport'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...await original<object>(), apiFetch: vi.fn() }))

let view: Awaited<ReturnType<typeof mountIntel>> | undefined
afterEach(() => { view?.close(); view = undefined; vi.clearAllMocks() })
const record = {
  id: 'audit-private', action: 'users.update', resource_type: 'user', resource_id: 'private-user',
  resource_label_snapshot: 'Private controlled account', actor_user_id: 'actor',
  actor_label_snapshot: 'Private audit actor', success: true,
  metadata_json: { reason: 'Private audit reason' }, created_at: '2026-11-01T00:00:00Z',
}
const data = { logs: [record], total: 1, page: 1, page_size: 50 }
function changeFilter(value: string) {
  const input = view!.host.querySelector<HTMLInputElement>('#audit-log-action-filter')!
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

describe('audit log read admission', () => {
  it('keeps denied audit identity and resource metadata private through a later outage', async () => {
    let status = 200
    const rejected: number[] = []
    vi.mocked(apiFetch).mockImplementation((path) => {
      if (status === 200) return Promise.resolve(data) as never
      rejected.push(status)
      return Promise.reject(new ApiError('Audit read unavailable', status, path))
    })
    view = await mountIntel(<AuditLogsPage />, '/')
    await settle()
    expect(view.host.textContent).toContain(record.actor_label_snapshot)
    expect(view.host.textContent).toContain(record.resource_label_snapshot)
    for (const next of [403, 503]) {
      status = next
      await act(async () => { await view!.client.invalidateQueries() })
      await settle()
      expect(rejected).toContain(next)
      expect(view.host.textContent).not.toContain(record.actor_label_snapshot)
      expect(view.host.textContent).not.toContain(record.resource_label_snapshot)
    }
    status = 200
    await act(async () => { intelButton(view!.host, 'Refresh audit log').click() })
    await settle()
    expect(view.host.textContent).toContain(record.actor_label_snapshot)
    expect(intelButton(view.host, 'Export JSON').disabled).toBe(false)
  })

  it('keeps a cached unfiltered audit page quarantined after a filtered read denial', async () => {
    let status = 200
    const rejected: number[] = []
    const pending = deferred<unknown>()
    vi.mocked(apiFetch).mockImplementation((path) => {
      if (status === 200) return Promise.resolve(data) as never
      if (status === 403) {
        rejected.push(403)
        return Promise.reject(new ApiError('Audit read unavailable', 403, path))
      }
      return pending.promise as never
    })
    view = await mountIntel(<AuditLogsPage />, '/')
    await settle()
    expect(view.host.textContent).toContain(record.actor_label_snapshot)
    changeFilter('auth.login')
    await settle()
    status = 403
    await act(async () => { await view!.client.invalidateQueries() })
    await settle()
    expect(rejected).toContain(403)
    status = 503
    changeFilter('')
    await settle()
    expect(view.host.textContent).not.toContain(record.actor_label_snapshot)
    expect(view.host.textContent).not.toContain(record.resource_label_snapshot)
  })

  it('pauses audit export during an ordinary read outage without discarding prior context', async () => {
    let unavailable = false
    vi.mocked(apiFetch).mockImplementation((path) => unavailable
      ? Promise.reject(new ApiError('Audit temporarily unavailable', 503, path))
      : Promise.resolve(data) as never)
    view = await mountIntel(<AuditLogsPage />, '/')
    await settle()
    unavailable = true
    await act(async () => { await view!.client.invalidateQueries() })
    await settle()
    expect(view.host.textContent).toContain(record.actor_label_snapshot)
    const button = intelButton(view.host, 'Export JSON')
    expect(button.disabled).toBe(true)
    act(() => button.click())
    expect(vi.mocked(apiFetch).mock.calls.some(([path]) => path.startsWith('/audit-logs/export'))).toBe(false)
  })
})
