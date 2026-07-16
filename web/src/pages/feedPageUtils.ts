import { ApiError } from '../api/client'
import { Feed, FeedExportResponse, FeedImportEntry } from '../types/api'
import { formatDateTime } from '../utils/datetime'

export type FeedSaveStatus = 'idle' | 'saving' | 'saved' | 'error'

export type FeedSaveState = {
  status: FeedSaveStatus
  message?: string
}

export type FeedImportPreviewSummary = {
  totalEntries: number
  uniqueEntries: number
  duplicateEntries: number
  createCount: number
  overwriteCount: number
  skipCount: number
  matchingExistingFeeds: Feed[]
}

type BulkSummary = {
  attempted: number
  succeeded: number
  failed: number
  failedFeedNames: string[]
}

export function feedSaveStatusText(status: FeedSaveStatus): string {
  if (status === 'saving') return 'Saving...'
  if (status === 'saved') return 'Saved.'
  return 'Save failed.'
}

export function feedSaveStatusClass(status: FeedSaveStatus, isDirty: boolean): string {
  if (status === 'error') return 'text-red-600'
  if (status === 'saved') return 'text-emerald-700 dark:text-emerald-300'
  if (isDirty) return 'text-amber-700 dark:text-amber-300'
  return 'text-slate dark:text-slate-300'
}

export function summarizeBulkResults(feeds: Feed[], results: PromiseSettledResult<unknown>[]): BulkSummary {
  const attempted = results.length
  const failedFeedNames = results.flatMap((result, index) =>
    result.status === 'rejected' ? [feeds[index]?.name ?? `Feed ${index + 1}`] : [],
  )
  const failed = failedFeedNames.length
  return {
    attempted,
    failed,
    succeeded: attempted - failed,
    failedFeedNames,
  }
}

export function formatBulkResultNotice(actionLabel: string, result: BulkSummary): string {
  const feedLabel = result.attempted === 1 ? 'feed' : 'feeds'
  const failureSuffix = result.failedFeedNames.length ? ` Failed: ${result.failedFeedNames.join(', ')}.` : ''
  return `${actionLabel} ${result.succeeded}/${result.attempted} ${feedLabel}.${failureSuffix}`
}

export function timestamp(value: string | null): number {
  if (!value) return 0
  const parsed = new Date(value).getTime()
  return Number.isNaN(parsed) ? 0 : parsed
}

export function formatDate(value: string | null): string {
  return value ? formatDateTime(value) : 'Never'
}

export function isNewFeedFormDirty(form: {
  name: string
  url: string
  description: string
  siteUrl: string
  language: string
  fetchMode: 'interval' | 'schedule'
  interval: number
  scheduleCron: string
}) {
  return (
    form.name !== '' ||
    form.url !== '' ||
    form.description !== '' ||
    form.siteUrl !== '' ||
    form.language !== '' ||
    form.fetchMode !== 'interval' ||
    form.interval !== 1800 ||
    form.scheduleCron !== '0 * * * *'
  )
}

export function parseImportEntries(payload: unknown): FeedImportEntry[] {
  const entries = Array.isArray(payload)
    ? payload
    : typeof payload === 'object' && payload !== null && Array.isArray((payload as { feeds?: unknown }).feeds)
      ? (payload as { feeds: unknown[] }).feeds
      : null

  if (!entries) {
    throw new Error('JSON must be an array of feeds or an object with a feeds array')
  }

  return entries.map((rawEntry, index) => {
    if (typeof rawEntry !== 'object' || rawEntry === null) {
      throw new Error(`Entry ${index + 1} must be an object`)
    }
    const entry = rawEntry as Record<string, unknown>
    const url = typeof entry.url === 'string' ? entry.url.trim() : ''
    if (!url) {
      throw new Error(`Entry ${index + 1} is missing a valid url`)
    }

    const fetchMode = entry.fetch_mode === 'schedule' ? 'schedule' : 'interval'
    const fetchInterval = Number(entry.fetch_interval_seconds)
    const parsedInterval = Number.isFinite(fetchInterval) && fetchInterval >= 60 ? Math.floor(fetchInterval) : 1800
    const scheduleCron = typeof entry.schedule_cron === 'string' && entry.schedule_cron.trim() ? entry.schedule_cron.trim() : null

    return {
      name: stringOrNull(entry.name),
      url,
      description: stringOrNull(entry.description),
      site_url: stringOrNull(entry.site_url),
      language: stringOrNull(entry.language),
      enabled: typeof entry.enabled === 'boolean' ? entry.enabled : true,
      fetch_mode: fetchMode,
      fetch_interval_seconds: fetchMode === 'interval' ? parsedInterval : null,
      schedule_cron: fetchMode === 'schedule' ? scheduleCron || '0 * * * *' : null,
    }
  })
}

