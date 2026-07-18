import { type Dispatch, type SetStateAction } from 'react'

import { AIAuditEntryResponse, AISettings, AITestConnectionResponse } from '../types/api'
import { AISettingsDraft, AISettingsDraftValidation } from './aiSettingsDraft'
import {
  AuditPreviewList,
  CheckboxRow,
  Field,
  FieldError,
  Metric,
  Panel,
  PromptArea,
  TextAreaList,
} from './aiSettingsSupport'
import { formatTimestamp, formatUtcTime, updateDraft } from './aiSettingsUtils'

export function ConfigurationTab({
  draft,
  setDraft,
  draftDirty,
  settings,
  readiness,
  isLoading,
  isError,
  errorMessage,
  savePending,
  saveDisabled,
  saveDisabledReason,
  validation,
  onSave,
  onTestConnection,
  testPending,
  testDisabledReason,
  testResult,
  promptHistory,
  manualActions,
}: {
  draft: AISettingsDraft
  setDraft: Dispatch<SetStateAction<AISettingsDraft>>
  draftDirty: boolean
  settings: AISettings | undefined
  readiness: string | null
  isLoading: boolean
  isError: boolean
  errorMessage: string
  savePending: boolean
  saveDisabled: boolean
  saveDisabledReason: string | null
  validation: AISettingsDraftValidation
  onSave: () => void
  onTestConnection: () => void
  testPending: boolean
  testDisabledReason: string | null
  testResult: AITestConnectionResponse | null
  promptHistory: AIAuditEntryResponse[]
  manualActions: AIAuditEntryResponse[]
}) {
  const testSavedConnectionDisabled = testPending || draftDirty || !settings?.ai_configured || Boolean(testDisabledReason)
  const providerTestMessage = draftDirty
    ? 'Save your draft changes first. Test Saved Connection only checks the last saved provider settings.'
    : testDisabledReason
      ? testDisabledReason
      : settings?.ai_configured
        ? 'Test the saved provider configuration. Unsaved draft changes are not included.'
        : 'Save the provider settings before testing the saved connection.'

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(320px,1fr)]">
      <div className="space-y-4">
        {isLoading && (
          <div className="rounded-xl border border-slate/20 bg-white/80 p-4 text-sm dark:border-cyan-900/40 dark:bg-[#041612]/90">
            Loading AI settings...
          </div>
        )}
        {isError && (
          <div className="rounded-xl border border-red-500/20 bg-red-500/10 p-4 text-sm text-red-700 dark:text-red-300">
            Failed to load AI settings. {errorMessage}
          </div>
        )}

        <Panel title="Provider" subtitle="ThreatLens currently speaks to one OpenAI-compatible chat endpoint. Secrets stay in the environment.">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate/15 bg-slate/5 px-3 py-3 dark:border-cyan-900/30 dark:bg-white/[0.03]">
            <div className="text-sm text-slate dark:text-white/70">
              {providerTestMessage}
            </div>
            <button
              type="button"
              className="rounded border border-slate/30 px-3 py-2 text-sm font-semibold disabled:opacity-50 dark:border-cyan-900/40"
              onClick={onTestConnection}
              disabled={testSavedConnectionDisabled}
            >
              {testPending ? 'Testing...' : 'Test Saved Connection'}
            </button>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            <Field label="Base URL">
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={draft.base_url}
                onChange={(event) => updateDraft(setDraft, 'base_url', event.target.value)}
                aria-invalid={Boolean(validation.base_url)}
              />
              <FieldError message={validation.base_url} />
            </Field>
            <Field label="Model">
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={draft.model}
                onChange={(event) => updateDraft(setDraft, 'model', event.target.value)}
                aria-invalid={Boolean(validation.model)}
              />
              <FieldError message={validation.model} />
            </Field>
            <Field label="Temperature">
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={draft.temperature}
                onChange={(event) => updateDraft(setDraft, 'temperature', event.target.value)}
                inputMode="decimal"
                aria-invalid={Boolean(validation.temperature)}
              />
              <FieldError message={validation.temperature} />
            </Field>
            <Field label="Max Completion Tokens">
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={draft.max_completion_tokens}
                onChange={(event) => updateDraft(setDraft, 'max_completion_tokens', event.target.value)}
                inputMode="numeric"
                aria-invalid={Boolean(validation.max_completion_tokens)}
              />
              <FieldError message={validation.max_completion_tokens} />
            </Field>
            <Field label="Request Timeout Seconds" className="md:col-span-2">
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={draft.request_timeout_seconds}
                onChange={(event) => updateDraft(setDraft, 'request_timeout_seconds', event.target.value)}
                inputMode="numeric"
                aria-invalid={Boolean(validation.request_timeout_seconds)}
              />
              <FieldError message={validation.request_timeout_seconds} />
            </Field>
            <Field label="Max Retry Attempts" className="md:col-span-2">
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={draft.request_max_retries}
                onChange={(event) => updateDraft(setDraft, 'request_max_retries', event.target.value)}
                inputMode="numeric"
                aria-invalid={Boolean(validation.request_max_retries)}
              />
              <FieldError message={validation.request_max_retries} />
              <span className="mt-1 block text-xs text-slate dark:text-white/60">
                Retry malformed or failed AI responses up to X additional times before marking the run as failed.
              </span>
            </Field>
          </div>
          {testResult && (
            <div className="mt-4 rounded-xl border border-slate/20 bg-white/70 p-3 text-sm dark:border-cyan-900/40 dark:bg-[#072019]/80">
              <p className="font-semibold">
                {testResult.skipped ? 'Connection test paused' : testResult.success ? 'Connection succeeded' : 'Connection failed'}
              </p>
              <p className="mt-1 text-slate dark:text-white/70">
                Model: {testResult.model || 'unknown'}
                {typeof testResult.latency_ms === 'number' ? `, ${testResult.latency_ms} ms` : ''}
              </p>
              {testResult.skipped && (
                <p className="mt-1 text-slate dark:text-white/70">
                  Running: {testResult.running_task_count ?? 0}, queued: {testResult.queued_task_count ?? 0}
                </p>
              )}
              {testResult.error && <p className="mt-1 text-red-600">{testResult.error}</p>}
            </div>
          )}
        </Panel>

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

        <Panel title="Company Context" subtitle="This context is global so relevance scoring stays consistent across users.">
          <div className="grid gap-3 md:grid-cols-2">
            <Field label="Company Name">
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={draft.company_name}
                onChange={(event) => updateDraft(setDraft, 'company_name', event.target.value)}
                aria-invalid={Boolean(validation.company_name)}
              />
              <FieldError message={validation.company_name} />
            </Field>
            <Field label="Industry">
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={draft.company_industry}
                onChange={(event) => updateDraft(setDraft, 'company_industry', event.target.value)}
                aria-invalid={Boolean(validation.company_industry)}
              />
              <FieldError message={validation.company_industry} />
            </Field>
            <TextAreaList label="Regions" value={draft.company_regions} onChange={(value) => updateDraft(setDraft, 'company_regions', value)} />
            <TextAreaList label="Technology Stack" value={draft.company_stack} onChange={(value) => updateDraft(setDraft, 'company_stack', value)} />
            <TextAreaList label="Priority Topics" value={draft.company_priority_topics} onChange={(value) => updateDraft(setDraft, 'company_priority_topics', value)} />
            <TextAreaList label="Keywords" value={draft.company_keywords} onChange={(value) => updateDraft(setDraft, 'company_keywords', value)} />
            <TextAreaList label="Exclusions" value={draft.company_exclusions} onChange={(value) => updateDraft(setDraft, 'company_exclusions', value)} />
            <Field label="Additional Company Context" className="md:col-span-2">
              <textarea
                className="mt-1 h-28 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                value={draft.company_profile_text}
                onChange={(event) => updateDraft(setDraft, 'company_profile_text', event.target.value)}
                aria-invalid={Boolean(validation.company_profile_text)}
              />
              <FieldError message={validation.company_profile_text} />
            </Field>
          </div>
        </Panel>

        <Panel title="Prompt Tuning" subtitle="Built-in defaults stay visible here, but you can edit and save them directly.">
          <div className="grid gap-3">
            <PromptArea
              label="Item Enrichment System Prompt"
              value={draft.item_enrichment_system_prompt}
              onChange={(value) => updateDraft(setDraft, 'item_enrichment_system_prompt', value)}
              error={validation.item_enrichment_system_prompt}
            />
            <PromptArea
              label="Daily Brief System Prompt"
              value={draft.daily_brief_system_prompt}
              onChange={(value) => updateDraft(setDraft, 'daily_brief_system_prompt', value)}
              error={validation.daily_brief_system_prompt}
            />
            <PromptArea
              label="Global Instructions"
              value={draft.global_instructions}
              onChange={(value) => updateDraft(setDraft, 'global_instructions', value)}
              error={validation.global_instructions}
            />
            <PromptArea
              label="Item Summary Instructions"
              value={draft.item_summary_instructions}
              onChange={(value) => updateDraft(setDraft, 'item_summary_instructions', value)}
              error={validation.item_summary_instructions}
            />
            <PromptArea
              label="Relevance Instructions"
              value={draft.relevance_instructions}
              onChange={(value) => updateDraft(setDraft, 'relevance_instructions', value)}
              error={validation.relevance_instructions}
            />
            <PromptArea
              label="Daily Brief Instructions"
              value={draft.daily_brief_instructions}
              onChange={(value) => updateDraft(setDraft, 'daily_brief_instructions', value)}
              error={validation.daily_brief_instructions}
            />
          </div>
        </Panel>

        <div className="grid gap-4 lg:grid-cols-2">
          <Panel title="Prompt History" subtitle="Recent AI configuration and prompt changes.">
            <AuditPreviewList entries={promptHistory} emptyLabel="No AI prompt changes yet." />
          </Panel>
          <Panel title="Manual Actions" subtitle="Recent admin-triggered AI actions.">
            <AuditPreviewList entries={manualActions} emptyLabel="No manual actions yet." />
          </Panel>
        </div>
      </div>

      <div className="space-y-4">
        <Panel title="Configuration Status" subtitle={readiness ?? 'Loading runtime state...'}>
          <dl className="space-y-2 text-sm">
            <Metric label="Configured" value={settings?.ai_configured ? 'Yes' : 'No'} />
            <Metric label="API Key In Env" value={settings?.api_key_configured ? 'Yes' : 'No / Optional'} />
            <Metric label="Model" value={settings?.model || 'Not configured'} />
            <Metric label="Retry attempts" value={settings?.request_max_retries ?? 0} />
            <Metric
              label="Daily brief schedule"
              value={settings ? formatUtcTime(settings.daily_brief_schedule_hour_utc, settings.daily_brief_schedule_minute_utc) : '09:00 UTC'}
            />
            <Metric label="Created" value={settings?.created_at ? formatTimestamp(settings.created_at) : 'n/a'} />
            <Metric label="Updated" value={settings?.updated_at ? formatTimestamp(settings.updated_at) : 'n/a'} />
          </dl>
        </Panel>

        <div className="sticky top-4 rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
          <h3 className="font-display text-lg">Save Changes</h3>
          <p className="mt-1 text-sm text-slate dark:text-white/70">
            Provider, feature, company-context, and prompt changes affect future AI runs and are recorded in prompt history.
          </p>
          <button
            type="button"
            className="mt-4 w-full rounded bg-ink px-3 py-2 text-sm font-semibold text-white disabled:opacity-50 dark:bg-cyan dark:text-slate-950"
            onClick={onSave}
            disabled={savePending || saveDisabled}
            title={saveDisabledReason ?? undefined}
          >
            {savePending ? 'Saving...' : 'Save Settings'}
          </button>
          {saveDisabledReason && (
            <p role="status" aria-live="polite" aria-atomic="true" className="mt-2 text-xs text-amber-700 dark:text-amber-300">
              {saveDisabledReason}
            </p>
          )}
        </div>
      </div>
    </div>
  )
}
