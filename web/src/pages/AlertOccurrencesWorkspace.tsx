import type { ChangeEvent } from 'react'

import { resolveApiErrorMessage } from '../api/errors'
import type { AlertOccurrence } from '../types/alerts'
import { formatDateTime } from '../utils/datetime'
import { AlertOccurrenceDetail } from './AlertOccurrenceDetail'
import { AlertOccurrenceDialogs } from './AlertOccurrenceDialogs'
import {
  AlertOccurrencePageError,
  AlertOccurrenceRefreshWarning,
  AlertOccurrenceStateChip,
  AlertOccurrenceStateFlags,
  AlertSeverityChip,
} from './AlertOccurrenceShared'
import {
  ALERT_OCCURRENCE_PAGE_SIZES,
  ALERT_OCCURRENCE_STATES,
  ALERT_SEVERITIES,
  alertOccurrenceActiveFilterCount,
  alertOccurrenceResultRange,
  getAlertOccurrenceSource,
  shortIdentifier,
  type AlertBooleanFilter,
} from './alertOccurrenceModel'
import {
  useAlertOccurrencesController,
  type AlertOccurrencesController,
} from './useAlertOccurrencesController'

export function AlertOccurrencesWorkspace({ active = true }: { active?: boolean }) {
  const controller = useAlertOccurrencesController(active)
  const { occurrencesQuery } = controller
  const data = occurrencesQuery.data
  const activeFilterCount =
    alertOccurrenceActiveFilterCount(controller.filters) +
    Number(Boolean(controller.loadedPageSearch.trim()))
  const canBackfill = controller.currentUserQuery.data?.role === 'admin'

  return (
    <section
      className="tl-surface min-w-0 overflow-hidden rounded-xl"
      aria-labelledby="alert-occurrences-heading"
    >
      <header className="border-b border-slate/20 px-3 py-3 dark:border-white/10 sm:px-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 id="alert-occurrences-heading" className="font-display text-xl">
              Alert occurrence triage
            </h2>
            <p className="mt-1 max-w-3xl text-sm text-slate dark:text-slate-300">
              Review durable rule matches, record lifecycle decisions, and retain source and
              activity history.
            </p>
          </div>
          <div className="grid w-full grid-cols-2 gap-2 sm:flex sm:w-auto">
            <button
              id="alert-occurrences-refresh"
              type="button"
              className="min-h-10 rounded border border-slate/30 px-3 py-1.5 text-sm font-semibold disabled:opacity-60 dark:border-white/15"
              disabled={occurrencesQuery.isFetching}
              aria-label="Refresh alert occurrences"
              onClick={() => void controller.refreshCollection()}
            >
              {occurrencesQuery.isFetching ? 'Refreshing...' : 'Refresh'}
            </button>
            {canBackfill && (
              <button
                type="button"
                className="min-h-10 rounded bg-ink px-3 py-1.5 text-sm font-semibold text-white dark:bg-cyan dark:text-[#053c2e]"
                onClick={controller.backfill.openDialog}
              >
                Backfill history
              </button>
            )}
          </div>
        </div>

        <OccurrenceStats controller={controller} />
        <OccurrenceFilters controller={controller} activeFilterCount={activeFilterCount} />

        <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs text-slate dark:text-slate-400">
          <span aria-live="polite">
            {data ? occurrenceResultSummary(controller) : 'Loading result count...'}
            {occurrencesQuery.isFetching && data ? ' · Updating results...' : ''}
          </span>
          {occurrencesQuery.dataUpdatedAt > 0 && (
            <span>Updated {formatDateTime(new Date(occurrencesQuery.dataUpdatedAt))}</span>
          )}
        </div>
        {occurrencesQuery.isPlaceholderData && (
          <p role="status" className="mt-2 text-xs text-amber-800 dark:text-amber-200">
            Showing the previous page while page {controller.page} loads. Selection and actions are
            paused.
          </p>
        )}
        {controller.filterValidationError && (
          <p role="alert" className="mt-2 text-sm text-red-700 dark:text-red-300">
            {controller.filterValidationError} Adjust the time range to load occurrences.
          </p>
        )}
      </header>

      <OccurrenceActionFeedback controller={controller} />

      {occurrencesQuery.isError && data && (
        <AlertOccurrenceRefreshWarning
          error={occurrencesQuery.error}
          fallback="Alert occurrences could not be refreshed"
          onRetry={() => void controller.refreshCollection()}
        />
      )}
      {!data && occurrencesQuery.isLoading && (
        <p
          role="status"
          aria-busy="true"
          className="px-4 py-10 text-center text-sm text-slate dark:text-slate-300"
        >
          Loading alert occurrences...
        </p>
      )}
      {!data && occurrencesQuery.isError && (
        <AlertOccurrencePageError
          error={occurrencesQuery.error}
          fallback="Alert occurrences could not be loaded"
          onRetry={() => void occurrencesQuery.refetch()}
        />
      )}

      {data && (
        <div className="xl:grid xl:grid-cols-[minmax(0,1.35fr)_minmax(340px,0.65fr)]">
          <div className="min-w-0">
            {data.items.length === 0 ? (
              <OccurrenceEmptyState controller={controller} activeFilterCount={activeFilterCount} />
            ) : controller.visibleOccurrences.length > 0 ? (
              <>
                <OccurrenceBulkToolbar controller={controller} />
                <OccurrenceDesktopTable controller={controller} />
                <OccurrenceMobileList controller={controller} />
              </>
            ) : (
              <div className="px-4 py-10 text-center">
                <h3 className="text-base font-semibold">No matches on this loaded page</h3>
                <p className="mt-1 text-sm text-slate dark:text-slate-300">
                  Clear the page search or use the server filters and pagination to inspect more
                  occurrences.
                </p>
                <button
                  type="button"
                  className="mt-4 min-h-10 rounded border border-slate/30 px-3 py-1.5 text-sm font-semibold dark:border-white/15"
                  onClick={() => controller.setLoadedPageSearch('')}
                >
                  Clear page search
                </button>
              </div>
            )}
            {data.items.length > 0 && <OccurrencePagination controller={controller} />}
          </div>
          {(data.items.length > 0 || controller.selectedOccurrenceId) && (
            <AlertOccurrenceDetail controller={controller} />
          )}
        </div>
      )}

      <AlertOccurrenceDialogs controller={controller} />
    </section>
  )
}

