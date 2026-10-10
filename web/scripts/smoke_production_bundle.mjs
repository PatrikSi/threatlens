import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { dirname, resolve, sep } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

import { JSDOM } from 'jsdom'

const scriptDirectory = dirname(fileURLToPath(import.meta.url))
const distDirectory = resolve(scriptDirectory, '..', 'dist')
const indexPath = resolve(distDirectory, 'index.html')

let dom

class NoopBroadcastChannel {
  addEventListener() {}

  removeEventListener() {}

  postMessage() {}

  close() {}
}

try {
  const indexHtml = await readFile(indexPath, 'utf8')
  dom = new JSDOM(indexHtml, {
    pretendToBeVisual: true,
    url: 'http://localhost:3000/login',
  })

  installBrowserGlobals(dom.window)

  const requestedPaths = []
  const unexpectedRequests = []
  const fetchStub = async (input, init = {}) => {
    const requestUrl = resolveRequestUrl(input, dom.window.location.href)
    const method = resolveRequestMethod(input, init)
    const request = `${method} ${requestUrl.pathname}`
    requestedPaths.push(request)

    if (method === 'GET' && requestUrl.pathname === '/api/v1/auth/registration-settings') {
      return jsonResponse({ allow_self_registration: false, ai_enabled: false })
    }
    if (method === 'GET' && requestUrl.pathname === '/api/v1/auth/oidc/settings') {
      return jsonResponse({ enabled: false, provider_name: null })
    }

    unexpectedRequests.push(request)
    throw new Error(`Production bundle attempted an unexpected request: ${request}`)
  }
  globalThis.fetch = fetchStub
  dom.window.fetch = fetchStub

  const entrySource = dom.window.document
    .querySelector('script[type="module"][src]')
    ?.getAttribute('src')
  assert.ok(entrySource, 'dist/index.html does not reference a module entry')

  const entryPath = resolve(distDirectory, entrySource.replace(/^\/+/, ''))
  assert.ok(
    entryPath.startsWith(`${distDirectory}${sep}`),
    `Production module entry escapes dist/: ${entrySource}`,
  )

  await import(pathToFileURL(entryPath).href)
  await waitFor(() => {
    const heading = Array.from(dom.window.document.querySelectorAll('h1, h2'))
      .find((element) => element.textContent?.trim() === 'Analyst Login')
    return Boolean(
      heading
      && dom.window.document.querySelector('#login-email')
      && dom.window.document.querySelector('#login-password')
      && requestedPaths.includes('GET /api/v1/auth/registration-settings')
      && requestedPaths.includes('GET /api/v1/auth/oidc/settings'),
    )
  })

  assert.deepEqual(
    unexpectedRequests,
    [],
    `Production bundle made unexpected requests: ${unexpectedRequests.join(', ')}`,
  )
  assert.equal(
    dom.window.document.querySelector('h2')?.textContent?.trim(),
    'Analyst Login',
    'Production login heading did not render',
  )
  const emailInput = dom.window.document.querySelector('#login-email')
  const passwordInput = dom.window.document.querySelector('#login-password')
  assert.equal(emailInput?.getAttribute('type'), 'text', 'Production login email text field did not render')
  assert.equal(emailInput.getAttribute('inputmode'), 'email', 'Production login email keyboard hint is missing')
  assert.equal(emailInput.getAttribute('autocomplete'), 'email', 'Production login email autocomplete is missing')
  assert.equal(emailInput.required, true, 'Production login email field must remain required')
  assert.ok(
    Array.from(emailInput.labels ?? []).some((label) => label.textContent?.trim() === 'Email'),
    'Production login email field is missing its associated Email label',
  )
  assert.equal(
    passwordInput?.getAttribute('type'),
    'password',
    'Production login password field did not render',
  )
  assert.equal(passwordInput.required, true, 'Production login password field must remain required')

  const loginForm = emailInput.form
  assert.ok(loginForm, 'Production login email field is not associated with a form')
  assert.equal(passwordInput.form, loginForm, 'Production login credentials must share the same form')
  const initialEmail = emailInput.value
  const initialPassword = passwordInput.value
  try {
    passwordInput.value = 'production-smoke-control'
    for (const [category, email] of [
      ['Unicode local part', 'revi\u00e9w@example.com'],
      ['Unicode domain', 'review@\u00e9xample.com'],
      ['Unicode astral local part', '\u{1f512}@example.com'],
    ]) {
      emailInput.value = email
      assert.equal(loginForm.checkValidity(), true, `Production login form blocks an API-supported ${category}`)
    }
    emailInput.value = ''
    assert.equal(loginForm.checkValidity(), false, 'Production login form must block an empty email')
    emailInput.value = 'review@example.com'
    passwordInput.value = ''
    assert.equal(loginForm.checkValidity(), false, 'Production login form must block an empty password')
  } finally {
    emailInput.value = initialEmail
    passwordInput.value = initialPassword
  }

  console.log('Production bundle rendered the login screen successfully.')
} catch (error) {
  console.error('Production bundle smoke test failed.')
  console.error(error instanceof Error ? error.stack : error)
  process.exitCode = 1
} finally {
  dom?.window.close()
}

// The mounted application intentionally owns long-lived query timers. This is
// an isolated CLI smoke process, so terminate after the artifact assertions.
process.exit(process.exitCode ?? 0)

function installBrowserGlobals(window) {
  const browserGlobals = [
    'document',
    'navigator',
    'location',
    'history',
    'HTMLElement',
    'HTMLFormElement',
    'Node',
    'Element',
    'Event',
    'StorageEvent',
    'MutationObserver',
    'DOMException',
  ]

  Object.defineProperty(globalThis, 'window', {
    configurable: true,
    value: window,
  })
  for (const name of browserGlobals) {
    Object.defineProperty(globalThis, name, {
      configurable: true,
      value: window[name],
    })
  }
  Object.defineProperty(globalThis, 'getComputedStyle', {
    configurable: true,
    value: window.getComputedStyle.bind(window),
  })
  Object.defineProperty(globalThis, 'requestAnimationFrame', {
    configurable: true,
    value: window.requestAnimationFrame.bind(window),
  })
  Object.defineProperty(globalThis, 'cancelAnimationFrame', {
    configurable: true,
    value: window.cancelAnimationFrame.bind(window),
  })
  Object.defineProperty(globalThis, 'BroadcastChannel', {
    configurable: true,
    value: NoopBroadcastChannel,
  })
}

function resolveRequestUrl(input, baseUrl) {
  if (typeof input === 'string' || input instanceof URL) {
    return new URL(input, baseUrl)
  }
  if (input && typeof input === 'object' && 'url' in input) {
    return new URL(String(input.url), baseUrl)
  }
  throw new Error(`Production bundle supplied an unsupported request target: ${String(input)}`)
}

function resolveRequestMethod(input, init) {
  const inputMethod = input && typeof input === 'object' && 'method' in input
    ? String(input.method)
    : 'GET'
  return String(init.method ?? inputMethod).toUpperCase()
}

function jsonResponse(body) {
  return new Response(JSON.stringify(body), {
    headers: { 'content-type': 'application/json' },
    status: 200,
  })
}

async function waitFor(predicate, timeoutMs = 5_000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (predicate()) return
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 10))
  }
  throw new Error('Production login screen did not render within 5 seconds')
}
