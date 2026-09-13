import AxeBuilder from '@axe-core/playwright'
import type { Page } from '@playwright/test'
import type { AIProvider, AIProviderRouting, AIProviderWriteRequest, AISettings } from '../src/types/ai'
import { createRequestFromDraft, DEFAULT_DRAFT } from '../src/pages/aiSettingsDraft'
import { test, expect } from './fixtures'

const savedSettings: AISettings = {
  ...createRequestFromDraft({ ...DEFAULT_DRAFT, base_url: 'http://localhost:11434/v1', model: 'legacy-model' }),
  id: 'settings-1',
  ai_enabled: true,
  ai_configured: true,
  api_key_configured: false,
  provider_routing_supported: true,
  effective_feature_configured: { item_enrichment: true, daily_brief: true, report: true },
  created_at: '2026-09-11T00:00:00Z',
  updated_at: '2026-09-11T00:00:00Z',
  prompt_previews: {
    item_enrichment: { label: 'Enrichment', system_prompt: '', notes: [] },
    daily_brief: { label: 'Daily brief', system_prompt: '', notes: [] },
  },
}

async function providerRoutes(page: Page) {
  const providers = new Map<string, AIProvider>()
  const deletedProviders = new Set<string>()
  let assignments: AIProviderRouting = {
    version: 1,
    default_provider_id: null,
    item_enrichment_provider_id: null,
    daily_brief_provider_id: null,
    report_provider_id: null,
  }
  const writes: string[] = []
  await page.route('**/api/v1/ai/**', async (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname.replace('/api/v1', '')
    const method = route.request().method()
    if (method !== 'GET') writes.push(path)
    if (path === '/ai/settings') return route.fulfill({ json: savedSettings })
    if (path === '/ai/ops/overview')
      return route.fulfill({ status: 503, json: { detail: 'Overview unavailable in this fixture' } })
    if (path === '/ai/ops/live')
      return route.fulfill({
        json: {
          worker_count: 1,
          active_count: 0,
          reserved_count: 0,
          scheduled_count: 0,
          queued_count: 0,
          oldest_queued_age_seconds: null,
        },
      })
    if (path === '/ai/ops/runs') return route.fulfill({ json: { items: [], total: 0, limit: 10, offset: 0 } })
    if (path === '/ai/ops/prompt-history' || path === '/ai/ops/manual-actions') return route.fulfill({ json: [] })
    if (path === '/ai/provider-routing') {
      if (method === 'PUT') {
        const submitted = route.request().postDataJSON() as AIProviderRouting
        expect(submitted.version).toBe(assignments.version)
        assignments = { ...submitted, version: assignments.version + 1 }
      }
      return route.fulfill({ json: assignments })
    }
    if (path === '/ai/providers' && method === 'GET')
      return route.fulfill({ json: { items: [...providers.values()], total: providers.size, limit: 25, offset: 0 } })
    if (path === '/ai/providers' && method === 'POST') {
      const submitted = route.request().postDataJSON() as AIProviderWriteRequest
      const provider: AIProvider = {
        ...submitted,
        id: submitted.id!,
        version: 1,
        api_key_configured: Boolean(submitted.api_key),
        credential_error: null,
        created_at: '2026-09-11T00:00:00Z',
        updated_at: '2026-09-11T00:00:00Z',
      }
      // Provider response contracts never return credentials.
      delete (provider as AIProviderWriteRequest).api_key
      providers.set(provider.id, provider)
      return route.fulfill({ status: 201, json: provider })
    }
    const id = path.split('/')[3]
    const provider = providers.get(id)
    if (provider && path.endsWith('/test-connection')) {
      expect(route.request().postDataJSON()).toEqual({ version: provider.version })
      return route.fulfill({
        json: { success: true, provider: 'openai_compatible', model: provider.model, latency_ms: 20, error: null },
      })
    }
    if (provider && method === 'DELETE') {
      expect(url.searchParams.get('version')).toBe(String(provider.version))
      providers.delete(provider.id)
      deletedProviders.add(provider.id)
      return route.fulfill({ status: 204 })
    }
    if (provider) return route.fulfill({ json: provider })
    if (deletedProviders.has(id) && method === 'GET')
      return route.fulfill({ status: 404, json: { detail: 'Provider not found' } })
    throw new Error(`Unexpected AI fixture request: ${method} ${path}`)
  })
  return { providers, writes, assignments: () => assignments }
}