function occurrenceResultSummary(controller: AlertOccurrencesController): string {
  const data = controller.occurrencesQuery.data
  if (!data) return 'Loading result count...'
  const serverRange = alertOccurrenceResultRange(
    data.total,
    data.page,
    data.page_size,
    data.items.length,
  )
  if (!controller.loadedPageSearch.trim()) return serverRange
  return `${controller.visibleOccurrences.length} of ${data.items.length} loaded rows match · ${serverRange} server results`
}

function OccurrenceStats({ controller }: { controller: AlertOccurrencesController }) {
  const stats = controller.stats
  const loaded = Boolean(controller.occurrencesQuery.data)
  const values = [
    { label: 'Server-filtered total', value: loaded ? stats.matching : '--' },
    { label: 'New on loaded page', value: loaded ? stats.newOnPage : '--' },
    { label: 'Active on loaded page', value: loaded ? stats.activeOnPage : '--' },
    { label: 'High or critical on page', value: loaded ? stats.elevatedOnPage : '--' },
  ]
  return (
    <dl className="mt-3 grid grid-cols-2 gap-px overflow-hidden border border-slate/15 bg-slate/15 sm:grid-cols-4 dark:border-white/10 dark:bg-white/10">
      {values.map((entry) => (
        <div key={entry.label} className="min-w-0 bg-white px-2.5 py-2 dark:bg-[#041612] sm:px-3">
          <dt className="text-xs text-slate dark:text-slate-400">{entry.label}</dt>
          <dd className="mt-0.5 text-lg font-semibold leading-tight text-ink dark:text-white">
            {entry.value}
          </dd>
        </div>
      ))}
    </dl>
  )
}

