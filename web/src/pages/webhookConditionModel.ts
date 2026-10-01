import type {
  WebhookCondition,
  WebhookConditionField,
  WebhookConditionGroup,
} from '../types/webhookAutomation'
import type { NotificationEventType } from '../types/notifications'

const INDICATOR_EVENTS = new Set<NotificationEventType>([
  'intel.extraction.ready', 'intel.indicators.changed', 'hunt.approved',
])
const ARTICLE_EVENTS = new Set<NotificationEventType>([
  ...INDICATOR_EVENTS, 'rss_item_new', 'alert_match', 'article.ai.ready',
])

export const INDICATOR_FIELDS = new Set<WebhookConditionField>([
  'ioc_type', 'ioc_role', 'analyst_verdict', 'extraction_confidence', 'maliciousness_confidence',
])
export const isIndicatorGroup = (group: WebhookConditionGroup) =>
  group.op === 'indicators_any' || group.op === 'indicators_all'

export const CONDITION_FIELDS: {
  value: WebhookConditionField
  label: string
  numeric?: boolean
  hint: string
}[] = [
  {
    value: 'feed_id',
    label: 'Feed',
    hint: 'Exact feed identifiers, separated by commas.',
  },
  {
    value: 'tag_id',
    label: 'Tag ID',
    hint: 'Exact tag identifiers, separated by commas.',
  },
  {
    value: 'tag',
    label: 'Tag name',
    hint: 'Exact tag names, separated by commas.',
  },
  {
    value: 'alert_rule_id',
    label: 'Alert rule',
    hint: 'Exact rule identifiers; unrelated alert rules will not match.',
  },
  {
    value: 'team_id',
    label: 'Team',
    hint: 'Exact team identifiers. Team access is checked independently.',
  },
  {
    value: 'ioc_type',
    label: 'Indicator type',
    hint: 'For example: domain, url, email, ipv4, ipv6, hash_sha256.',
  },
  {
    value: 'ioc_role',
    label: 'AI indicator role',
    hint: 'malicious_infrastructure, benign, reference, or unknown.',
  },
  {
    value: 'analyst_verdict',
    label: 'Analyst verdict',
    hint: 'Current team review: malicious, benign, reference, example, retracted, or unreviewed. Missing review does not match.',
  },
  {
    value: 'extraction_confidence',
    label: 'Extraction confidence',
    numeric: true,
    hint: '0–1, using the lowest score among non-excluded indicators. This is not a maliciousness verdict.',
  },
  {
    value: 'maliciousness_confidence',
    label: 'Maliciousness confidence',
    numeric: true,
    hint: '0–1, using the lowest available score. Every non-excluded indicator needs a score; missing confidence does not match.',
  },
  {
    value: 'ai_relevance_score',
    label: 'AI relevance score',
    numeric: true,
    hint: '0–1 from current shared AI article analysis, independent of team assessments. Missing or stale AI does not match. Use AI article analysis ready for reliable AI-based routing.',
  },
  {
    value: 'ai_relevance_label',
    label: 'AI relevance level',
    hint: 'High, medium or low from current shared AI article analysis; this is not team-specific relevance. Missing or stale AI does not match.',
  },
  {
    value: 'freshness_seconds',
    label: 'Evidence age (seconds)',
    numeric: true,
    hint: 'Use “at most” for recent source retrieval, for example 86400 for one day. Legacy notifications use event age.',
  },
  {
    value: 'attack_technique',
    label: 'ATT&CK technique',
    hint: 'Exact technique IDs, for example T1059 or T1059.001.',
  },
  {
    value: 'hunt_review_status',
    label: 'Hunt review status',
    hint: 'accepted, rejected, or suggested.',
  },
]

export function countConditions(condition: WebhookCondition | null): number {
  return condition
    ? 1 +
        ('conditions' in condition
          ? condition.conditions.reduce(
              (sum, entry) => sum + countConditions(entry),
              0,
            )
          : 0)
    : 0
}

export function newCondition(eventType?: NotificationEventType, indicatorScope = false): WebhookCondition {
  if (!indicatorScope && eventType === 'article.ai.ready')
    return { field: 'ai_relevance_score', operator: 'gte', value: 0.8 }
  if (!indicatorScope && eventType && !INDICATOR_EVENTS.has(eventType))
    return { field: 'freshness_seconds', operator: 'lte', value: 86400 }
  return {
    field: 'ioc_role',
    operator: 'in',
    value: ['malicious_infrastructure'],
  }
}