test('adds, tests, assigns and deletes an AI provider using accessible keyboard controls', async ({
  page,
  api,
}, info) => {
  api.identity = {
    ...api.identity,
    role: 'admin',
    features: { ...api.identity.features, ai_enabled: true, ai_configured: true },
  }
  const state = await providerRoutes(page)
  await page.goto('/settings/ai')
  await page.getByRole('tab', { name: 'Configuration', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Legacy provider', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Add provider', exact: true }).focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('heading', { name: 'New provider', exact: true })).toBeFocused()
  await page.keyboard.press('Tab')
  await expect(page.getByLabel('Provider name', { exact: true })).toBeFocused()
  await page.getByLabel('Provider name', { exact: true }).fill('Local analysis')
  await page.getByLabel('Provider base URL', { exact: true }).fill('http://localhost:11434/v1')
  await page.getByLabel('Provider model', { exact: true }).fill('local-model')
  await page.getByLabel('Provider API key', { exact: true }).fill('write-only-test-secret')
  await page.getByRole('button', { name: 'Save provider', exact: true }).click()
  await expect(page.getByLabel('Replacement API key', { exact: true })).toHaveValue('')
  await page.getByRole('button', { name: 'Test this saved provider', exact: true }).click()
  await expect(page.getByText('Connection succeeded · Model: local-model · 20 ms')).toBeVisible()
  await page.getByRole('button', { name: 'Assign selected provider to default provider', exact: true }).click()
  await page.getByRole('button', { name: 'Save feature assignments', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Delete provider', exact: true })).toBeDisabled()
  expect(state.assignments().default_provider_id).toBe([...state.providers.keys()][0])
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
    .analyze()
  await info.attach('axe-ai-providers', { body: JSON.stringify(results, null, 2), contentType: 'application/json' })
  expect(results.violations.map(({ id, nodes }) => ({ id, targets: nodes.map((node) => node.target) }))).toEqual([])
  await page
    .getByRole('heading', { name: 'Provider connections', exact: true })
    .locator('..')
    .screenshot({ path: info.outputPath('ai-provider-settings.png') })
  await page.getByRole('button', { name: 'Use legacy settings for default provider', exact: true }).click()
  await page.getByRole('button', { name: 'Save feature assignments', exact: true }).click()
  await page.getByRole('button', { name: 'Delete provider', exact: true }).click()
  const dialog = page.getByRole('alertdialog', { name: 'Delete provider?', exact: true })
  await expect(dialog.getByRole('button', { name: 'Cancel', exact: true })).toBeFocused()
  await page.keyboard.press('Tab')
  await expect(dialog.getByRole('button', { name: 'Delete provider', exact: true })).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(dialog).toBeHidden()
  await expect(page.getByRole('button', { name: 'Add provider', exact: true })).toBeFocused()
  await expect(page.getByText('0 providers', { exact: true })).toBeVisible()
  expect(state.providers.size).toBe(0)
})

test('keeps provider drafts across AI tabs and confirms navigation before discarding them', async ({ page, api }) => {
  api.identity = {
    ...api.identity,
    role: 'admin',
    features: { ...api.identity.features, ai_enabled: true, ai_configured: true },
  }
  const state = await providerRoutes(page)
  await page.goto('/settings/ai')
  await page.getByRole('tab', { name: 'Configuration', exact: true }).click()
  await page.getByRole('button', { name: 'Add provider', exact: true }).click()
  await page.getByLabel('Provider name', { exact: true }).fill('Unsaved local model')
  await page.getByRole('tab', { name: 'Statistics', exact: true }).click()
  await page.getByRole('tab', { name: 'Configuration', exact: true }).click()
  await expect(page.getByLabel('Provider name', { exact: true })).toHaveValue('Unsaved local model')
  await page.getByRole('link', { name: 'Feeds', exact: true }).first().click()
  const dialog = page.getByRole('alertdialog', { name: 'Discard unsaved changes?', exact: true })
  await expect(dialog).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(dialog).toBeHidden()
  await expect(page.getByLabel('Provider name', { exact: true })).toHaveValue('Unsaved local model')
  expect(state.writes).toEqual([])
})