function OccurrenceFilters({
  controller,
  activeFilterCount,
}: {
  controller: AlertOccurrencesController
  activeFilterCount: number
}) {
  const { filters } = controller
  return (
    <div className="mt-3 border-t border-slate/15 pt-3 dark:border-white/10">
      <div className="grid gap-2 md:grid-cols-[minmax(220px,1fr)_minmax(180px,0.65fr)]">
        <div>
          <label
            htmlFor="alert-occurrence-search"
            className="text-xs font-semibold text-slate dark:text-slate-300"
          >
            Search loaded page
          </label>
          <input
            id="alert-occurrence-search"
            type="search"
            maxLength={255}
            className="mt-1 min-h-10 w-full rounded border border-slate/30 bg-white px-3 py-1.5 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
            value={controller.loadedPageSearch}
            onChange={(event) => controller.setLoadedPageSearch(event.target.value)}
            placeholder="Rule, source, keyword, or ID"
          />
        </div>
        <div>
          <label
            htmlFor="alert-occurrence-rule"
            className="text-xs font-semibold text-slate dark:text-slate-300"
          >
            Alert rule
          </label>
          <select
            id="alert-occurrence-rule"
            className="mt-1 min-h-10 w-full rounded border border-slate/30 bg-white px-3 py-1.5 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
            value={filters.ruleId}
            onChange={(event) => controller.updateFilters({ ruleId: event.target.value })}
          >
            <option value="">All rules</option>
            {(controller.rulesQuery.data ?? []).map((rule) => (
              <option key={rule.id} value={rule.id}>
                {rule.name}
                {rule.enabled ? '' : ' (disabled)'}
              </option>
            ))}
          </select>
        </div>
      </div>

      {controller.rulesQuery.isError && (
        <p role="alert" className="mt-2 text-xs text-amber-800 dark:text-amber-200">
          {resolveApiErrorMessage(
            controller.rulesQuery.error,
            'The alert rule filter could not be loaded',
          )}
        </p>
      )}

      <div className="mt-2 grid gap-2 lg:grid-cols-2">
        <FilterCheckboxGroup
          legend="Status"
          options={ALERT_OCCURRENCE_STATES}
          selected={filters.lifecycleStates}
          onToggle={(value) =>
            controller.toggleStateFilter(value as (typeof filters.lifecycleStates)[number])
          }
        />
        <FilterCheckboxGroup
          legend="Severity"
          options={ALERT_SEVERITIES}
          selected={filters.severities}
          onToggle={(value) =>
            controller.toggleSeverityFilter(value as (typeof filters.severities)[number])
          }
        />
      </div>

      <details className="mt-2 border-t border-slate/15 pt-2 dark:border-white/10">
        <summary className="cursor-pointer text-sm font-semibold text-ink dark:text-slate-200">
          More filters
        </summary>
        <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <BooleanFilterSelect
            id="alert-occurrence-suppressed"
            label="Suppression"
            value={filters.suppressed}
            onChange={(suppressed) => controller.updateFilters({ suppressed })}
          />
          <BooleanFilterSelect
            id="alert-occurrence-snoozed"
            label="Snooze"
            value={filters.snoozed}
            onChange={(snoozed) => controller.updateFilters({ snoozed })}
          />
          <div>
            <label
              htmlFor="alert-occurrence-since"
              className="text-xs font-semibold text-slate dark:text-slate-300"
            >
              Created since
            </label>
            <input
              id="alert-occurrence-since"
              type="datetime-local"
              max={filters.until || undefined}
              className="mt-1 min-h-10 w-full rounded border border-slate/30 bg-white px-2 py-1.5 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
              value={filters.since}
              onChange={(event) => controller.updateFilters({ since: event.target.value })}
            />
          </div>
          <div>
            <label
              htmlFor="alert-occurrence-until"
              className="text-xs font-semibold text-slate dark:text-slate-300"
            >
              Created until
            </label>
            <input
              id="alert-occurrence-until"
              type="datetime-local"
              min={filters.since || undefined}
              className="mt-1 min-h-10 w-full rounded border border-slate/30 bg-white px-2 py-1.5 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
              value={filters.until}
              onChange={(event) => controller.updateFilters({ until: event.target.value })}
            />
          </div>
        </div>
      </details>

      {activeFilterCount > 0 && (
        <div className="mt-2 flex items-center justify-between gap-2">
          <span className="text-xs text-slate dark:text-slate-400">
            {activeFilterCount} active filter{activeFilterCount === 1 ? '' : 's'}
          </span>
          <button
            type="button"
            className="min-h-9 rounded border border-slate/25 px-2.5 py-1 text-xs font-semibold dark:border-white/15"
            onClick={controller.clearFilters}
          >
            Clear filters
          </button>
        </div>
      )}
    </div>
  )
}

