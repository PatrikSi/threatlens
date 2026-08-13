import { type Dispatch, type SetStateAction } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { AITaskEventResponse, AITaskRunResponse, ItemListEntry } from '../types/api'
import { formatDateOnly, formatDateTime } from '../utils/datetime'
import { AISettingsDraft } from './aiSettingsDraft'

export const AI_RUN_PAGE_SIZE = 20

export function invalidateAiQueries(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: ['ai'] })
}

export function markAiQueriesStale(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: ['ai'], refetchType: 'none' })
}

export function updateDraft<K extends keyof AISettingsDraft>(
  setter: Dispatch<SetStateAction<AISettingsDraft>>,
  key: K,
  value: AISettingsDraft[K],
) {
  setter((current) => ({ ...current, [key]: value }))
}

export function formatTimestamp(value: string | null | undefined) {
  return value ? formatDateTime(value) : 'unknown'
}

export function formatTaskTypeLabel(value: string) {
  if (value === 'item_enrichment') return 'Item Enrichment'
  if (value === 'daily_brief') return 'Daily Brief'
  if (value === 'report') return 'Intelligence Report'
  if (value === 'connection_test') return 'Connection Test'
  if (value === 'reprocess') return 'Reprocess'
  return value
}

export function isDailyBriefBackfillRun(run: AITaskRunResponse) {
  return run.task_type === 'reprocess' && run.metadata.scope === 'daily_brief_backfill'
}

export function formatRunTaskLabel(run: AITaskRunResponse) {
  return isDailyBriefBackfillRun(run) ? 'Daily Brief' : formatTaskTypeLabel(run.task_type)
}

export function formatTriggerLabel(value: string) {
  if (value === 'auto') return 'Auto'
  if (value === 'manual') return 'Manual'
  if (value === 'scheduled') return 'Scheduled'
  return value
}

export function formatStatusLabel(value: string, reason?: string | null) {
  if (value === 'skipped' && reason === 'canceled') return 'Canceled'
  if (value === 'ready') return 'Ready'
  if (value === 'error') return 'Error'
  if (value === 'queued') return 'Queued'
  if (value === 'running') return 'Running'
  if (value === 'skipped') return 'Skipped'
  return value
}

export function formatFeatureKey(value: string) {
  if (value === 'daily_brief') return 'Daily Brief'
  if (value === 'auto_enrichment') return 'Auto-Enrichment'
  return value.replace(/_/g, ' ').replace(/\b\w/g, (char) => char.toUpperCase())
}

export function statusTone(value: string): 'success' | 'warning' | 'danger' | 'neutral' | 'info' {
  if (value === 'ready') return 'success'
  if (value === 'error') return 'danger'
  if (value === 'running' || value === 'queued') return 'info'
  return 'neutral'
}

export function formatAgeSeconds(value: number) {
  if (value < 60) return `${value}s`
  if (value < 3600) return `${Math.round(value / 60)}m`
  return `${(value / 3600).toFixed(1)}h`
}

export function formatUtcTime(hour: number, minute: number) {
  return `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')} UTC`
}

export function formatDuration(value: number | null) {
  if (value == null) return 'n/a'
  if (value < 1000) return `${value} ms`
  if (value < 60_000) return `${(value / 1000).toFixed(1)} s`
  return `${(value / 60_000).toFixed(1)} min`
}

export function remainingCount(run: AITaskRunResponse) {
  if (!run.target_count) {
    return '?'
  }
  return Math.max(0, run.target_count - run.processed_count)
}

export function shouldUseLookbackWindow(
  startTime: string,
  endTime: string,
  selectedItems: ItemListEntry[],
) {
  return !startTime && !endTime && selectedItems.length === 0
}

export function canCancelRun(run: AITaskRunResponse) {
  return !run.finished_at && (run.status === 'queued' || run.status === 'running')
}

export function cancelActionLabel(run: AITaskRunResponse) {
  return run.status === 'queued' ? 'Remove From Queue' : 'Stop Task'
}

export function canInspectProviderExchange(run: AITaskRunResponse) {
  return run.task_type === 'item_enrichment' || run.task_type === 'daily_brief' || run.task_type === 'report' || run.task_type === 'connection_test'
}

