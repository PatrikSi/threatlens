// @vitest-environment jsdom
import { Component, act, type ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import type { AITaskRunResponse } from '../types/api'
import type { AlertOccurrence } from '../types/alerts'
import { AiSettingsPage } from './AiSettingsPage'
import { AlertOccurrencesWorkspace } from './AlertOccurrencesWorkspace'
import { deferred, mountIntel, settle } from './articleIntelligenceTestSupport'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: () => {} })
vi.mock('../api/client', async (original) => ({ ...await original<object>(), apiFetch: vi.fn() }))
vi.mock('../hooks/useCurrentUser', () => ({ useCurrentUser: () => ({ data: { id: 'analyst', role: 'admin', features: { ai_enabled: true }, access: { permissions: ['*:*'] } }, isError: false }) }))

const renderErrors: Error[] = []
class WorkflowBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  componentDidCatch(error: Error) { renderErrors.push(error) }
  render() { return this.state.failed ? <p role="alert">Workflow rendering failed</p> : this.props.children }
}
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
afterEach(() => { view?.close(); view = undefined; renderErrors.length = 0; vi.restoreAllMocks(); vi.clearAllMocks() })

const run: AITaskRunResponse = {
  id: 'a855d5bd-8cba-41a6-a314-cc18027dfe96', task_type: 'item_enrichment', trigger_source: 'manual', status: 'ready', metadata: {},
  reason: null, celery_task_id: null, worker_name: null, actor_user_id: null, actor_email: null, item_id: null,
  item_title: 'Private retired history', item_url: null, feed_name: null, item_first_seen_at: null, item_published_at: null,
  daily_brief_id: null, parent_run_id: null, model: null, prompt_tokens: null, completion_tokens: null, total_tokens: null,
  latency_ms: null, duration_ms: null, prompt_char_count: null, response_char_count: null, input_text_chars: null, error: null,
  target_count: null, processed_count: 0, success_count: 0, error_count: 0, skipped_count: 0, skipped_unchanged_count: 0, skipped_ineligible_count: 0,
  queued_at: '2026-11-01T00:00:00Z', started_at: null, finished_at: null, created_at: '2026-11-01T00:00:00Z', updated_at: '2026-11-01T00:00:00Z',
}
const occurrence = {
  id: 'private-occurrence', alert_interest_id: 'rule', rule_id_snapshot: 'rule', owner_user_id: 'analyst', item_id: 'item', item_id_snapshot: 'item',
  integration_event_id: null, rule_revision: 1, item_content_hash: 'a'.repeat(64), alert_name_snapshot: 'Private retired watchlist',
  alert_category_snapshot: 'software', alert_keywords_snapshot: ['private'], matched_keywords: ['private'], source_snapshot_json: { item: { title: 'Private retired occurrence' } },
  severity_snapshot: 'high', lifecycle_state: 'new', is_suppressed: false, suppressed_at: null, suppression_reason: null, is_snoozed: false,
  snoozed_until: null, snooze_reason: null, closure_disposition: null, acknowledged_at: null, acknowledged_by_user_id: null, investigating_at: null,
  investigating_by_user_id: null, closed_at: null, closed_by_user_id: null, version: 1, created_at: run.created_at, updated_at: run.created_at,
} satisfies AlertOccurrence

describe('retired workflow placeholders', () => {
  it('retires denied scoped placeholders when the AI history filter changes without a render loop', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    let denied = false
    const filtered = deferred<unknown>()
    vi.mocked(apiFetch).mockImplementation((path) => {
      if (path.startsWith('/ai/ops/runs/')) return Promise.resolve({ run: { ...run, item_title: 'Readable selected detail' }, events: [{ id: 'permitted-event', task_run_id: run.id, event_type: 'progress', message: 'Readable selected timeline', payload: {}, created_at: run.created_at }] }) as never
      if (path.startsWith('/ai/ops/runs?')) {
        if (path.includes('task_type=report')) return filtered.promise as never
        return denied ? Promise.reject(new ApiError('History denied', 403, path)) : Promise.resolve({ items: [run], total: 1, limit: 20, offset: 0 }) as never
      }
      if (path === '/feeds') return Promise.resolve([]) as never
      return new Promise(() => {})
    })
    view = await mountIntel(<WorkflowBoundary><AiSettingsPage /></WorkflowBoundary>, `/?run=${run.id}`)
    expect(view.host.textContent).toContain('Private retired history')
    expect(view.host.textContent).toContain('Readable selected timeline')
    denied = true
    await act(async () => { await view!.client.invalidateQueries({ queryKey: ['ai', 'ops', 'runs'] }) })
    await settle()
    expect(view.host.textContent).not.toContain('Private retired history')
    const filter = view.host.querySelector<HTMLSelectElement>('#ai-history-task-type-filter')!
    act(() => { filter.value = 'report'; filter.dispatchEvent(new Event('change', { bubbles: true })) })
    await settle()
    expect(renderErrors).toEqual([])
    expect(view.host.textContent).not.toContain('Private retired history')
    expect(view.host.textContent).toContain('Readable selected timeline')
    expect(view.host.querySelector(`a[href="/settings/ai?run=${run.id}"]`)?.textContent).toBe(`Run ${run.id}`)
  })

  it('retires denied scoped placeholders when the occurrence filter changes without a render loop', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    let denied = false
    const filtered = deferred<unknown>()
    vi.mocked(apiFetch).mockImplementation((path) => {
      if (path.startsWith('/alerts/occurrences?')) {
        if (path.includes('severities=critical')) return filtered.promise as never
        return denied ? Promise.reject(new ApiError('Occurrences denied', 403, path)) : Promise.resolve({ items: [occurrence], total: 1, page: 1, page_size: 25 }) as never
      }
      return Promise.resolve([]) as never
    })
    view = await mountIntel(<WorkflowBoundary><AlertOccurrencesWorkspace /></WorkflowBoundary>, '/?view=occurrences&queue_scope=personal')
    expect(view.host.textContent).toContain('Private retired occurrence')
    denied = true
    await act(async () => { await view!.client.invalidateQueries({ queryKey: ['alerts', 'occurrences', 'list'] }) })
    await settle()
    expect(view.host.textContent).not.toContain('Private retired occurrence')
    await act(async () => { await view!.router.navigate('/?view=occurrences&queue_scope=personal&severities=critical') })
    await settle()
    expect(renderErrors).toEqual([])
    expect(view.host.textContent).not.toContain('Private retired occurrence')
    expect(view.host.querySelector('[aria-label="Refresh alert occurrences"]')).not.toBeNull()
  })
})