function FilterCheckboxGroup({
  legend,
  options,
  selected,
  onToggle,
}: {
  legend: string
  options: ReadonlyArray<{ value: string; label: string }>
  selected: string[]
  onToggle: (value: string) => void
}) {
  return (
    <fieldset className="min-w-0">
      <legend className="text-xs font-semibold text-slate dark:text-slate-300">{legend}</legend>
      <div className="mt-1 flex min-w-0 flex-wrap gap-1.5">
        {options.map((option) => (
          <label
            key={option.value}
            className={`flex min-h-9 cursor-pointer items-center gap-1.5 rounded border px-2 py-1 text-xs font-semibold md:min-h-0 ${
              selected.includes(option.value)
                ? 'border-blue-400 bg-blue-50 text-blue-800 dark:border-cyan-700 dark:bg-cyan-950/30 dark:text-cyan-100'
                : 'border-slate/25 text-slate-700 dark:border-white/15 dark:text-slate-200'
            }`}
          >
            <input
              type="checkbox"
              className="accent-cyan"
              checked={selected.includes(option.value)}
              onChange={() => onToggle(option.value)}
            />
            {option.label}
          </label>
        ))}
      </div>
    </fieldset>
  )
}

function BooleanFilterSelect({
  id,
  label,
  value,
  onChange,
}: {
  id: string
  label: string
  value: AlertBooleanFilter
  onChange: (value: AlertBooleanFilter) => void
}) {
  return (
    <div>
      <label htmlFor={id} className="text-xs font-semibold text-slate dark:text-slate-300">
        {label}
      </label>
      <select
        id={id}
        className="mt-1 min-h-10 w-full rounded border border-slate/30 bg-white px-2 py-1.5 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
        value={value}
        onChange={(event) => onChange(event.target.value as AlertBooleanFilter)}
      >
        <option value="any">Any state</option>
        <option value="yes">Yes</option>
        <option value="no">No</option>
      </select>
    </div>
  )
}

function OccurrenceActionFeedback({ controller }: { controller: AlertOccurrencesController }) {
  return (
    <>
      {controller.conflictNotice && (
        <div
          role="alert"
          className="border-b border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-700/40 dark:bg-amber-950/20 dark:text-amber-200 sm:px-4"
        >
          {controller.conflictNotice}
        </div>
      )}
      {controller.actionError && !controller.closeTarget && !controller.snoozeTarget && (
        <div
          role="alert"
          className="border-b border-red-300/60 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-800/50 dark:bg-red-950/25 dark:text-red-200 sm:px-4"
        >
          {controller.actionError}
        </div>
      )}
      {controller.actionFeedback && (
        <div
          role="status"
          className="border-b border-green-300/60 bg-green-50 px-3 py-2 text-sm text-green-900 dark:border-green-800/50 dark:bg-green-950/25 dark:text-green-200 sm:px-4"
        >
          {controller.actionFeedback}
        </div>
      )}
    </>
  )
}