function stringOrNull(value: unknown): string | null {
  if (typeof value !== 'string') return null
  const trimmed = value.trim()
  return trimmed ? trimmed : null
}

export function findDuplicateUrls(entries: FeedImportEntry[]): string[] {
  const counts = new Map<string, number>()
  for (const entry of entries) {
    const key = entry.url.toLowerCase()
    counts.set(key, (counts.get(key) || 0) + 1)
  }
  return Array.from(counts.entries())
    .filter(([, count]) => count > 1)
    .map(([url]) => url)
}

export function buildFeedImportPreviewSummary(
  entries: FeedImportEntry[] | null,
  existingFeeds: Feed[],
  overwriteExisting: boolean,
): FeedImportPreviewSummary | null {
  if (!entries?.length) {
    return null
  }

  const existingByUrl = new Map(
    existingFeeds
      .filter((feed) => feed.url.trim())
      .map((feed) => [feed.url.trim().toLowerCase(), feed] as const),
  )
  const uniqueUrls = new Set<string>()
  const matchingExistingFeeds: Feed[] = []
  let duplicateEntries = 0
  let createCount = 0

  for (const entry of entries) {
    const normalizedUrl = entry.url.trim().toLowerCase()
    if (uniqueUrls.has(normalizedUrl)) {
      duplicateEntries += 1
      continue
    }

    uniqueUrls.add(normalizedUrl)
    const existingFeed = existingByUrl.get(normalizedUrl)
    if (existingFeed) {
      matchingExistingFeeds.push(existingFeed)
    } else {
      createCount += 1
    }
  }

  const overwriteCount = overwriteExisting ? matchingExistingFeeds.length : 0
  const skipCount = overwriteExisting ? 0 : matchingExistingFeeds.length

  return {
    totalEntries: entries.length,
    uniqueEntries: uniqueUrls.size,
    duplicateEntries,
    createCount,
    overwriteCount,
    skipCount,
    matchingExistingFeeds,
  }
}

export function downloadFeedExport(payload: FeedExportResponse) {
  const body = JSON.stringify(payload, null, 2)
  const blob = new Blob([body], { type: 'application/json' })
  const objectUrl = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  const dateSuffix = new Date().toISOString().slice(0, 10)
  anchor.href = objectUrl
  anchor.download = `threatlens-feeds-${dateSuffix}.json`
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(objectUrl)
}

export function formatFeedExportNotice(payload: FeedExportResponse) {
  const feedCount = `${payload.feeds.length} feed${payload.feeds.length === 1 ? '' : 's'}`
  if (!payload.warnings.length) {
    return `Feed export downloaded with ${feedCount}.`
  }
  const warningCount = `${payload.warnings.length} warning${payload.warnings.length === 1 ? '' : 's'}`
  return `Feed export downloaded with ${feedCount} and ${warningCount}: ${payload.warnings[0]}`
}

export function resolveMutationError(error: unknown): string {
  if (error instanceof ApiError) {
    return error.message
  }
  if (error instanceof Error && error.message.trim()) {
    return error.message
  }
  return 'Unknown error'
}
