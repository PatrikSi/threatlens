import { useId } from 'react'
import { Field, FieldError } from './aiSettingsSupport'
import type { ProviderAdmissionDraft } from './aiProviderAdmissionDraft'

type Props = {
  draft: ProviderAdmissionDraft
  validation: Partial<Record<keyof ProviderAdmissionDraft, string>>
  onChange: (key: keyof ProviderAdmissionDraft, value: string) => void
}

export function AiProviderAdmissionFields({ draft, validation, onChange }: Props) {
  const id = useId()
  return (
    <fieldset className="mt-4 rounded border border-slate/20 p-3">
      <legend className="px-1 font-semibold">Provider workload limits</legend>
      <p id={`${id}-help`} className="text-sm text-slate dark:text-white/70">
        Limits apply to this provider across API and worker processes. Use 0 for unlimited.
        The rolling-hour budget includes conservative token reservations when provider usage is unknown.
      </p>
      <div className="mt-3 grid gap-3 md:grid-cols-2">
        {([
          ['max_concurrent_requests', 'Concurrent request limit'],
          ['hourly_token_budget', 'Rolling-hour token budget'],
        ] as const).map(([key, label]) => (
          <Field key={key} label={label}>
            <input className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
              aria-label={label} inputMode="numeric" value={draft[key]} onChange={(event) => onChange(key, event.target.value)}
              aria-invalid={Boolean(validation[key])} aria-describedby={`${id}-help ${id}-${key}-error`} />
            <span id={`${id}-${key}-error`}><FieldError message={validation[key]} /></span>
          </Field>
        ))}
      </div>
    </fieldset>
  )
}
