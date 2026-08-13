import { describe, expect, it } from 'vitest'

import { SMTPTemplateDefault } from '../types/api'
import { DEFAULT_SMTP_HOOK_DRAFT } from './smtpHookDraft'
import {
  createNewHookDraft,
  resolveSMTPEventAvailability,
  resolveTestValidationError,
  smtpTemplateForAvailableEvents,
} from './smtpIntegrationPresentation'

describe('SMTP integration presentation helpers', () => {
  it('excludes AI daily briefs from the available all-events selection when AI is disabled', () => {
    const availability = resolveSMTPEventAvailability(false, [
      'rss_item_new',
      'alert_match',
      'feed_failing',
      'webhook_failed',
    ])

    expect(availability.availableEventTypes).toEqual([
      'rss_item_new',
      'alert_match',
      'feed_failing',
      'webhook_failed',
    ])
    expect(availability.currentSendFor).toBe('all')
    expect(availability.unavailableDailyBriefSelected).toBe(false)
  })

  it('preserves and identifies a legacy daily-brief selection while AI is disabled', () => {
    const availability = resolveSMTPEventAvailability(false, ['daily_digest'])

    expect(availability.currentSendFor).toBe('custom')
    expect(availability.unavailableDailyBriefSelected).toBe(true)
  })

  it('limits an all-events template to events available to the current user', () => {
    const template: SMTPTemplateDefault = {
      send_for: 'all',
      event_types: ['rss_item_new', 'alert_match', 'feed_failing', 'webhook_failed', 'daily_digest'],
      subject_template: 'Subject',
      html_template: '<p>Body</p>',
    }

    const result = smtpTemplateForAvailableEvents(template, 'all', ['rss_item_new', 'alert_match'])

    expect(result.event_types).toEqual(['rss_item_new', 'alert_match'])
    expect(template.event_types).toContain('daily_digest')
  })

  it('loads the new-hook RSS template without mutating the default draft', () => {
    const draft = createNewHookDraft([{
      send_for: 'rss_item_new',
      event_types: ['rss_item_new'],
      subject_template: 'New item',
      html_template: '<p>New item</p>',
    }])

    expect(draft.subject_template).toBe('New item')
    expect(DEFAULT_SMTP_HOOK_DRAFT.subject_template).not.toBe('New item')
  })

  it('validates test-send recipients after connection settings', () => {
    const draft = {
      ...DEFAULT_SMTP_HOOK_DRAFT,
      host: 'smtp.example.com',
      from_email: 'sender@example.com',
    }

    expect(resolveTestValidationError(draft, true, 'invalid', null)).toBe(
      'Enter a valid test recipient email address.',
    )
    expect(resolveTestValidationError(draft, true, 'analyst@example.com', null)).toBeNull()
  })
})
