import { FormEvent } from 'react'
import { Link, useLocation } from 'react-router-dom'

import { resolveApiErrorMessage } from '../api/errors'
import { DialogSurface } from '../components/ConfirmDialog'
import type { InvestigationSeverity, InvestigationStatus, InvestigationSummary } from '../types/investigations'
import { formatDateTime } from '../utils/datetime'
import {
  INVESTIGATION_PAGE_SIZE,
  INVESTIGATION_SEVERITIES,
  INVESTIGATION_STATUSES,
  investigationResultRange,
} from './investigationPageModel'
import {
  InvestigationLoading,
  InvestigationPageError,
  InvestigationRefreshWarning,
  InvestigationSeverityChip,
  InvestigationStatusChip,
} from './InvestigationShared'
import type { InvestigationsPageController } from './useInvestigationsPage'

export function InvestigationListWorkspace({ controller }: { controller: InvestigationsPageController }) {
  const location = useLocation()
  const { investigationsQuery, filters } = controller
  const data = investigationsQuery.data
  const activeFilterCount = filters.statuses.length
    + filters.severities.length
    + Number(filters.assignedToMe)
    + Number(filters.includeArchived)
    + Number(Boolean(filters.query))

  return (
    <section className="tl-surface min-w-0 overflow-hidden rounded-xl" aria-labelledby="investigations-heading">
      <header className="border-b border-slate/20 px-3 py-3 dark:border-white/10 sm:px-4 sm:py-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h1 id="investigations-heading" className="font-display text-xl sm:text-2xl">Investigations</h1>
            <p className="mt-1 max-w-3xl text-sm text-slate dark:text-slate-300">
              Organize evidence, analyst notes, ownership, and lifecycle decisions in one auditable workspace.
            </p>
          </div>
          {controller.canCreate && (
            <button
              type="button"
              className="min-h-11 w-full rounded bg-ink px-3 py-2 text-sm font-semibold text-white sm:w-auto dark:bg-cyan dark:text-[#053c2e]"
              onClick={() => controller.setCreateOpen(true)}
            >
              Create investigation
            </button>
          )}
        </div>

        <InvestigationFilters controller={controller} activeFilterCount={activeFilterCount} />

        <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs text-slate dark:text-slate-400">
          <span aria-live="polite">
            {data
              ? investigationResultRange(data.total, data.page, data.page_size, data.investigations.length)
              : 'Loading result count...'}
            {investigationsQuery.isFetching && data ? ' · Updating results...' : ''}
          </span>
          {investigationsQuery.dataUpdatedAt > 0 && (
            <span>Updated {formatDateTime(new Date(investigationsQuery.dataUpdatedAt))}</span>
          )}
        </div>
        {investigationsQuery.isPlaceholderData && (
          <p role="status" className="mt-2 text-xs text-amber-800 dark:text-amber-200">
            Showing the previous results while the current filters load.
          </p>
        )}
      </header>

      {investigationsQuery.isError && data && (
        <div className="px-3 pt-3 sm:px-4">
          <InvestigationRefreshWarning onRetry={() => void investigationsQuery.refetch()}>
            {resolveApiErrorMessage(investigationsQuery.error, 'Investigations could not be refreshed')} The last loaded results remain visible.
          </InvestigationRefreshWarning>
        </div>
      )}

      {!data && investigationsQuery.isLoading && <InvestigationLoading message="Loading investigations..." />}
      {!data && investigationsQuery.isError && (
        <div className="p-3 sm:p-4">
          <InvestigationPageError
            error={investigationsQuery.error}
            fallback="Investigations could not be loaded"
            onRetry={() => void investigationsQuery.refetch()}
          />
        </div>
      )}

      {data && data.investigations.length === 0 && (
        <div className="px-4 py-10 text-center">
          <h2 className="text-base font-semibold">No matching investigations</h2>
          <p className="mt-1 text-sm text-slate dark:text-slate-300">
            {activeFilterCount > 0
              ? 'Clear or broaden the current filters to see more investigations.'
              : controller.canCreate
                ? 'Create the first investigation to begin collecting evidence and analyst decisions.'
                : 'No investigations are currently visible to your account.'}
          </p>
          {filters.page > 1 && (
            <button
              type="button"
              className="mt-4 min-h-11 rounded border border-slate/30 px-3 py-2 text-sm font-semibold md:min-h-0 dark:border-white/15"
              onClick={() => controller.updateFilters({ page: 1 })}
            >
              Return to first page
            </button>
          )}
          {activeFilterCount > 0 && filters.page === 1 && (
            <button
              type="button"
              className="mt-4 min-h-11 rounded border border-slate/30 px-3 py-2 text-sm font-semibold md:min-h-0 dark:border-white/15"
              onClick={controller.clearFilters}
            >
              Clear filters
            </button>
          )}
        </div>
      )}

      {data && data.investigations.length > 0 && (
        <>
          <InvestigationDesktopTable investigations={data.investigations} listSearch={location.search} />
          <InvestigationMobileCards investigations={data.investigations} listSearch={location.search} />
          <InvestigationPagination
            page={data.page}
            total={data.total}
            pageSize={data.page_size || INVESTIGATION_PAGE_SIZE}
            disabled={investigationsQuery.isFetching}
            onPageChange={(page) => controller.updateFilters({ page })}
          />
        </>
      )}

      <CreateInvestigationDialog controller={controller} />
    </section>
  )
}

