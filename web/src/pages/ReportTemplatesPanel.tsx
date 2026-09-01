import { useIsMutating } from '@tanstack/react-query'

import { resolveApiErrorMessage } from '../api/errors'
import type { ReportTemplate } from '../types/api'
import type { ReportingController } from './useReportingController'

export function ReportTemplatesPanel({ controller }: { controller: ReportingController }) {
  const templates = controller.templatesQuery.data ?? []
  return (
    <section className="rounded-lg border border-slate/20 bg-white/80 dark:border-cyan-900/40 dark:bg-[#041612]/90">
      <header className="border-b border-slate/15 px-3 py-3 dark:border-white/10 sm:px-4">
        <h2 className="font-display text-lg">Report templates</h2>
        <p className="mt-0.5 text-xs text-slate dark:text-slate-400">
          Start from built-in security reporting patterns or maintain private and shared variants.
        </p>
      </header>
      {controller.templatesQuery.isLoading && <p role="status" className="p-4 text-sm">Loading templates...</p>}
      {controller.templatesQuery.isError && <p role="alert" className="p-4 text-sm text-red-700 dark:text-red-300">{resolveApiErrorMessage(controller.templatesQuery.error, 'Report templates could not be loaded')}</p>}
      <div className="grid gap-px bg-slate/15 dark:bg-white/10 sm:grid-cols-2 xl:grid-cols-3">
        {templates.map((template) => <TemplateEntry key={template.id} template={template} controller={controller} />)}
      </div>
    </section>
  )
}

function TemplateEntry({ template, controller }: { template: ReportTemplate; controller: ReportingController }) {
  const isOwner = template.owner_user_id === controller.currentUser.data?.id
  const clonePending = useIsMutating({
    mutationKey: ['reports', 'templates', 'clone'],
    predicate: (mutation) => mutation.state.variables === template.id,
  }) > 0
  const deletePending = useIsMutating({
    mutationKey: ['reports', 'templates', 'delete'],
    predicate: (mutation) => mutation.state.variables === template.id,
  }) > 0
  const actionPending = clonePending || deletePending
  return (
    <article className="min-w-0 bg-white/90 p-3 dark:bg-[#041612]/95 sm:p-4">
      <div className="flex items-start justify-between gap-2">
        <h3 className="break-words font-display text-base">{template.name}</h3>
        <span className="shrink-0 text-[10px] font-bold uppercase text-slate dark:text-slate-400">
          {template.builtin_key ? 'Built-in' : template.visibility}
        </span>
      </div>
      <p className="mt-1 text-sm leading-5 text-slate dark:text-slate-300">{template.description}</p>
      <dl className="mt-3 grid grid-cols-2 gap-2 text-xs">
        <div>
          <dt className="font-bold uppercase text-slate dark:text-slate-400">Audience</dt>
          <dd className="mt-0.5 capitalize">{template.prompt.audience.replaceAll('_', ' ')}</dd>
        </div>
        <div>
          <dt className="font-bold uppercase text-slate dark:text-slate-400">Sections</dt>
          <dd className="mt-0.5">{template.sections.filter((section) => section.enabled).length}</dd>
        </div>
      </dl>
      <div className="mt-3 flex flex-wrap gap-1.5">
        {controller.canAuthor ? (
          <>
            <button
              type="button"
              className="rounded bg-ink px-2.5 py-1.5 text-xs font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50 dark:bg-cyan dark:text-[#053c2e]"
              disabled={actionPending}
              onClick={() => {
                controller.setSelectedTemplateId(template.id)
                controller.setActiveTab('reports')
              }}
            >
              Use template
            </button>
            <button
              type="button"
              className="rounded border border-slate/20 px-2.5 py-1.5 text-xs font-semibold disabled:cursor-not-allowed disabled:opacity-50 dark:border-white/10"
              disabled={actionPending}
              onClick={() => { if (!actionPending) controller.cloneTemplateMutation.mutate(template.id) }}
            >
              {clonePending ? 'Cloning...' : 'Clone'}
            </button>
          </>
        ) : (
          <span className="tl-chip tl-chip-neutral">Read-only</span>
        )}
        {controller.canAuthor && !template.builtin_key && (isOwner || controller.isAdmin) && (
          <button
            type="button"
            className="rounded border border-red-300 px-2.5 py-1.5 text-xs font-semibold text-red-700 disabled:cursor-not-allowed disabled:opacity-50 dark:border-red-800 dark:text-red-300"
            disabled={actionPending}
            onClick={() => {
              if (!actionPending && window.confirm(`Delete ${template.name}?`)) {
                controller.deleteTemplateMutation.mutate(template.id)
              }
            }}
          >
            {deletePending ? 'Deleting...' : 'Delete'}
          </button>
        )}
      </div>
    </article>
  )
}
