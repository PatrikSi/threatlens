import AxeBuilder from '@axe-core/playwright'
import { test, expect, feed, revalidateSession } from './fixtures'
import { assessmentFixture, contextFixture, extractionFixture } from '../tests/fixtures/articleIntelligence'

const item = {
  id: 'item-1', feed_id: feed.id, feed_name: feed.name, title: 'Service behavior article',
  url: 'https://publisher.example.test/article', canonical_url: null, summary: 'A new service was observed.',
  published_at: '2026-09-16T10:00:00Z', first_seen_at: '2026-09-16T10:00:00Z',
  status: 'content_fetched', classification: null, is_read: true, is_starred: false, tags: [],
  ai_relevance_score: null, ai_relevance_label: null, ai_status: 'ready',
}

test('reviews evidence and hunt cards, retains drafts during outages and creates reviewed investigation work', async ({ page, api }, info) => {
  api.identity.features = { ...api.identity.features, ai_enabled: true, ai_configured: true }
  let data = structuredClone(assessmentFixture)
  const writes: Record<string, unknown>[] = []
  await page.route('**/api/v1/items?*', (route) => route.fulfill({ json: { items: [item], total: 1, page: 1, page_size: 100 } }))
  await page.route('**/api/v1/items/item-1', (route) => route.fulfill({ json: {
    ...item, source_guid: null, last_error: null, state: { is_read: true, is_starred: false, note: null, updated_at: null }, article: null,
    ai_insight: { status: 'ready', summary_text: null, relevance_score: null, relevance_label: null, relevance_reasons: [], model: 'Fixture model', generated_at: null, error: null, structured_extraction: extractionFixture, structured_extraction_stale: false },
  } }))
  await page.route('**/api/v1/teams?*', (route) => route.fulfill({ json: { items: [{ id: 'team-1', name: 'Endpoint team' }], total: 1, page: 1, page_size: 50 } }))
  await page.route('**/api/v1/items/item-1/team-assessment**', async (route) => {
    if (route.request().method() !== 'GET') {
      const body = route.request().postDataJSON() as Record<string, unknown>
      writes.push(body)
      expect(body.expected_version).toBe(data.assessment!.version)
      data = structuredClone(data)
      data.assessment!.version += 1
      const hunt = data.assessment!.result!.hunts[0]
      if (route.request().method() === 'PATCH') {
        hunt.review_status = 'accepted'
        hunt.review_note = String(body.note)
      } else hunt.investigation_id = 'investigation-1'
    }
    return route.fulfill({ json: data })
  })
  await page.goto('/?assessment_team=team-1')
  const article = page.locator('article.tl-dashboard-rss-card').filter({ hasText: item.title })
  await article.locator('button.tl-dashboard-rss-toggle').click()
  const evidence = page.getByRole('region', { name: 'Shared article evidence', exact: true })
  await expect(evidence.getByText('Reported by source', { exact: false }).first()).toBeVisible()
  await evidence.getByText('Supporting passages (1)').first().click()
  await expect(evidence.getByText('<script>not executable</script>', { exact: true })).toBeVisible()
  const assessment = page.getByRole('region', { name: 'Team assessment', exact: true })
  await expect(assessment.getByLabel('Assessment team')).toHaveValue('team-1')
  await expect(assessment.getByRole('button', { name: 'Create team investigation', exact: true })).toHaveCount(0)
  const note = page.locator('[aria-label="Team assessment"] textarea')
  await note.fill('Reviewed the available process logs.')
  api.sessionStatus = 503
  await revalidateSession(page)
  await expect(page.getByRole('dialog', { name: 'Session check unavailable' })).toBeVisible()
  await expect(note).toHaveValue('Reviewed the available process logs.')
  await expect(note).toBeDisabled()
  api.sessionStatus = 200
  await revalidateSession(page)
  await expect(note).toBeEnabled()
  await article.locator('button.tl-dashboard-rss-toggle').click()
  await article.locator('button.tl-dashboard-rss-toggle').click()
  await expect(note).toHaveValue('Reviewed the available process logs.')
  const accept = assessment.getByRole('button', { name: 'Accept suggestion', exact: true })
  await accept.focus()
  await page.keyboard.press('Enter')
  await expect(assessment.getByRole('button', { name: 'Create team investigation', exact: true })).toBeEnabled()
  expect(writes[0]).toMatchObject({ team_id: 'team-1', expected_version: 1, status: 'accepted', note: 'Reviewed the available process logs.' })
  await assessment.getByRole('button', { name: 'Create team investigation', exact: true }).click()
  await expect(assessment.getByRole('link', { name: 'Open investigation', exact: true })).toHaveAttribute('href', '/investigations/investigation-1')
  const accessibility = await new AxeBuilder({ page }).include('[aria-label="Team assessment"]').withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()
  await info.attach('axe-team-assessment', { body: JSON.stringify(accessibility, null, 2), contentType: 'application/json' })
  expect(accessibility.violations).toEqual([])
})


test('edits a team AI profile with one navigation guard and a stable save version', async ({ page }, info) => {
  let context = structuredClone(contextFixture)
  const team = { id: 'team-1', key: 'endpoint', name: 'Endpoint team', description: 'Endpoint monitoring', membership_group_id: 'members', manager_group_id: 'managers', active: true, revision: 1, can_manage: true, created_at: '2026-09-16T10:00:00Z', updated_at: '2026-09-16T10:00:00Z' }
  await page.route('**/api/v1/teams?*', (route) => route.fulfill({ json: { items: [team], total: 1, page: 1, page_size: 25 } }))
  await page.route('**/api/v1/teams/team-1', (route) => route.fulfill({ json: team }))
  await page.route('**/api/v1/teams/team-1/members?*', (route) => route.fulfill({ json: { items: [], total: 0, page: 1, page_size: 50 } }))
  await page.route('**/api/v1/iam/groups', (route) => route.fulfill({ json: [] }))
  await page.route('**/api/v1/teams/team-1/ai-context', (route) => {
    if (route.request().method() === 'PATCH') {
      const body = route.request().postDataJSON()
      expect(body.expected_version).toBe(context.version)
      context = { ...context, ...body, version: context.version + 1 }
    }
    return route.fulfill({ json: context })
  })
  await page.goto('/teams?team=team-1&panel=ai-context')
  await page.getByLabel('Technology stack').fill('Linux\nKubernetes')
  await page.getByRole('link', { name: 'Team details', exact: true }).click()
  const discard = page.getByRole('alertdialog', { name: 'Discard unsaved changes?', exact: true })
  await expect(discard).toContainText('Discard unsaved team AI context?')
  await discard.getByRole('button', { name: 'Cancel', exact: true }).click()
  await expect(page.getByLabel('Technology stack')).toHaveValue('Linux\nKubernetes')
  await page.getByRole('button', { name: 'Save AI context', exact: true }).click()
  await expect(page.getByText('Team AI context saved.', { exact: false })).toBeVisible()
  expect(context.technology_stack).toEqual(['Linux', 'Kubernetes'])
  const accessibility = await new AxeBuilder({ page }).include('[aria-label="Team AI context"]').withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()
  await info.attach('axe-team-ai-context', { body: JSON.stringify(accessibility, null, 2), contentType: 'application/json' })
  expect(accessibility.violations).toEqual([])
  await page.getByRole('link', { name: 'Team details', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Team settings', exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Team AI context', exact: true })).toHaveCount(0)
})
