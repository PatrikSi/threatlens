import {
  NotificationEventType,
  SMTPDelivery,
  SMTPSecurityMode,
  SMTPTemplateDefault,
} from '../types/api'
import {
  applySMTPTemplateDefault,
  DEFAULT_SMTP_HOOK_DRAFT,
  SMTPHookDraft,
} from './smtpHookDraft'

export type NoticeState = {
  tone: 'success' | 'error'
  message: string
}

export type SendForValue = NotificationEventType | 'all' | 'custom'

export const EMPTY_TEMPLATE_DEFAULTS: SMTPTemplateDefault[] = []
export const ALL_EVENT_TYPES: NotificationEventType[] = [
  'rss_item_new',
  'alert_match',
  'feed_failing',
  'webhook_failed',
  'daily_digest',
  'report_ready',
]
export const SMTP_EVENT_OPTIONS: Array<{
  value: NotificationEventType
  label: string
  description: string
}> = [
  { value: 'rss_item_new', label: 'New RSS item', description: 'Email each new item received from the selected feeds.' },
  { value: 'alert_match', label: 'Alert match', description: 'Email when an item matches one or more alert interests.' },
  { value: 'feed_failing', label: 'Feed failing', description: 'Email when a feed reaches the repeated-failure threshold.' },
  { value: 'webhook_failed', label: 'Webhook failed', description: 'Email when a webhook delivery reaches a terminal failure.' },
  { value: 'daily_digest', label: 'AI daily brief', description: 'Email the generated AI daily brief as soon as it is ready.' },
  { value: 'report_ready', label: 'Intelligence report', description: 'Email a scheduled or manually delivered report when generation completes.' },
]

export function createNewHookDraft(defaults: SMTPTemplateDefault[]): SMTPHookDraft {
  const template = defaults.find((entry) => entry.send_for === 'rss_item_new')
  return template ? applySMTPTemplateDefault(DEFAULT_SMTP_HOOK_DRAFT, template) : { ...DEFAULT_SMTP_HOOK_DRAFT }
}

export function resolveSendForValue(
  eventTypes: NotificationEventType[],
  availableEventTypes: NotificationEventType[] = ALL_EVENT_TYPES,
): SendForValue {
  if (eventTypes.length === 1 && availableEventTypes.includes(eventTypes[0])) {
    return eventTypes[0]
  }
  const selected = new Set(eventTypes)
  if (
    availableEventTypes.every((eventType) => selected.has(eventType))
    && selected.size === availableEventTypes.length
  ) {
    return 'all'
  }
  return 'custom'
}

export function resolveSMTPEventAvailability(
  aiDailyBriefAvailable: boolean,
  eventTypes: NotificationEventType[],
  aiReportingAvailable = true,
) {
  const availableEventOptions = SMTP_EVENT_OPTIONS.filter(
    (option) => (option.value !== 'daily_digest' || aiDailyBriefAvailable) &&
      (option.value !== 'report_ready' || aiReportingAvailable),
  )
  const availableEventTypes = availableEventOptions.map((option) => option.value)
  return {
    availableEventOptions,
    availableEventTypes,
    currentSendFor: resolveSendForValue(eventTypes, availableEventTypes),
    unavailableDailyBriefSelected: !aiDailyBriefAvailable && eventTypes.includes('daily_digest'),
    unavailableReportSelected: !aiReportingAvailable && eventTypes.includes('report_ready'),
  }
}

export function smtpTemplateForAvailableEvents(
  template: SMTPTemplateDefault,
  sendFor: SendForValue,
  availableEventTypes: NotificationEventType[],
): SMTPTemplateDefault {
  return sendFor === 'all' ? { ...template, event_types: availableEventTypes } : template
}

export function aiDailyBriefIsAvailable(
  currentUser: { features: { ai_daily_brief_enabled: boolean } } | undefined,
) {
  return currentUser?.features.ai_daily_brief_enabled === true
}

export function aiReportingIsAvailable(
  currentUser: { features: { ai_reporting_enabled?: boolean } } | undefined,
) {
  return currentUser?.features.ai_reporting_enabled === true
}

export function resolveTestValidationError(
  draft: SMTPHookDraft,
  sendTestEmail: boolean,
  testRecipient: string,
  firstValidationError: string | null,
) {
  if (firstValidationError) return firstValidationError
  if (!draft.host.trim()) {
    return draft.credential_source_id
      ? 'The selected credential source does not have an SMTP host.'
      : 'SMTP host is required before testing.'
  }
  if (!sendTestEmail) return null
  const recipient = testRecipient.trim()
  if (!recipient) return 'Recipient email is required before sending a test email.'
  if (!looksLikeEmail(recipient)) return 'Enter a valid test recipient email address.'
  if (!draft.from_email.trim()) return 'Sender email is required before sending a test email.'
  return null
}

export function toggleValue(values: string[], value: string) {
  return values.includes(value) ? values.filter((candidate) => candidate !== value) : [...values, value]
}

export function describeSendFor(eventTypes: NotificationEventType[]) {
  const value = resolveSendForValue(eventTypes)
  if (value === 'all') return 'All notification events'
  if (value === 'custom') return `${eventTypes.length} notification events`
  return describeEventType(value)
}

export function describeEventType(eventType: NotificationEventType) {
  return SMTP_EVENT_OPTIONS.find((option) => option.value === eventType)?.label ?? eventType
}

export function describeEventDescription(value: SendForValue) {
  if (value === 'all') return 'Send this email template for every currently available notification event.'
  if (value === 'custom') return 'This upgraded destination retains its existing multi-event selection until you choose a new option.'
  return SMTP_EVENT_OPTIONS.find((option) => option.value === value)?.description
    ?? 'Configure the event that sends this email.'
}

export function describeFeedScope(scope: 'all' | 'selected', count: number) {
  return scope === 'all' ? 'Any feed' : `${count} selected feed${count === 1 ? '' : 's'}`
}

export function describeSecurity(security: SMTPSecurityMode) {
  if (security === 'ssl_tls') return 'SSL/TLS'
  if (security === 'none') return 'None'
  return 'STARTTLS'
}

export function describeDeliveryState(state: SMTPDelivery['state']) {
  if (state === 'retry_wait') return 'Retry scheduled'
  if (state === 'dead_letter') return 'Dead letter'
  if (state === 'sending') return 'Sending'
  if (state === 'pending') return 'Pending'
  if (state === 'succeeded') return 'Succeeded'
  return 'Failed'
}

export function deliveryStateBadgeClass(state: SMTPDelivery['state']) {
  if (state === 'succeeded') return 'tl-chip-success'
  if (state === 'failed' || state === 'dead_letter') return 'tl-chip-danger'
  if (state === 'retry_wait') return 'tl-chip-warning'
  return 'tl-chip-neutral'
}

function looksLikeEmail(value: string) {
  return /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(value)
}
