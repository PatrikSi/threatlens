import { useState } from 'react'

import type { ReportPromptConfig, ReportSectionConfig } from '../types/api'
import { ExportFilterPanel } from './ExportFilterPanel'
import { parseListInput } from './reportingPageModel'
import type { ReportingController } from './useReportingController'

const INPUT_CLASS = 'mt-1 w-full rounded border border-slate/30 bg-white px-2.5 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]'

export function ReportBuilder({ controller }: { controller: ReportingController }) {
  const capabilities = controller.capabilitiesQuery.data!
  const preview = controller.previewQuery.data
  const filterController = {
    filterDraft: controller.filterDraft,
    setFilterDraft: controller.setFilterDraft,
    validationErrors: controller.validationErrors,
  }

  return (
    <section aria-labelledby="report-builder-heading" className="space-y-3">
      <header className="rounded-lg border border-slate/20 bg-white/80 px-3 py-3 dark:border-cyan-900/40 dark:bg-[#041612]/90 sm:px-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 id="report-builder-heading" className="font-display text-lg">Build a report</h2>
            <p className="mt-0.5 text-xs text-slate dark:text-slate-400">Select evidence, shape the analysis, and inspect context use before queueing.</p>
          </div>
          <select
            aria-label="Report template"
            className="min-h-9 rounded border border-slate/30 bg-white px-2.5 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
            value={controller.selectedTemplateId}
            onChange={(event) => controller.setSelectedTemplateId(event.target.value)}
          >
            {controller.templatesQuery.data?.map((template) => <option key={template.id} value={template.id}>{template.name}</option>)}
          </select>
        </div>
        <TemplateSaveControls controller={controller} />
      </header>

      <div className="grid items-start gap-3 xl:grid-cols-[minmax(0,1.25fr)_minmax(360px,0.75fr)]">
        <ExportFilterPanel capabilities={capabilities} controller={filterController} includeUserStateFilters={false} />
        <div className="space-y-3">
          <PromptPanel controller={controller} />
          <SectionsPanel sections={controller.sections} onChange={controller.setSections} />
          <ContextPanel controller={controller} />
        </div>
      </div>

      {preview && (
        <div className="rounded-lg border border-slate/20 bg-white/80 px-3 py-3 dark:border-cyan-900/40 dark:bg-[#041612]/90 sm:px-4">
          <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
            <h3 className="font-display text-base">Source preview</h3>
            <span className="text-xs text-slate dark:text-slate-400">{preview.estimate.selected_source_count.toLocaleString()} selected, {preview.estimate.omitted_source_count.toLocaleString()} omitted</span>
          </div>
          <div className="divide-y divide-slate/15 overflow-hidden rounded border border-slate/20 dark:divide-white/10 dark:border-white/10">
            {preview.items.map((item) => {
              const excluded = controller.excludedItemIds.includes(item.id)
              return (
                <label key={item.id} className="grid cursor-pointer gap-1 px-2.5 py-2 text-sm hover:bg-slate/5 dark:hover:bg-white/[0.03] sm:grid-cols-[auto_minmax(0,1fr)_auto] sm:items-center sm:gap-3">
                  <input
                    type="checkbox"
                    checked={!excluded}
                    className="mt-0.5 h-4 w-4 accent-cyan sm:mt-0"
                    onChange={() => controller.setExcludedItemIds((current) => excluded ? current.filter((id) => id !== item.id) : [...current, item.id])}
                    aria-label={`${excluded ? 'Include' : 'Exclude'} ${item.title}`}
                  />
                  <span className="min-w-0">
                    <span className="block break-words font-semibold">{item.title}</span>
                    <span className="block text-xs text-slate dark:text-slate-400">{item.feed_name} · {item.classification ?? 'Unclassified'} · {item.ioc_count} IOCs</span>
                  </span>
                  <span className="text-xs tabular-nums text-slate dark:text-slate-400">~{item.estimated_tokens} tokens</span>
                </label>
              )
            })}
          </div>
        </div>
      )}

      <footer className="rounded-lg border border-slate/20 bg-white/80 px-3 py-3 dark:border-cyan-900/40 dark:bg-[#041612]/90 sm:px-4">
        <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-end">
          <div className="grid gap-2 sm:grid-cols-2">
            <label className="text-xs font-semibold text-slate dark:text-slate-300">
              Report title
              <input className={INPUT_CLASS} value={controller.title} maxLength={255} onChange={(event) => controller.setTitle(event.target.value)} placeholder="Use template and period" />
            </label>
            <div className="grid grid-cols-[auto_minmax(0,1fr)] items-end gap-2">
              <label className="flex min-h-10 items-center gap-2 rounded border border-slate/20 px-2.5 text-xs font-semibold dark:border-white/10">
                <input type="checkbox" className="h-4 w-4 accent-cyan" checked={controller.deliverWhenReady} onChange={(event) => controller.setDeliverWhenReady(event.target.checked)} />
                Deliver
              </label>
              <label className="text-xs font-semibold text-slate dark:text-slate-300">
                Delivery content
                <select className={INPUT_CLASS} disabled={!controller.deliverWhenReady} value={controller.deliveryMode} onChange={(event) => controller.setDeliveryMode(event.target.value as typeof controller.deliveryMode)}>
                  <option value="link">Ready notice</option>
                  <option value="summary">Summary</option>
                  <option value="full">Full report</option>
                </select>
              </label>
            </div>
          </div>
          <div>
            {controller.createBlockedReason && <p id="report-create-blocked" className="mb-1 max-w-md text-xs text-slate dark:text-slate-400">{controller.createBlockedReason}</p>}
            <button
              type="button"
              className="min-h-10 w-full rounded bg-ink px-4 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50 dark:bg-cyan dark:text-[#053c2e] lg:w-auto"
              disabled={Boolean(controller.createBlockedReason) || controller.createReportMutation.isPending}
              aria-describedby={controller.createBlockedReason ? 'report-create-blocked' : undefined}
              onClick={() => controller.createReportMutation.mutate()}
            >
              {controller.createReportMutation.isPending ? 'Queueing report...' : 'Generate report'}
            </button>
          </div>
        </div>
      </footer>
    </section>
  )
}

