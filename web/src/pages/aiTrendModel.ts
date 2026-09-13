import type { AITimeSeriesPointResponse } from '../types/ai'

export type TrendUnit = 'requests' | 'tokens' | 'ms' | '%'

export function measuredLatency(point: AITimeSeriesPointResponse, key: 'average_latency_ms' | 'p95_latency_ms'): number | null {
  return point.latency_samples != null && point.latency_samples > 0 ? finiteValue(point[key]) : null
}

export function recordedTokens(point: AITimeSeriesPointResponse): number | null {
  if (point.requests > 0 && (point.known_usage_requests === 0 || (point.known_usage_requests == null && point.total_tokens === 0))) return null
  return finiteValue(point.total_tokens)
}

export function successRate(point: AITimeSeriesPointResponse): number | null {
  return point.requests > 0 ? 100 * (point.requests - point.failures) / point.requests : null
}

export function finiteValue(value: number | null): number | null {
  return value != null && Number.isFinite(value) && value >= 0 ? value : null
}

export function formatTrendValue(value: number | null, unit: TrendUnit): string {
  if (value == null) return 'Unavailable'
  const number = value.toLocaleString(undefined, { maximumFractionDigits: unit === '%' || unit === 'ms' ? 1 : 0 })
  return unit === '%' ? `${number}%` : unit === 'ms' ? `${number} ms` : number
}

export function trendDate(bucket: string, full = false): string {
  const date = new Date(`${bucket.slice(0, 10)}T00:00:00Z`)
  return Number.isNaN(date.getTime()) ? bucket : new Intl.DateTimeFormat(undefined, {
    month: 'short', day: 'numeric', ...(full ? { year: 'numeric' as const } : {}), timeZone: 'UTC',
  }).format(date)
}

/** Missing measurements break the line; they must never become zero or be interpolated. */
export function trendPath(values: readonly (number | null)[], x: (index: number) => number, y: (value: number) => number): string {
  let connected = false
  return values.map((raw, index) => {
    const value = finiteValue(raw)
    if (value == null) { connected = false; return '' }
    const command = connected ? 'L' : 'M'
    connected = true
    return `${command}${x(index)},${y(value)}`
  }).filter(Boolean).join(' ')
}