function InvestigationFilters({
  controller,
  activeFilterCount,
}: {
  controller: InvestigationsPageController
  activeFilterCount: number
}) {
  const { filters } = controller
  return (
    <div className="mt-3 border-t border-slate/15 pt-3 dark:border-white/10">
      <form className="flex min-w-0 flex-col gap-2 sm:flex-row" onSubmit={controller.submitSearch}>
        <label htmlFor="investigation-search" className="sr-only">Search investigations</label>
        <input
          id="investigation-search"
          type="search"
          maxLength={255}
          className="min-h-11 min-w-0 flex-1 rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
          value={controller.searchDraft}
          onChange={(event) => controller.setSearchDraft(event.target.value)}
          placeholder="Search title or description"
        />
        <button type="submit" className="min-h-11 rounded border border-slate/30 px-3 py-2 text-sm font-semibold dark:border-white/15">
          Search
        </button>
      </form>

      <div className="mt-2 flex min-w-0 flex-wrap items-center gap-2">
        <FilterMenu
          label="Status"
          options={INVESTIGATION_STATUSES}
          selected={filters.statuses}
          onToggle={(value) => controller.toggleStatus(value as InvestigationStatus)}
        />
        <FilterMenu
          label="Severity"
          options={INVESTIGATION_SEVERITIES}
          selected={filters.severities}
          onToggle={(value) => controller.toggleSeverity(value as InvestigationSeverity)}
        />
        <label className="flex min-h-11 items-center gap-2 rounded border border-slate/20 px-3 py-2 text-sm md:min-h-0 md:py-1.5 dark:border-white/10">
          <input
            type="checkbox"
            className="accent-cyan"
            checked={filters.assignedToMe}
            onChange={(event) => controller.updateFilters({ assignedToMe: event.target.checked })}
          />
          Assigned to me
        </label>
        <label className="flex min-h-11 items-center gap-2 rounded border border-slate/20 px-3 py-2 text-sm md:min-h-0 md:py-1.5 dark:border-white/10">
          <input
            type="checkbox"
            className="accent-cyan"
            checked={filters.includeArchived}
            onChange={(event) => controller.updateFilters({ includeArchived: event.target.checked })}
          />
          Include archived
        </label>
        {activeFilterCount > 0 && (
          <button
            type="button"
            className="min-h-11 rounded px-3 py-2 text-sm font-semibold text-cyan hover:bg-cyan/10 md:min-h-0 md:py-1.5"
            onClick={controller.clearFilters}
          >
            Clear ({activeFilterCount})
          </button>
        )}
      </div>
    </div>
  )
}

function FilterMenu<T extends string>({
  label,
  options,
  selected,
  onToggle,
}: {
  label: string
  options: ReadonlyArray<{ value: T; label: string }>
  selected: T[]
  onToggle: (value: T) => void
}) {
  return (
    <details className="relative">
      <summary className="flex min-h-11 cursor-pointer list-none items-center rounded border border-slate/20 px-3 py-2 text-sm font-semibold md:min-h-0 md:py-1.5 dark:border-white/10">
        {label}{selected.length > 0 ? ` (${selected.length})` : ''}
      </summary>
      <fieldset className="absolute left-0 z-20 mt-1 min-w-48 space-y-1 rounded border border-slate/20 bg-white p-2 shadow-lg dark:border-cyan-900/50 dark:bg-[#041612]">
        <legend className="sr-only">Filter by {label.toLowerCase()}</legend>
        {options.map((option) => (
          <label key={option.value} className="flex min-h-11 items-center gap-2 rounded px-2 py-2 text-sm hover:bg-cyan/10">
            <input
              type="checkbox"
              className="accent-cyan"
              checked={selected.includes(option.value)}
              onChange={() => onToggle(option.value)}
            />
            {option.label}
          </label>
        ))}
      </fieldset>
    </details>
  )
}