export function formatRunSelectionLabel(run: AITaskRunResponse) {
  const scope = run.item_title?.trim() || run.feed_name?.trim() || run.model?.trim() || formatTimestamp(run.queued_at)
  return `Select ${formatRunTaskLabel(run)} run ${scope}`
}

export function describeRunScope(run: AITaskRunResponse) {
  if (run.task_type === 'daily_brief') {
    return run.status === 'queued' || run.status === 'running'
      ? 'Manual daily brief run queued for generation.'
      : 'Daily brief run.'
  }
  if (run.task_type !== 'reprocess') {
    return run.reason || 'AI task in progress.'
  }

  if (isDailyBriefBackfillRun(run)) {
    const days = asNumber(run.metadata.days) ?? run.target_count
    if (days === 1) {
      return 'Reprocessing today\'s daily brief.'
    }
    return days
      ? `Reprocessing daily briefs for the last ${days} days, ending today.`
      : 'Reprocessing daily briefs for recent days.'
  }

  const days = asNumber(run.metadata.days)
  const limit = asNumber(run.metadata.limit)
  const explicitItemCount = asNumber(run.metadata.explicit_item_count)
  const feedIds = Array.isArray(run.metadata.feed_ids) ? (run.metadata.feed_ids as unknown[]) : []
  const startTime = typeof run.metadata.start_time === 'string' ? run.metadata.start_time : null
  const endTime = typeof run.metadata.end_time === 'string' ? run.metadata.end_time : null

  if (explicitItemCount && explicitItemCount > 0) {
    return `Reprocessing ${explicitItemCount} selected article${explicitItemCount === 1 ? '' : 's'}.`
  }

  const parts: string[] = []
  if (days) {
    parts.push(`last ${days} day${days === 1 ? '' : 's'}`)
  }
  if (limit) {
    parts.push(`up to ${limit} articles`)
  }
  if (feedIds.length) {
    parts.push(`${feedIds.length} feed${feedIds.length === 1 ? '' : 's'}`)
  }
  if (startTime || endTime) {
    parts.push(`time range ${formatTimestamp(startTime)} to ${formatTimestamp(endTime)}`)
  }

  return parts.length ? `Reprocessing ${parts.join(' · ')}.` : 'Reprocessing recent eligible articles.'
}

export function metadataString(value: unknown) {
  return typeof value === 'string' && value.trim().length > 0 ? value : null
}

export function formatDailyBriefChildRunTitle(run: AITaskRunResponse) {
  const briefDate = metadataString(run.metadata.brief_date)
  return briefDate ? `Daily brief for ${formatDateOnly(briefDate)}` : 'Daily brief run'
}

export function formatDailyBriefChildRunMeta(run: AITaskRunResponse) {
  const referenceTime = metadataString(run.metadata.reference_time)
  const parts: string[] = []
  if (referenceTime) {
    parts.push(`reference ${formatTimestamp(referenceTime)}`)
  }
  if (run.model) {
    parts.push(run.model)
  }
  return parts.join(' · ') || 'Queued daily brief generation'
}

export function asNumber(value: unknown) {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value
  }
  if (typeof value === 'string') {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

export function truncate(value: string, max: number) {
  if (!value) return ''
  if (value.length <= max) return value
  return `${value.slice(0, max - 1)}…`
}

export function findLatestProviderExchangeEvent(events: AITaskEventResponse[]) {
  const exchanges = events.filter(
    (event) =>
      event.event_type === 'provider_exchange' ||
      event.event_type === 'provider_exchange_failed' ||
      event.event_type === 'provider_exchange_retry',
  )
  return exchanges.length ? exchanges[exchanges.length - 1] : null
}

export function humanizeKey(value: string) {
  return value.replace(/_/g, ' ').replace(/\b\w/g, (char) => char.toUpperCase())
}

export function parseTimestamp(value: string | null | undefined) {
  if (!value) {
    return null
  }
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

export function formatMetadataValue(value: unknown) {
  if (typeof value === 'boolean') {
    return value ? 'Yes' : 'No'
  }
  if (Array.isArray(value)) {
    return value.join(', ')
  }
  if (value == null) {
    return 'n/a'
  }
  if (typeof value === 'object') {
    return JSON.stringify(value)
  }
  return String(value)
}

export function formatDebugPayload(value: unknown) {
  if (typeof value === 'string') {
    return value
  }
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}
