// @vitest-environment jsdom
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiFetch } from '../api/client'
import type { AISettings } from '../types/api'
import { AiSettingsPage } from './AiSettingsPage'
import { createRequestFromDraft, DEFAULT_DRAFT } from './aiSettingsDraft'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...(await original<object>()), apiFetch: vi.fn() }))
vi.mock('../hooks/useCurrentUser', () => ({ useCurrentUser: () => ({ data: {
  id: 'admin', role: 'admin', features: { ai_enabled: true }, access: { permissions: ['*:*'] },
} }) }))
vi.mock('react-router-dom', async (original) => ({
  ...(await original<object>()), useBlocker: () => ({ state: 'unblocked' }),
}))

const baseline: AISettings = {
  ...createRequestFromDraft({ ...DEFAULT_DRAFT, model: 'model-before-save', base_url: 'https://model.example.test/v1' }),
  id: 'settings', ai_enabled: true, ai_configured: true, api_key_configured: true,
  created_at: '2026-09-12T00:00:00Z', updated_at: '2026-09-12T00:00:00Z',
  prompt_previews: {
    item_enrichment: { label: 'Articles', system_prompt: 'Summarize', notes: [] },
    daily_brief: { label: 'Briefs', system_prompt: 'Summarize', notes: [] },
  },
}
let root: Root | undefined
let client: QueryClient
let host: HTMLDivElement
function input(label: string) {
  const field = [...host.querySelectorAll('label')].find((node) => node.textContent?.startsWith(label))
  return field!.querySelector('input')!
}
function change(label: string, value: string) {
  const field = input(label)
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(field, value)
  field.dispatchEvent(new Event('input', { bubbles: true }))
}
function button(label: string) {
  return [...host.querySelectorAll('button')].find((node) => node.textContent?.trim() === label)!
}
async function settle() {
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)) })
}
afterEach(() => { act(() => root?.unmount()); client?.clear(); host?.remove(); vi.clearAllMocks() })

describe('reprocess submission completion with a real query cache', () => {
  it.each(['unchanged', 'days', 'limit', 'date', 'feed removal', 'item removal', 'picker text', 'failure'])(
    'preserves the current draft when the submitted scope differs: %s', async (changeKind) => {
      let complete!: (value: unknown) => void
      let fail!: (reason: Error) => void
      const pending = new Promise((resolve, reject) => { complete = resolve; fail = reject })
      let submitted: Record<string, unknown> | undefined
      vi.mocked(apiFetch).mockImplementation((path, options) => {
        if (path === '/ai/settings') return Promise.resolve(baseline) as never
        if (path.startsWith('/feeds')) return Promise.resolve([{ id: 'feed-1', name: 'Synthetic feed', url: 'https://fixture.invalid' }]) as never
        if (path.startsWith('/items?')) return Promise.resolve({ items: [{ id: 'item-1', title: 'Synthetic article' }], total: 1 }) as never
        if (path === '/ai/reprocess') {
          submitted = JSON.parse(String(options?.body))
          return pending as never
        }
        return new Promise(() => {})
      })
      client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
      host = document.createElement('div'); document.body.append(host); root = createRoot(host)
      act(() => root!.render(<QueryClientProvider client={client}><MemoryRouter><AiSettingsPage /></MemoryRouter></QueryClientProvider>))
      await settle()
      act(() => button('Jobs').click()); await settle()
      act(() => {
        change('Reprocess lookback (days)', '14')
        change('Maximum articles', '250')
        change('Start time', '2026-09-01T08:00')
        change('End time', '2026-09-11T09:00')
        input('Synthetic feed').click()
      })
      await settle()
      act(() => [...host.querySelectorAll('button')].find((entry) => entry.textContent?.startsWith('Synthetic article'))!.click())
      expect(button('Queue reprocess').disabled).toBe(false)
      act(() => button('Queue reprocess').click()); await settle()
      expect(submitted).toMatchObject({ limit: 250, feed_ids: ['feed-1'], item_ids: ['item-1'] })
      expect(input('Reprocess lookback (days)').matches(':disabled')).toBe(false)
      act(() => {
        if (changeKind === 'days') change('Reprocess lookback (days)', '30')
        if (changeKind === 'limit') change('Maximum articles', '300')
        if (changeKind === 'date') change('End time', '')
        if (changeKind === 'feed removal') input('Synthetic feed').click()
        if (changeKind === 'item removal') button('Synthetic article · remove').click()
        if (changeKind === 'picker text') {
          const picker = host.querySelector<HTMLInputElement>('input[placeholder="Search items by title, summary, or URL"]')!
          Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(picker, 'Next search')
          picker.dispatchEvent(new Event('input', { bubbles: true }))
        }
      })
      await act(async () => {
        if (changeKind === 'failure') fail(new Error('Queue unavailable'))
        else complete({ queued: true, run_id: 'accepted-run', task_id: 'task' })
      })
      await settle()
      if (changeKind === 'unchanged') {
        expect(input('Reprocess lookback (days)').value).toBe('7')
        expect(input('Start time').value).toBe('')
        expect(input('Synthetic feed').checked).toBe(false)
        expect(button('Synthetic article · remove')).toBeUndefined()
      } else {
        expect(input('Reprocess lookback (days)').value).toBe(changeKind === 'days' ? '30' : '14')
        expect(input('Maximum articles').value).toBe(changeKind === 'limit' ? '300' : '250')
        expect(input('End time').value).toBe(changeKind === 'date' ? '' : '2026-09-11T09:00')
        expect(input('Synthetic feed').checked).toBe(changeKind !== 'feed removal')
        expect(Boolean(button('Synthetic article · remove'))).toBe(changeKind !== 'item removal')
        if (changeKind === 'picker text') {
          expect(host.querySelector<HTMLInputElement>('input[placeholder="Search items by title, summary, or URL"]')?.value).toBe('Next search')
        }
      }
    },
  )
})
