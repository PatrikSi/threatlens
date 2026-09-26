import { test, expect } from './fixtures'

const appOrigin = 'http://threatlens-browser.invalid'
test.use({ appOrigin, baseURL: appOrigin })

test('reviews, retries and withdraws a publication on HTTP LAN origins without reusing accepted request IDs', async ({ page }) => {
  await page.routeWebSocket('**/*', (socket) => socket.close())
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  const requests: { idempotency_key: string; preview_fingerprint: string }[] = []
  let saved = false
  let revision = 1
  let refreshFails = false
  const publication = () => ({ id: 'publication-1', team_id: 'team-1', format: 'misp', marking: 'TLP:AMBER',
    status: revision === 1 ? 'active' : 'withdrawn', revision, indicator_count: 1, withdrawn_count: revision - 1,
    created_at: '2026-09-26T10:00:00Z', updated_at: '2026-09-26T10:00:00Z' })
  await page.route('**/api/v1/exports/capabilities', (route) => route.fulfill({ json: {
    formats: [{ id: 'csv', label: 'CSV', extension: '.csv', media_type: 'text/csv', description: 'Articles',
      supports_article_text: true, supports_iocs: true, supports_user_state: true }],
    feeds: [], tags: [], classifications: [], max_items: 10000, max_pdf_items: 500,
    max_uncompressed_bytes: 262144000, preview_limit: 25,
  } }))
  await page.route('**/api/v1/exports/preview', (route) => route.fulfill({ json: {
    total_matches: 1, articles_with_text: 1, items_with_iocs: 1, preview_limit: 25,
    exceeds_export_limit: false, exceeds_pdf_limit: false, items: [],
  } }))
  await page.route('**/api/v1/exports/jobs?*', (route) => route.fulfill({ json: { items: [], has_more: false } }))
  await page.route('**/api/v1/teams?*', (route) => route.fulfill({ json: {
    items: [{ id: 'team-1', name: 'Endpoint team' }], total: 1, page: 1, page_size: 50,
  } }))
  await page.route('**/api/v1/teams/team-1/indicator-publications?*', (route) => route.fulfill({ json: {
    items: saved ? [publication()] : [], has_more: false, next_cursor: null,
  } }))
  await page.route('**/api/v1/teams/team-1/indicator-publications/preview', (route) => refreshFails
    ? route.fulfill({ status: 503, json: { detail: 'Preview temporarily unavailable' } })
    : route.fulfill({ json: { fingerprint: 'exact-evidence-revision', matched_articles: 1, excluded_or_unreviewed: 2,
      max_articles: 100, max_indicators: 250, indicators: [{ assessment_id: 'review-1', item_id: 'item-1', ioc_id: 'ioc-1',
        type: 'domain', value: 'evil.net', title: 'Reviewed article', assessment_version: 1, evidence_count: 2, expires_at: null }] } }))
  await page.route('**/api/v1/teams/team-1/indicator-publications', (route) => {
    requests.push(route.request().postDataJSON())
    if (requests.length === 1) return route.fulfill({ status: 503, json: { detail: 'Publication response unavailable' } })
    saved = true
    return route.fulfill({ status: 201, json: publication() })
  })
  await page.route('**/api/v1/teams/team-1/indicator-publications/publication-1/withdraw', (route) => {
    expect(route.request().postDataJSON()).toEqual({ expected_revision: 1 })
    revision = 2
    return route.fulfill({ json: publication() })
  })
  await page.goto('/export')
  expect(await page.evaluate(() => typeof crypto.randomUUID)).toBe('undefined')
  await page.getByRole('button', { name: 'Reviewed team publications' }).click()
  await page.getByRole('combobox', { name: 'Publication team' }).selectOption('team-1')
  await page.getByRole('button', { name: 'Preview reviewed indicators', exact: true }).click()
  await expect(page.getByRole('cell', { name: 'domain: evil.net' })).toBeVisible()
  const approve = page.getByRole('checkbox', { name: /I reviewed this exact selection/ })
  await approve.focus()
  await page.keyboard.press('Space')
  const publish = page.getByRole('button', { name: 'Approve reviewed publication', exact: true })
  await publish.click()
  await expect(page.getByRole('alert').filter({ hasText: 'Publication response unavailable' })).toBeVisible()
  await publish.click()
  await expect(page.getByRole('status').filter({ hasText: 'Publication saved' })).toBeVisible()
  expect(requests).toHaveLength(2)
  expect(requests[1]).toEqual(requests[0])
  expect(requests[0].idempotency_key).toMatch(/^[\da-f]{8}-[\da-f]{4}-4[\da-f]{3}-[89ab][\da-f]{3}-[\da-f]{12}$/)
  await approve.check()
  await publish.click()
  await expect.poll(() => requests.length).toBe(3)
  expect(requests[2].idempotency_key).not.toBe(requests[0].idempotency_key)
  await page.getByRole('button', { name: 'Withdraw publication', exact: true }).click()
  await page.getByRole('button', { name: 'Confirm withdrawal', exact: true }).click()
  await expect(page.getByText(/MISP · withdrawn · revision 2/)).toBeVisible()
  refreshFails = true
  await page.getByRole('button', { name: 'Preview reviewed indicators', exact: true }).click()
  await expect(page.getByRole('alert').filter({ hasText: 'Preview temporarily unavailable' })).toBeVisible()
  await expect(publish).toHaveCount(0)
  expect(errors).toEqual([])
})