function TemplateSaveControls({ controller }: { controller: ReportingController }) {
  const [mode, setMode] = useState<'create' | 'update' | null>(null)
  const [name, setName] = useState('')
  const [visibility, setVisibility] = useState<'private' | 'shared'>('private')
  const selected = controller.selectedTemplate
  const canUpdate = Boolean(
    selected &&
    !selected.builtin_key &&
    (selected.owner_user_id === controller.currentUser.data?.id || controller.isAdmin),
  )

  function open(nextMode: 'create' | 'update') {
    setMode(nextMode)
    setName(nextMode === 'update' && selected ? selected.name : `${selected?.name ?? 'Intelligence report'} copy`)
    setVisibility(nextMode === 'update' && selected ? selected.visibility : 'private')
  }

  return (
    <div className="mt-3 border-t border-slate/15 pt-3 dark:border-white/10">
      <div className="flex flex-wrap gap-1.5">
        <button type="button" className="rounded border border-slate/20 px-2.5 py-1.5 text-xs font-semibold dark:border-white/10" onClick={() => open('create')}>Save as template</button>
        {canUpdate && <button type="button" className="rounded border border-slate/20 px-2.5 py-1.5 text-xs font-semibold dark:border-white/10" onClick={() => open('update')}>Update template</button>}
      </div>
      {mode && (
        <form
          className="mt-2 grid gap-2 sm:grid-cols-[minmax(0,1fr)_160px_auto] sm:items-end"
          onSubmit={(event) => {
            event.preventDefault()
            controller.templateMutation.mutate(
              { mode, name, visibility },
              { onSuccess: () => setMode(null) },
            )
          }}
        >
          <label className="text-xs font-semibold text-slate dark:text-slate-300">
            Template name
            <input className={INPUT_CLASS} required maxLength={255} value={name} onChange={(event) => setName(event.target.value)} />
          </label>
          <label className="text-xs font-semibold text-slate dark:text-slate-300">
            Visibility
            <select className={INPUT_CLASS} value={visibility} onChange={(event) => setVisibility(event.target.value as typeof visibility)}>
              <option value="private">Private</option>
              {controller.isAdmin && <option value="shared">Shared</option>}
            </select>
          </label>
          <div className="flex gap-1.5">
            <button type="submit" className="min-h-10 rounded bg-ink px-3 py-2 text-xs font-semibold text-white disabled:opacity-50 dark:bg-cyan dark:text-[#053c2e]" disabled={controller.templateMutation.isPending}>{controller.templateMutation.isPending ? 'Saving...' : 'Save'}</button>
            <button type="button" className="min-h-10 rounded border border-slate/20 px-3 py-2 text-xs font-semibold dark:border-white/10" onClick={() => setMode(null)}>Cancel</button>
          </div>
        </form>
      )}
    </div>
  )
}

