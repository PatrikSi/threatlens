import { useMemo, useState } from 'react'

import type {
  ArticleExportFilters,
  ReportSchedule,
  ReportScheduleWrite,
  ReportTemplate,
} from '../types/api'
import { formatReportDate } from './reportingPageModel'
import type { ReportingController } from './useReportingController'

const INPUT_CLASS = 'mt-1 w-full rounded border border-slate/30 bg-white px-2.5 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]'

export function ReportSchedulesPanel({ controller }: { controller: ReportingController }) {
  const [showCreate, setShowCreate] = useState(false)
  const templates = controller.templatesQuery.data ?? []

  return (
    <section className="rounded-lg border border-slate/20 bg-white/80 dark:border-cyan-900/40 dark:bg-[#041612]/90">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-slate/15 px-3 py-3 dark:border-white/10 sm:px-4">
        <div>
          <h2 className="font-display text-lg">Report schedules</h2>
          <p className="mt-0.5 text-xs text-slate dark:text-slate-400">Run weekly or monthly reports in an IANA time zone with bounded catch-up.</p>
        </div>
        <button type="button" className="rounded bg-ink px-3 py-1.5 text-xs font-semibold text-white dark:bg-cyan dark:text-[#053c2e]" onClick={() => setShowCreate((current) => !current)}>
          {showCreate ? 'Close' : 'New schedule'}
        </button>
      </header>

      {showCreate && templates.length > 0 && (
        <ScheduleEditor
          templates={templates}
          submitLabel="Create schedule"
          onCancel={() => setShowCreate(false)}
          onSubmit={(payload) => controller.createScheduleMutation.mutate(payload, {
            onSuccess: () => setShowCreate(false),
          })}
        />
      )}
      {showCreate && templates.length === 0 && (
        <p role="alert" className="border-b border-slate/15 p-4 text-sm text-red-700 dark:border-white/10 dark:text-red-300">Create or restore a report template before adding a schedule.</p>
      )}
      {controller.schedulesQuery.isLoading && <p role="status" className="p-4 text-sm">Loading report schedules...</p>}
      {!controller.schedulesQuery.isLoading && !controller.schedulesQuery.data?.length && <p className="p-4 text-sm text-slate dark:text-slate-300">No report schedules are configured.</p>}
      <div className="divide-y divide-slate/15 dark:divide-white/10">
        {controller.schedulesQuery.data?.map((schedule) => (
          <ScheduleRow key={schedule.id} schedule={schedule} templates={templates} controller={controller} />
        ))}
      </div>
    </section>
  )
}

