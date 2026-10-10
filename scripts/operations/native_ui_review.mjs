import fs from 'node:fs/promises'
import path from 'node:path'
import crypto from 'node:crypto'
import { chromium, firefox, webkit } from '/workspace/node_modules/playwright/index.mjs'
import { expect } from '/workspace/node_modules/@playwright/test/index.mjs'
import AxeBuilder from '/workspace/node_modules/@axe-core/playwright/dist/index.mjs'

const privateRoot = process.env.REVIEW_ARTIFACT_ROOT ?? '/tmp/threatlens-premerge-native-review'
const config = JSON.parse(await fs.readFile(`${privateRoot}/config.json`, 'utf8'))
const stateRoot = '/review-state'
const settings = Object.fromEntries((await fs.readFile(`${stateRoot}/fixture.env`, 'utf8'))
  .split(/\r?\n/).filter(line => line && !line.startsWith('#') && line.includes('='))
  .map(line => { const separator = line.indexOf('='); return [line.slice(0, separator), line.slice(separator + 1)] }))
const seed = JSON.parse(await fs.readFile(`${stateRoot}/seed.json`, 'utf8'))
const selectedChecks = process.env.REVIEW_CHECKS?.split(',').map(value => value.trim()).filter(Boolean) ?? null
const expectedCheckCount = process.env.REVIEW_EXPECTED_CHECKS ? Number(process.env.REVIEW_EXPECTED_CHECKS) : (selectedChecks ? null : 234)
if (expectedCheckCount !== null && (!Number.isInteger(expectedCheckCount) || expectedCheckCount < 1)) throw new Error('Invalid REVIEW_EXPECTED_CHECKS')
const runId = crypto.randomUUID().slice(0, 8)
const output = `${privateRoot}/run-${runId}`
await fs.mkdir(output, { mode: 0o700 })
const secrets = Object.entries(settings).filter(([key, value]) => /PASSWORD|SECRET|ENCRYPTION_KEY/.test(key) && value.length > 7).map(([, value]) => value)
function sanitize(value) {
  let result = String(value)
  for (const secret of secrets) result = result.split(secret).join('[redacted]')
  return result.replace(/(Bearer\s+)[^\s"']+/gi, '$1[redacted]').replace(/([?&](?:token|code|password|secret)=)[^&\s]+/gi, '$1[redacted]')
}
const evidence = {
  version: config.version, sourceSha: process.env.REVIEW_SOURCE_SHA ?? 'not_supplied',
  startedAt: new Date().toISOString(), baseURL: config.baseURL,
  runtime: { node: process.version, playwright: '1.63.0', image: 'mcr.microsoft.com/playwright:v1.63.0-noble' },
  scope: config, selectedChecks, expectedCheckCount, steps: [], pageErrors: [], consoleErrors: [], apiServerErrors: [], blockedExternalRequests: [], createdSyntheticResources: [], cleanup: [], diagnostics: [], diagnosticsDropped: 0,
}
let page, context, engineName, themeName, layoutName, activeCheck = null
const diagnosticsStarted = performance.now()
const maxDiagnostics = 1000
function diagnosticURL(value) {
  if (!value) return ''
  try {
    const url = new URL(value, config.baseURL)
    if (!['http:', 'https:'].includes(url.protocol)) return url.protocol
    return sanitize(`${url.origin}${url.pathname}`).slice(0, 2048)
  } catch { return '[invalid-url]' }
}
function diagnosticText(value, limit = 4096) {
  return sanitize(value).replace(/\bhttps?:\/\/[^\s"'<>]+/g, diagnosticURL).slice(0, limit)
}
function diagnosticContext(created) {
  return { timestamp: new Date().toISOString(), elapsedMs: Math.round(performance.now() - diagnosticsStarted), engine: engineName, theme: themeName, layout: layoutName, activeCheck, path: sanitize(new URL(created.url()).pathname).slice(0, 2048) }
}
function recordDiagnostic(kind, details) {
  if (evidence.diagnostics.length >= maxDiagnostics) {
    evidence.diagnostics.shift()
    evidence.diagnosticsDropped += 1
  }
  evidence.diagnostics.push({ kind, ...details })
}
const slug = value => value.replace(/[^a-z0-9]+/gi, '-').replace(/^-|-$/g, '').slice(0, 110)
const stepKey = name => `${engineName}-${themeName}-${layoutName}-${slug(name)}`
async function screenshot(name) {
  const filename = `${stepKey(name)}.png`
  await page.screenshot({ path: `${output}/${filename}`, fullPage: true })
  return filename
}
async function persist() { await fs.writeFile(`${output}/results.json`, JSON.stringify(evidence, null, 2) + '\n') }
async function step(name, action) {
  if (selectedChecks && name !== 'real local cookie login' && !selectedChecks.some(pattern => name.includes(pattern))) return true
  const started = performance.now()
  const value = { name, engine: engineName, theme: themeName, layout: layoutName, startedAt: new Date().toISOString() }
  activeCheck = name
  console.log(JSON.stringify({ ...value, status: 'started' }))
  try {
    value.details = await action()
    value.status = 'passed'
  } catch (error) {
    value.status = 'failed'
    value.error = sanitize(error instanceof Error ? error.message : error)
    if (error?.reviewDetails) value.details = error.reviewDetails
    try { value.screenshot = await screenshot(`${name}-failure`) } catch {}
    try { await page.close(); page = await newReviewPage(context) } catch (recoveryError) { value.recoveryError = sanitize(recoveryError.message) }
  }
  value.elapsedSeconds = +((performance.now() - started) / 1000).toFixed(2)
  value.finishedAt = new Date().toISOString()
  evidence.steps.push(value)
  await persist()
  console.log(JSON.stringify({ name, engine: engineName, theme: themeName, layout: layoutName, status: value.status, elapsedSeconds: value.elapsedSeconds }))
  activeCheck = null
  return value.status === 'passed'
}
async function newReviewPage(browserContext) {
  const created = await browserContext.newPage()
  created.setDefaultTimeout(config.defaultTimeoutMs)
  created.on('pageerror', error => {
    const details = { ...diagnosticContext(created), message: sanitize(error.message), stack: diagnosticText(error.stack ?? '', 8192) }
    evidence.pageErrors.push(details)
    recordDiagnostic('pageerror', { ...details, message: diagnosticText(error.message) })
  })
  created.on('console', message => {
    if (message.type() !== 'error') return
    const location = message.location()
    const details = { ...diagnosticContext(created), message: sanitize(message.text()), location: { url: diagnosticURL(location.url), lineNumber: location.lineNumber, columnNumber: location.columnNumber } }
    evidence.consoleErrors.push(details)
    recordDiagnostic('console', { ...details, message: diagnosticText(message.text()) })
  })
  created.on('response', response => {
    const url = new URL(response.url()), request = response.request()
    if (url.pathname.startsWith('/api/') && response.status() >= 500) evidence.apiServerErrors.push({ ...diagnosticContext(created), path: url.pathname, status: response.status(), method: request.method() })
    if (url.origin === new URL(config.baseURL).origin && ['script', 'stylesheet', 'font', 'image'].includes(request.resourceType())) recordDiagnostic('static-response', { ...diagnosticContext(created), url: diagnosticURL(response.url()), status: response.status(), method: request.method(), resourceType: request.resourceType() })
  })
  created.on('requestfailed', request => recordDiagnostic('requestfailed', { ...diagnosticContext(created), url: diagnosticURL(request.url()), method: request.method(), resourceType: request.resourceType(), reason: diagnosticText(request.failure()?.errorText ?? '') }))
  return created
}

async function goto(route) {
  const response = await page.goto(`${config.baseURL}${route}`)
  expect(response?.status()).toBe(200)
  await expect(page.locator('main')).toBeVisible()
  await page.waitForLoadState('networkidle', { timeout: config.defaultTimeoutMs })
  const requestedPath = new URL(route, config.baseURL).pathname
  expect(new URL(page.url()).pathname).toBe(config.intentionalRedirects[requestedPath] ?? requestedPath)
  expect(await page.locator('body').innerText()).toContain(`v${config.version}`)
  await expect(page.getByRole('heading', { name: /^Page failed/ })).toHaveCount(0)
}
async function bounded(promise, label) {
  let timer
  try { return await Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(`${label} exceeded configured ${config.defaultTimeoutMs}ms driver bound`)), config.defaultTimeoutMs) })]) }
  finally { clearTimeout(timer) }
}
async function surface(name, includeAxe = true) {
  const overflow = await page.evaluate(() => ({
    viewport: innerWidth,
    width: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth),
    offenders: [...document.querySelectorAll('main *')].filter(element => {
      const rect = element.getBoundingClientRect(), style = getComputedStyle(element)
      return rect.width && style.visibility !== 'hidden' && style.display !== 'none' && rect.right > innerWidth + 2
    }).slice(0, 8).map(element => ({ tag: element.tagName, id: element.id, role: element.getAttribute('role') })),
  }))
  const builder = new AxeBuilder({ page }).withTags(config.axeTags)
  const publisherFrameExcluded = name === 'article-preview'
  if (publisherFrameExcluded) builder.exclude('aside[role="dialog"] iframe')
  const audit = includeAxe ? await bounded(builder.analyze(), `Accessibility analysis ${name}`) : { violations: [] }
  const violations = audit.violations.map(rule => ({ id: rule.id, impact: rule.impact, count: rule.nodes.length,
    targets: rule.nodes.map(node => node.target), summaries: rule.nodes.map(node => sanitize(node.failureSummary ?? '')) }))
  const result = { path: new URL(page.url()).pathname, overflow, axeViolations: violations, screenshot: await screenshot(name), axeScope: publisherFrameExcluded ? 'Application shell and drawer; scripts-disabled publisher iframe excluded from Axe traversal and checked for real content/title/sandbox separately.' : 'Rendered application page and frames' }
  if (overflow.width > overflow.viewport + 2 || violations.length) {
    const error = new Error(`Surface review: overflow=${overflow.width - overflow.viewport}px; accessibility violations=${violations.map(rule => rule.id).join(',')}`)
    error.reviewDetails = result
    throw error
  }
  return result
}
async function readableControl(control) {
  const result = await control.evaluate(element => {
    const style = getComputedStyle(element), canvas = document.createElement('canvas'), context = canvas.getContext('2d')
    const pixel = color => { context.clearRect(0, 0, 1, 1); context.fillStyle = color; context.fillRect(0, 0, 1, 1); return [...context.getImageData(0, 0, 1, 1).data] }
    const foreground = pixel(style.color), background = pixel(style.backgroundColor)
    const effective = background.slice(0, 3).map(value => value * background[3] / 255 + 255 * (1 - background[3] / 255))
    const luminance = channels => channels.slice(0, 3).reduce((total, value, index) => { const channel = value / 255; return total + (channel <= .04045 ? channel / 12.92 : ((channel + .055) / 1.055) ** 2.4) * [.2126, .7152, .0722][index] }, 0)
    const text = luminance(foreground), fill = luminance(effective)
    return { color: style.color, backgroundColor: style.backgroundColor, ratio: (Math.max(text, fill) + .05) / (Math.min(text, fill) + .05) }
  })
  expect(result.ratio).toBeGreaterThanOrEqual(4.5)
  return result
}
async function login() {
  await page.goto(`${config.baseURL}/login`)
  await page.getByLabel('Email', { exact: true }).fill(settings.ADMIN_EMAIL)
  await page.getByLabel('Password', { exact: true }).fill(settings.ADMIN_PASSWORD)
  await page.locator('form button[type=submit]').click()
  await page.waitForURL(url => url.pathname !== '/login' && url.pathname !== '/start')
  await page.waitForLoadState('networkidle', { timeout: config.defaultTimeoutMs })
  const dashboardTimeRange = page.getByRole('combobox', { name: 'Dashboard time range', exact: true })
  await expect(dashboardTimeRange).toBeVisible()
  await expect(dashboardTimeRange).toBeEnabled()
  await expect(page.getByRole('heading', { name: /^Page failed/ })).toHaveCount(0)
}
async function articleWorkflow() {
  await goto('/')
  const opener = page.getByRole('button', { name: 'Preview Original', exact: true }).first()
  await opener.click()
  const drawer = page.locator('aside[role=dialog]')
  await expect(drawer).toBeVisible()
  await expect(drawer.locator('iframe')).toBeVisible()
  const iframeTitle = await drawer.locator('iframe').getAttribute('title')
  expect(iframeTitle?.trim().length).toBeGreaterThan(0)
  expect(await drawer.locator('iframe').getAttribute('sandbox')).toBe('allow-popups allow-popups-to-escape-sandbox')
  await expect(drawer.frameLocator('iframe').locator('body')).toContainText('Synthetic defensive intelligence')
  console.log(JSON.stringify({ engine: engineName, theme: themeName, name: 'article preview source content', status: 'confirmed' }))
  await expect(drawer.getByRole('button', { name: 'Close original article preview' })).toBeFocused()
  const details = await surface('article-preview')
  await page.keyboard.press('Escape')
  await expect(drawer).toHaveCount(0)
  await expect(opener).toBeFocused()
  const articleDetails = page.getByRole('button', { name: /^Open article details:/ }).first()
  await articleDetails.click()
  const assessment = page.getByRole('region', { name: 'Team assessment', exact: true })
  await expect(assessment).toBeVisible()
  await assessment.getByRole('combobox', { name: 'Assessment team', exact: true }).selectOption(seed.team_id)
  await expect(assessment.getByText('View team AI context', { exact: true })).toBeVisible()
  const keyboardReaders = []
  for (const name of ['RSS summary text', 'Full article text']) {
    const reader = page.getByRole('region', { name: new RegExp(`^${name} for `) })
    await expect(reader).toBeVisible()
    await reader.focus()
    await page.keyboard.press('Shift+Tab')
    await page.keyboard.press('Tab')
    await expect(reader).toBeFocused()
    await page.keyboard.press('Home')
    const before = await reader.evaluate(element => ({ top: element.scrollTop, height: element.scrollHeight, viewport: element.clientHeight }))
    if (before.height > before.viewport + 1) {
      await page.keyboard.press('End')
      await expect.poll(() => reader.evaluate(element => element.scrollTop)).toBeGreaterThan(before.top)
    }
    keyboardReaders.push({ name: await reader.getAttribute('aria-label'), keyboardTabFocusConfirmed: true, scrollable: before.height > before.viewport + 1, scrollBefore: before.top, scrollAfter: await reader.evaluate(element => element.scrollTop), scrollHeight: before.height, clientHeight: before.viewport })
  }
  return { keyboardReaders, iframeTitle, realPublisherTextConfirmed: true, sandboxScriptsDisabled: true, preview: details, assessment: await surface('article-team-assessment'), externalProviderNotInvoked: true }
}
async function teamDraftWorkflow() {
  await goto(`/teams?mode=admin&team=${seed.team_id}`)
  const name = page.getByLabel('Name', { exact: true })
  const original = await name.inputValue()
  await name.fill(`${original} unsaved review`)
  await page.getByRole('link', { name: 'My teams', exact: true }).click()
  const discard = page.getByRole('alertdialog', { name: 'Discard unsaved changes?', exact: true })
  await expect(discard).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(discard).toBeHidden()
  await expect(name).toHaveValue(`${original} unsaved review`)
  await name.focus()
  await page.keyboard.press('Tab')
  await expect(page.getByRole('textbox', { name: 'Description', exact: true })).toBeFocused()
  const details = await surface('team-draft-preservation')
  await page.getByRole('link', { name: 'My teams', exact: true }).click()
  await discard.getByRole('button', { name: 'Discard changes', exact: true }).click()
  await expect(page).toHaveURL(`${config.baseURL}/teams`)
  return details
}
async function mobileAccessWorkflow() {
  await goto('/settings/access')
  const region = page.getByRole('region', { name: 'Items needing attention', exact: true })
  const wrapper = region.getByRole('table').locator('..')
  const before = await wrapper.evaluate(element => ({ left: element.scrollLeft, width: element.clientWidth, scrollWidth: element.scrollWidth }))
  expect(before.scrollWidth).toBeGreaterThan(before.width)
  await wrapper.focus()
  await page.keyboard.press('Shift+Tab')
  await page.keyboard.press('Tab')
  await expect(wrapper).toBeFocused()
  await page.keyboard.press('ArrowRight')
  await expect.poll(() => wrapper.evaluate(element => element.scrollLeft)).toBeGreaterThan(before.left)
  return { keyboardHorizontalScroll: { keyboardTabFocusConfirmed: true, clientWidth: before.width, scrollWidth: before.scrollWidth, before: before.left, after: await wrapper.evaluate(element => element.scrollLeft) }, ...await surface('mobile route /settings/access') }
}
async function investigationWorkflow() {
  await goto('/investigations')
  await page.getByRole('button', { name: 'Create investigation', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'Create investigation', exact: true })
  const title = `Native review ${engineName} ${runId}`
  await dialog.getByLabel('Title', { exact: true }).fill(title)
  await dialog.getByLabel('Description', { exact: true }).fill('Synthetic pre-merge browser workflow; no production data.')
  await dialog.getByLabel('Title', { exact: true }).focus()
  await page.keyboard.press('Tab')
  await expect(dialog.getByLabel('Description', { exact: true })).toBeFocused()
  const creating = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/investigations' && response.request().method() === 'POST')
  await dialog.getByRole('button', { name: 'Create', exact: true }).click()
  const response = await creating
  expect(response.status()).toBe(201)
  const investigation = await response.json()
  evidence.createdSyntheticResources.push({ type: 'investigation', id: investigation.id, name: title })
  await page.waitForURL(`**/investigations/${investigation.id}**`)
  await page.getByRole('button', { name: /^Notes \(/ }).click()
  const note = `Synthetic note ${engineName} ${runId}`
  await page.getByLabel('Add note', { exact: true }).fill(note)
  await page.getByRole('button', { name: 'Add note', exact: true }).click()
  await expect(page.getByText(note, { exact: true })).toBeVisible()
  const details = await surface('investigation-notes')
  await page.reload()
  await expect(page.getByText(note, { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Close investigation', exact: true }).click()
  await page.getByRole('alertdialog', { name: 'Close investigation?', exact: true }).getByRole('button', { name: 'Close investigation', exact: true }).click()
  await page.getByRole('button', { name: 'Archive', exact: true }).click()
  const archiveDialog = page.getByRole('alertdialog', { name: 'Archive investigation?', exact: true })
  const archiving = page.waitForResponse(response => new URL(response.url()).pathname === `/api/v1/investigations/${investigation.id}` && response.request().method() === 'PATCH' && response.request().postDataJSON()?.status === 'archived')
  await archiveDialog.getByRole('button', { name: 'Archive investigation', exact: true }).click()
  const archived = await archiving
  expect(archived.status()).toBe(200)
  expect((await archived.json()).status).toBe('archived')
  await expect(archiveDialog).toBeHidden()
  await expect(page.getByText('Archived', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Reopen investigation', exact: true })).toBeVisible()
  evidence.cleanup.push({ type: 'investigation', id: investigation.id, archivedThroughUI: true })
  return { id: investigation.id, persistedAfterReload: true, archivedThroughUI: true, ...details }
}
async function exportWorkflow() {
  await goto('/export')
  const prefix = `native-review-${engineName}-${runId}`
  await page.getByLabel('Filename prefix', { exact: true }).fill(prefix)
  const queued = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/exports/jobs' && response.request().method() === 'POST')
  await page.getByRole('button', { name: 'Generate in background', exact: true }).click()
  const response = await queued
  expect(response.status()).toBe(202)
  const job = await response.json()
  evidence.createdSyntheticResources.push({ type: 'export_job', id: job.id, name: prefix })
  const jobs = page.getByRole('region', { name: 'Background exports', exact: true })
  const card = jobs.locator('li').filter({ has: page.getByRole('heading', { name: new RegExp(prefix) }) })
  await expect(card.getByRole('button', { name: 'Download export', exact: true })).toBeVisible({ timeout: config.defaultTimeoutMs })
  const downloadEvent = page.waitForEvent('download')
  await card.getByRole('button', { name: 'Download export', exact: true }).click()
  const download = await downloadEvent
  const filename = `${output}/${slug(download.suggestedFilename())}`
  await download.saveAs(filename)
  const bytes = await fs.readFile(filename)
  expect(bytes.length).toBeGreaterThan(100)
  const details = { id: job.id, downloadBytes: bytes.length, downloadSha256: crypto.createHash('sha256').update(bytes).digest('hex'), screenshot: await screenshot('background-export-ready') }
  await card.getByRole('button', { name: 'Delete export', exact: true }).click()
  await expect(card.getByRole('button', { name: 'Download export', exact: true })).toHaveCount(0)
  evidence.cleanup.push({ type: 'export_job', id: job.id, deletedThroughUI: true })
  return details
}
async function publicationWorkflow() {
  await goto('/export')
  await page.getByRole('button', { name: /^Reviewed team publications/ }).click()
  await page.getByRole('combobox', { name: 'Publication team', exact: true }).selectOption(seed.team_id)
  await page.getByRole('button', { name: 'Preview reviewed indicators', exact: true }).click()
  await expect(page.getByText(/reviewed indicators in \d+ articles;/)).toBeVisible()
  await page.getByRole('button', { name: 'Publication consumers', exact: true }).click()
  return { consumerSelect: await readableControl(page.getByRole('combobox', { name: 'Consumer', exact: true })), publicationSelect: await readableControl(page.getByRole('combobox', { name: 'Publication', exact: true })), ...await surface('reviewed-publication-consumers'), noPublicationApproved: true }
}
async function providerDraftWorkflow() {
  await goto('/settings/ai')
  const configuration = page.getByRole('tab', { name: 'Configuration', exact: true })
  await configuration.click()
  const providers = page.locator('#ai-provider-settings')
  if (!await providers.evaluate(element => element.open)) {
    await providers.locator(':scope > summary').focus()
    await page.keyboard.press('Enter')
  }
  await providers.getByRole('button', { name: 'Add provider', exact: true }).click()
  const name = page.getByRole('textbox', { name: 'Provider name', exact: true })
  await name.fill(`Unsaved native review ${runId}`)
  await page.getByRole('tab', { name: 'Jobs', exact: true }).click()
  await configuration.click()
  await expect(name).toHaveValue(`Unsaved native review ${runId}`)
  const details = await surface('provider-draft-preservation')
  await page.getByRole('link', { name: 'My account', exact: true }).click()
  const discard = page.getByRole('alertdialog', { name: 'Discard unsaved changes?', exact: true })
  await expect(discard).toBeVisible()
  await discard.getByRole('button', { name: 'Cancel', exact: true }).click()
  await expect(name).toHaveValue(`Unsaved native review ${runId}`)
  await page.getByRole('link', { name: 'My account', exact: true }).click()
  await discard.getByRole('button', { name: 'Discard changes', exact: true }).click()
  await expect(page).toHaveURL(`${config.baseURL}/settings/account`)
  await expect(page.getByRole('heading', { name: 'My account', exact: true })).toBeVisible()
  return { ...details, providerSaved: false, providerTested: false, qualificationExecuted: false }
}

async function mcpConsentWorkflow() {
  await goto(`/mcp/authorize?${new URLSearchParams(seed.consent_request)}`)
  const password = page.getByLabel('Current password (local accounts)')
  await expect(password).toBeVisible()
  return { password: await readableControl(password), mfa: await readableControl(page.getByLabel('MFA code (if enabled)')), ...await surface('mcp-consent'), consentNotGranted: true }
}

for (const [engine, browserType] of Object.entries({ chromium, firefox, webkit })) {
  engineName = engine
  const browser = await browserType.launch()
  evidence.runtime.engineVersions ??= {}
  evidence.runtime.engineVersions[engine] = browser.version()
  try {
    for (const theme of config.themes) {
      themeName = theme; layoutName = 'desktop'
      context = await browser.newContext({ viewport: config.desktop, acceptDownloads: true })
      try {
        await context.addInitScript(({ theme, appOrigin }) => { if (window === window.top && location.origin === appOrigin) localStorage.setItem('threatlens.theme', theme) }, { theme, appOrigin: new URL(config.baseURL).origin })
        const local = new URL(config.baseURL)
        await context.route('**/*', route => {
          const url = new URL(route.request().url())
          if (url.origin === local.origin || url.origin === config.ownedFixtureSourceOrigin || url.protocol === 'data:' || url.protocol === 'blob:') return route.continue()
          evidence.blockedExternalRequests.push({ engine, theme, hostname: url.hostname, resourceType: route.request().resourceType() })
          return route.abort('blockedbyclient')
        })
        await context.routeWebSocket('**/*', socket => {
          const url = new URL(socket.url())
          if (url.hostname === local.hostname && url.port === local.port) return socket.connectToServer()
          evidence.blockedExternalRequests.push({ engine, theme, hostname: url.hostname, resourceType: 'websocket' })
          socket.close()
        })
        page = await newReviewPage(context)
        if (!await step('real local cookie login', login)) continue
        for (const route of config.routes) await step(`route ${route}`, async () => { await goto(route); return surface(`route ${route}`) })
        for (const route of Object.keys(config.intentionalRedirects)) await step(`intentional redirect ${route}`, async () => { await goto(route); return { actualPath: new URL(page.url()).pathname, expectedPath: config.intentionalRedirects[route] } })
        if (theme === 'light' && selectedChecks?.some(pattern => 'article preview and team assessment navigation'.includes(pattern))) await step('article preview and team assessment navigation', articleWorkflow)
        if (theme === 'light' && selectedChecks?.some(pattern => 'reviewed publication preview and consumer contrast'.includes(pattern))) await step('reviewed publication preview and consumer contrast', publicationWorkflow)
        if (theme === 'dark') {
          await step('article preview and team assessment navigation', articleWorkflow)
          await step('team draft and keyboard navigation', teamDraftWorkflow)
          await step('synthetic investigation create and note persistence', investigationWorkflow)
          await step('real background export worker download delete', exportWorkflow)
          await step('reviewed publication preview and consumer contrast', publicationWorkflow)
          await step('real MCP consent preview', mcpConsentWorkflow)
          await step('provider draft tab retention and discard navigation', providerDraftWorkflow)
          for (const panel of ['hunts', 'ai-context', 'ai-governance', 'indicator-suppressions', 'integrations']) await step(`team panel ${panel}`, async () => { await goto(`/teams?team=${seed.team_id}&panel=${panel}`); const region = { hunts: 'Team hunt queue', 'ai-context': 'Team AI context', 'ai-governance': 'Team AI destinations', 'indicator-suppressions': 'Team indicator suppressions', integrations: 'Team integrations' }[panel]; await expect(page.getByRole('region', { name: region, exact: true })).toBeVisible(); return surface(`team panel ${panel}`) })
        }
        layoutName = 'mobile'
        await page.setViewportSize(config.mobile)
        if (selectedChecks?.some(pattern => pattern.includes('/settings/access'))) await step('mobile route /settings/access', mobileAccessWorkflow)
        for (const route of config.mobileRoutes) await step(`mobile route ${route}`, async () => { await goto(route); return surface(`mobile route ${route}`) })
        await step('mobile existing investigation', async () => { await goto(`/investigations/${seed.investigation_id}`); return surface('mobile-existing-investigation') })
      } finally {
        for (const resource of evidence.createdSyntheticResources.filter(resource => resource.type === 'export_job' && !evidence.cleanup.some(entry => entry.type === resource.type && entry.id === resource.id))) {
          try {
            const csrf = (await context.cookies(config.baseURL)).find(cookie => cookie.name === 'threatlens_csrf')?.value
            const response = await context.request.post(`${config.baseURL}/api/v1/exports/jobs/${resource.id}/cancel`, { headers: csrf ? { 'x-csrf-token': csrf } : {} })
            evidence.cleanup.push({ type: resource.type, id: resource.id, canceledOwnedResourceAfterFailedUI: true, status: response.status() })
          } catch (error) { evidence.cleanup.push({ type: resource.type, id: resource.id, error: sanitize(error.message) }) }
        }
        for (const resource of evidence.createdSyntheticResources.filter(resource => resource.type === 'investigation' && !evidence.cleanup.some(entry => entry.type === resource.type && entry.id === resource.id))) {
          try {
            const csrf = (await context.cookies(config.baseURL)).find(cookie => cookie.name === 'threatlens_csrf')?.value
            const headers = csrf ? { 'x-csrf-token': csrf } : {}
            const endpoint = `${config.baseURL}/api/v1/investigations/${resource.id}`
            const response = await context.request.get(endpoint)
            if (response.status() !== 200) throw new Error(`Owned investigation cleanup GET status ${response.status()}`)
            let current = await response.json()
            for (const status of ['closed', 'archived']) {
              if (current.status === 'archived' || current.status === status) continue
              const changed = await context.request.patch(endpoint, { headers, data: { expected_version: current.version, status } })
              if (changed.status() !== 200) throw new Error(`Owned investigation cleanup ${status} PATCH status ${changed.status()}`)
              current = await changed.json()
            }
            expect(current.status).toBe('archived')
            evidence.cleanup.push({ type: resource.type, id: resource.id, archivedOwnedResourceAfterFailedUI: true, status: current.status })
          } catch (error) { evidence.cleanup.push({ type: resource.type, id: resource.id, error: sanitize(error.message) }) }
        }
        await persist()
        await context.close()
      }
    }
  } finally { await browser.close() }
}
evidence.finishedAt = new Date().toISOString()
evidence.summary = { executedChecks: evidence.steps.length, expectedCheckCount, expectedCountMatched: expectedCheckCount === null || evidence.steps.length === expectedCheckCount, passed: evidence.steps.filter(step => step.status === 'passed').length, failed: evidence.steps.filter(step => step.status === 'failed').length, pageErrors: evidence.pageErrors.length, consoleErrors: evidence.consoleErrors.length, apiServerErrors: evidence.apiServerErrors.length, blockedExternalRequests: evidence.blockedExternalRequests.length, createdSyntheticResources: evidence.createdSyntheticResources.length, cleanupFailures: evidence.cleanup.filter(entry => entry.error || entry.status >= 400).length }
evidence.status = !evidence.summary.expectedCountMatched || evidence.summary.failed || evidence.summary.pageErrors || evidence.summary.consoleErrors || evidence.summary.apiServerErrors || evidence.summary.cleanupFailures ? 'failed' : 'passed'
await persist()
console.log(JSON.stringify({ result: `${output}/results.json`, status: evidence.status, summary: evidence.summary }))
process.exitCode = evidence.status === 'passed' ? 0 : 1