function PromptPanel({ controller }: { controller: ReportingController }) {
  const update = (values: Partial<ReportPromptConfig>) => controller.setPrompt((current) => ({ ...current, ...values }))
  return (
    <section className="rounded-lg border border-slate/20 bg-white/80 p-3 dark:border-cyan-900/40 dark:bg-[#041612]/90">
      <h3 className="font-display text-base">Analysis brief</h3>
      <div className="mt-2 grid gap-2 sm:grid-cols-2">
        <label className="text-xs font-semibold text-slate dark:text-slate-300">Audience
          <select className={INPUT_CLASS} value={controller.prompt.audience} onChange={(event) => update({ audience: event.target.value })}>
            <option value="security_team">Security team</option><option value="executive">Executives</option><option value="soc">SOC</option><option value="vulnerability_management">Vulnerability management</option>
          </select>
        </label>
        <label className="text-xs font-semibold text-slate dark:text-slate-300">Detail
          <select className={INPUT_CLASS} value={controller.prompt.detail_level} onChange={(event) => update({ detail_level: event.target.value as ReportPromptConfig['detail_level'] })}>
            <option value="brief">Brief</option><option value="standard">Standard</option><option value="detailed">Detailed</option>
          </select>
        </label>
        <label className="text-xs font-semibold text-slate dark:text-slate-300">Tone
          <select className={INPUT_CLASS} value={controller.prompt.tone} onChange={(event) => update({ tone: event.target.value as ReportPromptConfig['tone'] })}>
            <option value="analytical">Analytical</option><option value="concise">Concise</option><option value="executive">Executive</option><option value="technical">Technical</option>
          </select>
        </label>
        <label className="flex items-end pb-2 text-xs font-semibold text-slate dark:text-slate-300">
          <span className="flex min-h-9 w-full items-center gap-2 rounded border border-slate/20 px-2.5 dark:border-white/10"><input type="checkbox" className="h-4 w-4 accent-cyan" checked={controller.prompt.use_company_context} onChange={(event) => update({ use_company_context: event.target.checked })} />Use company context</span>
        </label>
      </div>
      <label className="mt-2 block text-xs font-semibold text-slate dark:text-slate-300">Objective
        <textarea className={`${INPUT_CLASS} min-h-20 resize-y`} maxLength={2000} value={controller.prompt.objective} onChange={(event) => update({ objective: event.target.value })} />
      </label>
      <label className="mt-2 block text-xs font-semibold text-slate dark:text-slate-300">Custom instructions
        <textarea className={`${INPUT_CLASS} min-h-20 resize-y`} maxLength={4000} value={controller.prompt.custom_instructions ?? ''} onChange={(event) => update({ custom_instructions: event.target.value || null })} placeholder="Evidence-grounded constraints or emphasis" />
      </label>
      <div className="mt-2 grid gap-2 sm:grid-cols-2">
        <label className="text-xs font-semibold text-slate dark:text-slate-300">Focus topics
          <input className={INPUT_CLASS} value={controller.prompt.focus_topics.join(', ')} onChange={(event) => update({ focus_topics: parseListInput(event.target.value) })} placeholder="identity, edge devices" />
        </label>
        <label className="text-xs font-semibold text-slate dark:text-slate-300">Exclude topics
          <input className={INPUT_CLASS} value={controller.prompt.excluded_topics.join(', ')} onChange={(event) => update({ excluded_topics: parseListInput(event.target.value) })} placeholder="consumer fraud" />
        </label>
      </div>
    </section>
  )
}

