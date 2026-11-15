// @vitest-environment jsdom
import { act } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { apiFetch } from '../api/client'
import { mountIntel, settle } from './articleIntelligenceTestSupport'
import { useTaggingSettingsController } from './useTaggingSettingsController'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
vi.mock('../api/client', async (original) => ({ ...(await original<object>()), apiFetch: vi.fn() }))
const identity = vi.hoisted(() => ({ isError: false, isLoading: false, data: { access: { permissions: ['write:tagging'] } } }))
vi.mock('../hooks/useCurrentUser', () => ({ useCurrentUser: () => identity }))
let controller: ReturnType<typeof useTaggingSettingsController>
let view: Awaited<ReturnType<typeof mountIntel>> | undefined
function Harness() { controller = useTaggingSettingsController(); return null }
const settings = { id: 'settings', enabled_categories: ['vulnerability'], min_auto_tag_confidence: 0.45,
  secondary_tag_limit: 2, created_at: '2026-11-15T09:00:00Z', updated_at: '2026-11-15T09:00:00Z' }
beforeEach(() => {
  identity.isError = false
  vi.mocked(apiFetch).mockImplementation(async (path, options) => {
    if (path === '/feeds') return [] as never
    if (path === '/tagging/settings') return options?.method === 'PUT'
      ? { ...settings, ...JSON.parse(String(options.body)) } as never
      : { settings, rules: [] } as never
    throw new Error(`Unexpected path: ${path}`)
  })
})
afterEach(() => { view?.close(); view = undefined; vi.clearAllMocks() })
function requests() { return vi.mocked(apiFetch).mock.calls.filter(([, options]) => options?.method === 'PUT') }
async function open() { view = await mountIntel(<Harness />); await settle() }

describe('tagging defaults admission', () => {
  it.each(['', '0', '0.049', '0.996', 'Infinity', 'not-a-number'])('rejects confidence %j while preserving the draft', async (value) => {
    await open()
    act(() => controller.setSettingsDraft((current) => ({ ...current, min_auto_tag_confidence: value })))
    act(() => controller.onSaveSettings())
    await settle()
    expect(requests()).toHaveLength(0)
    expect(controller.notice?.tone).toBe('error')
    expect(controller.notice?.message).toContain('Minimum tagging confidence')
    expect(controller.settingsDraft.min_auto_tag_confidence).toBe(value)
  })

  it.each(['', '-1', '1.5', '3', 'invalid'])('rejects secondary limit %j without applying a fallback', async (value) => {
    await open()
    act(() => controller.setSettingsDraft((current) => ({ ...current, secondary_tag_limit: value })))
    act(() => controller.onSaveSettings())
    await settle()
    expect(requests()).toHaveLength(0)
    expect(controller.notice?.message).toContain('Secondary tag limit')
    expect(controller.settingsDraft.secondary_tag_limit).toBe(value)
  })

  it.each([{ confidence: '0.05', limit: '0' }, { confidence: '0.45', limit: '1' }, { confidence: '0.995', limit: '2' }])('preserves valid boundaries $confidence / $limit', async ({ confidence, limit }) => {
    await open()
    act(() => controller.setSettingsDraft((current) => ({ ...current, min_auto_tag_confidence: confidence, secondary_tag_limit: limit })))
    act(() => controller.onSaveSettings())
    await settle()
    expect(requests()).toHaveLength(1)
    expect(JSON.parse(String(requests()[0][1]?.body))).toEqual({ enabled_categories: ['vulnerability'],
      min_auto_tag_confidence: Number(confidence), secondary_tag_limit: Number(limit) })
  })

  it('pauses writes with a retained permission when current identity refresh fails', async () => {
    await open()
    expect(controller.canManageTagging).toBe(true)
    identity.isError = true
    act(() => controller.setSettingsDraft((current) => ({ ...current, min_auto_tag_confidence: '0.7' })))
    expect(controller.canManageTagging).toBe(false)
    act(() => controller.onSaveSettings())
    await settle()
    expect(requests()).toHaveLength(0)
    expect(controller.settingsDraft.min_auto_tag_confidence).toBe('0.7')
  })
})
