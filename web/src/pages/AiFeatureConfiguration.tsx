import { CheckboxRow, Field, FieldError, Panel } from './aiSettingsSupport'
import { updateDraft } from './aiSettingsUtils'
import { AiConfigurationDraftProps } from './AiSettingsConfigurationTypes'

export function AiFeatureControls({ draft, setDraft, validation }: AiConfigurationDraftProps) {
  return (
    <Panel title="Feature Controls" subtitle="Enable the AI features that should run and tune the relevance thresholds they rely on.">
      <div className="grid gap-3 md:grid-cols-2">
        <CheckboxRow label="AI article summaries" checked={draft.summary_enabled} onChange={(checked) => updateDraft(setDraft, 'summary_enabled', checked)} />
        <CheckboxRow label="AI relevance scoring" checked={draft.relevance_enabled} onChange={(checked) => updateDraft(setDraft, 'relevance_enabled', checked)} />
        <CheckboxRow
          label="Daily brief generation and panel"
          checked={draft.daily_brief_enabled}
          onChange={(checked) => updateDraft(setDraft, 'daily_brief_enabled', checked)}
        />
        <CheckboxRow
          label="Intelligence report generation"
          checked={draft.reporting_enabled}
          onChange={(checked) => updateDraft(setDraft, 'reporting_enabled', checked)}
        />
        <CheckboxRow label="Auto-enrich new items" checked={draft.auto_enrich_new_items} onChange={(checked) => updateDraft(setDraft, 'auto_enrich_new_items', checked)} />
        <Field label="Medium Relevance Threshold">
          <input
            className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
            value={draft.relevance_medium_threshold}
            onChange={(event) => updateDraft(setDraft, 'relevance_medium_threshold', event.target.value)}
            inputMode="decimal"
            aria-invalid={Boolean(validation.relevance_medium_threshold)}
          />
          <FieldError message={validation.relevance_medium_threshold} />
        </Field>
        <Field label="High Relevance Threshold">
          <input
            className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
            value={draft.relevance_high_threshold}
            onChange={(event) => updateDraft(setDraft, 'relevance_high_threshold', event.target.value)}
            inputMode="decimal"
            aria-invalid={Boolean(validation.relevance_high_threshold)}
          />
          <FieldError message={validation.relevance_high_threshold} />
        </Field>
      </div>
    </Panel>
  )
}

export function AiReportingConfiguration({ draft, setDraft, validation }: AiConfigurationDraftProps) {
  const fields: Array<{ key: keyof typeof draft; label: string; note: string }> = [
    { key: 'report_context_window_tokens', label: 'Model Context Window', note: 'The actual context window supported by the configured model.' },
    { key: 'report_reserved_output_tokens', label: 'Output Token Reserve', note: 'Held back from every report call for valid structured output.' },
    { key: 'report_source_token_cap', label: 'Per-source Token Cap', note: 'Long article text is truncated to this conservative estimate.' },
    { key: 'report_max_sources', label: 'Maximum Sources', note: 'Highest-ranked matching sources frozen into one report.' },
    { key: 'report_max_model_calls', label: 'Maximum Model Calls', note: 'Hard ceiling across evidence batches and report sections.' },
    { key: 'report_context_safety_percent', label: 'Context Safety Margin (%)', note: 'Extra space for tokenizer differences and provider framing.' },
  ]
  return (
    <Panel title="Report Context Guardrails" subtitle="Bound each stage so local and smaller-context models receive predictable work.">
      <div className="grid gap-3 md:grid-cols-2">
        {fields.map((field) => (
          <Field key={field.key} label={field.label}>
            <input className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]" value={String(draft[field.key])} onChange={(event) => updateDraft(setDraft, field.key, event.target.value as never)} inputMode="numeric" aria-invalid={Boolean(validation[field.key])} />
            <FieldError message={validation[field.key]} />
            <span className="mt-1 block text-xs text-slate dark:text-white/60">{field.note}</span>
          </Field>
        ))}
      </div>
    </Panel>
  )
}

export function AiDailyBriefConfiguration({ draft, setDraft, validation }: AiConfigurationDraftProps) {
  return (
    <Panel title="Daily Brief Settings" subtitle="Control when the scheduled daily brief runs, how much content it reviews, and how much history to keep.">
      <div className="grid gap-3 md:grid-cols-2">
        <Field label="Daily Brief Run Time (UTC)">
          <input
            type="time"
            className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
            value={draft.daily_brief_run_time_utc}
            onChange={(event) => updateDraft(setDraft, 'daily_brief_run_time_utc', event.target.value || '09:00')}
            aria-invalid={Boolean(validation.daily_brief_run_time_utc)}
          />
          <FieldError message={validation.daily_brief_run_time_utc} />
          <span className="mt-1 block text-xs text-slate dark:text-white/60">
            Scheduled checks run every 5 minutes and fire after this UTC time. Manual queueing stays available in Operations.
          </span>
        </Field>
        <Field label="Daily Brief Window Hours">
          <input
            className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
            value={draft.daily_brief_window_hours}
            onChange={(event) => updateDraft(setDraft, 'daily_brief_window_hours', event.target.value)}
            inputMode="numeric"
            aria-invalid={Boolean(validation.daily_brief_window_hours)}
          />
          <FieldError message={validation.daily_brief_window_hours} />
          <span className="mt-1 block text-xs text-slate dark:text-white/60">
            How far back ThreatLens looks when building the scheduled brief.
          </span>
        </Field>
        <Field label="Daily Brief Max Articles">
          <input
            className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
            value={draft.daily_brief_max_items}
            onChange={(event) => updateDraft(setDraft, 'daily_brief_max_items', event.target.value)}
            inputMode="numeric"
            aria-invalid={Boolean(validation.daily_brief_max_items)}
          />
          <FieldError message={validation.daily_brief_max_items} />
          <span className="mt-1 block text-xs text-slate dark:text-white/60">
            Cap how many articles are handed to the model for a single brief.
          </span>
        </Field>
        <Field label="Retained Daily Briefings">
          <input
            className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
            value={draft.daily_brief_history_limit}
            onChange={(event) => updateDraft(setDraft, 'daily_brief_history_limit', event.target.value)}
            inputMode="numeric"
            aria-invalid={Boolean(validation.daily_brief_history_limit)}
          />
          <FieldError message={validation.daily_brief_history_limit} />
          <span className="mt-1 block text-xs text-slate dark:text-white/60">
            Keep only the most recent X daily briefings for dashboard selection.
          </span>
        </Field>
      </div>
    </Panel>
  )
}