function InvestigationDesktopTable({ investigations, listSearch }: { investigations: InvestigationSummary[]; listSearch: string }) {
  return (
    <div className="hidden min-w-0 overflow-x-auto md:block">
      <table className="w-full table-fixed text-left text-sm" aria-label="Investigations">
        <thead className="border-b border-slate/20 bg-slate/5 text-xs uppercase text-slate dark:border-white/10 dark:bg-white/[0.025] dark:text-slate-400">
          <tr>
            <th scope="col" className="w-[34%] px-4 py-2 font-semibold">Investigation</th>
            <th scope="col" className="w-[18%] px-3 py-2 font-semibold">State</th>
            <th scope="col" className="w-[20%] px-3 py-2 font-semibold">Assignment</th>
            <th scope="col" className="w-[12%] px-3 py-2 font-semibold">Content</th>
            <th scope="col" className="w-[16%] px-3 py-2 text-right font-semibold">Updated</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate/15 dark:divide-white/10">
          {investigations.map((investigation) => (
            <tr key={investigation.id} className="align-top hover:bg-cyan/5">
              <th scope="row" className="min-w-0 px-4 py-3 font-normal">
                <Link className="break-words font-semibold text-ink hover:text-cyan dark:text-slate-100 dark:hover:text-cyan-200" to={`/investigations/${investigation.id}`} state={{ investigationListSearch: listSearch }}>
                  {investigation.title}
                </Link>
                {investigation.description && <p className="mt-1 line-clamp-2 text-xs text-slate dark:text-slate-400">{investigation.description}</p>}
              </th>
              <td className="px-3 py-3"><div className="flex flex-wrap gap-1.5"><InvestigationStatusChip status={investigation.status} /><InvestigationSeverityChip severity={investigation.severity} /></div></td>
              <td className="min-w-0 px-3 py-3 text-xs">
                <p className="truncate">{investigation.assignee_email ?? 'Unassigned'}</p>
                <p className="mt-1 capitalize text-slate dark:text-slate-400">{investigation.current_user_role ? `${investigation.current_user_role} access` : 'Team read-only'}</p>
              </td>
              <td className="px-3 py-3 text-xs text-slate dark:text-slate-300">
                <p>{investigation.evidence_count} evidence</p>
                <p className="mt-1">{investigation.note_count} notes</p>
              </td>
              <td className="px-3 py-3 text-right text-xs text-slate dark:text-slate-400">
                <time dateTime={investigation.updated_at}>{formatDateTime(investigation.updated_at)}</time>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function InvestigationMobileCards({ investigations, listSearch }: { investigations: InvestigationSummary[]; listSearch: string }) {
  return (
    <div className="space-y-2 px-2 py-2 md:hidden" data-layout="mobile-cards">
      {investigations.map((investigation) => (
        <article key={investigation.id} className="min-w-0 rounded border border-slate/20 bg-white/60 p-3 dark:border-white/10 dark:bg-white/[0.025]">
          <div className="flex min-w-0 items-start justify-between gap-2">
            <Link className="flex min-h-11 min-w-0 items-center break-words font-semibold leading-snug" to={`/investigations/${investigation.id}`} state={{ investigationListSearch: listSearch }}>{investigation.title}</Link>
            <InvestigationSeverityChip severity={investigation.severity} />
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-2"><InvestigationStatusChip status={investigation.status} /><span className="text-xs capitalize text-slate dark:text-slate-400">{investigation.current_user_role ?? 'team read-only'}</span></div>
          <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 text-xs">
            <div className="min-w-0"><dt className="text-slate dark:text-slate-400">Assigned</dt><dd className="mt-0.5 truncate">{investigation.assignee_email ?? 'Unassigned'}</dd></div>
            <div><dt className="text-slate dark:text-slate-400">Content</dt><dd className="mt-0.5">{investigation.evidence_count} evidence · {investigation.note_count} notes</dd></div>
            <div className="col-span-2"><dt className="text-slate dark:text-slate-400">Updated</dt><dd className="mt-0.5"><time dateTime={investigation.updated_at}>{formatDateTime(investigation.updated_at)}</time></dd></div>
          </dl>
        </article>
      ))}
    </div>
  )
}

export function InvestigationPagination({
  page,
  total,
  pageSize,
  disabled,
  onPageChange,
}: {
  page: number
  total: number
  pageSize: number
  disabled: boolean
  onPageChange: (page: number) => void
}) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  if (totalPages <= 1) return null
  return (
    <nav aria-label="Investigation pages" className="flex items-center justify-between gap-3 border-t border-slate/15 px-3 py-3 text-sm dark:border-white/10 sm:px-4">
      <button type="button" className="min-h-11 rounded border border-slate/20 px-3 py-2 font-semibold disabled:opacity-50 md:min-h-0 md:py-1.5 dark:border-white/10" disabled={disabled || page <= 1} onClick={() => onPageChange(page - 1)}>Previous</button>
      <span>Page {page} of {totalPages}</span>
      <button type="button" className="min-h-11 rounded border border-slate/20 px-3 py-2 font-semibold disabled:opacity-50 md:min-h-0 md:py-1.5 dark:border-white/10" disabled={disabled || page >= totalPages} onClick={() => onPageChange(page + 1)}>Next</button>
    </nav>
  )
}

function CreateInvestigationDialog({ controller }: { controller: InvestigationsPageController }) {
  const draft = controller.createDraft
  const error = controller.createInvestigation.error
  const onSubmit = (event: FormEvent) => controller.submitCreate(event)
  return (
    <DialogSurface
      open={controller.createOpen}
      title="Create investigation"
      description="Start a focused workspace for evidence, analyst decisions, and handoff."
      dismissDisabled={controller.createInvestigation.isPending}
      panelClassName="max-w-xl [&_button]:min-h-11 md:[&_button]:min-h-0"
      onClose={() => controller.setCreateOpen(false)}
      footer={null}
    >
      <form className="space-y-3" onSubmit={onSubmit}>
        <div>
          <label htmlFor="investigation-create-title" className="text-sm font-semibold">Title</label>
          <input id="investigation-create-title" required maxLength={255} autoFocus className="mt-1 min-h-11 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]" value={draft.title} onChange={(event) => controller.setCreateDraft((current) => ({ ...current, title: event.target.value }))} />
        </div>
        <div>
          <label htmlFor="investigation-create-description" className="text-sm font-semibold">Description</label>
          <textarea id="investigation-create-description" maxLength={10_000} rows={4} className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]" value={draft.description} onChange={(event) => controller.setCreateDraft((current) => ({ ...current, description: event.target.value }))} />
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <div><label htmlFor="investigation-create-severity" className="text-sm font-semibold">Severity</label><select id="investigation-create-severity" className="mt-1 min-h-11 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]" value={draft.severity} onChange={(event) => controller.setCreateDraft((current) => ({ ...current, severity: event.target.value as InvestigationSeverity }))}>{INVESTIGATION_SEVERITIES.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></div>
          <div><label htmlFor="investigation-create-visibility" className="text-sm font-semibold">Visibility</label><select id="investigation-create-visibility" className="mt-1 min-h-11 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]" value={draft.visibility} onChange={(event) => controller.setCreateDraft((current) => ({ ...current, visibility: event.target.value as 'private' | 'team' }))}><option value="private">Private to members</option><option value="team">Visible to the team</option></select></div>
        </div>
        <label className="flex min-h-11 items-center gap-2 text-sm"><input type="checkbox" className="accent-cyan" checked={draft.assignToMe} onChange={(event) => controller.setCreateDraft((current) => ({ ...current, assignToMe: event.target.checked }))} />Assign to me</label>
        {error && <p role="alert" className="text-sm text-red-700 dark:text-red-200">{resolveApiErrorMessage(error, 'Investigation could not be created', { retryGuidance: 'Review the submitted values and try again. Your draft has been preserved.' })}</p>}
        <div className="grid grid-cols-2 gap-2 pt-2 sm:flex sm:justify-end">
          <button type="button" className="min-h-11 rounded border border-slate/20 px-3 py-2 text-sm font-semibold dark:border-white/10" disabled={controller.createInvestigation.isPending} onClick={() => controller.setCreateOpen(false)}>Cancel</button>
          <button type="submit" className="min-h-11 rounded bg-ink px-3 py-2 text-sm font-semibold text-white disabled:opacity-50 dark:bg-cyan dark:text-[#053c2e]" disabled={controller.createInvestigation.isPending || !draft.title.trim()}>{controller.createInvestigation.isPending ? 'Creating...' : 'Create'}</button>
        </div>
      </form>
    </DialogSurface>
  )
}
