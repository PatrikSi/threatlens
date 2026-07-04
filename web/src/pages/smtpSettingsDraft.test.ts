import { describe, expect, it } from 'vitest'

import {
  createSMTPDraftFromSettings,
  createSMTPRequestFromDraft,
  DEFAULT_SMTP_DRAFT,
  validateSMTPSettingsDraft,
} from './smtpSettingsDraft'

describe('smtpSettingsDraft', () => {
  it('keeps saved passwords write-only when creating a draft from settings', () => {
    const draft = createSMTPDraftFromSettings({
      id: 'smtp-1',
      name: 'SMTP',
      integration_type: 'smtp',
      direction: 'destination',
      enabled: true,
      configured: true,
      schema_version: 1,
      host: 'smtp.example.com',
      port: 587,
      security: 'starttls',
      username: 'relay-user',
      password_configured: true,
      has_unreadable_secret: false,
      from_email: 'threatlens@example.com',
      from_name: 'ThreatLens',
      timeout_seconds: 10,
      health_status: 'healthy',
      last_test_at: null,
      last_success_at: null,
      last_error_at: null,
      last_error: null,
      last_test_duration_ms: null,
      created_at: '2026-07-04T00:00:00Z',
      updated_at: '2026-07-04T00:00:00Z',
    })

    expect(draft.password).toBe('')
    expect(createSMTPRequestFromDraft(draft)).not.toHaveProperty('password')
  })

  it('validates required enabled settings and password ownership', () => {
    const errors = validateSMTPSettingsDraft({
      ...DEFAULT_SMTP_DRAFT,
      enabled: true,
      password: 'secret',
    })

    expect(errors.host).toContain('Host is required')
    expect(errors.from_email).toContain('Sender email is required')
    expect(errors.username).toContain('Username is required')
  })

  it('serializes password replacement and clear actions explicitly', () => {
    expect(
      createSMTPRequestFromDraft({
        ...DEFAULT_SMTP_DRAFT,
        host: 'smtp.example.com',
        username: 'relay-user',
        password: 'new-secret',
        from_email: 'threatlens@example.com',
      }),
    ).toMatchObject({ password: 'new-secret' })

    expect(
      createSMTPRequestFromDraft({
        ...DEFAULT_SMTP_DRAFT,
        host: 'smtp.example.com',
        clear_password: true,
        from_email: 'threatlens@example.com',
      }),
    ).toMatchObject({ clear_password: true })
  })
})
