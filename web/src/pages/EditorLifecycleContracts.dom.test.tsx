// @vitest-environment jsdom
import { act, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { useQueryClient, type QueryClient } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { afterEach, expect, vi } from 'vitest'
import { ApiError, apiFetch } from '../api/client'
import { invalidateSession } from '../api/sessionLifecycle'
import { AuthProvider, useAuth } from '../components/AuthContext'
import { SessionQueryProvider } from '../components/SessionQueryProvider'
import { defineEditorLifecycleContract, type EditorLifecycleDriver } from '../testing/editorLifecycleContract'
import type { Feed, TaggingRule, TaggingSettingsBundleResponse } from '../types/api'
import { createDefaultRuleDraft } from './taggingSettingsModel'
import { useFeedsPageController } from './useFeedsPageController'
import { useTaggingSettingsController } from './useTaggingSettingsController'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...await original<typeof import('../api/client')>(), apiFetch: vi.fn() }))
vi.mock('../hooks/useCurrentUser', () => ({ useCurrentUser: () => ({ data: { id: 'editor-user', role: 'analyst', access: { permissions: ['*:*'] }, features: {} } }) }))

const rule: TaggingRule = {
  ...createDefaultRuleDraft(), id: 'rule-1', name: 'Original', tag_name: 'vpn', pattern: 'vpn',
  min_classification_confidence: null, created_at: '2026-09-10T00:00:00Z', updated_at: '2026-09-10T00:00:00Z',
}
const feed = {
  id: 'feed-1', name: 'Original', url: 'https://example.test/feed', enabled: true, fetch_mode: 'interval',
  fetch_interval_seconds: 1800, schedule_cron: null, error_count: 0, created_at: '2026-09-10T00:00:00Z',
} as Feed
const settings: TaggingSettingsBundleResponse['settings'] = {
  id: 'settings', enabled_categories: ['vulnerability'], min_auto_tag_confidence: 0.45,
  secondary_tag_limit: 2, created_at: rule.created_at, updated_at: rule.updated_at,
}
type Adapter = { value: string; edit: (value: string) => void; submit: () => void; select: (index: number) => void; discardDialog: ReactNode }
let root: Root
let client: QueryClient
let router: ReturnType<typeof createMemoryRouter>
let current: Adapter
let changeSession: () => void
let rules: TaggingRule[]
let feeds: Feed[]

function useTaggingAdapter(): Adapter {
  const controller = useTaggingSettingsController()
  return {
    value: controller.ruleDraft.name,
    edit: (value) => controller.setRuleDraft((draft) => ({ ...draft, name: value })),
    submit: controller.onSaveRule,
    select: (index) => controller.onSelectRule(rules[index]),
    discardDialog: controller.confirmDiscardUnsavedTaggingChanges.discardDialog,
  }
}
function useFeedAdapter(): Adapter {
  const controller = useFeedsPageController()
  return {
    value: controller.feedEditDraft?.name ?? '',
    edit: (value) => controller.updateFeedEditDraft({ name: value }),
    submit: controller.onSaveFeedDetail,
    select: (index) => controller.openFeedDetail(feeds[index]),
    discardDialog: controller.confirmDiscardUnsavedFeedScheduleChanges.discardDialog,
  }
}
function dialog() { return document.querySelector('[role=alertdialog]') }
function clickDialog(label: string) {
  const button = [...(dialog()?.querySelectorAll('button') ?? [])].find((entry) => entry.textContent?.trim() === label)
  expect(button, `Rendered discard action ${label}`).toBeDefined()
  act(() => button!.click())
}
async function settle() { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)) }) }

async function mountContract(kind: 'tagging' | 'feed'): Promise<EditorLifecycleDriver> {
  rules = [rule, { ...rule, id: 'rule-2', name: 'Other' }]
  feeds = [feed, { ...feed, id: 'feed-2', name: 'Other' }]
  let resolve!: (value: unknown) => void
  let reject!: (error: Error) => void
  const pending = new Promise((done, fail) => { resolve = done; reject = fail })
  const useAdapter = kind === 'tagging' ? useTaggingAdapter : useFeedAdapter
  vi.mocked(apiFetch).mockImplementation((path, init) => {
    if (init?.method === 'PATCH') return pending as never
    if (path === '/tagging/settings') return Promise.resolve({ settings, rules }) as never
    if (path === '/feeds') return Promise.resolve(feeds) as never
    if (path.startsWith('/items?')) return Promise.resolve({ items: [], total: 0, page: 1, page_size: 10 }) as never
    throw new Error(`Unregistered editor contract request ${path}`)
  })
  function Editor() {
    current = useAdapter()
    return <><span>Editor</span>{current.discardDialog}</>
  }
  function SessionProbe() {
    client = useQueryClient()
    client.setDefaultOptions({ queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } })
    changeSession = useAuth().markAuthenticated
    return <RouterProvider router={router} />
  }
  router = createMemoryRouter([
    { path: '/editor', element: <Editor /> }, { path: '/destination', element: <p>Destination</p> },
  ], { initialEntries: ['/editor'] })
  const container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  act(() => root.render(<AuthProvider><SessionQueryProvider><SessionProbe /></SessionQueryProvider></AuthProvider>))
  await settle()
  await settle()
  act(() => current.select(0))
  await settle()
  const leave = async () => { await act(async () => { await router.navigate('/destination') }) }
  return {
    value: () => current.value,
    edit: (value) => act(() => current.edit(value)),
    submit: async () => {
      act(() => current.submit())
      await settle()
      expect(vi.mocked(apiFetch).mock.calls.filter(([, init]) => init?.method === 'PATCH')).toHaveLength(1)
    },
    complete: async () => {
      await act(async () => resolve({ ...(kind === 'tagging' ? rule : feed), name: 'Submitted' }))
      await settle()
    },
    conflict: async () => {
      await act(async () => reject(new ApiError('Resource changed', 409, '/editor')))
      await settle()
    },
    refresh: async () => {
      rules = [{ ...rule, name: 'Changed on server' }, rules[1]]
      feeds = [{ ...feed, name: 'Changed on server' }, feeds[1]]
      await act(async () => { await client.invalidateQueries({ queryKey: kind === 'tagging' ? ['tagging', 'settings'] : ['feeds'] }) })
      await settle()
    },
    reselect: async () => {
      act(() => current.select(1))
      if (dialog()) clickDialog('Discard changes')
      act(() => current.select(0))
      await settle()
    },
    leave,
    cancelLeave: () => clickDialog('Cancel'),
    discardAndLeave: async () => { await leave(); clickDialog('Discard changes'); await settle() },
    onEditorRoute: () => router.state.location.pathname === '/editor',
    discardVisible: () => Boolean(dialog()),
    retireSession: async () => {
      const previous = client
      act(() => changeSession())
      await settle()
      expect(client).not.toBe(previous)
    },
    hasAcceptedCache: () => {
      const inventory = kind === 'tagging'
        ? client.getQueryData<TaggingSettingsBundleResponse>(['tagging', 'settings'])?.rules
        : client.getQueryData<Feed[]>(['feeds'])
      return Boolean(inventory?.some((entry) => entry.name === 'Submitted'))
    },
  }
}

afterEach(() => {
  act(() => root?.unmount())
  router?.dispose()
  client?.clear()
  invalidateSession()
  vi.resetAllMocks()
  localStorage.clear()
  sessionStorage.clear()
  document.body.replaceChildren()
})

defineEditorLifecycleContract('Tagging rule controller', () => mountContract('tagging'))
defineEditorLifecycleContract('Feed editor controller', () => mountContract('feed'))
