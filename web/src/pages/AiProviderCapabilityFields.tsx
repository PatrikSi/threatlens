import { useId } from 'react'
import { Field, FieldError } from './aiSettingsSupport'
import { REASONING_EFFORTS, type ProviderCapabilitiesDraft } from './aiProviderCapabilitiesDraft'

const inputClass = 'mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]'

type Props = {
  draft: ProviderCapabilitiesDraft
  validation: Partial<Record<keyof ProviderCapabilitiesDraft, string>>
  onChange: (key: keyof ProviderCapabilitiesDraft, value: ProviderCapabilitiesDraft[keyof ProviderCapabilitiesDraft]) => void
}

export function AiProviderCapabilityFields({ draft, validation, onChange }: Props) {
  const id = useId()
  const invalidCapabilities = (['request_dialect', 'reasoning_effort', 'structured_output_mode',
    'model_context_window_tokens', 'model_max_output_tokens'] as const).some((key) => Boolean(validation[key]))
  return (
    <details className="mt-4 rounded border border-slate/20 p-3" open={invalidCapabilities || undefined}>
      <summary className="cursor-pointer font-semibold">Model compatibility and limits</summary>
      <p id={`${id}-help`} className="mt-2 text-sm text-slate dark:text-white/70">
        Copy capabilities from your provider’s documentation for this exact model. Model names do not select these settings
        automatically. Reasoning and JSON options vary by model; a listed option does not guarantee support.
      </p>
      <div className="mt-3 grid gap-3 md:grid-cols-2">
        <Field label="Request format">
          <select className={inputClass} aria-label="Request format" aria-describedby={`${id}-help`}
            value={draft.request_dialect} onChange={(event) => onChange('request_dialect', event.target.value as ProviderCapabilitiesDraft['request_dialect'])}>
            <option value="chat_completions">Compatible chat (max_tokens)</option>
            <option value="chat_completions_modern">Modern chat (max_completion_tokens)</option>
          </select>
          <FieldError message={validation.request_dialect} />
        </Field>
        <Field label="Reasoning effort">
          <select className={inputClass} aria-label="Reasoning effort" aria-describedby={`${id}-help`}
            value={draft.reasoning_effort} onChange={(event) => onChange('reasoning_effort', event.target.value as ProviderCapabilitiesDraft['reasoning_effort'])}>
            <option value="">Omit (provider default)</option>
            {REASONING_EFFORTS.map((effort) => <option key={effort} value={effort}>{effort}</option>)}
          </select>
          <FieldError message={validation.reasoning_effort} />
        </Field>
        <Field label="JSON response mode">
          <select className={inputClass} aria-label="JSON response mode" value={draft.structured_output_mode}
            onChange={(event) => onChange('structured_output_mode', event.target.value as ProviderCapabilitiesDraft['structured_output_mode'])}>
            <option value="off">Off (prompt instructions only)</option>
            <option value="json_object">Provider JSON object mode</option>
          </select>
          <span className="mt-1 block text-xs">JSON object mode does not guarantee schema compliance or factual accuracy.</span>
          <FieldError message={validation.structured_output_mode} />
        </Field>
        {([
          ['model_context_window_tokens', 'Model context limit (tokens)'],
          ['model_max_output_tokens', 'Model output limit (tokens)'],
        ] as const).map(([key, label]) => (
          <Field key={key} label={label}>
            <input className={inputClass} aria-label={label} inputMode="numeric" placeholder="Unverified"
              value={draft[key]} onChange={(event) => onChange(key, event.target.value)}
              aria-invalid={Boolean(validation[key])} aria-describedby={`${id}-${key}-help ${id}-${key}-error`} />
            <span id={`${id}-${key}-help`} className="mt-1 block text-xs">
              Leave blank if unverified. Set the documented model limit to reject oversized requests before sending them.
            </span>
            <span id={`${id}-${key}-error`}><FieldError message={validation[key]} /></span>
          </Field>
        ))}
      </div>
      <p className="mt-3 text-xs">
        Context checks estimate the assembled input, reserve 15% for tokenization differences plus protocol overhead,
        and include the requested output. Reports also use their report budgets and safety margin. Reasoning can consume
        the completion allowance. Over-budget requests fail with guidance; these checks do not shorten prompts automatically.
      </p>
    </details>
  )
}
