// @vitest-environment jsdom

import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { act, useRef } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, apiFetch } from '../api/client'
import type { AITaskRunDetailResponse, AITaskRunListResponse, AITaskRunResponse } from '../types/api'
import { SelectedRunSection } from './AiTaskRunDetail'
import { TaskHistoryPanel } from './AiTaskHistoryPanel'
import { useAiActivityRunState } from './useAiActivityRunState'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...(await original<object>()), apiFetch: vi.fn() }))

const run: AITaskRunResponse = {
  id: 'restricted-run', task_type: 'item_enrichment', status: 'ready', metadata: { scope: 'Private evidence' },
  item_title: 'Restricted article title', trigger_source: 'manual', actor_email: 'restricted-analyst@example.com',
  reason: null, celery_task_id: null, worker_name: null, actor_user_id: null,
  item_id: null, item_url: null, feed_name: null, item_first_seen_at: null, item_published_at: null,
  daily_brief_id: null, parent_run_id: null, model: null, prompt_tokens: null,
  completion_tokens: null, total_tokens: null, latency_ms: null, duration_ms: null,
  prompt_char_count: null, response_char_count: null, input_text_chars: null, error: null,
  target_count: 1, processed_count: 1, success_count: 1, error_count: 0,
  skipped_count: 0, skipped_unchanged_count: 0, skipped_ineligible_count: 0,
  queued_at: '2026-09-12T12:00:00Z', started_at: null, finished_at: null,
  created_at: '2026-09-12T12:00:00Z', updated_at: '2026-09-12T12:00:00Z',
}
const detail: AITaskRunDetailResponse = { run, events: [] }
let root: Root | null = null
let host: HTMLDivElement | null = null
let client: QueryClient | null = null
const noChange = () => {}

function Fixture() {
  const ref = useRef<HTMLDivElement>(null)
  const runDetailQuery = useQuery({ queryKey: ['run-detail'], queryFn: () => apiFetch<AITaskRunDetailResponse>('/detail') })
  const runsQuery = useQuery({ queryKey: ['run-history'], queryFn: () => apiFetch<AITaskRunListResponse>('/history') })
  const state = useAiActivityRunState({ runPage: 0, setRunPage: noChange, selectedRunId: run.id, runDetailQuery, runsQuery })
  return <>
    <TaskHistoryPanel days={30} selectedModel="" filters={{ taskType: '', status: '', triggerSource: '', onlyFailures: false }}
      setFilters={noChange} runPage={0} setRunPage={noChange} runsQuery={runsQuery} selectedRunId={run.id}
      onSelectRun={noChange} onInspectRun={state.setInspectedRunId} history={state.history} />
    <SelectedRunSection selectedRunSectionRef={ref} runDetailQuery={runDetailQuery} briefSources={[]}
      briefSourcesLoading={false} briefSourcesErrorMessage="" onCancelRun={noChange} cancelingRunId={null} runState={state} />
  </>
}

async function settle() {
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 15)) })
}

async function mount() {
  vi.mocked(apiFetch).mockImplementation((path) => Promise.resolve(path === '/history'
    ? { items: [run], total: 1, offset: 0, limit: 25 }
    : detail) as never)
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  act(() => root!.render(<MemoryRouter><QueryClientProvider client={client!}><Fixture /></QueryClientProvider></MemoryRouter>))
  await settle()
  expect(host.textContent).toContain('restricted-analyst@example.com')
}

afterEach(() => {
  act(() => root?.unmount())
  client?.clear()
  host?.remove()
  root = null
  client = null
  host = null
  vi.resetAllMocks()
})

describe('AI run access during asynchronous refresh', () => {
  it.each([401, 403, 404, 503])('handles selected run and history HTTP %s without treating revoked access as an outage', async (status) => {
    await mount()
    vi.mocked(apiFetch).mockRejectedValue(new ApiError('Run refresh rejected', status, '/detail'))
    await act(async () => { await client!.invalidateQueries() })
    await settle()

    expect(host!.textContent).toContain('Run refresh rejected')
    if (status === 503) {
      expect(host!.textContent).toContain('restricted-analyst@example.com')
      expect(host!.textContent).toContain('Private evidence')
    } else {
      expect(host!.textContent).not.toContain('restricted-analyst@example.com')
      expect(host!.textContent).not.toContain('Private evidence')
      expect(host!.textContent).not.toContain('Restricted article title')
    }
  })

  it.each([401, 403, 404])('removes an inspected run title after its separate request returns HTTP %s', async (status) => {
    await mount()
    const inspect = [...host!.querySelectorAll('button')].find((button) => button.textContent === 'View Request / Response')!
    act(() => inspect.click())
    await settle()
    expect(document.querySelector('[role="dialog"]')?.textContent).toContain('Restricted article title')

    vi.mocked(apiFetch).mockRejectedValue(new ApiError('Inspection access withdrawn', status, '/inspect'))
    await act(async () => { await client!.invalidateQueries({ queryKey: ['ai', 'ops', 'inspect-run'] }) })
    await settle()
    const modal = document.querySelector('[role="dialog"]')
    expect(modal?.textContent).toContain('Inspection access withdrawn')
    expect(modal?.textContent).not.toContain('Restricted article title')
    expect(modal?.textContent).not.toContain('restricted-analyst@example.com')
  })
})
