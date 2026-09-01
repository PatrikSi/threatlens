import type {
  OperationsHealthGapInterval,
  OperationsHealthHistorySample,
} from '../types/operations'
import { formatDateTime } from '../utils/datetime'

export interface TimeSeriesDefinition {
  key: string
  label: string
  color: string
  value: (sample: OperationsHealthHistorySample) => number | null
}

export function AccessibleTimeSeries({
  title,
  description,
  samples,
  series,
  resolutionSeconds,
  rangeStart,
  rangeEnd,
  gapIntervals = [],
  gapIntervalsTruncated = false,
  formatValue = (value) => value.toLocaleString(),
}: {
  title: string
  description: string
  samples: OperationsHealthHistorySample[]
  series: TimeSeriesDefinition[]
  resolutionSeconds: number
  rangeStart: string
  rangeEnd: string
  gapIntervals?: OperationsHealthGapInterval[]
  gapIntervalsTruncated?: boolean
  formatValue?: (value: number) => string
}) {
  const points = samples.flatMap((sample) => series.map((definition) => ({
    sample,
    definition,
    value: definition.value(sample),
  }))).filter((point): point is typeof point & { value: number } => point.value != null && Number.isFinite(point.value))
  const maxValue = Math.max(1, ...points.map((point) => point.value))
  const requestedStart = Date.parse(rangeStart)
  const requestedEnd = Date.parse(rangeEnd)
  const firstTime = Number.isFinite(requestedStart)
    ? requestedStart
    : samples.length
      ? Date.parse(samples[0].sampled_at)
      : 0
  const lastTime = Number.isFinite(requestedEnd)
    ? requestedEnd
    : samples.length
      ? Date.parse(samples[samples.length - 1].sampled_at)
      : firstTime
  const timeSpan = Math.max(1, lastTime - firstTime)
  const chartWidth = 640
  const chartHeight = 128
  const padding = { top: 10, right: 8, bottom: 20, left: 36 }
  const innerWidth = chartWidth - padding.left - padding.right
  const innerHeight = chartHeight - padding.top - padding.bottom

  return (
    <figure className="min-w-0 rounded border border-slate/15 px-3 py-3 dark:border-white/10">
      <figcaption>
        <h3 className="font-semibold">{title}</h3>
        <p className="mt-0.5 text-xs text-slate dark:text-slate-400">{description}</p>
      </figcaption>
      {points.length === 0 ? (
        <p className="mt-3 border-y border-dashed border-slate/20 py-6 text-center text-sm text-slate dark:border-white/10 dark:text-slate-300">
          Numeric observations are unavailable for this trend and range.
        </p>
      ) : (
        <div className="mt-3 min-w-0 overflow-x-auto">
          <svg
            viewBox={`0 0 ${chartWidth} ${chartHeight}`}
            role="img"
            aria-labelledby={`${chartId(title)}-title ${chartId(title)}-description`}
            className="h-36 min-w-[34rem] w-full"
          >
            <title id={`${chartId(title)}-title`}>{title}</title>
            <desc id={`${chartId(title)}-description`}>{description}. Missing samples are rendered as gaps.</desc>
            <line x1={padding.left} y1={padding.top + innerHeight} x2={chartWidth - padding.right} y2={padding.top + innerHeight} className="stroke-slate/25 dark:stroke-white/15" />
            <line x1={padding.left} y1={padding.top} x2={padding.left} y2={padding.top + innerHeight} className="stroke-slate/25 dark:stroke-white/15" />
            <text x={padding.left - 4} y={padding.top + 5} textAnchor="end" className="fill-slate text-[9px] dark:fill-slate-400">{formatValue(maxValue)}</text>
            <text x={padding.left - 4} y={padding.top + innerHeight} textAnchor="end" className="fill-slate text-[9px] dark:fill-slate-400">0</text>
            {series.flatMap((definition) => {
              const values = samples.map((sample) => ({
                sample,
                time: Date.parse(sample.sampled_at),
                value: definition.value(sample),
              }))
              return values.slice(1).flatMap((current, index) => {
                const previous = values[index]
                if (previous.value == null || current.value == null) return []
                const gapSeconds = (current.time - previous.time) / 1_000
                if (
                  gapIntervalsTruncated ||
                  gapIntervals.some((gap) => intervalOverlapsGap(previous.time, current.time, gap)) ||
                  (gapIntervals.length === 0 && gapSeconds > resolutionSeconds * 1.75)
                ) return []
                return [(
                  <line
                    key={`${definition.key}-${current.sample.sampled_at}`}
                    data-series-segment={definition.key}
                    x1={padding.left + ((previous.time - firstTime) / timeSpan) * innerWidth}
                    y1={padding.top + innerHeight - (previous.value / maxValue) * innerHeight}
                    x2={padding.left + ((current.time - firstTime) / timeSpan) * innerWidth}
                    y2={padding.top + innerHeight - (current.value / maxValue) * innerHeight}
                    stroke={definition.color}
                    strokeWidth="2"
                    vectorEffect="non-scaling-stroke"
                  />
                )]
              })
            })}
            {points.map((point) => (
              <circle
                key={`${point.definition.key}-${point.sample.sampled_at}-point`}
                data-series-point={point.definition.key}
                cx={padding.left + ((Date.parse(point.sample.sampled_at) - firstTime) / timeSpan) * innerWidth}
                cy={padding.top + innerHeight - (point.value / maxValue) * innerHeight}
                r="2.5"
                fill={point.definition.color}
                vectorEffect="non-scaling-stroke"
              />
            ))}
            <text x={padding.left} y={chartHeight - 3} textAnchor="start" className="fill-slate text-[9px] dark:fill-slate-400">{shortTime(rangeStart)}</text>
            <text x={chartWidth - padding.right} y={chartHeight - 3} textAnchor="end" className="fill-slate text-[9px] dark:fill-slate-400">{shortTime(rangeEnd)}</text>
          </svg>
        </div>
      )}
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs">
        {series.map((definition) => (
          <span key={definition.key} className="inline-flex items-center gap-1.5">
            <span className="h-0.5 w-4" style={{ backgroundColor: definition.color }} aria-hidden="true" />
            {definition.label}
          </span>
        ))}
      </div>
      <details className="mt-2 text-xs">
        <summary className="min-h-11 cursor-pointer py-2 font-semibold text-cyan md:min-h-0 md:py-1">View exact data</summary>
        <div className="mt-2 max-h-72 overflow-auto">
          <table className="w-full min-w-[36rem] text-left">
            <thead className="sticky top-0 bg-white dark:bg-[#041612]">
              <tr className="border-b border-slate/20 dark:border-white/10">
                <th scope="col" className="px-2 py-2">Observed</th>
                {series.map((definition) => <th key={definition.key} scope="col" className="px-2 py-2">{definition.label}</th>)}
              </tr>
            </thead>
            <tbody>
              {samples.map((sample) => (
                <tr key={sample.sampled_at} className="border-b border-slate/10 last:border-0 dark:border-white/5">
                  <td className="whitespace-nowrap px-2 py-1.5">{formatDateTime(sample.sampled_at)}</td>
                  {series.map((definition) => {
                    const value = definition.value(sample)
                    return <td key={definition.key} className="px-2 py-1.5 font-mono">{value == null ? 'Unavailable' : formatValue(value)}</td>
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </figure>
  )
}

function chartId(value: string): string {
  return `operations-chart-${value.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`
}

function shortTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function intervalOverlapsGap(
  start: number,
  end: number,
  gap: OperationsHealthGapInterval,
): boolean {
  if (gap.kind !== 'internal') return false
  const gapStart = Date.parse(gap.start_at)
  const gapEnd = Date.parse(gap.end_at)
  return Number.isFinite(gapStart) && Number.isFinite(gapEnd) && gapStart < end && gapEnd > start
}
