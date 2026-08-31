import {
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from 'react'

import {
  AIAuditEntryResponse,
  AILiveTaskResponse,
  AIOpsOverviewResponse,
} from '../types/api'
import { formatDateOnly } from '../utils/datetime'
import { formatTaskTypeLabel, formatTimestamp } from './aiSettingsUtils'

export function Panel({
  title,
  subtitle,
  children,
}: {
  title: string
  subtitle?: string
  children: ReactNode
}) {
  return (
    <section className="rounded-xl border border-slate/20 bg-white/80 p-3 dark:border-cyan-900/40 dark:bg-[#041612]/90">
      <h3 className="font-display text-lg">{title}</h3>
      {subtitle && <p className="mt-1 text-sm text-slate dark:text-white/70">{subtitle}</p>}
      <div className="mt-3">{children}</div>
    </section>
  )
}

export function FieldError({ message }: { message?: string }) {
  if (!message) {
    return null
  }
  return <p className="mt-1 text-xs text-red-600 dark:text-red-300">{message}</p>
}

export function OverviewSection({
  title,
  description,
  children,
}: {
  title: string
  description: string
  children: ReactNode
}) {
  return (
    <section className="space-y-3">
      <div className="border-b border-slate/15 pb-2 dark:border-cyan-900/30">
        <h2 className="font-display text-lg">{title}</h2>
        <p className="mt-1 text-sm text-slate dark:text-white/70">{description}</p>
      </div>
      <div className="space-y-3">{children}</div>
    </section>
  )
}

export function TabButton({
  id,
  controls,
  active,
  onClick,
  onKeyDown,
  children,
  fullWidth = false,
}: {
  id: string
  controls: string
  active: boolean
  onClick: () => void
  onKeyDown: (event: ReactKeyboardEvent<HTMLButtonElement>) => void
  children: ReactNode
  fullWidth?: boolean
}) {
  return (
    <button
      type="button"
      id={id}
      role="tab"
      aria-selected={active}
      aria-controls={controls}
      tabIndex={active ? 0 : -1}
      onClick={onClick}
      onKeyDown={onKeyDown}
      className={
        fullWidth
          ? `block rounded px-3 py-2 text-center text-sm transition lg:text-left ${
              active
                ? 'bg-cyan/15 text-cyan dark:bg-cyan-900/35 dark:text-cyan-300'
                : 'text-slate hover:bg-slate/10 dark:text-slate-200 dark:hover:bg-white/[0.06]'
            }`
          : `rounded-full px-3 py-2 text-sm font-semibold transition ${
              active
                ? 'bg-ink text-white dark:bg-cyan dark:text-slate-950'
                : 'border border-slate/20 bg-white/70 text-slate dark:border-cyan-900/40 dark:bg-[#072019]/80 dark:text-white/75'
            }`
      }
    >
      {children}
    </button>
  )
}

export function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-slate/20 bg-white/80 px-3 py-2 dark:border-cyan-900/40 dark:bg-[#041612]/90">
      <p className="text-xs uppercase text-slate dark:text-white/55">{label}</p>
      <p className="mt-0.5 text-lg font-semibold">{value}</p>
    </div>
  )
}

export function MiniStat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg border border-slate/10 bg-slate/5 px-3 py-2 dark:border-cyan-900/30 dark:bg-white/[0.03]">
      <p className="text-xs uppercase text-slate dark:text-white/55">{label}</p>
      <p className="mt-1 text-base font-semibold">{value}</p>
    </div>
  )
}

export function Metric({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <dt className="text-slate dark:text-white/65">{label}</dt>
      <dd className="text-right font-semibold">{value}</dd>
    </div>
  )
}

export function StatusPill({ label, tone }: { label: string; tone: 'success' | 'warning' | 'danger' | 'neutral' | 'info' }) {
  const toneClass =
    tone === 'success'
      ? 'tl-chip-success'
      : tone === 'warning'
        ? 'tl-chip-warning'
        : tone === 'danger'
          ? 'tl-chip-danger'
          : tone === 'info'
            ? 'tl-chip-info'
            : 'tl-chip-neutral'
  return <span className={`tl-chip uppercase ${toneClass}`}>{label}</span>
}

export function ProgressBar({ value, max, className = '' }: { value: number; max: number; className?: string }) {
  const pct = max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0
  return (
    <div className={`h-2 rounded-full bg-slate-200 dark:bg-[#072019] ${className}`}>
      <div className="h-2 rounded-full bg-cyan" style={{ width: `${pct}%` }} />
    </div>
  )
}

