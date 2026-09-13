import { AIAuditEntryResponse, AISettings } from '../types/api'
import { AuditPreviewList, Metric, Panel } from './aiSettingsSupport'
import { formatTimestamp, formatUtcTime } from './aiSettingsUtils'
import type { AISettingsDraft } from './aiSettingsDraft'

export function AiReportBudgetSummary({ settings, draft, isError }: {
  settings: AISettings | undefined
  draft: AISettingsDraft
  isError: boolean
}) {
  const fields = [
    { key: 'report_reserved_output_tokens', label: 'Initial completion', unit: ' tokens' },
    { key: 'report_context_window_tokens', label: 'Context window', unit: ' tokens' },
    { key: 'report_context_safety_percent', label: 'Safety margin', unit: '%' },
  ] as const
  const reportBudgetDirty = fields.some(({ key }) => settings?.[key] !== undefined && Number(draft[key]) !== settings[key])

  return (
    <section aria-labelledby="saved-report-budgets-title" className="rounded-xl border border-cyan/20 bg-cyan/10 p-3 dark:border-cyan-800/40 dark:bg-cyan-950/40">
      <h3 id="saved-report-budgets-title" className="font-semibold">Saved report budgets</h3>
      {settings ? (
        <dl className="mt-2 grid gap-2 text-sm sm:grid-cols-3">
          {fields.map(({ key, label, unit }) => (
            <div key={key}>
              <dt>{label}</dt>
              <dd className="font-semibold">{settings[key] === undefined ? 'Not reported' : `${settings[key].toLocaleString('en-US')}${unit}`}</dd>
            </div>
          ))}
        </dl>
      ) : <p className="mt-2 text-sm">Saved report budgets are unavailable until AI settings load.</p>}
      {isError && settings && <p className="mt-2 text-sm">Last loaded values; AI settings could not be refreshed.</p>}
      <p className="mt-2 text-sm">
        These limits apply with any assigned report provider. Changing a provider default does not change the report budgets.
        Retry output is also limited by the context space left after each prompt.
      </p>
      {reportBudgetDirty && (
        <p role="status" className="mt-2 text-sm">
          Report budget edits are unsaved. Use Save changes to apply them; the values above are from the last loaded settings.
        </p>
      )}
      <a href="#ai-report-budget-controls" className="mt-2 inline-block rounded text-sm font-semibold underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2"
        onClick={(event) => {
          event.preventDefault()
          document.getElementById('ai-report-budget-controls')?.focus()
        }}
      >Review report budget controls</a>
    </section>
  )
}

export function AiConfigurationAudit({
  promptHistory,
  manualActions,
}: {
  promptHistory: AIAuditEntryResponse[]
  manualActions: AIAuditEntryResponse[]
}) {
  return (
    <div className="grid gap-3 lg:grid-cols-2">
      <Panel title="Prompt history" subtitle="Recent AI configuration and prompt changes.">
        <AuditPreviewList entries={promptHistory} emptyLabel="No AI prompt changes yet." />
      </Panel>
      <Panel title="Manual actions" subtitle="Recent admin-triggered AI actions.">
        <AuditPreviewList entries={manualActions} emptyLabel="No manual actions yet." />
      </Panel>
    </div>
  )
}

export function AiConfigurationSidebar({
  settings,
  readiness,
  savePending,
  saveDisabled,
  saveDisabledReason,
  onSave,
}: {
  settings: AISettings | undefined
  readiness: string | null
  savePending: boolean
  saveDisabled: boolean
  saveDisabledReason: string | null
  onSave: () => void
}) {
  return (
    <div className="space-y-3">
      <Panel title="Configuration status" subtitle={readiness ?? 'Loading runtime state...'}>
        <dl className="space-y-2 text-sm">
          <Metric label="Configured" value={settings?.ai_configured ? 'Yes' : 'No'} />
          <Metric label="API key in environment" value={settings?.api_key_configured ? 'Yes' : 'No (optional)'} />
          <Metric label="Legacy model" value={settings?.model || 'Not configured'} />
          <Metric label="Legacy retry attempts" value={settings?.request_max_retries ?? 0} />
          <Metric
            label="Daily brief schedule"
            value={settings ? formatUtcTime(settings.daily_brief_schedule_hour_utc, settings.daily_brief_schedule_minute_utc) : '09:00 UTC'}
          />
          <Metric label="Created" value={settings?.created_at ? formatTimestamp(settings.created_at) : 'n/a'} />
          <Metric label="Updated" value={settings?.updated_at ? formatTimestamp(settings.updated_at) : 'n/a'} />
        </dl>
      </Panel>

      <div className="sticky top-3 rounded-xl border border-slate/20 bg-white/80 p-3 dark:border-cyan-900/40 dark:bg-[#041612]/90">
        <h3 className="font-display text-lg">Save changes</h3>
        <p className="mt-1 text-sm text-slate dark:text-white/70">
          Save legacy provider, shared feature, company-context and prompt settings here. Named provider connections and
          feature assignments have their own Save buttons.
        </p>
        <button
          type="button"
          className="mt-3 w-full rounded bg-ink px-3 py-2 text-sm font-semibold text-white disabled:opacity-50 dark:bg-cyan dark:text-slate-950"
          onClick={onSave}
          disabled={savePending || saveDisabled}
          title={saveDisabledReason ?? undefined}
        >
          {savePending ? 'Saving...' : 'Save changes'}
        </button>
        {saveDisabledReason && (
          <p role="status" aria-live="polite" aria-atomic="true" className="mt-2 text-xs text-amber-700 dark:text-amber-300">
            {saveDisabledReason}
          </p>
        )}
      </div>
    </div>
  )
}
