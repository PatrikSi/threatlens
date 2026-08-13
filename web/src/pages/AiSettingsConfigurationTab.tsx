import { AiConfigurationAudit, AiConfigurationSidebar } from './AiConfigurationSummary'
import { AiCompanyContextConfiguration, AiPromptConfiguration } from './AiContextConfiguration'
import { AiDailyBriefConfiguration, AiFeatureControls } from './AiFeatureConfiguration'
import { AiProviderConfiguration } from './AiProviderConfiguration'
import { AiSettingsConfigurationTabProps } from './AiSettingsConfigurationTypes'

export function ConfigurationTab(props: AiSettingsConfigurationTabProps) {
  const draftProps = {
    draft: props.draft,
    setDraft: props.setDraft,
    validation: props.validation,
  }

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(320px,1fr)]">
      <div className="space-y-4">
        {props.isLoading && (
          <div className="rounded-xl border border-slate/20 bg-white/80 p-4 text-sm dark:border-cyan-900/40 dark:bg-[#041612]/90">
            Loading AI settings...
          </div>
        )}
        {props.isError && (
          <div className="rounded-xl border border-red-500/20 bg-red-500/10 p-4 text-sm text-red-700 dark:text-red-300">
            {props.errorMessage}
          </div>
        )}

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
    </div>
  )
}
