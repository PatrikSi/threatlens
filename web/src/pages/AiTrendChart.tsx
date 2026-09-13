import { useEffect, useId, useRef, useState } from 'react'
import { finiteValue, formatTrendValue, trendDate, trendPath, type TrendUnit } from './aiTrendModel'

export interface AiTrendSeries {
  key: string
  label: string
  color: string
  dashed?: boolean
  values: (number | null)[]
}

export function AiTrendChart({ title, description, dates, series, unit, selectedIndex, onSelect, emptyLabel }: {
  title: string
  description: string
  dates: string[]
  series: AiTrendSeries[]
  unit: TrendUnit
  selectedIndex: number
  onSelect: (index: number) => void
  emptyLabel: string
}) {
  const id = useId()
  const host = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(640)
  useEffect(() => {
    const element = host.current
    if (!element || typeof ResizeObserver === 'undefined') return
    const update = () => setWidth(Math.max(260, element.clientWidth))
    update()
    const observer = new ResizeObserver(update)
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  const height = 220
  const padding = { left: 58, right: 18, top: 20, bottom: 28 }
  const plotWidth = width - padding.left - padding.right
  const plotHeight = height - padding.top - padding.bottom
  const observations = series.flatMap((line) => line.values).map(finiteValue).filter((value): value is number => value != null)
  const maximum = unit === '%' ? 100 : Math.max(1, ...observations)
  const axisMaximum = unit === '%' ? 100 : niceMaximum(maximum)
  const x = (index: number) => padding.left + (dates.length <= 1 ? plotWidth / 2 : index * plotWidth / (dates.length - 1))
  const y = (value: number) => padding.top + plotHeight * (1 - value / axisMaximum)
  const dateIndices = [...new Set([0, ...(width >= 460 ? [Math.floor((dates.length - 1) / 2)] : []), dates.length - 1])]
  const ticks = axisMaximum < 2 && unit !== '%' ? [0, axisMaximum] : [0, axisMaximum / 2, axisMaximum]

  return <figure aria-labelledby={`${id}-heading`} className="min-w-0 rounded-xl border border-slate/20 bg-white/80 p-3 dark:border-cyan-900/40 dark:bg-[#041612]/90">
    <figcaption>
      <h3 id={`${id}-heading`} className="font-display text-lg">{title}</h3>
      <p className="mt-1 min-h-10 text-sm text-slate dark:text-white/70">{description}</p>
    </figcaption>
    <div ref={host} className="mt-2 min-w-0">
      {observations.length ? <svg viewBox={`0 0 ${width} ${height}`} className="h-[220px] w-full" role="img"
        aria-labelledby={`${id}-title`} aria-describedby={`${id}-description`}
        onPointerMove={(event) => {
          const bounds = event.currentTarget.getBoundingClientRect()
          if (!bounds.width) return
          const position = ((event.clientX - bounds.left) * width / bounds.width - padding.left) / plotWidth
          onSelect(Math.max(0, Math.min(dates.length - 1, Math.round(position * (dates.length - 1)))))
        }}>
        <title id={`${id}-title`}>{title}</title>
        <desc id={`${id}-description`}>{description} Daily UTC buckets. Missing measurements appear as gaps. Use the Trend date slider or exact data table for values.</desc>
        <text x={padding.left} y={12} className="fill-slate text-[11px] dark:fill-slate-300">{unit}</text>
        {ticks.map((value) => <g key={value}>
          <line x1={padding.left} x2={width - padding.right} y1={y(value)} y2={y(value)} className="stroke-slate/20 dark:stroke-white/15" />
          <text x={padding.left - 8} y={y(value) + 4} textAnchor="end" className="fill-slate text-[11px] dark:fill-slate-300">{compactNumber(value)}</text>
        </g>)}
        {dateIndices.map((index) => <text key={index} x={x(index)} y={height - 5}
          textAnchor={dates.length === 1 ? 'middle' : index === 0 ? 'start' : index === dates.length - 1 ? 'end' : 'middle'}
          className="fill-slate text-[11px] dark:fill-slate-300">{trendDate(dates[index])}</text>)}
        <line x1={x(selectedIndex)} x2={x(selectedIndex)} y1={padding.top} y2={height - padding.bottom}
          className="stroke-slate/40 dark:stroke-white/40" strokeDasharray="3 4" />
        {series.map((line) => <g key={line.key}>
          <path data-trend-series={line.key} d={trendPath(line.values, x, y)} fill="none" stroke={line.color}
            strokeWidth={2.5} strokeDasharray={line.dashed ? '6 4' : undefined} strokeLinejoin="round" />
          {line.values.map((raw, index) => {
            const value = finiteValue(raw)
            if (value == null) return null
            const isolated = finiteValue(line.values[index - 1] ?? null) == null && finiteValue(line.values[index + 1] ?? null) == null
            if (index !== selectedIndex && !isolated) return null
            return <circle key={index} data-trend-point={line.key} cx={x(index)} cy={y(value)} r={3.5} fill={line.color} />
          })}
        </g>)}
      </svg> : <p className="flex h-[220px] items-center justify-center border-y border-dashed border-slate/20 px-4 text-center text-sm dark:border-white/15">{emptyLabel}</p>}
    </div>
    <dl className="mt-2 flex flex-wrap gap-x-5 gap-y-2 text-sm" aria-label={`${title} selected values`}>
      {series.map((line) => <div key={line.key}>
        <dt className="inline-flex items-center gap-1.5 text-xs text-slate dark:text-slate-300">
          <svg width="20" height="8" aria-hidden="true"><line x1="0" x2="20" y1="4" y2="4" stroke={line.color} strokeWidth="2.5" strokeDasharray={line.dashed ? '5 3' : undefined} /></svg>
          {line.label}
        </dt>
        <dd className="font-semibold tabular-nums">{formatTrendValue(finiteValue(line.values[selectedIndex] ?? null), unit)}</dd>
      </div>)}
    </dl>
  </figure>
}

function niceMaximum(value: number): number {
  const power = 10 ** Math.floor(Math.log10(value))
  const scaled = value / power
  return (scaled <= 1 ? 1 : scaled <= 2 ? 2 : scaled <= 5 ? 5 : 10) * power
}

function compactNumber(value: number): string {
  return value.toLocaleString(undefined, { notation: 'compact', maximumFractionDigits: 1 })
}