function OccurrenceBulkToolbar({ controller }: { controller: AlertOccurrencesController }) {
  if (controller.selectedOccurrences.length === 0) return null
  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-slate/15 bg-slate-50 px-3 py-2 text-sm dark:border-white/10 dark:bg-white/[0.025] sm:px-4">
      <span className="font-semibold">{controller.selectedOccurrences.length} selected</span>
      <button
        type="button"
        className="min-h-9 rounded border border-slate/30 px-2.5 py-1 text-xs font-semibold disabled:opacity-50 dark:border-white/15"
        disabled={
          !controller.canBulkAcknowledge ||
          controller.mutationPending ||
          controller.occurrencesQuery.isPlaceholderData
        }
        title={
          controller.canBulkAcknowledge
            ? undefined
            : 'Only new occurrences can be acknowledged together.'
        }
        onClick={controller.acknowledgeSelected}
      >
        Acknowledge selected
      </button>
      <button
        type="button"
        className="min-h-9 rounded bg-ink px-2.5 py-1 text-xs font-semibold text-white disabled:opacity-50 dark:bg-cyan dark:text-[#053c2e]"
        disabled={
          !controller.canBulkClose ||
          controller.mutationPending ||
          controller.occurrencesQuery.isPlaceholderData
        }
        title={
          controller.canBulkClose ? undefined : 'Closed occurrences cannot be closed again in bulk.'
        }
        onClick={controller.requestCloseSelected}
      >
        Close selected
      </button>
      <button
        type="button"
        className="ml-auto min-h-9 rounded border border-slate/25 px-2.5 py-1 text-xs font-semibold dark:border-white/15"
        onClick={controller.clearSelection}
      >
        Clear selection
      </button>
    </div>
  )
}

function OccurrenceDesktopTable({ controller }: { controller: AlertOccurrencesController }) {
  const allVisibleSelected =
    controller.visibleOccurrences.length > 0 &&
    controller.visibleOccurrences.every((occurrence) => controller.selectedIds.has(occurrence.id))
  return (
    <div className="hidden overflow-x-auto md:block">
      <table className="w-full min-w-[780px] table-fixed text-left text-sm">
        <thead>
          <tr className="border-b border-slate/20 dark:border-white/10">
            <th className="w-11 px-3 py-2">
              <input
                type="checkbox"
                className="accent-cyan"
                checked={allVisibleSelected}
                aria-label="Select all occurrences on the loaded page"
                disabled={controller.occurrencesQuery.isPlaceholderData}
                onChange={controller.toggleSelectAllVisible}
              />
            </th>
            <th className="w-[38%] px-2 py-2">Source and rule</th>
            <th className="w-36 px-2 py-2">Severity and status</th>
            <th className="w-32 px-2 py-2">Matched</th>
            <th className="w-36 px-2 py-2">Created</th>
            <th className="w-24 px-3 py-2 text-right">Action</th>
          </tr>
        </thead>
        <tbody>
          {controller.visibleOccurrences.map((occurrence) => (
            <OccurrenceTableRow
              key={occurrence.id}
              occurrence={occurrence}
              controller={controller}
            />
          ))}
        </tbody>
      </table>
    </div>
  )
}