/** Advisory only: preserve saved predicates and OR/NOT semantics when the event changes. */
export function conditionEventWarning(field: WebhookConditionField, eventType?: NotificationEventType): string | null {
  if (!eventType) return null
  if ((INDICATOR_FIELDS.has(field) || field === 'attack_technique') && !INDICATOR_EVENTS.has(eventType))
    return 'This event does not carry indicator or ATT&CK evidence. Choose Indicator extraction ready, Indicators changed or Hunt approved, or change this condition.'
  if ((field === 'ai_relevance_score' || field === 'ai_relevance_label') && !ARTICLE_EVENTS.has(eventType))
    return 'This event does not carry shared article relevance. Choose AI article analysis ready, or change this condition.'
  if (field === 'alert_rule_id' && eventType !== 'alert_match')
    return 'Alert rule evidence is available on Alert match events. Choose that event type, or change this condition.'
  if (field === 'team_id' && eventType !== 'hunt.approved' && eventType !== 'intel.indicators.changed')
    return 'Team evidence is available on Hunt approved and team-specific Indicators changed events. Team ownership of a destination does not add team evidence to other events. Change this condition or choose an event carrying team evidence.'
  if (field === 'hunt_review_status' && eventType !== 'hunt.approved')
    return 'Hunt review evidence is available on Hunt approved events. Choose that event type, or change this condition.'
  if ((field === 'tag' || field === 'tag_id') && !ARTICLE_EVENTS.has(eventType))
    return 'This event does not carry article tags. Choose an article event, or change this condition.'
  if (field === 'feed_id' && (eventType === 'daily_digest' || eventType === 'report_ready'))
    return 'Brief and report events do not identify a single feed. Change this condition or choose an event for an individual article or feed.'
  return null
}

export function normalizeConditions(
  group: WebhookConditionGroup | null,
): WebhookConditionGroup | null {
  if (!group) return null
  return {
    ...group,
    conditions: group.conditions.map((node) =>
      'conditions' in node
        ? normalizeConditions(node)!
        : {
            ...node,
            value: Array.isArray(node.value)
              ? node.value.map((value) => value.trim())
              : node.value,
          },
    ),
  }
}

export function validateConditions(
  group: WebhookConditionGroup | null,
): string | null {
  if (!group) return null
  if (countConditions(group) > 32)
    return 'Use at most 32 conditions and groups.'
  function inspect(node: WebhookCondition, depth: number, indicatorScope = false): string | null {
    if (depth > 4) return 'Use at most four levels of conditions.'
    if ('conditions' in node) {
      if (isIndicatorGroup(node)) {
        if (indicatorScope) return 'Indicator groups cannot contain another indicator group.'
        indicatorScope = true
      }
      if (!node.conditions.length)
        return 'Each condition group needs at least one condition.'
      if (node.op === 'not' && node.conditions.length !== 1)
        return 'An exclusion must contain exactly one condition or group.'
      return (
        node.conditions
          .map((child) => inspect(child, depth + 1, indicatorScope))
          .find(Boolean) ?? null
      )
    }
    if (indicatorScope && !INDICATOR_FIELDS.has(node.field))
      return 'Same-indicator groups may contain only indicator fields. Move event fields outside this group.'
    if (typeof node.value === 'number') {
      if (!Number.isFinite(node.value) || node.value < 0)
        return 'Enter a non-negative numeric condition.'
      if (node.field !== 'freshness_seconds' && node.value > 1)
        return node.field === 'ai_relevance_score' ? 'AI relevance score must be between 0 and 1.' : 'Confidence must be between 0 and 1.'
      if (node.field === 'freshness_seconds' && node.value > 31_536_000)
        return 'Evidence age cannot exceed 365 days (31536000 seconds).'
    } else {
      if (!node.value.length || node.value.some((value) => !value.trim()))
        return 'Each condition needs a value; remove empty comma-separated entries.'
      if (
        node.value.length > 50 ||
        node.value.some((value) => value.trim().length > 200)
      )
        return 'Use at most 50 values per condition, each at most 200 characters.'
    }
    return null
  }
  return inspect(group, 1)
}