export function TimeSeriesBars({
  points,
  valueKey,
  accentClass,
  secondaryKey,
  secondaryClass,
}: {
  points: AIOpsOverviewResponse['time_series']
  valueKey: 'requests' | 'total_tokens'
  accentClass: string
  secondaryKey?: 'failures'
  secondaryClass?: string
}) {
  const maxPrimary = Math.max(...points.map((point) => Number(point[valueKey]) || 0), 1)
  const maxSecondary = secondaryKey ? Math.max(...points.map((point) => Number(point[secondaryKey]) || 0), 1) : 1

  return (
    <div className="space-y-2">
      <div className="flex h-28 items-end gap-1">
        {points.map((point) => {
          const primaryHeight = `${Math.max(4, ((Number(point[valueKey]) || 0) / maxPrimary) * 100)}%`
          const secondaryHeight = secondaryKey ? `${Math.max(0, ((Number(point[secondaryKey]) || 0) / maxSecondary) * 38)}%` : '0%'
          return (
            <div key={String(point.bucket)} className="flex min-w-0 flex-1 flex-col justify-end gap-1">
              {secondaryKey && secondaryClass && <div className={`rounded-t ${secondaryClass}`} style={{ height: secondaryHeight }} />}
              <div className={`rounded-t ${accentClass}`} style={{ height: primaryHeight }} />
            </div>
          )
        })}
      </div>
      <div className="flex justify-between gap-2 text-[11px] text-slate dark:text-white/55">
        <span>{points[0]?.bucket ? formatDateOnly(String(points[0].bucket)) : ''}</span>
        <span>{points[points.length - 1]?.bucket ? formatDateOnly(String(points[points.length - 1].bucket)) : ''}</span>
      </div>
    </div>
  )
}

export function LiveTaskCard({ task }: { task: AILiveTaskResponse }) {
  return (
    <div className="rounded-lg border border-slate/10 px-3 py-2 text-sm dark:border-cyan-900/30">
      <div className="flex items-center justify-between gap-3">
        <span className="font-semibold">{formatTaskTypeLabel(task.task_name)}</span>
        <span className="text-xs text-slate dark:text-white/60">{task.worker_name}</span>
      </div>
      <p className="mt-1 text-xs text-slate dark:text-white/60">
        {task.state}
        {task.eta ? ` · eta ${task.eta}` : ''}
        {task.received_at ? ` · received ${task.received_at}` : ''}
      </p>
    </div>
  )
}

export function AuditPreviewList({ entries, emptyLabel }: { entries: AIAuditEntryResponse[]; emptyLabel: string }) {
  if (!entries.length) {
    return <EmptyInline>{emptyLabel}</EmptyInline>
  }
  return (
    <div className="space-y-2">
      {entries.map((entry) => (
        <div key={entry.id} className="rounded-lg border border-slate/10 px-3 py-2 text-sm dark:border-cyan-900/30">
          {(() => {
            const changedFields = Array.isArray(entry.metadata.changed_fields)
              ? (entry.metadata.changed_fields as unknown[]).filter(
                  (field): field is string => typeof field === 'string' && field.trim().length > 0,
                )
              : []
            return (
              <>
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="font-semibold">{entry.action}</p>
              <p className="mt-1 text-xs text-slate dark:text-white/60">{entry.actor_email || 'system'}</p>
            </div>
            <span className="text-xs text-slate dark:text-white/60">{formatTimestamp(entry.created_at)}</span>
          </div>
          {changedFields.length > 0 && (
            <p className="mt-2 text-xs text-slate dark:text-white/60">
              Changed: {changedFields.join(', ')}
            </p>
          )}
              </>
            )
          })()}
        </div>
      ))}
    </div>
  )
}

export function CheckboxRow({
  label,
  checked,
  onChange,
}: {
  label: string
  checked: boolean
  onChange: (checked: boolean) => void
}) {
  return (
    <label className="flex items-center justify-between gap-3 rounded border border-slate/20 bg-white/60 px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]/80">
      <span>{label}</span>
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
    </label>
  )
}

export function TextAreaList({
  label,
  value,
  onChange,
}: {
  label: string
  value: string
  onChange: (value: string) => void
}) {
  return (
    <Field label={label}>
      <textarea
        className="mt-1 h-24 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </Field>
  )
}

export function PromptArea({
  label,
  value,
  onChange,
  error,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  error?: string
}) {
  return (
    <Field label={label}>
      <textarea
        className="mt-1 h-28 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        aria-invalid={Boolean(error)}
      />
      <FieldError message={error} />
    </Field>
  )
}

export function Field({
  label,
  children,
  className = '',
}: {
  label: string
  children: ReactNode
  className?: string
}) {
  return (
    <label className={`text-sm ${className}`}>
      <span className="font-semibold">{label}</span>
      {children}
    </label>
  )
}

export function EmptyInline({ children }: { children: ReactNode }) {
  return <p className="text-sm text-slate dark:text-white/60">{children}</p>
}
