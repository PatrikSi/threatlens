import type { Page } from '@playwright/test'
import { expect } from './fixtures'

/** Real browser cookies, Origin and CSRF handling, without intercepting the API. */
export async function writeApi<T>(page: Page, path: string, method: string, data?: unknown): Promise<T> {
  const result = await page.evaluate(async ({ path, method, data }) => {
    const csrf = document.cookie.split('; ').find((cookie) => cookie.startsWith('threatlens_csrf='))?.split('=')[1]
    const response = await fetch(`/api/v1${path}`, {
      method,
      headers: { 'content-type': 'application/json', 'x-csrf-token': decodeURIComponent(csrf ?? '') },
      body: data === undefined ? undefined : JSON.stringify(data),
    })
    return { status: response.status, text: await response.text() }
  }, { path, method, data })
  expect(result.status, `${method} ${path}: ${result.text}`).toBeGreaterThanOrEqual(200)
  expect(result.status, `${method} ${path}: ${result.text}`).toBeLessThan(300)
  return (result.text ? JSON.parse(result.text) : undefined) as T
}
