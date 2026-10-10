import type { Page } from '@playwright/test'
import { test, expect, control, signIn } from './fixtures'

async function evaluateEvent(page: Page) {
  const response = page.waitForResponse((entry) =>
    entry.url().endsWith('/notifications/webhooks/preview') && entry.request().method() === 'POST',
  )
  await page.getByRole('button', { name: 'Evaluate event', exact: true }).click()
  const result = await response
  expect(result.status(), await result.text()).toBe(200)
  return result.json()
}

test('real webhook editor saves AI criteria and previews revision-pinned full text', async ({ page, request, identity }) => {
  test.setTimeout(90_000)
  const evidence = await control(request, 'webhook-article')
  await signIn(page, identity)
  await page.goto('/settings/integrations/webhooks')
  const name = `SOC relevance ${identity.id}`
  await page.getByLabel('Name', { exact: true }).fill(name)
  await page.getByLabel('Webhook URL', { exact: true }).fill('https://example.com/soc-receiver')
  await page.getByRole('combobox', { name: 'Event type', exact: true }).selectOption('article.ai.ready')
  await page.getByLabel('Enabled', { exact: true }).uncheck()

  await page.getByLabel('Search payload fields', { exact: true }).fill('full text')
  await page.getByRole('combobox', { name: 'Payload field', exact: true }).selectOption('item.full_text')
  await page.getByLabel('Output key', { exact: true }).fill('evidence.article_text')
  await page.getByRole('button', { name: 'Add payload field', exact: true }).click()
  await expect(page.getByRole('status').filter({ hasText: 'Added item.full_text' })).toBeVisible()

  await page.getByText('Automation and event conditions', { exact: true }).click()
  await page.getByRole('combobox', { name: 'Payload format', exact: true }).selectOption('automation_v1')
  await page.getByLabel('Include extracted article text', { exact: true }).check()
  await page.getByRole('button', { name: 'Add event conditions', exact: true }).click()
  await page.getByRole('combobox', { name: 'Field', exact: true }).selectOption('ai_relevance_score')
  await page.getByRole('combobox', { name: 'Comparison', exact: true }).selectOption('gte')
  await page.getByLabel('Threshold', { exact: true }).fill('0.8')
  await expect(page.getByRole('combobox', { name: 'Sample event', exact: true })).toContainText(evidence.title)
  await page.getByRole('combobox', { name: 'Sample event', exact: true }).selectOption(evidence.event_id)
  const preview = await evaluateEvent(page)
  expect(preview.matches).toBe(true)
  expect(preview.automation_payload.data.article_text).toMatchObject({
    text: evidence.text, status: 'available', source_revision: 1,
  })
  expect(preview.automation_payload.data.ai_relevance).toMatchObject({ score: 0.92, label: 'high' })
  await expect(page.getByText('This event matches.', { exact: true })).toBeVisible()

  const save = page.waitForResponse((entry) =>
    entry.url().endsWith('/notifications/webhooks') && entry.request().method() === 'POST',
  )
  await page.getByRole('button', { name: 'Create webhook', exact: true }).click()
  const saved = await save
  expect(saved.status(), await saved.text()).toBe(201)
  expect(await saved.json()).toMatchObject({
    event_type: 'article.ai.ready', include_article_text: true, enabled: false,
    conditions: { op: 'all', conditions: [{ field: 'ai_relevance_score', operator: 'gte', value: 0.8 }] },
  })
  await page.reload()
  await page.getByRole('button', { name: new RegExp(name) }).click()
  await page.getByText('Automation and event conditions', { exact: true }).click()
  await expect(page.getByLabel('Include extracted article text', { exact: true })).toBeChecked()
  await expect(page.getByLabel('Threshold', { exact: true })).toHaveValue('0.8')
  await page.getByRole('combobox', { name: 'Sample event', exact: true }).selectOption(evidence.event_id)
  await page.getByLabel('Threshold', { exact: true }).fill('0.99')
  expect((await evaluateEvent(page)).matches).toBe(false)
  await page.getByLabel('Threshold', { exact: true }).fill('0.8')

  await control(request, `webhook-articles/${evidence.item_id}/refresh`)
  const stale = await evaluateEvent(page)
  expect(stale.matches).toBe(false)
  expect(stale.automation_payload.data.article_text).toMatchObject({ text: '', status: 'source_changed' })
  expect(JSON.stringify(stale)).not.toContain('Replacement evidence must not appear')
  await expect(page.getByText('This event does not match.', { exact: true })).toBeVisible()
})

test('real webhook template picker preserves JSON escaping in a non-sending preview', async ({ page, request, identity }) => {
  const evidence = await control(request, 'webhook-article')
  await signIn(page, identity)
  await page.goto('/settings/integrations/webhooks')
  await page.getByLabel('Name', { exact: true }).fill('SOC full text template')
  await page.getByLabel('Webhook URL', { exact: true }).fill('https://example.com/soc-receiver')
  await page.getByRole('combobox', { name: 'Event type', exact: true }).selectOption('article.ai.ready')
  await page.getByLabel('Search payload fields', { exact: true }).fill('full text')
  await page.getByRole('combobox', { name: 'Payload field', exact: true }).selectOption('item.full_text')
  await page.getByLabel('Output key', { exact: true }).fill('article_text')
  await page.getByRole('button', { name: 'Add payload field', exact: true }).click()
  await page.getByText('Automation and event conditions', { exact: true }).click()
  await page.getByRole('combobox', { name: 'Sample event', exact: true }).selectOption(evidence.event_id)
  const preview = await evaluateEvent(page)
  expect(preview.matches).toBe(true)
  expect(JSON.parse(preview.template_body).article_text).toBe(evidence.text)
  const hooks = await page.request.get('/api/v1/notifications/webhooks')
  expect(hooks.status()).toBe(200)
  expect(await hooks.json()).toEqual([])
})