function ScheduleEditor({
  templates,
  initial,
  submitLabel,
  onSubmit,
  onCancel,
}: {
  templates: ReportTemplate[]
  initial?: ReportSchedule
  submitLabel: string
  onSubmit: (payload: ReportScheduleWrite) => void
  onCancel: () => void
}) {
  const defaults = createScheduleEditorDefaults(templates, initial)
  const [templateId, setTemplateId] = useState(defaults.templateId)
  const [filters, setFilters] = useState<ArticleExportFilters>(defaults.filters)
  const [name, setName] = useState(defaults.name)
  const [enabled, setEnabled] = useState(defaults.enabled)
  const [cadence, setCadence] = useState<ReportSchedule['cadence']>(defaults.cadence)
  const [dayOfWeek, setDayOfWeek] = useState(defaults.dayOfWeek)
  const [dayOfMonth, setDayOfMonth] = useState(defaults.dayOfMonth)
  const [time, setTime] = useState(defaults.time)
  const [timezone, setTimezone] = useState(defaults.timezone)
  const [windowType, setWindowType] = useState<ReportSchedule['window_type']>(defaults.windowType)
  const [rollingDays, setRollingDays] = useState(defaults.rollingDays)
  const [customInstructions, setCustomInstructions] = useState(defaults.customInstructions)
  const [deliveryEnabled, setDeliveryEnabled] = useState(defaults.deliveryEnabled)
  const [deliveryMode, setDeliveryMode] = useState<ReportSchedule['delivery_mode']>(defaults.deliveryMode)
  const [skipEmpty, setSkipEmpty] = useState(defaults.skipEmpty)
  const [missedRunPolicy, setMissedRunPolicy] = useState<ReportSchedule['missed_run_policy']>(defaults.missedRunPolicy)

  const payload = useMemo<ReportScheduleWrite>(() => {
    const [hour, minute] = time.split(':').map(Number)
    return {
      template_id: templateId,
      name,
      enabled,
      cadence,
      day_of_week: dayOfWeek,
      day_of_month: dayOfMonth,
      hour: Number.isInteger(hour) ? hour : 9,
      minute: Number.isInteger(minute) ? minute : 0,
      timezone,
      window_type: windowType,
      rolling_days: rollingDays,
      filters,
      custom_instructions: customInstructions.trim() || null,
      delivery_enabled: deliveryEnabled,
      delivery_mode: deliveryMode,
      skip_empty: skipEmpty,
      missed_run_policy: missedRunPolicy,
    }
  }, [cadence, customInstructions, dayOfMonth, dayOfWeek, deliveryEnabled, deliveryMode, enabled, filters, missedRunPolicy, name, rollingDays, skipEmpty, templateId, time, timezone, windowType])

  function changeCadence(next: ReportSchedule['cadence']) {
    setCadence(next)
    setWindowType(next === 'weekly' ? 'previous_complete_week' : 'previous_complete_month')
  }

  return (
    <form className="grid gap-3 border-b border-slate/15 p-3 dark:border-white/10 sm:grid-cols-2 lg:grid-cols-4" onSubmit={(event) => { event.preventDefault(); onSubmit(payload) }}>
      <label className="text-xs font-semibold">
        Name
        <input required maxLength={255} className={INPUT_CLASS} value={name} onChange={(event) => setName(event.target.value)} />
      </label>
      <label className="text-xs font-semibold">
        Template
        <select
          required
          className={INPUT_CLASS}
          value={templateId}
          onChange={(event) => {
            const nextId = event.target.value
            setTemplateId(nextId)
            setFilters(templates.find((template) => template.id === nextId)?.default_filters ?? emptyFilters())
          }}
        >
          {templates.map((template) => <option key={template.id} value={template.id}>{template.name}</option>)}
        </select>
      </label>
      <label className="text-xs font-semibold">
        Cadence
        <select className={INPUT_CLASS} value={cadence} onChange={(event) => changeCadence(event.target.value as ReportSchedule['cadence'])}>
          <option value="weekly">Weekly</option>
          <option value="monthly">Monthly</option>
        </select>
      </label>
      <label className="text-xs font-semibold">
        {cadence === 'weekly' ? 'Day of week' : 'Day of month'}
        {cadence === 'weekly' ? (
          <select className={INPUT_CLASS} value={dayOfWeek} onChange={(event) => setDayOfWeek(Number(event.target.value))}>
            {['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'].map((label, index) => <option key={label} value={index}>{label}</option>)}
          </select>
        ) : (
          <input className={INPUT_CLASS} type="number" min={1} max={28} value={dayOfMonth} onChange={(event) => setDayOfMonth(Number(event.target.value))} />
        )}
      </label>
      <label className="text-xs font-semibold">
        Local time
        <input className={INPUT_CLASS} type="time" required value={time} onChange={(event) => setTime(event.target.value)} />
      </label>
      <label className="text-xs font-semibold">
        IANA time zone
        <input className={INPUT_CLASS} required maxLength={64} value={timezone} onChange={(event) => setTimezone(event.target.value)} />
      </label>
      <label className="text-xs font-semibold">
        Source window
        <select className={INPUT_CLASS} value={windowType} onChange={(event) => setWindowType(event.target.value as ReportSchedule['window_type'])}>
          {cadence === 'weekly' && <option value="previous_complete_week">Previous complete week</option>}
          {cadence === 'monthly' && <option value="previous_complete_month">Previous complete month</option>}
          <option value="rolling_days">Rolling days</option>
        </select>
      </label>
      {windowType === 'rolling_days' && (
        <label className="text-xs font-semibold">
          Rolling days
          <input className={INPUT_CLASS} type="number" min={1} max={365} value={rollingDays} onChange={(event) => setRollingDays(Number(event.target.value))} />
        </label>
      )}
      <label className="text-xs font-semibold">
        Missed runs
        <select className={INPUT_CLASS} value={missedRunPolicy} onChange={(event) => setMissedRunPolicy(event.target.value as ReportSchedule['missed_run_policy'])}>
          <option value="latest">Generate latest only</option>
          <option value="skip">Skip missed runs</option>
          <option value="all">Catch up, maximum four</option>
        </select>
      </label>
      <label className="text-xs font-semibold sm:col-span-2 lg:col-span-3">
        Additional instructions
        <textarea className={`${INPUT_CLASS} min-h-20 resize-y`} maxLength={4000} value={customInstructions} onChange={(event) => setCustomInstructions(event.target.value)} placeholder="Optional schedule-specific emphasis" />
      </label>
      <div className="grid gap-2 sm:grid-cols-2 lg:col-span-4 lg:grid-cols-4">
        <Toggle label="Enabled" checked={enabled} onChange={setEnabled} />
        <Toggle label="Skip periods with no sources" checked={skipEmpty} onChange={setSkipEmpty} />
        <Toggle label="Deliver when ready" checked={deliveryEnabled} onChange={setDeliveryEnabled} />
        <label className="text-xs font-semibold">
          Delivery content
          <select className={INPUT_CLASS} disabled={!deliveryEnabled} value={deliveryMode} onChange={(event) => setDeliveryMode(event.target.value as ReportSchedule['delivery_mode'])}>
            <option value="link">Ready notice</option>
            <option value="summary">Summary</option>
            <option value="full">Full report</option>
          </select>
        </label>
      </div>
      <div className="flex gap-1.5 sm:col-span-2 lg:col-span-4 lg:justify-end">
        <button type="button" className="min-h-10 rounded border border-slate/20 px-3 py-2 text-sm font-semibold dark:border-white/10" onClick={onCancel}>Cancel</button>
        <button type="submit" className="min-h-10 rounded bg-ink px-3 py-2 text-sm font-semibold text-white dark:bg-cyan dark:text-[#053c2e]">{submitLabel}</button>
      </div>
    </form>
  )
}

