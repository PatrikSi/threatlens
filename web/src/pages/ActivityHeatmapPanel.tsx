import { useId, useState } from 'react'
import type { StatsActivityHeatmapResponse } from '../types/api'
import { formatDateOnly } from '../utils/datetime'

const PAGE_SIZE = 10

export function ActivityHeatmapPanel({ data }: { data: StatsActivityHeatmapResponse }) {
  const [selection, setSelection] = useState(0)
  const [requestedPage, setRequestedPage] = useState(0)
  const id = useId()
  const isHourly = data.bucket_unit === 'hour'
  const maxCount = Math.max(1, data.max_count)
  const columnCount = isHourly ? Math.max(1, data.bucket_labels.length || data.rows[0]?.counts.length || 1) : 1
  const bucketLabels = data.bucket_labels.length === columnCount ? data.bucket_labels
    : Array.from({ length: columnCount }, (_, index) => `Bucket ${index + 1}`)
  const calendar = isHourly ? null : buildDailyCalendar(data.rows)
  const calendarWeekCount = Math.max(1, calendar?.weekCount ?? 1)
  const bucketCount = data.rows.length * columnCount
  const selectedIndex = Math.min(selection, Math.max(0, bucketCount - 1))
  const selectedDay = Math.floor(selectedIndex / columnCount)
  const selectedBucket = selectedIndex % columnCount
  const selectedRow = data.rows[selectedDay]
  const selectedCount = selectedRow?.counts[selectedBucket]
  const selectedLabel = selectedRow ? `${selectedRow.day}${isHourly ? ` ${bucketLabels[selectedBucket]}` : ''} UTC: ${selectedCount === undefined ? 'count unavailable' : `${selectedCount} posts`}` : ''
  const pageCount = Math.ceil(data.rows.length / PAGE_SIZE)
  const page = Math.min(requestedPage, Math.max(0, pageCount - 1))

  if (!bucketCount) return <p className="mt-3 text-sm">No activity buckets are available in this window.</p>

  return <div className="mt-3 space-y-4">
    <p className="text-xs font-semibold uppercase text-slate dark:text-slate-300">
      Last {data.window_days} Days ({isHourly ? 'Hourly' : 'Daily'}, UTC)
    </p>
    <div className="rounded border border-slate/20 bg-white/70 p-2 dark:border-cyan-900/40 dark:bg-[#072019]/70" aria-hidden="true">
      {isHourly ? <>
        <div className="mb-1 grid items-center gap-2 text-[10px] text-slate dark:text-slate-300" style={{ gridTemplateColumns: '82px minmax(0, 1fr)' }}>
          <span />
          <div className="grid gap-1" style={{ gridTemplateColumns: `repeat(${columnCount}, minmax(0, 1fr))` }}>
            {bucketLabels.map((label, index) => <span key={index} className="text-center">{index % 3 === 0 ? label.slice(0, 2) : ''}</span>)}
          </div>
        </div>
        <div className="max-h-[520px] space-y-1 overflow-auto pr-1">
          {data.rows.map((row, dayIndex) => <div key={row.day} className="grid items-center gap-2" style={{ gridTemplateColumns: '82px minmax(0, 1fr)' }}>
            <span className="font-mono text-[11px] text-slate dark:text-slate-300">{formatDateOnly(row.day).slice(0, 5)}</span>
            <div className="grid gap-1" style={{ gridTemplateColumns: `repeat(${columnCount}, minmax(0, 1fr))` }}>
              {row.counts.slice(0, columnCount).map((count, bucketIndex) => {
                const index = dayIndex * columnCount + bucketIndex
                return <div key={bucketIndex} className={`h-4 rounded ${selectedIndex === index ? 'outline outline-2 outline-offset-1 outline-cyan' : ''}`}
                  style={heatCellStyle(count, maxCount)} onMouseMove={() => setSelection(index)} onClick={() => setSelection(index)} />
              })}
            </div>
          </div>)}
        </div>
      </> : <div className="grid items-start gap-2 pb-1" style={{ gridTemplateColumns: '82px minmax(0, 1fr)' }}>
        <div className="mt-5 grid grid-rows-7 gap-1 text-[10px] text-slate dark:text-slate-300">
          {['', 'Mon', '', 'Wed', '', 'Fri', ''].map((label, index) => <span key={index} className="h-4 leading-4">{label}</span>)}
        </div>
        <div className="min-w-0 space-y-1">
          <div className="grid gap-1 text-[10px] text-slate dark:text-slate-300" style={{ gridTemplateColumns: `repeat(${calendarWeekCount}, minmax(0, 1fr))` }}>
            {Array.from({ length: calendarWeekCount }, (_, weekIndex) => <span key={weekIndex} className="h-3 overflow-visible leading-3">{calendar?.monthLabels.get(weekIndex) ?? ''}</span>)}
          </div>
          <div className="grid grid-flow-col grid-rows-7 gap-1" style={{ gridTemplateColumns: `repeat(${calendarWeekCount}, minmax(0, 1fr))` }}>
            {(calendar?.cells ?? []).map((cell, index) => cell
              ? <div key={cell.day} className={`h-4 rounded ${selectedRow?.day === cell.day ? 'outline outline-2 outline-offset-1 outline-cyan' : ''}`}
                  style={heatCellStyle(cell.count, maxCount)} onMouseMove={() => setSelection(cell.rowIndex)} onClick={() => setSelection(cell.rowIndex)} />
              : <div key={`pad-${index}`} className="h-4 rounded bg-transparent" />)}
          </div>
        </div>
      </div>}
    </div>
    <div className="flex items-center gap-2 text-[11px] text-slate dark:text-slate-300" aria-hidden="true">
      <span>Low</span><div className="h-2 w-28 rounded" style={{ background: 'linear-gradient(90deg, rgba(6,182,212,0.1), rgba(6,182,212,0.95))' }} /><span>High</span>
    </div>
    <div className="space-y-1">
      <label className="block text-sm font-semibold" htmlFor={id}>Inspect activity bucket</label>
      <p id={`${id}-help`} className="text-xs">Use Left and Right arrows to move between {isHourly ? 'hours' : 'days'}, or Home and End to reach the first and last bucket.</p>
      <input id={id} type="range" min={0} max={Math.max(0, bucketCount - 1)} step={1} value={selectedIndex}
        aria-describedby={`${id}-help`} aria-valuetext={selectedLabel} className="w-full accent-cyan"
        onChange={(event) => setSelection(Number(event.target.value))} />
      <p role="status" className="font-mono text-sm">{selectedLabel}</p>
    </div>
    <details>
      <summary className="cursor-pointer text-sm font-semibold">Show exact activity counts</summary>
      <div className="mt-2 overflow-x-auto" role="region" aria-label="Exact activity counts" tabIndex={0}>
        <table className="w-full text-left text-xs">
          <caption className="mb-2 text-left">Article counts by {isHourly ? 'day and hour' : 'day'} (UTC). Days {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, data.rows.length)} of {data.rows.length}.</caption>
          <thead><tr><th scope="col" className="p-2">Day</th>{isHourly
            ? bucketLabels.map((label, index) => <th scope="col" key={index} className="p-2">{label}</th>)
            : <th scope="col" className="p-2">Posts</th>}</tr></thead>
          <tbody>{data.rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE).map((row) => <tr key={row.day}>
            <th scope="row" className="whitespace-nowrap p-2">{row.day}</th>
            {Array.from({ length: columnCount }, (_, index) => <td key={index} className="p-2">{row.counts[index] ?? 'Unavailable'}</td>)}
          </tr>)}</tbody>
        </table>
      </div>
      {pageCount > 1 && <nav aria-label="Activity counts pages" className="mt-2 flex items-center gap-3 text-sm">
        <button type="button" className="rounded border px-2 py-1 disabled:opacity-50" disabled={!page} onClick={() => setRequestedPage(page - 1)}>Previous days</button>
        <span role="status">Page {page + 1} of {pageCount}</span>
        <button type="button" className="rounded border px-2 py-1 disabled:opacity-50" disabled={page + 1 >= pageCount} onClick={() => setRequestedPage(page + 1)}>Next days</button>
      </nav>}
    </details>
  </div>
}