function OccurrenceTableRow({
  occurrence,
  controller,
}: {
  occurrence: AlertOccurrence
  controller: AlertOccurrencesController
}) {
  const source = getAlertOccurrenceSource(occurrence)
  const selected = controller.selectedOccurrenceId === occurrence.id
  return (
    <tr
      className={`border-b border-slate/10 last:border-0 dark:border-white/5 ${selected ? 'tl-row-selected' : ''}`}
    >
      <td className="px-3 py-2.5 align-top">
        <input
          type="checkbox"
          className="accent-cyan"
          checked={controller.selectedIds.has(occurrence.id)}
          disabled={controller.occurrencesQuery.isPlaceholderData}
          aria-label={`Select occurrence from ${occurrence.alert_name_snapshot}`}
          onChange={() => controller.toggleSelection(occurrence.id)}
        />
      </td>
      <td className="px-2 py-2.5 align-top">
        <p className="break-words font-semibold text-ink dark:text-slate-100">{source.title}</p>
        <p
          className="mt-0.5 truncate text-xs text-slate dark:text-slate-400"
          title={occurrence.alert_name_snapshot}
        >
          {occurrence.alert_name_snapshot} · revision {occurrence.rule_revision}
        </p>
        {source.feedName && (
          <p className="mt-0.5 truncate text-xs text-slate dark:text-slate-400">
            {source.feedName}
          </p>
        )}
        <div className="mt-1.5">
          <AlertOccurrenceStateFlags occurrence={occurrence} />
        </div>
      </td>
      <td className="px-2 py-2.5 align-top">
        <div className="flex flex-col items-start gap-1.5">
          <AlertSeverityChip severity={occurrence.severity_snapshot} />
          <AlertOccurrenceStateChip state={occurrence.lifecycle_state} />
        </div>
      </td>
      <td className="px-2 py-2.5 align-top">
        <div className="flex max-h-16 flex-wrap gap-1 overflow-hidden">
          {occurrence.matched_keywords.slice(0, 4).map((keyword) => (
            <span key={keyword} className="tl-chip tl-chip-neutral">
              {keyword}
            </span>
          ))}
          {occurrence.matched_keywords.length > 4 && (
            <span className="tl-chip tl-chip-neutral">
              +{occurrence.matched_keywords.length - 4}
            </span>
          )}
        </div>
      </td>
      <td className="px-2 py-2.5 align-top text-xs text-slate dark:text-slate-400">
        <time dateTime={occurrence.created_at}>{formatDateTime(occurrence.created_at)}</time>
        <p className="mt-1">ID {shortIdentifier(occurrence.id)}</p>
      </td>
      <td className="px-3 py-2.5 text-right align-top">
        <button
          type="button"
          className="min-h-9 rounded border border-slate/30 px-2.5 py-1 text-xs font-semibold dark:border-white/15"
          aria-label={`Inspect occurrence from ${occurrence.alert_name_snapshot}`}
          aria-pressed={selected}
          disabled={controller.occurrencesQuery.isPlaceholderData}
          onClick={(event) => controller.selectOccurrence(occurrence.id, event.currentTarget)}
        >
          Inspect
        </button>
      </td>
    </tr>
  )
}

function OccurrenceMobileList({ controller }: { controller: AlertOccurrencesController }) {
  return (
    <div className="divide-y divide-slate/15 md:hidden dark:divide-white/10">
      {controller.visibleOccurrences.map((occurrence) => {
        const source = getAlertOccurrenceSource(occurrence)
        return (
          <article key={occurrence.id} className="px-3 py-3">
            <div className="flex items-start gap-2.5">
              <input
                type="checkbox"
                className="mt-1 accent-cyan"
                checked={controller.selectedIds.has(occurrence.id)}
                disabled={controller.occurrencesQuery.isPlaceholderData}
                aria-label={`Select occurrence from ${occurrence.alert_name_snapshot}`}
                onChange={() => controller.toggleSelection(occurrence.id)}
              />
              <div className="min-w-0 flex-1">
                <h3 className="break-words text-sm font-semibold">{source.title}</h3>
                <p className="mt-0.5 break-words text-xs text-slate dark:text-slate-400">
                  {occurrence.alert_name_snapshot} · revision {occurrence.rule_revision}
                </p>
              </div>
            </div>
            <div className="mt-2 flex flex-wrap gap-1.5">
              <AlertSeverityChip severity={occurrence.severity_snapshot} />
              <AlertOccurrenceStateChip state={occurrence.lifecycle_state} />
              <AlertOccurrenceStateFlags occurrence={occurrence} />
            </div>
            <div className="mt-2 flex flex-wrap gap-1">
              {occurrence.matched_keywords.slice(0, 3).map((keyword) => (
                <span key={keyword} className="tl-chip tl-chip-neutral">
                  {keyword}
                </span>
              ))}
              {occurrence.matched_keywords.length > 3 && (
                <span className="tl-chip tl-chip-neutral">
                  +{occurrence.matched_keywords.length - 3}
                </span>
              )}
            </div>
            <div className="mt-2 flex items-end justify-between gap-3">
              <p className="text-xs text-slate dark:text-slate-400">
                {formatDateTime(occurrence.created_at)}
              </p>
              <button
                type="button"
                className="min-h-10 rounded border border-slate/30 px-3 py-1.5 text-xs font-semibold dark:border-white/15"
                aria-label={`Inspect occurrence from ${occurrence.alert_name_snapshot}`}
                disabled={controller.occurrencesQuery.isPlaceholderData}
                onClick={(event) => controller.selectOccurrence(occurrence.id, event.currentTarget)}
              >
                Inspect
              </button>
            </div>
          </article>
        )
      })}
    </div>
  )
}

