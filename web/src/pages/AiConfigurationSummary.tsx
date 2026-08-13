import { AIAuditEntryResponse, AISettings } from '../types/api'
import { AuditPreviewList, Metric, Panel } from './aiSettingsSupport'
import { formatTimestamp, formatUtcTime } from './aiSettingsUtils'

export function AiConfigurationAudit({
  promptHistory,
  manualActions,
}: {
  promptHistory: AIAuditEntryResponse[]
  manualActions: AIAuditEntryResponse[]
}) {
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Panel title="Prompt History" subtitle="Recent AI configuration and prompt changes.">
        <AuditPreviewList entries={promptHistory} emptyLabel="No AI prompt changes yet." />
      </Panel>
      <Panel title="Manual Actions" subtitle="Recent admin-triggered AI actions.">
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
  )
}
