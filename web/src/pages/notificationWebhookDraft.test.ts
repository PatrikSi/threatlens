import { describe, expect, it } from 'vitest'

import {
  applyBodyMode,
  applyEventType,
  createDefaultDraft,
  createRequestFromDraft,
  normalizeDraftUrlQuery,
  resolveNotificationEventAvailability,
} from './notificationWebhookDraft'

describe('notification webhook draft shaping', () => {
  it('moves URL query parameters into structured fields without losing fragments', () => {
    const draft = createDefaultDraft()
    draft.url_template = 'https://hooks.example.test/events?token=a%20b&empty#delivery'
    draft.query_params = [{ key: 'token', value: 'old' }, { key: 'tenant', value: 'security' }]

    const normalized = normalizeDraftUrlQuery(draft)

    expect(normalized.url_template).toBe('https://hooks.example.test/events#delivery')
    expect(normalized.query_params).toEqual([
      { key: 'tenant', value: 'security' },
      { key: 'token', value: 'a b' },
      { key: 'empty', value: '' },
    ])
  })

  it('builds the API request with trimmed fields and one explicit content type', () => {
    const draft = createDefaultDraft()
    draft.name = '  Alert relay  '
    draft.url_template = ' https://hooks.example.test/events '
    draft.headers = [
      { key: ' Authorization ', value: 'Bearer token' },
      { key: 'content-type', value: 'text/plain' },
      { key: ' ', value: 'ignored' },
    ]
    draft.content_type = ' application/json '

    expect(createRequestFromDraft(draft)).toMatchObject({
      name: 'Alert relay',
      url_template: 'https://hooks.example.test/events',
      headers: [
        { key: 'Authorization', value: 'Bearer token' },
        { key: 'Content-Type', value: 'application/json' },
      ],
    })
  })

  it('updates generated JSON fields but preserves customized payload fields', () => {
    const defaultDraft = applyEventType(createDefaultDraft(), 'alert_match')
    expect(defaultDraft.body_fields.some((field) => field.key === 'alert.primary_name')).toBe(true)

    const customDraft = createDefaultDraft()
    customDraft.body_fields = [{ key: 'custom', value: '{{ item.title }}' }]
    expect(applyEventType(customDraft, 'feed_failing').body_fields).toEqual(customDraft.body_fields)
  })

  it('initializes fields when changing body modes and gates AI brief choices', () => {
    const draft = createDefaultDraft()
    draft.body_fields = []

    expect(applyBodyMode(draft, 'form').body_fields).toEqual([{ key: '', value: '' }])
    expect(resolveNotificationEventAvailability(false, 'daily_digest')).toMatchObject({
      unavailableDailyBriefSelected: true,
    })
    expect(
      resolveNotificationEventAvailability(false, 'daily_digest').availableEventOptions.some(
        (option) => option.value === 'daily_digest',
      ),
    ).toBe(false)
  })
})