function OccurrencePagination({ controller }: { controller: AlertOccurrencesController }) {
  const data = controller.occurrencesQuery.data
  if (!data) return null
  return (
    <nav
      aria-label="Alert occurrence pages"
      className="flex flex-col gap-2 border-t border-slate/15 px-3 py-3 text-sm dark:border-white/10 sm:flex-row sm:items-center sm:justify-between sm:px-4"
    >
      <label className="flex items-center gap-2 text-xs text-slate dark:text-slate-300">
        Rows per page
        <select
          className="min-h-10 rounded border border-slate/30 bg-white px-2 py-1 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
          value={controller.pageSize}
          disabled={controller.occurrencesQuery.isFetching}
          onChange={(event: ChangeEvent<HTMLSelectElement>) =>
            controller.setPageSize(Number(event.target.value))
          }
        >
          {ALERT_OCCURRENCE_PAGE_SIZES.map((size) => (
            <option key={size} value={size}>
              {size}
            </option>
          ))}
        </select>
      </label>
      <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-2">
        <button
          type="button"
          className="min-h-10 rounded border border-slate/30 px-3 py-1.5 text-xs font-semibold disabled:opacity-50 dark:border-white/15"
          disabled={controller.page <= 1 || controller.occurrencesQuery.isFetching}
          aria-label="Previous alert occurrence page"
          onClick={() => controller.setPage(controller.page - 1)}
        >
          Previous
        </button>
        <span className="whitespace-nowrap text-center text-xs text-slate dark:text-slate-300">
          Page {controller.page} of {controller.pageCount}
        </span>
        <button
          type="button"
          className="min-h-10 rounded border border-slate/30 px-3 py-1.5 text-xs font-semibold disabled:opacity-50 dark:border-white/15"
          disabled={
            controller.page >= controller.pageCount || controller.occurrencesQuery.isFetching
          }
          aria-label="Next alert occurrence page"
          onClick={() => controller.setPage(controller.page + 1)}
        >
          Next
        </button>
      </div>
    </nav>
  )
}

function OccurrenceEmptyState({
  controller,
  activeFilterCount,
}: {
  controller: AlertOccurrencesController
  activeFilterCount: number
}) {
  return (
    <div className="px-4 py-10 text-center">
      <h3 className="text-base font-semibold">No matching alert occurrences</h3>
      <p className="mt-1 text-sm text-slate dark:text-slate-300">
        {activeFilterCount > 0
          ? 'Clear or broaden the current filters to see more occurrence history.'
          : 'New rule matches will appear here after durable alert evaluation completes.'}
      </p>
      {controller.page > 1 && (
        <button
          type="button"
          className="mt-4 min-h-10 rounded border border-slate/30 px-3 py-1.5 text-sm font-semibold dark:border-white/15"
          onClick={() => controller.setPage(1)}
        >
          Return to first page
        </button>
      )}
      {activeFilterCount > 0 && controller.page === 1 && (
        <button
          type="button"
          className="mt-4 min-h-10 rounded border border-slate/30 px-3 py-1.5 text-sm font-semibold dark:border-white/15"
          onClick={controller.clearFilters}
        >
          Clear filters
        </button>
      )}
    </div>
  )
}
