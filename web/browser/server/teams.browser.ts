import AxeBuilder from '@axe-core/playwright'
import { test, expect, control, signIn } from './fixtures'
import { writeApi } from './enterprise-helpers'

type Group = { id: string; revision: number }
test.use({ actionTimeout: 10_000 })

test('real named team creation and group-backed access withdrawal', async ({ page, request, identity }, info) => {
  test.setTimeout(90_000)
  const member = await control(request, 'users', { role: 'analyst' })
  const suffix = member.id.slice(0, 8)
  await signIn(page, identity)
  const members = await writeApi<Group>(page, '/iam/groups', 'POST', { key: `members-${suffix}`, name: `Members ${suffix}` })
  const managers = await writeApi<Group>(page, '/iam/groups', 'POST', { key: `managers-${suffix}`, name: `Managers ${suffix}` })
  const membership = await writeApi<{ id: string }>(page, `/iam/groups/${members.id}/members`, 'POST', { user_id: member.id, expected_group_revision: members.revision })
  await writeApi(page, `/iam/groups/${managers.id}/members`, 'POST', { user_id: identity.id, expected_group_revision: managers.revision })
  await page.goto('/teams?mode=admin&create=1')
  await page.getByLabel('Team name', { exact: true }).fill(`Browser team ${suffix}`)
  await page.getByLabel('Stable key', { exact: true }).fill(`browser-team-${suffix}`)
  await page.getByRole('combobox', { name: 'Member group', exact: true }).selectOption(members.id)
  await page.getByRole('combobox', { name: 'Manager group', exact: true }).selectOption(managers.id)
  const created = page.waitForResponse((response) => response.url().endsWith('/teams') && response.request().method() === 'POST')
  await page.getByRole('button', { name: 'Create team', exact: true }).click()
  const response = await created
  expect(response.status()).toBe(201)
  const team = await response.json()
  await expect(page).toHaveURL(new RegExp(`team=${team.id}`))
  await expect(page.getByRole('region', { name: 'Team settings', exact: true })).toBeVisible()
  await expect(page.getByLabel('Name', { exact: true })).toHaveValue(`Browser team ${suffix}`)
  const accessibility = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze()
  await info.attach('axe-named-team', { body: JSON.stringify(accessibility, null, 2), contentType: 'application/json' })
  expect(accessibility.violations.map(({ id, nodes }) => ({ id, targets: nodes.map((node) => node.target) }))).toEqual([])
  await signIn(page, member)
  await page.goto(`/teams?team=${team.id}`)
  await expect(page.getByRole('heading', { name: `Browser team ${suffix}`, exact: true })).toBeVisible()
  expect((await page.request.get(`/api/v1/teams/${team.id}`)).status()).toBe(200)
  const ruleName = `Browser shared watch ${suffix}`
  await writeApi(page, '/alerts', 'POST', {
    team_id: team.id, name: ruleName, category: 'other', keywords: ['browser'],
    due_after_minutes: 60, escalation_after_minutes: 15,
  })
  const article = await control(request, 'export-item')
  expect(await control(request, `evaluate-alerts/${article.id}`)).toMatchObject({ occurrences: 1, events: 0 })
  await page.goto(`/alerts?view=occurrences&team_id=${team.id}&queue_scope=team`)
  await page.getByRole('button', { name: `Inspect occurrence from ${ruleName}`, exact: true }).click()
  const triage = page.getByRole('region', { name: 'Team assignment and deadline', exact: true })
  await expect(triage).toContainText('Unassigned')
  await expect(triage.getByRole('button', { name: 'Change assignee', exact: true })).toHaveCount(0)
  await triage.getByRole('button', { name: 'Claim occurrence', exact: true }).click()
  await expect(triage).toContainText('Assigned to you')
  const triageUrl = page.url()
  const occurrenceId = new URL(triageUrl).searchParams.get('occurrence')
  await page.goto('/feeds')
  await page.goto(triageUrl)
  await expect(triage).toContainText('Assigned to you')
  await page.getByRole('combobox', { name: 'Assignment', exact: true }).selectOption('mine')
  await expect(page).toHaveURL(new RegExp(`assignee_user_id=${member.id}`))
  const triageAccessibility = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze()
  await info.attach('axe-team-triage', { body: JSON.stringify(triageAccessibility, null, 2), contentType: 'application/json' })
  expect(triageAccessibility.violations.map(({ id, nodes }) => ({ id, targets: nodes.map((node) => node.target) }))).toEqual([])
  await signIn(page, identity)
  const groups = await (await page.request.get('/api/v1/iam/groups')).json() as Group[]
  const revision = groups.find((group) => group.id === members.id)!.revision
  await writeApi(page, `/iam/groups/${members.id}/members/${membership.id}?expected_group_revision=${revision}`, 'DELETE')
  await signIn(page, member)
  await page.goto(`/teams?team=${team.id}`)
  await expect(page.getByRole('heading', { name: `Browser team ${suffix}`, exact: true })).not.toBeVisible()
  expect((await page.request.get(`/api/v1/teams/${team.id}`)).status()).toBe(404)
  expect(occurrenceId).toBeTruthy()
  expect((await page.request.get(`/api/v1/alerts/occurrences/${occurrenceId}`)).status()).toBe(404)
})