function SectionsPanel({ sections, onChange }: { sections: ReportSectionConfig[]; onChange: React.Dispatch<React.SetStateAction<ReportSectionConfig[]>> }) {
  return (
    <section className="rounded-lg border border-slate/20 bg-white/80 p-3 dark:border-cyan-900/40 dark:bg-[#041612]/90">
      <h3 className="font-display text-base">Report sections</h3>
      <div className="mt-2 space-y-1">
        {sections.map((section, index) => (
          <div key={section.key} className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-2 rounded border border-slate/15 px-2 py-1.5 dark:border-white/10">
            <input type="checkbox" className="h-4 w-4 accent-cyan" checked={section.enabled} onChange={(event) => onChange((current) => current.map((entry) => entry.key === section.key ? { ...entry, enabled: event.target.checked } : entry))} aria-label={`Enable ${section.title}`} />
            <input className="min-w-0 bg-transparent text-sm font-semibold outline-none" value={section.title} maxLength={255} onChange={(event) => onChange((current) => current.map((entry) => entry.key === section.key ? { ...entry, title: event.target.value } : entry))} aria-label={`${section.key} title`} />
            <div className="flex gap-1">
              <button type="button" className="h-7 w-7 rounded border border-slate/20 text-xs disabled:opacity-30 dark:border-white/10" disabled={index === 0} onClick={() => onChange((current) => move(current, index, index - 1))} aria-label={`Move ${section.title} up`}>↑</button>
              <button type="button" className="h-7 w-7 rounded border border-slate/20 text-xs disabled:opacity-30 dark:border-white/10" disabled={index === sections.length - 1} onClick={() => onChange((current) => move(current, index, index + 1))} aria-label={`Move ${section.title} down`}>↓</button>
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}

function ContextPanel({ controller }: { controller: ReportingController }) {
  const estimate = controller.previewQuery.data?.estimate
  const used = estimate ? estimate.estimated_source_tokens + estimate.estimated_fixed_prompt_tokens : 0
  const ratio = estimate ? Math.min(100, Math.round(100 * used / estimate.usable_input_tokens)) : 0
  return (
    <section className="rounded-lg border border-slate/20 bg-white/80 p-3 dark:border-cyan-900/40 dark:bg-[#041612]/90" aria-live="polite">
      <div className="flex items-baseline justify-between gap-2"><h3 className="font-display text-base">Context guardrails</h3><span className="text-xs tabular-nums text-slate dark:text-slate-400">{ratio}% per batch</span></div>
      <div className="mt-2 h-2 overflow-hidden rounded bg-slate/15 dark:bg-white/10"><div className={`h-full ${ratio > 85 ? 'bg-amber-500' : 'bg-cyan'}`} style={{ width: `${ratio}%` }} /></div>
      {controller.previewQuery.isLoading && <p className="mt-2 text-xs text-slate dark:text-slate-400">Estimating context use...</p>}
      {estimate && <div className="mt-2 grid grid-cols-2 gap-2 text-xs sm:grid-cols-4 xl:grid-cols-2">
        <Metric label="Selected" value={estimate.selected_source_count.toLocaleString()} />
        <Metric label="Coverage" value={`${estimate.coverage_percent}%`} />
        <Metric label="Batches" value={estimate.estimated_batches.toLocaleString()} />
        <Metric label="Model calls" value={estimate.estimated_model_calls.toLocaleString()} />
        <Metric label="Input estimate" value={`~${estimate.estimated_source_tokens.toLocaleString()}`} />
        <Metric label="Usable / batch" value={estimate.usable_input_tokens.toLocaleString()} />
      </div>}
      {estimate?.warnings.map((warning) => <p key={warning} className="mt-2 rounded border border-amber-300/60 bg-amber-50 px-2 py-1.5 text-xs text-amber-900 dark:border-amber-700/40 dark:bg-amber-950/20 dark:text-amber-200">{warning}</p>)}
      {controller.previewQuery.isError && <p role="alert" className="mt-2 text-xs text-red-700 dark:text-red-300">The context estimate could not be calculated.</p>}
    </section>
  )
}

function Metric({ label, value }: { label: string; value: string }) { return <div><p className="font-bold uppercase text-slate dark:text-slate-400">{label}</p><p className="mt-0.5 font-semibold tabular-nums">{value}</p></div> }
function move<T>(values: T[], from: number, to: number) { const result = [...values]; const [entry] = result.splice(from, 1); result.splice(to, 0, entry); return result }
