export type FeedHealthStatus = 'healthy' | 'stale' | 'failing' | 'disabled'

type FeedHealthInput = {
  enabled: boolean
  error_count: number
  last_error: string | null
  last_success_at: string | null
}

type FeedHealthInfo = {
  status: FeedHealthStatus
  label: string
}

const STALE_AFTER_MS = 24 * 60 * 60 * 1000

export function resolveFeedHealth(feed: FeedHealthInput): FeedHealthInfo {
  if (!feed.enabled) {
    return { status: 'disabled', label: 'Disabled' }
  }

  if (feed.error_count >= 3 || Boolean(feed.last_error)) {
    return { status: 'failing', label: 'Failing' }
  }

  if (!feed.last_success_at) {
    return { status: 'stale', label: 'Stale' }
  }

  const lastSuccessAt = Date.parse(feed.last_success_at)
  if (Number.isNaN(lastSuccessAt)) {
    return { status: 'stale', label: 'Stale' }
  }

  if (Date.now() - lastSuccessAt > STALE_AFTER_MS) {
    return { status: 'stale', label: 'Stale' }
  }

  return { status: 'healthy', label: 'Healthy' }
}

export function feedHealthBadgeClass(status: FeedHealthStatus): string {
  if (status === 'healthy') return 'tl-chip-success'
  if (status === 'failing') return 'tl-chip-danger'
  if (status === 'stale') return 'tl-chip-warning'
  return 'tl-chip-neutral'
}
