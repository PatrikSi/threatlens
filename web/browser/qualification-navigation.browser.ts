import type { AIProvider, AISettings, AITaskRunDetailResponse } from '../src/types/api'
import { createProviderDraft, createProviderRequest } from '../src/pages/aiProviderDraft'
import { createRequestFromDraft, DEFAULT_DRAFT } from '../src/pages/aiSettingsDraft'
import { test, expect } from './fixtures'

const runId = 'a855d5bd-8cba-41a6-a314-cc18027dfe96'
const settings: AISettings = {
  ...createRequestFromDraft({ ...DEFAULT_DRAFT, base_url: 'http://localhost:11434/v1', model: 'legacy-model' }),
  id: 'settings-1', ai_enabled: true, ai_configured: true, api_key_configured: false,
  provider_routing_supported: true,
  effective_feature_configured: { item_enrichment: true, daily_brief: true, report: true },
  created_at: '2026-09-27T00:00:00Z', updated_at: '2026-09-27T00:00:00Z',
  prompt_previews: {
    item_enrichment: { label: 'Enrichment', system_prompt: '', notes: [] },
    daily_brief: { label: 'Daily brief', system_prompt: '', notes: [] },
  },
}
const provider: AIProvider = {
  ...createProviderRequest({ ...createProviderDraft(), name: 'Qualified provider',
    base_url: 'http://localhost:11434/v1', model: 'qualified-model' }),
  id: 'provider-1', version: 1, api_key_configured: false, credential_error: null,
  created_at: '2026-09-27T00:00:00Z', updated_at: '2026-09-27T00:00:00Z',
}
const detail: AITaskRunDetailResponse = { run: {
  id: runId, task_type: 'connection_test', trigger_source: 'manual', status: 'ready',
  reason: null, celery_task_id: null, worker_name: null, actor_user_id: null, actor_email: null,
  item_id: null, item_title: null, item_url: null, feed_name: null, item_first_seen_at: null,
  item_published_at: null, daily_brief_id: null, parent_run_id: null, model: 'qualified-model',
  prompt_tokens: 40, completion_tokens: 20, total_tokens: 60, latency_ms: 100, duration_ms: 100,
  prompt_char_count: 100, response_char_count: 50, input_text_chars: 80, error: null, metadata: {},
  target_count: null, processed_count: 0, success_count: 0, error_count: 0, skipped_count: 0,
  skipped_unchanged_count: 0, skipped_ineligible_count: 0,
  queued_at: '2026-09-01T12:00:00Z', started_at: '2026-09-01T12:00:00Z',
  finished_at: '2026-09-01T12:00:01Z', created_at: '2026-09-01T12:00:00Z', updated_at: '2026-09-01T12:00:01Z',
}, events: [] }

test('opens an off-page qualification run and preserves its exact link across reload and navigation', async ({ page, api }) => {
  api.identity = { ...api.identity, role: 'admin', features: {
    ...api.identity.features, ai_enabled: true, ai_configured: true,
  } }
  const requests: string[] = []
  const responses: Record<string, unknown> = {
    '/ai/settings': settings,
    '/ai/quota-groups': { items: [], total: 0, limit: 100, offset: 0 },
    '/ai/ops/live': { worker_count: 1, active_count: 0, reserved_count: 0, scheduled_count: 0,
      queued_count: 0, oldest_queued_age_seconds: null },
    '/ai/ops/runs': { items: [], total: 0, limit: 10, offset: 0 },
    '/ai/ops/prompt-history': [], '/ai/ops/manual-actions': [],
    '/ai/provider-routing': { version: 1, default_provider_id: null, item_enrichment_provider_id: null,
      daily_brief_provider_id: null, report_provider_id: null, team_assessment_provider_id: null },
    '/ai/providers': { items: [provider], total: 1, limit: 25, offset: 0 },
    '/ai/providers/provider-1': provider,
    '/ai/providers/provider-1/qualifications': [{ run_id: runId, status: 'ready', provider_version: 1,
      token_budget: 24000, reserved_tokens: 60, features: ['extraction'], error: null,
      results: [{ feature: 'extraction', state: 'completed', contract_passed: true, latency_ms: 100, total_tokens: 60 }] }],
    [`/ai/ops/runs/${runId}`]: detail,
  }
  await page.route('**/api/v1/ai/**', (route) => {
    const path = new URL(route.request().url()).pathname.replace('/api/v1', '')
    expect(route.request().method(), 'Navigation must not authorize another provider call').toBe('GET')
    requests.push(path)
    expect(Object.hasOwn(responses, path), `Missing AI fixture: ${path}`).toBe(true)
    return route.fulfill({ json: responses[path] })
  })
  await page.goto('/settings/ai')
  await page.getByRole('tab', { name: 'Configuration', exact: true }).click()
  await page.getByRole('button', { name: 'Edit provider Qualified provider', exact: true }).click()
  await page.getByRole('button', { name: 'Feature qualification' }).click()
  const link = page.getByRole('link', { name: `Inspect run ${runId}`, exact: true })
  await expect(link).toHaveAttribute('href', `/settings/ai?run=${runId}`)
  await link.click()
  await expect(page).toHaveURL(`/settings/ai?run=${runId}`)
  const runLink = page.getByRole('link', { name: `Run ${runId}`, exact: true })
  await expect(runLink).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Selected run', exact: true })).toBeVisible()
  expect(requests).toContain(`/ai/ops/runs/${runId}`)
  await page.reload()
  await expect(runLink).toBeVisible()
  await expect(page.getByRole('tab', { selected: true })).toHaveAttribute('id', /activity/)
  await page.getByRole('tab', { name: 'Configuration', exact: true }).click()
  await page.goBack()
  await expect(page).toHaveURL('/settings/ai')
  await page.goForward()
  await expect(page).toHaveURL(`/settings/ai?run=${runId}`)
  await expect(runLink).toBeVisible()
})
