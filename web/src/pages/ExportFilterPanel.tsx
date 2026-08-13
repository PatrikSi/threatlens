import type { ArticleExportCapabilities, ArticleExportRelevanceLabel } from '../types/api'
import { applyDatePreset, type ExportDatePreset, type ExportFilterDraft } from './exportPageModel'
import { ExportMultiSelect } from './ExportMultiSelect'
import { updateExportFilterDraft, type ExportPageController } from './useExportPageController'

interface ExportFilterPanelProps {
  capabilities: ArticleExportCapabilities
  controller: ExportPageController
}

const INPUT_CLASS =
  'mt-1 w-full rounded border border-slate/30 bg-white px-2.5 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]'
const DATE_PRESETS: Array<{ id: ExportDatePreset; label: string }> = [
  { id: '7', label: '7 days' },
  { id: '30', label: '30 days' },
  { id: '90', label: '90 days' },
  { id: 'all', label: 'All time' },
  { id: 'custom', label: 'Custom' },
]
const RELEVANCE_LABELS: Array<{ id: ArticleExportRelevanceLabel; label: string }> = [
  { id: 'high', label: 'High' },
  { id: 'medium', label: 'Medium' },
  { id: 'low', label: 'Low' },
]

export function ExportFilterPanel({ capabilities, controller }: ExportFilterPanelProps) {
  const { filterDraft, setFilterDraft, validationErrors } = controller
  const update = (updates: Partial<ExportFilterDraft>) => updateExportFilterDraft(setFilterDraft, updates)

  return (
    <section className="rounded-lg border border-slate/20 bg-white/80 dark:border-cyan-900/40 dark:bg-[#041612]/90">
      <header className="border-b border-slate/15 px-3 py-3 dark:border-white/10 sm:px-4">
        <h2 className="font-display text-lg">Article filters</h2>
        <p className="mt-0.5 text-xs text-slate dark:text-slate-400">All selected conditions are applied together.</p>
      </header>

      <div className="space-y-4 p-3 sm:p-4">
        <div>
          <label htmlFor="export-search" className="text-xs font-bold uppercase text-slate dark:text-slate-300">
            Search
          </label>
          <input
            id="export-search"
            type="search"
            className={INPUT_CLASS}
            value={filterDraft.q}
            onChange={(event) => update({ q: event.target.value })}
            placeholder="Title, summary, URL, or article text"
            maxLength={500}
          />
        </div>

        <fieldset>
          <legend className="text-xs font-bold uppercase text-slate dark:text-slate-300">Date range</legend>
          <div className="mt-1 grid grid-cols-3 gap-1.5 sm:grid-cols-5">
            {DATE_PRESETS.map((preset) => (
              <button
                key={preset.id}
                type="button"
                aria-pressed={filterDraft.datePreset === preset.id}
                className={`rounded border px-2 py-1.5 text-xs font-semibold ${
                  filterDraft.datePreset === preset.id
                    ? 'border-cyan/50 bg-cyan/10 text-cyan-900 dark:border-cyan-500/40 dark:text-cyan-100'
                    : 'border-slate/25 text-slate-700 dark:border-cyan-900/40 dark:text-slate-200'
                }`}
                onClick={() => setFilterDraft((current) => applyDatePreset(current, preset.id))}
              >
                {preset.label}
              </button>
            ))}
          </div>
          <div className="mt-2 grid grid-cols-2 gap-2">
            <label className="text-xs font-semibold text-slate dark:text-slate-300">
              From
              <input
                type="date"
                className={INPUT_CLASS}
                value={filterDraft.sinceDate}
                onChange={(event) => update({ sinceDate: event.target.value, datePreset: 'custom' })}
              />
            </label>
            <label className="text-xs font-semibold text-slate dark:text-slate-300">
              Through
              <input
                type="date"
                className={INPUT_CLASS}
                value={filterDraft.untilDate}
                onChange={(event) => update({ untilDate: event.target.value, datePreset: 'custom' })}
              />
            </label>
          </div>
          <div className="mt-2 grid gap-2 sm:grid-cols-2">
            <label className="text-xs font-semibold text-slate dark:text-slate-300">
              Date field
              <select
                className={INPUT_CLASS}
                value={filterDraft.dateBasis}
                onChange={(event) => update({ dateBasis: event.target.value as ExportFilterDraft['dateBasis'] })}
              >
                <option value="published_at_or_first_seen_at">Published, then first seen</option>
                <option value="first_seen_at">First seen only</option>
              </select>
            </label>
            <label className="text-xs font-semibold text-slate dark:text-slate-300">
              Sort
              <select
                className={INPUT_CLASS}
                value={filterDraft.sort}
                onChange={(event) => update({ sort: event.target.value as ExportFilterDraft['sort'] })}
              >
                <option value="published_at_desc">Published: newest first</option>
                <option value="published_at_asc">Published: oldest first</option>
                <option value="first_seen_desc">First seen: newest first</option>
                <option value="first_seen_asc">First seen: oldest first</option>
              </select>
            </label>
          </div>
        </fieldset>

        <div className="grid gap-4 xl:grid-cols-2">
          <ExportMultiSelect
            id="export-feed-options"
            label="Feeds"
            entries={capabilities.feeds}
            selectedIds={filterDraft.feedIds}
            onChange={(feedIds) => update({ feedIds })}
            emptyMessage={capabilities.feeds.length ? 'No feeds match the search.' : 'No feeds are configured.'}
          />
          <ExportMultiSelect
            id="export-tag-options"
            label="Tags"
            entries={capabilities.tags}
            selectedIds={filterDraft.tagIds}
            onChange={(tagIds) => update({ tagIds })}
            emptyMessage={capabilities.tags.length ? 'No tags match the search.' : 'No article tags are available.'}
          />
        </div>

        {filterDraft.tagIds.length > 1 && (
          <label className="block text-xs font-semibold text-slate dark:text-slate-300">
            Tag matching
            <select
              className={INPUT_CLASS}
              value={filterDraft.tagsMode}
              onChange={(event) => update({ tagsMode: event.target.value as ExportFilterDraft['tagsMode'] })}
            >
              <option value="any">Match any selected tag</option>
              <option value="all">Match every selected tag</option>
            </select>
          </label>
        )}

        <div className="grid gap-4 sm:grid-cols-2">
          <CheckboxFilterGroup
            legend="Classification"
            entries={capabilities.classifications.map((value) => ({ id: value, label: formatClassification(value) }))}
            selected={filterDraft.classifications}
            onChange={(classifications) => update({ classifications })}
            emptyMessage="No classifications are available."
          />
          <CheckboxFilterGroup
            legend="AI relevance"
            entries={RELEVANCE_LABELS}
            selected={filterDraft.relevanceLabels}
            onChange={(relevanceLabels) => update({ relevanceLabels: relevanceLabels as ArticleExportRelevanceLabel[] })}
          />
        </div>

        <fieldset>
          <legend className="text-xs font-bold uppercase text-slate dark:text-slate-300">AI score range</legend>
          <div className="mt-1 grid grid-cols-2 gap-2">
            <label className="text-xs font-semibold text-slate dark:text-slate-300">
              Minimum
              <input
                type="number"
                min="0"
                max="1"
                step="0.01"
                inputMode="decimal"
                className={INPUT_CLASS}
                value={filterDraft.scoreMin}
                onChange={(event) => update({ scoreMin: event.target.value })}
                placeholder="0.00"
              />
            </label>
            <label className="text-xs font-semibold text-slate dark:text-slate-300">
              Maximum
              <input
                type="number"
                min="0"
                max="1"
                step="0.01"
                inputMode="decimal"
                className={INPUT_CLASS}
                value={filterDraft.scoreMax}
                onChange={(event) => update({ scoreMax: event.target.value })}
                placeholder="1.00"
              />
            </label>
          </div>
        </fieldset>

        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          <TriStateFilter label="Read state" value={filterDraft.isRead} onChange={(isRead) => update({ isRead })} />
          <TriStateFilter
            label="Starred state"
            value={filterDraft.isStarred}
            onChange={(isStarred) => update({ isStarred })}
          />
          <TriStateFilter
            label="Article text"
            value={filterDraft.hasArticleText}
            onChange={(hasArticleText) => update({ hasArticleText })}
          />
        </div>

        {validationErrors.length > 0 && (
          <div role="alert" className="rounded border border-red-300/70 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-800/60 dark:bg-red-950/30 dark:text-red-200">
            {validationErrors.map((error) => (
              <p key={error}>{error}</p>
            ))}
          </div>
        )}
      </div>
    </section>
  )
}

