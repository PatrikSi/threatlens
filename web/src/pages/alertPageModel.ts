import { AlertInterest } from '../types/api'
import { formatDateTime } from '../utils/datetime'

export const ALERT_CATEGORIES = [
  { value: 'software', label: 'Software' },
  { value: 'vendor', label: 'Vendor' },
  { value: 'apt_group', label: 'APT Group' },
  { value: 'vulnerability', label: 'Vulnerability' },
  { value: 'malware', label: 'Malware' },
  { value: 'technique', label: 'Technique' },
  { value: 'campaign', label: 'Campaign' },
  { value: 'infrastructure', label: 'Infrastructure' },
  { value: 'other', label: 'Other' },
]

export const ALERT_PREVIEW_LIMIT = 5

export function parseAlertKeywords(value: string): string[] {
  return value
    .split(',')
    .map((keyword) => keyword.trim())
    .filter(Boolean)
}

export function getAlertSaveDisabledReason(name: string, keywords: string[]): string | null {
  if (!name.trim()) {
    return 'Enter an interest name.'
  }
  return keywords.length === 0 ? 'Enter at least one keyword.' : null
}

export function shouldShowSaveGuidance(reason: string | null, dirty: boolean): boolean {
  return Boolean(reason) && dirty
}

export function groupAlertsByCategory(alerts: AlertInterest[]): Map<string, AlertInterest[]> {
  const groups = new Map(ALERT_CATEGORIES.map((category) => [category.value, [] as AlertInterest[]]))
  for (const alert of alerts) {
    const category = groups.has(alert.category) ? alert.category : 'other'
    groups.get(category)?.push(alert)
  }
  return groups
}

export function describeAlertCategory(category: string): string {
  return ALERT_CATEGORIES.find((entry) => entry.value === category)?.label ?? category
}

export function formatAlertTimestamp(value: string | null): string {
  return value ? formatDateTime(value) : 'Unknown time'
}

export function formatAlertPreviewSummary(value: string): string {
  return decodeHtmlEntities(value.replace(/<[^>]+>/g, ' ')).replace(/\s+/g, ' ').trim()
}

function decodeHtmlEntities(value: string): string {
  return value
    .replace(/&#x([0-9a-f]+);/gi, (match, codePoint: string) => decodeCodePoint(match, Number.parseInt(codePoint, 16)))
    .replace(/&#(\d+);/g, (match, codePoint: string) => decodeCodePoint(match, Number.parseInt(codePoint, 10)))
    .replace(/&nbsp;/gi, ' ')
    .replace(/&amp;/gi, '&')
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')
    .replace(/&quot;/gi, '"')
    .replace(/&#39;|&apos;/gi, "'")
}

function decodeCodePoint(fallback: string, codePoint: number): string {
  if (!Number.isFinite(codePoint)) {
    return fallback
  }
  try {
    return String.fromCodePoint(codePoint)
  } catch {
    return fallback
  }
}