function ScheduleRow({ schedule, templates, controller }: { schedule: ReportSchedule; templates: ReportTemplate[]; controller: ReportingController }) {
  const [editing, setEditing] = useState(false)
  const failureState = schedule.failure_state ?? 'healthy'
  return (
    <article>
      <div className="grid gap-2 px-3 py-3 sm:px-4 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="break-words font-semibold">{schedule.name}</h3>
            <span className={`rounded border px-1.5 py-0.5 text-[10px] font-bold uppercase ${schedule.enabled ? 'border-emerald-300 text-emerald-700 dark:border-emerald-800 dark:text-emerald-300' : 'border-slate/30 text-slate'}`}>{schedule.enabled ? 'Enabled' : 'Paused'}</span>
            {failureState !== 'healthy' && (
              <span className="rounded border border-amber-300 px-1.5 py-0.5 text-[10px] font-bold uppercase text-amber-800 dark:border-amber-700 dark:text-amber-200">
                {failureState}
              </span>
            )}
          </div>
          <p className="mt-1 text-xs capitalize text-slate dark:text-slate-400">{schedule.cadence} · {schedule.window_type.replaceAll('_', ' ')} · {String(schedule.hour).padStart(2, '0')}:{String(schedule.minute).padStart(2, '0')} {schedule.timezone}</p>
          <p className="mt-0.5 text-xs text-slate dark:text-slate-400">Next run: {formatReportDate(schedule.next_run_at)}</p>
          {failureState !== 'healthy' && schedule.last_error && (
            <p role="alert" className="mt-1 text-xs text-amber-800 dark:text-amber-200">
              {schedule.last_error}
              {schedule.retry_at ? ` Next retry: ${formatReportDate(schedule.retry_at)}.` : ''}
            </p>
          )}
        </div>
        <div className="flex flex-wrap gap-1.5">
          <button type="button" className="rounded border border-slate/20 px-2.5 py-1.5 text-xs font-semibold dark:border-white/10" onClick={() => setEditing((current) => !current)}>{editing ? 'Close' : 'Edit'}</button>
          <button type="button" className="rounded border border-slate/20 px-2.5 py-1.5 text-xs font-semibold dark:border-white/10" onClick={() => controller.updateScheduleMutation.mutate({ ...schedule, enabled: !schedule.enabled })}>{schedule.enabled ? 'Pause' : 'Enable'}</button>
          <button type="button" className="rounded border border-slate/20 px-2.5 py-1.5 text-xs font-semibold dark:border-white/10" onClick={() => controller.runScheduleMutation.mutate(schedule.id)}>Run now</button>
          <button type="button" className="rounded border border-red-300 px-2.5 py-1.5 text-xs font-semibold text-red-700 dark:border-red-800 dark:text-red-300" onClick={() => { if (window.confirm(`Delete ${schedule.name}?`)) controller.deleteScheduleMutation.mutate(schedule.id) }}>Delete</button>
        </div>
      </div>
      {editing && (
        <ScheduleEditor
          templates={templates}
          initial={schedule}
          submitLabel="Save schedule"
          onCancel={() => setEditing(false)}
          onSubmit={(payload) => controller.updateScheduleMutation.mutate(
            { ...schedule, ...payload },
            { onSuccess: () => setEditing(false) },
          )}
        />
      )}
    </article>
  )
}

