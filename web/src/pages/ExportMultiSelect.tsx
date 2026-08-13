import { useMemo, useState } from 'react'

import type { ArticleExportOptionEntry } from '../types/api'

interface ExportMultiSelectProps {
  id: string
  label: string
  entries: ArticleExportOptionEntry[]
  selectedIds: string[]
  onChange: (ids: string[]) => void
  emptyMessage: string
}

export function ExportMultiSelect({
  id,
  label,
  entries,
  selectedIds,
  onChange,
  emptyMessage,
}: ExportMultiSelectProps) {
  const [search, setSearch] = useState('')
  const selected = useMemo(() => new Set(selectedIds), [selectedIds])
  const visibleEntries = useMemo(() => {
    const query = search.trim().toLocaleLowerCase()
    return query ? entries.filter((entry) => entry.name.toLocaleLowerCase().includes(query)) : entries
  }, [entries, search])

  const toggle = (entryId: string) => {
    onChange(selected.has(entryId) ? selectedIds.filter((id) => id !== entryId) : [...selectedIds, entryId])
  }

  return (
    <fieldset className="min-w-0">
      <legend className="text-xs font-bold uppercase text-slate dark:text-slate-300">{label}</legend>
      <div className="mt-1 overflow-hidden rounded border border-slate/25 bg-white/60 dark:border-cyan-900/40 dark:bg-white/[0.02]">
        <div className="flex items-center gap-2 border-b border-slate/15 p-2 dark:border-white/10">
          <label htmlFor={`${id}-search`} className="sr-only">
            Search {label.toLocaleLowerCase()}
          </label>
          <input
            id={`${id}-search`}
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={`Search ${label.toLocaleLowerCase()}`}
            className="min-w-0 flex-1 rounded border border-slate/25 bg-white px-2.5 py-1.5 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
          />
          <span className="whitespace-nowrap text-xs text-slate dark:text-slate-400">{selectedIds.length} selected</span>
        </div>
        <div className="flex items-center justify-end gap-3 border-b border-slate/15 px-2 py-1.5 text-xs dark:border-white/10">
          <button
            type="button"
            className="font-semibold text-slate-700 underline disabled:opacity-40 dark:text-slate-100"
            disabled={!visibleEntries.length}
            onClick={() => onChange(Array.from(new Set([...selectedIds, ...visibleEntries.map((entry) => entry.id)])))}
          >
            Select shown
          </button>
          <button
            type="button"
            className="font-semibold text-slate-700 underline disabled:opacity-40 dark:text-slate-100"
            disabled={!selectedIds.length}
            onClick={() => onChange([])}
          >
            Clear
          </button>
        </div>
        <div className="max-h-44 overflow-auto p-1.5" id={id}>
          {visibleEntries.map((entry) => (
            <label
              key={entry.id}
              className={`flex min-w-0 items-center gap-2 rounded px-2 py-1.5 text-sm transition ${
                selected.has(entry.id)
                  ? 'bg-cyan/10 text-cyan-900 dark:bg-cyan-500/10 dark:text-cyan-100'
                  : 'text-slate-700 hover:bg-slate/5 dark:text-slate-200 dark:hover:bg-white/[0.04]'
              }`}
            >
              <input
                type="checkbox"
                checked={selected.has(entry.id)}
                onChange={() => toggle(entry.id)}
                className="h-3.5 w-3.5 shrink-0 accent-cyan"
              />
              <span className="truncate">{entry.name}</span>
            </label>
          ))}
          {!visibleEntries.length && <p className="px-2 py-3 text-sm text-slate dark:text-slate-400">{emptyMessage}</p>}
        </div>
      </div>
    </fieldset>
  )
}
