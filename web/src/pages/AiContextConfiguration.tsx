import { Field, FieldError, Panel, PromptArea, TextAreaList } from './aiSettingsSupport'
import { updateDraft } from './aiSettingsUtils'
import { AiConfigurationDraftProps } from './AiSettingsConfigurationTypes'

export function AiCompanyContextConfiguration({ draft, setDraft, validation }: AiConfigurationDraftProps) {
  return (
    <Panel title="Company context" subtitle="This context is global so relevance scoring stays consistent across users.">
      <div className="grid gap-3 md:grid-cols-2">
        <Field label="Company name">
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
        <TextAreaList label="Technology stack" value={draft.company_stack} onChange={(value) => updateDraft(setDraft, 'company_stack', value)} />
        <TextAreaList label="Priority topics" value={draft.company_priority_topics} onChange={(value) => updateDraft(setDraft, 'company_priority_topics', value)} />
        <TextAreaList label="Keywords" value={draft.company_keywords} onChange={(value) => updateDraft(setDraft, 'company_keywords', value)} />
        <TextAreaList label="Exclusions" value={draft.company_exclusions} onChange={(value) => updateDraft(setDraft, 'company_exclusions', value)} />
        <Field label="Additional company context" className="md:col-span-2">
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
  )
}

export function AiPromptConfiguration({ draft, setDraft, validation }: AiConfigurationDraftProps) {
  return (
    <Panel title="Prompt tuning" subtitle="Built-in defaults stay visible here, but you can edit and save them directly.">
      <div className="grid gap-3">
        <PromptArea
          label="Item enrichment system prompt"
          value={draft.item_enrichment_system_prompt}
          onChange={(value) => updateDraft(setDraft, 'item_enrichment_system_prompt', value)}
          error={validation.item_enrichment_system_prompt}
        />
        <PromptArea
          label="Daily brief system prompt"
          value={draft.daily_brief_system_prompt}
          onChange={(value) => updateDraft(setDraft, 'daily_brief_system_prompt', value)}
          error={validation.daily_brief_system_prompt}
        />
        <PromptArea
          label="Global instructions"
          value={draft.global_instructions}
          onChange={(value) => updateDraft(setDraft, 'global_instructions', value)}
          error={validation.global_instructions}
        />
        <PromptArea
          label="Item summary instructions"
          value={draft.item_summary_instructions}
          onChange={(value) => updateDraft(setDraft, 'item_summary_instructions', value)}
          error={validation.item_summary_instructions}
        />
        <PromptArea
          label="Relevance instructions"
          value={draft.relevance_instructions}
          onChange={(value) => updateDraft(setDraft, 'relevance_instructions', value)}
          error={validation.relevance_instructions}
        />
        <PromptArea
          label="Daily brief instructions"
          value={draft.daily_brief_instructions}
          onChange={(value) => updateDraft(setDraft, 'daily_brief_instructions', value)}
          error={validation.daily_brief_instructions}
        />
      </div>
    </Panel>
  )
}
