import type { NotificationEventType, NotificationTemplateVariable, NotificationWebhookField } from '../types/notifications'

export const ARTICLE_EVENTS = new Set<NotificationEventType>([
  'rss_item_new', 'alert_match', 'article.ai.ready', 'intel.extraction.ready', 'intel.indicators.changed', 'hunt.approved',
])

export function payloadVariableGroup(key: string): string {
  const prefix = key.split('.')[0]
  return ({ item: 'Article', ai: 'AI analysis', feed: 'Feed', event: 'Event', user: 'Owner', alert: 'Alert', failed_webhook: 'Delivery failure', digest: 'Digest', brief: 'Brief / report' } as Record<string, string>)[prefix] ?? 'Other'
}

export function variableAppliesToEvent(variable: NotificationTemplateVariable, event: NotificationEventType): boolean {
  const prefix = variable.key.split('.')[0]
  if (variable.key.startsWith('ai.summary') || variable.key.startsWith('ai.relevance_reasons')) return event === 'article.ai.ready'
  if (['event', 'user'].includes(prefix)) return true
  if (prefix === 'brief' || prefix === 'digest') return event === 'daily_digest' || event === 'report_ready'
  if (prefix === 'failed_webhook') return event === 'webhook_failed'
  if (prefix === 'alert') return event === 'alert_match'
  if (prefix === 'item' || prefix === 'ai') return ARTICLE_EVENTS.has(event)
  return true
}

/** Dotted JSON paths must not replace an existing scalar or object. */
export function payloadFieldConflict(fields: NotificationWebhookField[], key: string, nested: boolean): string | null {
  if (fields.some((field) => field.key.trim() === key)) return 'This output key is already configured. Edit the existing field or choose another key.'
  if (nested && fields.some((field) => {
    const existing = field.key.trim()
    return existing && (key.startsWith(`${existing}.`) || existing.startsWith(`${key}.`))
  })) return 'This key overlaps an existing JSON path. Choose a different key so existing fields are preserved.'
  return null
}
