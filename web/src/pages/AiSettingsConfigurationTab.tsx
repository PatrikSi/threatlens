import { AiConfigurationAudit, AiConfigurationSidebar, AiReportBudgetSummary } from './AiConfigurationSummary'
import { AiCompanyContextConfiguration, AiPromptConfiguration } from './AiContextConfiguration'
import { AiDailyBriefConfiguration, AiFeatureControls, AiReportingConfiguration } from './AiFeatureConfiguration'
import { AiProviderConfiguration } from './AiProviderConfiguration'
import { AiProviderConnections } from './AiProviderConnections'
import { AiSettingsConfigurationTabProps } from './AiSettingsConfigurationTypes'

export function ConfigurationTab(props: AiSettingsConfigurationTabProps) {
  const draftProps = {
    draft: props.draft,
    setDraft: props.setDraft,
    validation: props.validation,
  }

  return (
    <fieldset disabled={props.savePending} className="grid min-w-0 gap-3 xl:grid-cols-[minmax(0,2fr)_minmax(320px,1fr)]">
      {props.savePending && <p role="status" className="text-sm text-slate dark:text-slate-300 xl:col-span-2">Saving AI settings. Editing resumes when the save completes.</p>}
      <div className="space-y-3">
        {props.isLoading && (
          <div className="rounded-xl border border-slate/20 bg-white/80 p-3 text-sm dark:border-cyan-900/40 dark:bg-[#041612]/90">
            Loading AI settings...
          </div>
        )}
        {props.isError && (
          <div className="rounded-xl border border-red-500/20 bg-red-500/10 p-3 text-sm text-red-700 dark:text-red-300">
            {props.errorMessage}
          </div>
        )}

        <AiProviderConnections controller={props.providers} />
        <AiReportBudgetSummary settings={props.settings} draft={props.draft} isError={props.isError} />
        <AiProviderConfiguration
          {...draftProps}
          draftDirty={props.draftDirty}
          configured={props.settings?.ai_configured ?? false}
          testPending={props.testPending}
          testDisabledReason={props.testDisabledReason}
          testResult={props.testResult}
          onTestConnection={props.onTestConnection}
        />
        <AiFeatureControls {...draftProps} />
        <AiDailyBriefConfiguration {...draftProps} />
        <section id="ai-report-budget-controls" aria-label="Report context guardrails" tabIndex={-1} className="rounded-xl focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2">
          <AiReportingConfiguration {...draftProps} />
        </section>
        <AiCompanyContextConfiguration {...draftProps} />
        <AiPromptConfiguration {...draftProps} />
        <AiConfigurationAudit promptHistory={props.promptHistory} manualActions={props.manualActions} />
      </div>

      <AiConfigurationSidebar
        settings={props.settings}
        readiness={props.readiness}
        savePending={props.savePending}
        saveDisabled={props.saveDisabled}
        saveDisabledReason={props.saveDisabledReason}
        onSave={props.onSave}
      />
    </fieldset>
  )
}
