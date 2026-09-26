// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import { AutomationExecutions } from './AutomationExecutions'
import { intelButton, mountIntel, settle } from './articleIntelligenceTestSupport'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...(await original<object>()), apiFetch: vi.fn() }))
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
afterEach(() => { view?.close(); view = undefined; vi.clearAllMocks() })
const entry = { id: 'execution-1', action_id: 'action-1', status: 'completed', external_job_id: 'remote-job', sequence: 2, findings: 'One reviewed finding', investigation_note_id: null, policy_state: 'withdrawn', policy_revision: 1, policy_acknowledged_revision: 0, updated_at: '2026-09-26T00:00:00Z' }

describe('automation execution lifecycle', () => {
  it('separates execution status from policy acknowledgement and paginates bounded requests', async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({ items: [entry], next_cursor: 'cursor-1' }).mockResolvedValue({ items: [], next_cursor: null })
    view = await mountIntel(<AutomationExecutions writable={false} onClose={vi.fn()} />)
    await settle()
    expect(document.body.textContent).toContain('receiver acknowledgement pending')
    expect(document.body.textContent).toContain('One reviewed finding')
    expect(document.body.textContent).toContain('remote-job · completed')
    act(() => intelButton(document, 'Next executions').click())
    await settle()
    expect(vi.mocked(apiFetch).mock.calls.some(([path]) => path === '/notifications/automation/executions?limit=25&after=cursor-1')).toBe(true)
    expect(document.body.textContent).not.toContain('One reviewed finding')
  })
  it('hides cached findings after access is revoked and exposes a recoverable error', async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({ items: [entry], next_cursor: null }).mockRejectedValue(new ApiError('Your access changed', 403, '/notifications/automation/executions'))
    view = await mountIntel(<AutomationExecutions writable={false} onClose={vi.fn()} />)
    await settle()
    expect(document.body.textContent).toContain('One reviewed finding')
    act(() => intelButton(document, 'Refresh executions').click())
    await settle()
    expect(document.body.textContent).not.toContain('One reviewed finding')
    expect(document.body.textContent).toContain('Your access changed')
    expect(intelButton(document, 'Retry executions').disabled).toBe(false)
  })
})
