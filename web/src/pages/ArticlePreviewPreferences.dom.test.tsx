// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  user: {
    id: 'alice',
    access: { permissions: ['read:workspace', 'write:workspace_preferences'] },
  },
}))
vi.mock('../api/client', async (original) => ({
  ...(await original<typeof import('../api/client')>()),
  apiFetch: mocks.apiFetch,
}))
vi.mock('../hooks/useCurrentUser', () => ({
  useCurrentUser: () => ({ data: mocks.user, isError: false, error: null }),
}))

import { ApiError } from '../api/client'
import { useArticlePreviewPreferences } from '../hooks/useArticlePreviewPreferences'
import type { WorkspaceUserPreferenceResponse } from '../types/workspace'
import { workspaceQueryKeys } from '../workspace/workspaceApi'
import { ArticlePreviewPreferences } from './ArticlePreviewPreferences'
import { ArticlePreviewDrawer } from './DashboardPageComponents'
;(
  globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true
let root: Root
let container: HTMLDivElement
let client: QueryClient
let records: Record<string, WorkspaceUserPreferenceResponse>
const dirty = vi.fn()

function preferences(
  userId: string,
  enabled = false,
  revision = 0,
): WorkspaceUserPreferenceResponse {
  return {
    user_id: userId,
    role: 'viewer',
    landing_module_id: null,
    modules: [],
    dashboard_panel_ids: null,
    article_preview_external_resources: enabled,
    revision,
    created_at: null,
    updated_at: null,
    updated_by_user_id: null,
    unknown_module_ids: [],
    unknown_dashboard_panel_ids: [],
    warnings: [],
  }
}

function Harness() {
  const state = useArticlePreviewPreferences()
  return (
    <>
      <ArticlePreviewPreferences onDirtyChange={dirty} />
      <ArticlePreviewDrawer
        key={`${state.userId}:article`}
        defaultExternalResources={state.defaultExternalResources}
        preview={{
          itemId: 'article',
          url: 'https://source.example/article',
          title: 'Article',
          sourceLabel: 'Source',
        }}
        width={700}
        minWidth={400}
        maxWidth={1000}
        isResizing={false}
        onResizeStart={vi.fn()}
        onResizeBy={vi.fn()}
        onClose={vi.fn()}
      />
    </>
  )
}
function render(generation = 0) {
  act(() =>
    root.render(
      <QueryClientProvider client={client}>
        <Harness key={generation} />
      </QueryClientProvider>,
    ),
  )
}
async function settle() {
  for (let index = 0; index < 3; index += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
  }
}
function toggle() {
  return container.querySelector<HTMLInputElement>('input[role="switch"]')!
}
function button(label: string) {
  const result = [
    ...container.querySelectorAll<HTMLButtonElement>('button'),
  ].find((element) => element.textContent === label)
  if (!result) throw new Error(`Missing button: ${label}`)
  return result
}
function source() {
  return container.querySelector('iframe')!.getAttribute('src')!
}

beforeEach(() => {
  mocks.user = {
    id: 'alice',
    access: { permissions: ['read:workspace', 'write:workspace_preferences'] },
  }
  mocks.apiFetch.mockReset()
  dirty.mockReset()
  records = { alice: preferences('alice'), bob: preferences('bob') }
  mocks.apiFetch.mockImplementation(
    async (_path: string, options?: RequestInit) => {
      if (options?.method === 'PUT') {
        const body = JSON.parse(String(options.body))
        records[mocks.user.id] = preferences(
          mocks.user.id,
          body.article_preview_external_resources,
          body.expected_revision + 1,
        )
      }
      return records[mocks.user.id]
    },
  )
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
})
afterEach(() => {
  act(() => root.unmount())
  client.clear()
  container.remove()
})

it('persists both choices, waits for save, and restores the account default after reload', async () => {
  render()
  await settle()
  expect(toggle().checked).toBe(false)
  expect(source()).not.toContain('external_resources')
  act(() => toggle().click())
  expect(source()).not.toContain('external_resources')
  act(() => button('Save preview preference').click())
  await settle()
  expect(source()).toContain('external_resources=true')
  const request = mocks.apiFetch.mock.calls.find(
    ([, options]) => options?.method === 'PUT',
  )!
  expect(JSON.parse(String(request[1].body))).toEqual({
    expected_revision: 0,
    article_preview_external_resources: true,
  })
  expect(dirty).toHaveBeenLastCalledWith(false)
  render(1)
  await settle()
  expect(toggle().checked).toBe(true)
  act(() => toggle().click())
  act(() => button('Save preview preference').click())
  await settle()
  expect(source()).not.toContain('external_resources')
  expect(container.textContent).toContain('block external resources by default')
})

it('disables editing while saving and cannot apply a late save to the next account', async () => {
  render()
  await settle()
  let resolve!: (value: WorkspaceUserPreferenceResponse) => void
  mocks.apiFetch.mockImplementation((_path: string, options?: RequestInit) =>
    options?.method === 'PUT'
      ? new Promise((done) => {
          resolve = done
        })
      : Promise.resolve(records[mocks.user.id]),
  )
  act(() => toggle().click())
  act(() => button('Save preview preference').click())
  await settle()
  expect(toggle().closest('fieldset')!.disabled).toBe(true)
  mocks.user = { ...mocks.user, id: 'bob' }
  render()
  await settle()
  expect(toggle().checked).toBe(false)
  expect(source()).not.toContain('external_resources')
  await act(async () => resolve(preferences('alice', true, 1)))
  await settle()
  expect(toggle().checked).toBe(false)
  expect(source()).not.toContain('external_resources')
  expect(container.textContent).not.toContain(
    'will load external resources by default',
  )
})

it('preserves failed edits and allows deliberate discard and reload', async () => {
  render()
  await settle()
  mocks.apiFetch.mockImplementation((_path: string, options?: RequestInit) =>
    options?.method === 'PUT'
      ? Promise.reject(new Error('Connection interrupted. Retry the save.'))
      : Promise.resolve(records.alice),
  )
  act(() => toggle().click())
  act(() => button('Save preview preference').click())
  await settle()
  expect(toggle().checked).toBe(true)
  expect(dirty).toHaveBeenLastCalledWith(true)
  expect(container.querySelector('[role="alert"]')!.textContent).toContain(
    'Connection interrupted',
  )
  expect(source()).not.toContain('external_resources')
  act(() => button('Discard change and reload preference').click())
  await settle()
  expect(toggle().checked).toBe(false)
  expect(dirty).toHaveBeenLastCalledWith(false)
})

it('does not silently refresh the revision baseline of a dirty preference', async () => {
  render()
  await settle()
  act(() => toggle().click())
  act(() =>
    client.setQueryData(
      workspaceQueryKeys.preferences('alice'),
      preferences('alice', false, 2),
    ),
  )
  await settle()
  act(() => button('Save preview preference').click())
  await settle()
  const request = mocks.apiFetch.mock.calls.find(
    ([, options]) => options?.method === 'PUT',
  )!
  expect(JSON.parse(String(request[1].body)).expected_revision).toBe(0)
})

it('drops cached consent after a denied preference refresh and keeps the iframe sandboxed', async () => {
  records.alice = preferences('alice', true, 1)
  render()
  await settle()
  expect(source()).toContain('external_resources=true')
  mocks.apiFetch.mockRejectedValue(
    new ApiError('Access denied', 403, '/workspace/preferences'),
  )
  await act(async () => {
    await client.refetchQueries({
      queryKey: workspaceQueryKeys.preferences('alice'),
    })
  })
  await settle()
  expect(source()).not.toContain('external_resources')
  expect(container.querySelector('iframe')!.getAttribute('sandbox')).toBe(
    'allow-popups allow-popups-to-escape-sandbox',
  )
  expect(
    container.querySelector('iframe')!.getAttribute('referrerpolicy'),
  ).toBe('no-referrer')
})

it('does not restore revoked consent from a preference read started before the save', async () => {
  records.alice = preferences('alice', true, 1)
  render()
  await settle()
  let resolveRead!: (value: WorkspaceUserPreferenceResponse) => void
  mocks.apiFetch.mockImplementation((_path: string, options?: RequestInit) =>
    options?.method === 'PUT'
      ? Promise.resolve(preferences('alice', false, 2))
      : new Promise((resolve) => {
          resolveRead = resolve
        }),
  )
  let refreshing!: Promise<void>
  act(() => {
    refreshing = client.refetchQueries({
      queryKey: workspaceQueryKeys.preferences('alice'),
    })
  })
  act(() => toggle().click())
  act(() => button('Save preview preference').click())
  await settle()
  expect(source()).not.toContain('external_resources')
  await act(async () => {
    resolveRead(preferences('alice', true, 1))
    await refreshing
  })
  await settle()
  expect(toggle().checked).toBe(false)
  expect(source()).not.toContain('external_resources')
})