function CheckboxFilterGroup({
  legend,
  entries,
  selected,
  onChange,
  emptyMessage,
}: {
  legend: string
  entries: Array<{ id: string; label: string }>
  selected: string[]
  onChange: (values: string[]) => void
  emptyMessage?: string
}) {
  return (
    <fieldset>
      <legend className="text-xs font-bold uppercase text-slate dark:text-slate-300">{legend}</legend>
      <div className="mt-1 max-h-36 space-y-0.5 overflow-auto rounded border border-slate/20 p-1.5 dark:border-cyan-900/40">
        {entries.map((entry) => (
          <label key={entry.id} className="flex items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-slate/5 dark:hover:bg-white/[0.04]">
            <input
              type="checkbox"
              className="h-3.5 w-3.5 accent-cyan"
              checked={selected.includes(entry.id)}
              onChange={() => onChange(toggleArrayValue(selected, entry.id))}
            />
            <span>{entry.label}</span>
          </label>
        ))}
        {!entries.length && <p className="px-2 py-2 text-sm text-slate dark:text-slate-400">{emptyMessage}</p>}
      </div>
    </fieldset>
  )
}

function TriStateFilter({
  label,
  value,
  onChange,
}: {
  label: string
  value: ExportFilterDraft['isRead']
  onChange: (value: ExportFilterDraft['isRead']) => void
}) {
  return (
    <label className="text-xs font-semibold text-slate dark:text-slate-300">
      {label}
      <select className={INPUT_CLASS} value={value} onChange={(event) => onChange(event.target.value as ExportFilterDraft['isRead'])}>
        <option value="">Any</option>
        <option value="true">Yes</option>
        <option value="false">No</option>
      </select>
    </label>
  )
}

function toggleArrayValue(values: string[], value: string): string[] {
  return values.includes(value) ? values.filter((entry) => entry !== value) : [...values, value]
}

function formatClassification(value: string): string {
  return value
    .split(/[_-]/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toLocaleUpperCase() + part.slice(1))
    .join(' ')
}
