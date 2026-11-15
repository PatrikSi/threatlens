// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import { AuditLogsPage } from './AuditLogsPage'
import { deferred, intelButton, mountIntel, settle } from './articleIntelligenceTestSupport'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...await original<object>(), apiFetch: vi.fn() }))
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
afterEach(() => { view?.close(); view = undefined; vi.clearAllMocks(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('does not save an audit export completed after its read authority was withdrawn', async () => {
  const record = { id: 'audit-private', action: 'users.update', resource_type: 'user', resource_id: 'private-user', resource_label_snapshot: 'Private exported account', actor_user_id: 'actor', actor_label_snapshot: 'Private exported actor', success: true, metadata_json: {}, created_at: '2026-11-01T00:00:00Z' }
  const data = { logs: [record], total: 1, page: 1, page_size: 50 }
  const pending = deferred<unknown>()
  let status = 200
  const rejected: number[] = []
  const createObjectURL = vi.fn(() => 'blob:private-audit')
  class ExportURL extends URL {
    static createObjectURL = createObjectURL
    static revokeObjectURL = vi.fn()
  }
  vi.stubGlobal('URL', ExportURL)
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  vi.mocked(apiFetch).mockImplementation((path) => {
    if (path.startsWith('/audit-logs/export?')) return pending.promise as never
    if (status === 200) return Promise.resolve(data) as never
    rejected.push(status)
    return Promise.reject(new ApiError('Audit read unavailable', status, path))
  })
  view = await mountIntel(<AuditLogsPage />, '/')
  await settle()
  expect(view.host.textContent).toContain(record.actor_label_snapshot)
  act(() => intelButton(view!.host, 'Export JSON').click())
  await settle()
  expect(vi.mocked(apiFetch).mock.calls.filter(([path]) => path.startsWith('/audit-logs/export?'))).toHaveLength(1)
  status = 403
  await act(async () => { await view!.client.invalidateQueries({ queryKey: ['audit-logs'] }) })
  await settle()
  expect(rejected).toContain(403)
  await act(async () => { pending.resolve({ logs: [record], total: 1, truncated: false }); await pending.promise })
  await settle()
  expect(createObjectURL).not.toHaveBeenCalled()
  expect(view.host.textContent).not.toContain('Exported 1 logs.')
})
