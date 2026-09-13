import { test, expect } from './fixtures'

const filters = {
  q: null, feed_ids: [], tag_ids: [], tags_mode: 'any', classifications: [], ai_relevance_labels: [],
  ai_score_min: null, ai_score_max: null, is_read: null, is_starred: null, has_article_text: null,
  since: null, until: null, date_basis: 'published_at_or_first_seen_at', sort: 'published_at_desc',
}
const template = {
  id: 'browser-template', name: 'Threat landscape', owner_user_id: 'browser-analyst', builtin_key: null,
  description: '', report_type: 'custom', visibility: 'private', default_filters: filters,
  prompt: { audience: 'security_team', objective: 'Summarize developments.', tone: 'analytical',
    detail_level: 'standard', use_company_context: false, custom_instructions: null, focus_topics: [], excluded_topics: [] },
  sections: [{ key: 'summary', title: 'Executive summary', enabled: true }],
  created_at: '2026-09-08T00:00:00Z', updated_at: '2026-09-08T00:00:00Z',
}
const capabilities = {
  reporting_enabled: false, ai_configured: true, feeds: [], tags: [], classifications: [], max_sources: 100,
  preview_limit: 25, context_window_tokens: 8192, reserved_output_tokens: 1024, source_token_cap: 1000,
  max_model_calls: 12, safety_percent: 15,
}

test('rejects a stale schedule draft and adopts a newer version only after reopening', async ({ page, api }) => {
  api.identity.role = 'admin'
  let schedule = {
    id: 'browser-schedule', owner_user_id: 'browser-analyst', template_id: template.id, name: 'Weekly report',
    enabled: true, cadence: 'weekly', day_of_week: 0, day_of_month: 1, hour: 9, minute: 0, timezone: 'UTC',
    window_type: 'previous_complete_week', rolling_days: 7, filters, custom_instructions: 'Original instructions',
    delivery_enabled: false, delivery_mode: 'summary', skip_empty: true, missed_run_policy: 'latest',
    next_run_at: '2026-09-14T09:00:00Z', last_run_at: null, resource_version: 'v1',
    created_at: template.created_at, updated_at: template.updated_at,
  }
  const writes: Array<{ version: string | undefined; instructions: string }> = []
  let releaseSave!: () => void
  const pendingSave = new Promise<void>((resolve) => { releaseSave = resolve })
  await page.route('**/api/v1/reports/capabilities', (route) => route.fulfill({ json: capabilities }))
  await page.route('**/api/v1/reports/templates', (route) => route.fulfill({ json: [template] }))
  await page.route('**/api/v1/reports/library?*', (route) => route.fulfill({ json: {
    items: [], current_cursor: 'first', next_cursor: null, as_of: template.created_at,
  } }))
  await page.route('**/api/v1/reports/schedules', (route) => route.fulfill({ json: [schedule] }))
  await page.route('**/api/v1/reports/schedules/browser-schedule/run', (route) => {
    schedule = { ...schedule, resource_version: 'v2', custom_instructions: 'Another administrator changed this' }
    return route.fulfill({ json: [] })
  })
  await page.route('**/api/v1/reports/schedules/browser-schedule', async (route) => {
    const payload = route.request().postDataJSON()
    const version = route.request().headers()['if-match']
    writes.push({ version, instructions: payload.custom_instructions })
    await pendingSave
    if (version !== `"${schedule.resource_version}"`) {
      return route.fulfill({ status: 412, json: { detail: 'The report schedule changed after you loaded it.' } })
    }
    schedule = { ...schedule, ...payload, resource_version: 'v3' }
    await route.fulfill({ json: schedule })
  })
  try {
    await page.goto('/reporting')
    await page.getByRole('button', { name: 'Schedules', exact: true }).click()
    const row = page.locator('article').filter({ has: page.getByRole('heading', { name: 'Weekly report', exact: true }) })
    await row.getByRole('button', { name: 'Edit', exact: true }).click()
    const name = row.getByLabel('Name', { exact: true })
    await name.fill('My local draft')
    await row.getByRole('button', { name: 'Run now', exact: true }).click()
    await expect(row.getByText(/Your draft keeps its original version/)).toBeVisible()
    await row.getByRole('button', { name: 'Save schedule', exact: true }).click()
    await expect(name).toBeDisabled()
    await expect.poll(() => writes).toEqual([{ version: '"v1"', instructions: 'Original instructions' }])
    releaseSave()
    await expect(page.getByRole('alert').filter({ hasText: 'changed after you loaded it' })).toBeVisible()
    await expect(name).toBeEditable()
    await expect(name).toHaveValue('My local draft')
    await row.getByRole('button', { name: 'Cancel', exact: true }).click()
    await row.getByRole('button', { name: 'Edit', exact: true }).click()
    await row.getByRole('button', { name: 'Save schedule', exact: true }).click()
    await expect.poll(() => writes).toEqual([
      { version: '"v1"', instructions: 'Original instructions' },
      { version: '"v2"', instructions: 'Another administrator changed this' },
    ])
    await expect(row.getByRole('button', { name: 'Edit', exact: true })).toBeVisible()
  } finally { releaseSave() }
})


