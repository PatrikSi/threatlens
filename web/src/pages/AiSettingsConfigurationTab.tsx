import { useRef } from 'react'
import { AiConfigurationAudit, AiConfigurationActions, AiReportBudgetSummary } from './AiConfigurationSummary'
import { AiConfigurationSection } from './AiConfigurationSection'
import { revealAiConfigurationTarget } from './aiConfigurationFocus'
import { AiCompanyContextConfiguration, AiPromptConfiguration } from './AiContextConfiguration'
import { AiDailyBriefConfiguration, AiFeatureControls, AiReportingConfiguration } from './AiFeatureConfiguration'
import { AiProviderConfiguration } from './AiProviderConfiguration'
import { AiProviderConnections } from './AiProviderConnections'
import { AiSettingsConfigurationTabProps } from './AiSettingsConfigurationTypes'

export function ConfigurationTab(props: AiSettingsConfigurationTabProps) {
  const container = useRef<HTMLFieldSetElement>(null)
  const draftProps = {
    draft: props.draft,
    setDraft: props.setDraft,
    validation: props.validation,
  }

  return (
    <fieldset ref={container} disabled={props.savePending} className="min-w-0 space-y-3">
      {props.savePending && <p role="status" className="text-sm text-slate dark:text-slate-300">Saving AI settings. Editing resumes when the save completes.</p>}
      <AiConfigurationActions
        settings={props.settings}
        readiness={props.readiness}
        savePending={props.savePending}
        saveDisabled={props.saveDisabled}
        saveDisabledReason={props.saveDisabledReason}
        onSave={props.onSave}
        draftDirty={props.draftDirty}
        validationCount={Object.values(props.validation).filter(Boolean).length}
        onReviewValidation={() => revealAiConfigurationTarget(container.current?.querySelector<HTMLElement>(
          '[data-ai-shared-settings] [aria-invalid="true"]',
        ) ?? null)}
      />
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

      <AiConfigurationSection id="ai-provider-settings" title="Providers, routing and quotas"
        description="Manage named connections and choose the provider for each feature. These settings have their own Save buttons."
        shared={false} initiallyOpen>
        <AiProviderConnections controller={props.providers} />
      </AiConfigurationSection>
      <AiReportBudgetSummary settings={props.settings} draft={props.draft} isError={props.isError} />
      <AiConfigurationSection id="ai-feature-settings" title="Features and daily briefs"
        description="Choose enabled features, relevance thresholds and the briefing schedule.">
        <AiFeatureControls {...draftProps} />
        <AiDailyBriefConfiguration {...draftProps} />
      </AiConfigurationSection>
      <AiConfigurationSection id="ai-report-settings" title="Report budgets"
        description="Set context, output and workload limits independently of provider defaults.">
        <section id="ai-report-budget-controls" aria-label="Report context guardrails" tabIndex={-1}
          className="scroll-mt-40 rounded-xl focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2">
          <AiReportingConfiguration {...draftProps} />
        </section>
      </AiConfigurationSection>
      <AiConfigurationSection id="ai-context-settings" title="Company context"
        description="Describe the organization, technology stack and shared relevance priorities.">
        <AiCompanyContextConfiguration {...draftProps} />
      </AiConfigurationSection>
      <AiConfigurationSection id="ai-prompt-settings" title="Prompts and instructions"
        description="Tune article, relevance and briefing instructions.">
        <AiPromptConfiguration {...draftProps} />
      </AiConfigurationSection>
      <AiConfigurationSection id="ai-legacy-settings" title="Legacy provider settings"
        description="Configure the fallback endpoint, model capabilities and default request budgets.">
        <AiProviderConfiguration
          {...draftProps}
          draftDirty={props.draftDirty}
          configured={props.settings?.ai_configured ?? false}
          testPending={props.testPending}
          testDisabledReason={props.testDisabledReason}
          testResult={props.testResult}
          onTestConnection={props.onTestConnection}
        />
      </AiConfigurationSection>
      <AiConfigurationSection id="ai-configuration-history" title="Configuration history"
        description="Review recent prompt changes and manual actions." shared={false}>
        <AiConfigurationAudit promptHistory={props.promptHistory} manualActions={props.manualActions} />
      </AiConfigurationSection>
    </fieldset>
  )
}