interface DailyCalendarCell { day: string; count: number; rowIndex: number }

function buildDailyCalendar(rows: StatsActivityHeatmapResponse['rows']) {
  const dayCells: DailyCalendarCell[] = rows.map((row, rowIndex) => ({ day: row.day, count: row.counts[0] ?? 0, rowIndex }))
  if (!dayCells.length) return { cells: [], weekCount: 0, monthLabels: new Map<number, string>() }
  const leadingEmpty = new Date(`${dayCells[0].day}T00:00:00Z`).getUTCDay()
  const cells: Array<DailyCalendarCell | null> = [...Array.from({ length: leadingEmpty }, () => null), ...dayCells]
  const weekCount = Math.ceil(cells.length / 7)
  cells.push(...Array.from({ length: weekCount * 7 - cells.length }, () => null))
  const monthLabels = new Map<number, string>()
  let lastMonthKey = ''
  for (let weekIndex = 0; weekIndex < weekCount; weekIndex += 1) {
    const candidate = cells.slice(weekIndex * 7, weekIndex * 7 + 7).find((entry): entry is DailyCalendarCell => Boolean(entry))
    if (!candidate) continue
    const date = new Date(`${candidate.day}T00:00:00Z`)
    const monthKey = `${date.getUTCFullYear()}-${date.getUTCMonth()}`
    if (monthKey === lastMonthKey) continue
    monthLabels.set(weekIndex, new Intl.DateTimeFormat('en-GB', { month: 'short', timeZone: 'UTC' }).format(date))
    lastMonthKey = monthKey
  }
  return { cells, weekCount, monthLabels }
}

function heatCellStyle(count: number, maxCount: number) {
  if (count <= 0) return { backgroundColor: 'rgba(148, 163, 184, 0.14)' }
  const alpha = 0.2 + Math.min(1, count / maxCount) * 0.75
  return { backgroundColor: `rgba(6, 182, 212, ${alpha.toFixed(3)})` }
}
