import { describe, expect, it } from 'vitest'

import { SMTPHook, SMTPTemplateDefault } from '../types/api'
import {
  applySMTPTemplateDefault,
  createSMTPHookDraft,
  createSMTPHookRequest,
  DEFAULT_SMTP_HOOK_DRAFT,
  validateSMTPHookDraft,
} from './smtpHookDraft'

const hook: SMTPHook = {
  id: 'smtp-1',
  name: 'Alert relay',
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
  to_emails: ['analyst@example.com'],
  timeout_seconds: 10,
  event_types: ['alert_match'],
  feed_scope: 'all',
  feed_ids: [],
  subject_template: '[ThreatLens] Alert match',
  html_template: '<p>{{ alert.primary_name }}</p>',
  health_status: 'healthy',
  last_test_at: null,
  last_success_at: null,
  last_error_at: null,
  last_error: null,
  last_test_duration_ms: null,
  created_at: '2026-07-15T00:00:00Z',
  updated_at: '2026-07-15T00:00:00Z',
  is_default: false,
  uses_shared_credentials: true,
  credential_source_id: 'smtp-source',
  credential_source_name: 'Primary relay',
}

describe('smtpHookDraft', () => {
  it('creates an editable draft without exposing a saved password', () => {
    const draft = createSMTPHookDraft(hook)

    expect(draft.name).toBe('Alert relay')
    expect(draft.credential_source_id).toBe('smtp-source')
    expect(draft.password).toBe('')
  })

  it('omits local transport credentials when a source is selected', () => {
    const request = createSMTPHookRequest({
      ...createSMTPHookDraft(hook),
      password: 'must-not-be-sent',
      clear_password: true,
    })

    expect(request.credential_source_id).toBe('smtp-source')
    expect(request.settings.host).toBeNull()
    expect(request.settings.username).toBeNull()
    expect(request.settings).not.toHaveProperty('password')
    expect(request.settings).not.toHaveProperty('clear_password')
  })

  it('loads the event type and both templates together', () => {
    const template: SMTPTemplateDefault = {
      send_for: 'feed_failing',
      event_types: ['feed_failing'],
      subject_template: '[ThreatLens] Feed failing: {{ feed.name }}',
      html_template: '<p>{{ feed.last_error }}</p>',
    }

    const updated = applySMTPTemplateDefault(DEFAULT_SMTP_HOOK_DRAFT, template)

    expect(updated.event_types).toEqual(['feed_failing'])
    expect(updated.subject_template).toContain('{{ feed.name }}')
    expect(updated.html_template).toContain('{{ feed.last_error }}')
  })

  it('requires a hook name but accepts inherited transport fields', () => {
    const validation = validateSMTPHookDraft({
      ...DEFAULT_SMTP_HOOK_DRAFT,
      enabled: true,
      name: '',
      credential_source_id: 'smtp-source',
      host: '',
      from_email: 'threatlens@example.com',
      to_emails: 'analyst@example.com',
    })

    expect(validation.name).toBe('Name is required.')
    expect(validation.host).toBeUndefined()
  })
})
