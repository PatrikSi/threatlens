import type { Dispatch, SetStateAction } from 'react'

import { Panel } from './aiSettingsSupport'

export function ActivityFiltersPanel({
  days,
  setDays,
  selectedModel,
  setSelectedModel,
  modelOptions,
  setRunPage,
  onRefresh,
}: {
  days: number
  setDays: Dispatch<SetStateAction<number>>
  selectedModel: string
  setSelectedModel: Dispatch<SetStateAction<string>>
  modelOptions: string[]
  setRunPage: Dispatch<SetStateAction<number>>
  onRefresh: () => void
}) {
  return (
    <Panel title="Operations" subtitle="Queue AI work, monitor current jobs, and inspect the full run history in one place.">
      <div className="flex flex-wrap items-center gap-2">
        <label className="sr-only" htmlFor="ai-activity-window-days">
          Activity window
        </label>
        <select
          id="ai-activity-window-days"
          value={days}
          onChange={(event) => {
            setDays(Number(event.target.value))
            setRunPage(0)
          }}
          aria-label="Activity window"
          className="rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
        >
          <option value={1}>Last 24h</option>
          <option value={7}>Last 7d</option>
          <option value={30}>Last 30d</option>
          <option value={90}>Last 90d</option>
        </select>
        <label className="sr-only" htmlFor="ai-activity-model-filter">
          Model filter
        </label>
        <select
          id="ai-activity-model-filter"
          value={selectedModel}
          onChange={(event) => {
            setSelectedModel(event.target.value)
            setRunPage(0)
          }}
          aria-label="Model filter"
          className="rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
        >
          {modelOptions.map((model) => (
            <option key={model} value={model}>
              {model === 'all' ? 'All models' : model}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="rounded border border-slate/30 px-3 py-2 text-sm font-semibold dark:border-cyan-900/40"
          onClick={onRefresh}
        >
          Refresh
        </button>
      </div>
      <p className="mt-3 text-xs text-slate dark:text-white/60">
        These filters apply to the operations and run-history views below.
      </p>
    </Panel>
  )
}
