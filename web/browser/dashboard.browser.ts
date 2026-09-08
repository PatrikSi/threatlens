import { readFileSync } from 'node:fs'
import { test, expect, feed } from './fixtures'

const previewResponse = JSON.parse(readFileSync(new URL('./preview-response.json', import.meta.url), 'utf8'))

test('moves and resizes a floating dashboard panel by keyboard without losing focus', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('status').filter({ hasText: 'initialized with safe local defaults' })).toBeVisible()
  await page.getByRole('button', { name: 'Edit Layout', exact: true }).click()
  await page.getByLabel('RSS Panel 1 panel layout').selectOption('free')
  const panel = page.getByRole('region', { name: 'RSS Panel 1 dashboard panel' })
  const resize = panel.getByRole('button', { name: 'Resize panel', exact: true })
  const move = panel.getByRole('button', { name: 'Move RSS Panel 1 panel' })
  const before = (await panel.boundingBox())!
  await resize.focus()
  await page.keyboard.press('Shift+ArrowLeft')
  await page.keyboard.press('Shift+ArrowUp')
  await expect.poll(async () => (await panel.boundingBox())!.width).toBe(before.width - 40)
  await move.focus()
  await page.keyboard.press('ArrowRight')
  await expect.poll(async () => (await panel.boundingBox())!.x).toBe(before.x + 10)
  await expect(move).toBeFocused()
  await page.keyboard.press('Tab')
  await expect(move).not.toBeFocused()
  expect(await page.evaluate(() => document.activeElement?.closest('[aria-label="RSS Panel 1 dashboard panel"]') !== null)).toBe(true)
})

test('blocks publisher resources by default, allows explicit opt-in, and resets consent for another article', async ({ page }) => {
  const resourceRequests: string[] = []
  const items = ['one', 'two'].map((id) => ({
    id, feed_id: feed.id, feed_name: feed.name, title: `Article ${id}`,
    url: `https://publisher.example.test/${id}`, canonical_url: null, summary: 'Fixture summary',
    published_at: '2026-09-08T00:00:00Z', first_seen_at: '2026-09-08T00:00:00Z',
    status: 'ready', classification: null, is_read: true, is_starred: false, tags: [],
    ai_relevance_score: null, ai_relevance_label: null, ai_status: null,
  }))
  await page.route('**/api/v1/items?*', (route) => route.fulfill({ json: { items, total: 2, page: 1, page_size: 100 } }))
  await page.route('https://tracking.example.test/**', (route) => {
    resourceRequests.push(route.request().url())
    const stylesheet = new URL(route.request().url()).pathname.endsWith('.css')
    return route.fulfill({ contentType: stylesheet ? 'text/css' : 'image/png', body: stylesheet ? 'body { color: rgb(20, 30, 40); }' : Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64') })
  })
  await page.route('**/api/v1/items/*/article-preview*', (route) => {
    const article = route.request().url().includes('/one/') ? 'one' : 'two'
    return route.fulfill({
      contentType: 'text/html', body: previewResponse.html
        .replaceAll('tracking.example.test/pixel.png', `tracking.example.test/pixel.png?article=${article}`)
        .replaceAll('tracking.example.test/style.css', `tracking.example.test/style.css?article=${article}`),
      headers: route.request().url().includes('external_resources=true') ? previewResponse.allowedHeaders : previewResponse.blockedHeaders,
    })
  })
  await page.goto('/')
  await page.locator('article').filter({ hasText: 'Article one' }).getByRole('button', { name: 'Preview Original' }).click()
  const preview = page.getByRole('dialog', { name: 'Original article' })
  const consent = preview.getByLabel('Load external resources for this preview')
  await expect(page.frameLocator('iframe').getByText('Offline publisher fixture')).toBeVisible()
  expect(resourceRequests).toEqual([])
  await consent.check()
  // Browsers may request a stylesheet more than once. Verify allowed targets,
  // rather than a browser-specific number of network attempts.
  await expect.poll(() => [...new Set(resourceRequests.map((url) => new URL(url).pathname))].sort()).toEqual(['/pixel.png', '/style.css'])
  await preview.getByRole('button', { name: 'Close original article preview' }).click()
  await page.locator('article').filter({ hasText: 'Article two' }).getByRole('button', { name: 'Preview Original' }).click()
  await expect(consent).not.toBeChecked()
  await expect(page.frameLocator('iframe').getByText('Offline publisher fixture')).toBeVisible()
  // Unique per-article URLs ensure the second document cannot pass due to cache.
  expect(resourceRequests.every((url) => new URL(url).searchParams.get('article') === 'one')).toBe(true)
})