function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (value: boolean) => void }) {
  return (
    <label className="flex min-h-10 items-center gap-2 rounded border border-slate/20 px-2.5 text-xs font-semibold dark:border-white/10">
      <input type="checkbox" className="h-4 w-4 accent-cyan" checked={checked} onChange={(event) => onChange(event.target.checked)} />
      {label}
    </label>
  )
}

function createScheduleEditorDefaults(templates: ReportTemplate[], initial?: ReportSchedule) {
  if (initial) {
    return existingScheduleEditorDefaults(initial)
  }
  const firstTemplate = templates[0]
  return {
    templateId: firstTemplate?.id ?? '',
    filters: firstTemplate?.default_filters ?? emptyFilters(),
    name: 'Weekly Threat Landscape',
    enabled: true,
    cadence: 'weekly' as const,
    dayOfWeek: 0,
    dayOfMonth: 1,
    time: '09:00',
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
    windowType: 'previous_complete_week' as const,
    rollingDays: 7,
    customInstructions: '',
    deliveryEnabled: false,
    deliveryMode: 'summary' as const,
    skipEmpty: true,
    missedRunPolicy: 'latest' as const,
  }
}

function existingScheduleEditorDefaults(schedule: ReportSchedule) {
  return {
    templateId: schedule.template_id,
    filters: schedule.filters,
    name: schedule.name,
    enabled: schedule.enabled,
    cadence: schedule.cadence,
    dayOfWeek: schedule.day_of_week,
    dayOfMonth: schedule.day_of_month,
    time: `${String(schedule.hour).padStart(2, '0')}:${String(schedule.minute).padStart(2, '0')}`,
    timezone: schedule.timezone,
    windowType: schedule.window_type,
    rollingDays: schedule.rolling_days,
    customInstructions: schedule.custom_instructions ?? '',
    deliveryEnabled: schedule.delivery_enabled,
    deliveryMode: schedule.delivery_mode,
    skipEmpty: schedule.skip_empty,
    missedRunPolicy: schedule.missed_run_policy,
  }
}

function emptyFilters(): ArticleExportFilters {
  return {
    q: null,
    feed_ids: [],
    tag_ids: [],
    tags_mode: 'any',
    classifications: [],
    ai_relevance_labels: [],
    ai_score_min: null,
    ai_score_max: null,
    is_read: null,
    is_starred: null,
    has_article_text: null,
    since: null,
    until: null,
    date_basis: 'published_at_or_first_seen_at',
    sort: 'published_at_desc',
  }
}
