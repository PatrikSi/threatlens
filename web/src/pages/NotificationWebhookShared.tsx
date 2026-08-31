import { NotificationWebhookField } from '../types/api'
import { emptyWebhookField, updateWebhookField } from './notificationWebhookDraft'

export function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-slate/20 p-3 dark:border-cyan-900/40">
      <p className="text-xs font-semibold uppercase text-slate dark:text-white/60">{label}</p>
      <p className="mt-1 text-sm font-semibold">{value}</p>
    </div>
  )
}

export function KeyValueEditor({
  title,
  description,
  fields,
  addLabel,
  keyPlaceholder,
  valuePlaceholder,
  disabled,
  onChange,
}: {
  title: string
  description: string
  fields: NotificationWebhookField[]
  addLabel: string
  keyPlaceholder: string
  valuePlaceholder: string
  disabled: boolean
  onChange: (fields: NotificationWebhookField[]) => void
}) {
  const fieldIdPrefix = title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')

  return (
    <section className="rounded-lg border border-slate/20 p-3 dark:border-cyan-900/40">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h4 className="font-semibold">{title}</h4>
          <p className="mt-1 text-xs text-slate dark:text-white/65">{description}</p>
        </div>
        <button
          type="button"
          className="rounded border border-slate/30 px-3 py-1.5 text-xs font-semibold disabled:opacity-50 dark:border-cyan-900/40"
          disabled={disabled}
          onClick={() => onChange([...fields, emptyWebhookField()])}
        >
          {addLabel}
        </button>
      </div>

      <div className="mt-3 space-y-2">
        {fields.length === 0 && <p className="text-sm text-slate dark:text-white/70">No entries yet.</p>}
        {fields.map((field, index) => {
          const keyId = `${fieldIdPrefix}-${index}-key`
          const valueId = `${fieldIdPrefix}-${index}-value`
          return (
            <div key={`${title}-${index}`} className="grid gap-2 md:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_auto]">
              <label htmlFor={keyId} className="sr-only">
                {title} row {index + 1} key
              </label>
              <input
                id={keyId}
                className="min-w-0 rounded border border-slate/30 bg-white px-3 py-2 text-sm disabled:bg-slate/5 disabled:text-slate/60 dark:border-cyan-900/40 dark:bg-[#072019] dark:disabled:bg-white/[0.03] dark:disabled:text-white/45"
                disabled={disabled}
                value={field.key}
                onChange={(event) => onChange(updateWebhookField(fields, index, { key: event.target.value }))}
                placeholder={keyPlaceholder}
              />
              <label htmlFor={valueId} className="sr-only">
                {title} row {index + 1} value
              </label>
              <input
                id={valueId}
                className="min-w-0 rounded border border-slate/30 bg-white px-3 py-2 text-sm disabled:bg-slate/5 disabled:text-slate/60 dark:border-cyan-900/40 dark:bg-[#072019] dark:disabled:bg-white/[0.03] dark:disabled:text-white/45"
                disabled={disabled}
                value={field.value}
                onChange={(event) => onChange(updateWebhookField(fields, index, { value: event.target.value }))}
                placeholder={valuePlaceholder}
              />
              <button
                type="button"
                className="rounded border border-slate/30 px-3 py-2 text-sm disabled:opacity-50 dark:border-cyan-900/40"
                aria-label={`Remove ${title} row ${index + 1}`}
                disabled={disabled}
                onClick={() => onChange(fields.filter((_, candidateIndex) => candidateIndex !== index))}
              >
                Remove
              </button>
            </div>
          )
        })}
      </div>
    </section>
  )
}
