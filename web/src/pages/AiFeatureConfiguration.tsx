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
