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

export function feedHealthDotClass(status: FeedHealthStatus): string {
  if (status === 'healthy') return 'bg-emerald-500'
  if (status === 'failing') return 'bg-rose-500'
  if (status === 'disabled') return 'bg-slate-400'
  return 'bg-amber-500'
}

export function feedHealthBadgeClass(status: FeedHealthStatus): string {
  if (status === 'healthy') return 'border-emerald-300/80 bg-emerald-100/80 text-emerald-800 dark:border-emerald-800/40 dark:bg-emerald-950/35 dark:text-emerald-200'
  if (status === 'failing') return 'border-rose-300/80 bg-rose-100/80 text-rose-800 dark:border-rose-800/40 dark:bg-rose-950/35 dark:text-rose-200'
  if (status === 'disabled') return 'border-slate-300/80 bg-slate-100/80 text-slate-700 dark:border-slate-700/50 dark:bg-slate-900/45 dark:text-slate-200'
  return 'border-amber-300/80 bg-amber-100/80 text-amber-800 dark:border-amber-800/40 dark:bg-amber-950/35 dark:text-amber-200'
}