test('persists editorial review changes in both directions across reopening and reloading', async ({ page, api }) => {
  api.identity.role = 'admin'
  let schedule = {
    id: 'browser-schedule', owner_user_id: 'browser-analyst', template_id: template.id, name: 'Weekly report',
    enabled: true, cadence: 'weekly', day_of_week: 0, day_of_month: 1, hour: 9, minute: 0, timezone: 'UTC',
    window_type: 'previous_complete_week', rolling_days: 7, filters, custom_instructions: null,
    delivery_enabled: true, delivery_mode: 'summary', skip_empty: true, missed_run_policy: 'latest', review_required: false,
    next_run_at: '2026-09-14T09:00:00Z', last_run_at: null, resource_version: 'v1',
    created_at: template.created_at, updated_at: template.updated_at,
  }
  const writes: Array<{ review_required: boolean; version: string | undefined }> = []
  await page.route('**/api/v1/reports/capabilities', (route) => route.fulfill({ json: capabilities }))
  await page.route('**/api/v1/reports/templates', (route) => route.fulfill({ json: [template] }))
  await page.route('**/api/v1/reports/library?*', (route) => route.fulfill({ json: {
    items: [], current_cursor: 'first', next_cursor: null, as_of: template.created_at,
  } }))
  await page.route('**/api/v1/reports/schedules', (route) => route.fulfill({ json: [schedule] }))
  await page.route('**/api/v1/reports/schedules/browser-schedule', (route) => {
    const body = route.request().postDataJSON()
    const version = route.request().headers()['if-match']
    writes.push({ review_required: body.review_required, version })
    if (version !== `"${schedule.resource_version}"`) return route.fulfill({ status: 412, json: { detail: 'Schedule changed' } })
    schedule = { ...schedule, ...body, resource_version: `v${writes.length + 1}` }
    return route.fulfill({ json: schedule })
  })
  await page.goto('/reporting')
  await page.getByRole('button', { name: 'Schedules', exact: true }).click()
  const row = page.locator('article').filter({ has: page.getByRole('heading', { name: 'Weekly report', exact: true }) })
  const review = row.getByLabel('Require editorial review before publication', { exact: true })
  for (const required of [true, false]) {
    await row.getByRole('button', { name: 'Edit', exact: true }).click()
    await expect(review).toBeChecked({ checked: !required })
    await review.focus()
    await page.keyboard.press('Space')
    await row.getByRole('button', { name: 'Save schedule', exact: true }).click()
    await expect(row.getByRole('button', { name: 'Edit', exact: true })).toBeVisible()
    expect(schedule.review_required).toBe(required)
    await page.reload()
    await page.getByRole('button', { name: 'Schedules', exact: true }).click()
  }
  await row.getByRole('button', { name: 'Edit', exact: true }).click()
  await expect(review).not.toBeChecked()
  expect(writes).toEqual([
    { review_required: true, version: '"v1"' },
    { review_required: false, version: '"v2"' },
  ])
})
