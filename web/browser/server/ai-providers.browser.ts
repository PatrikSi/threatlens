import { test, expect, signIn } from './fixtures'

test('real AI provider settings preserve credentials and enforce versioned routing', async ({ page, identity }) => {
  test.setTimeout(90000)
  test.skip(process.env.THREATLENS_BROWSER_AI_PROVIDERS !== 'true', 'Run isolated harness with --ai-providers')
  async function saveAssignments() {
    const responsePromise = page.waitForResponse(
      (response) => response.url().endsWith('/ai/provider-routing') && response.request().method() === 'PUT',
    )
    await page.getByRole('button', { name: 'Save feature assignments', exact: true }).click()
    const response = await responsePromise
    expect(response.status()).toBe(200)
    await expect(page.getByRole('status').filter({ hasText: 'AI feature assignments saved.' })).toBeVisible()
    return response.json()
  }
  await signIn(page, identity)
  await page.goto('/settings/ai')
  await page.getByRole('tab', { name: 'Configuration', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Legacy provider', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Add provider', exact: true }).click()
  const providerName = `Browser provider ${identity.id}`
  await page.getByLabel('Provider name', { exact: true }).fill(providerName)
  await page.getByLabel('Provider base URL', { exact: true }).fill('https://provider.example.invalid/v1')
  await page.getByLabel('Provider model', { exact: true }).fill('fixture-model')
  await page.getByLabel('Provider API key', { exact: true }).fill('isolated-browser-secret')
  const createResponse = page.waitForResponse(
    (response) => response.url().endsWith('/ai/providers') && response.request().method() === 'POST',
  )
  await page.getByRole('button', { name: 'Save provider', exact: true }).click()
  const created = await createResponse
  expect(created.status()).toBe(201)
  const provider = await created.json()
  expect(provider).toMatchObject({
    name: providerName,
    model: 'fixture-model',
    version: 1,
    api_key_configured: true,
    credential_error: null,
  })
  expect(provider).not.toHaveProperty('api_key')
  expect(JSON.stringify(provider)).not.toContain('isolated-browser-secret')
  await expect(page.getByLabel('Replacement API key', { exact: true })).toHaveValue('')

  await page.reload()
  await page.getByRole('tab', { name: 'Configuration', exact: true }).click()
  await page.getByRole('button', { name: `Edit provider ${providerName}`, exact: true }).click()
  await page.getByLabel('Provider model', { exact: true }).fill('fixture-model-updated')
  const updateResponse = page.waitForResponse(
    (response) => response.url().endsWith(`/ai/providers/${provider.id}`) && response.request().method() === 'PUT',
  )
  await page.getByRole('button', { name: 'Save provider', exact: true }).click()
  const updated = await updateResponse
  expect(updated.status()).toBe(200)
  expect(await updated.json()).toMatchObject({ version: 2, model: 'fixture-model-updated', api_key_configured: true })
  await expect(page.getByRole('button', { name: 'Save provider', exact: true })).toBeDisabled()

  await page.getByLabel('Provider base URL', { exact: true }).fill('https://other.example.invalid/v1')
  const rejectedResponse = page.waitForResponse(
    (response) => response.url().endsWith(`/ai/providers/${provider.id}`) && response.request().method() === 'PUT',
  )
  await page.getByRole('button', { name: 'Save provider', exact: true }).click()
  expect((await rejectedResponse).status()).toBe(409)
  await expect(page.getByRole('alert').filter({ hasText: /key|credential/i })).toBeVisible()
  await expect(page.getByLabel('Provider base URL', { exact: true })).toHaveValue('https://other.example.invalid/v1')
  await page.getByRole('button', { name: 'Reload saved provider', exact: true }).click()
  const discard = page.getByRole('alertdialog', { name: 'Discard provider changes?', exact: true })
  await discard.getByRole('button', { name: 'Discard changes', exact: true }).click()
  await expect(page.getByLabel('Provider base URL', { exact: true })).toHaveValue('https://provider.example.invalid/v1')

  await page.getByRole('button', { name: 'Assign selected provider to default provider', exact: true }).click()
  await page.getByRole('button', { name: 'Assign selected provider to reports', exact: true }).click()
  expect(await saveAssignments()).toMatchObject({ default_provider_id: provider.id, report_provider_id: provider.id })
  const routing = await (await page.request.get('/api/v1/ai/provider-routing')).json()
  expect(routing).toMatchObject({
    default_provider_id: provider.id,
    report_provider_id: provider.id,
    item_enrichment_provider_id: null,
  })
  const effective = await (await page.request.get('/api/v1/ai/settings')).json()
  expect(effective.effective_feature_configured).toEqual({ item_enrichment: true, daily_brief: true, report: true })
  await expect(page.getByRole('button', { name: 'Delete provider', exact: true })).toBeDisabled()

  await page.getByRole('button', { name: 'Use legacy settings for default provider', exact: true }).click()
  await page.getByRole('button', { name: 'Use default provider for reports', exact: true }).click()
  expect(await saveAssignments()).toMatchObject({ default_provider_id: null, report_provider_id: null })
  await page.getByRole('button', { name: 'Delete provider', exact: true }).click()
  const deletion = page.getByRole('alertdialog', { name: 'Delete provider?', exact: true })
  const deleteResponse = page.waitForResponse(
    (response) => response.url().includes(`/ai/providers/${provider.id}?version=`) && response.request().method() === 'DELETE',
    { timeout: 15000 },
  )
  await deletion.getByRole('button', { name: 'Delete provider', exact: true }).click()
  expect((await deleteResponse).status()).toBe(204)
  await expect(deletion).toBeHidden()
  expect((await page.request.get(`/api/v1/ai/providers/${provider.id}`)).status()).toBe(404)
})
