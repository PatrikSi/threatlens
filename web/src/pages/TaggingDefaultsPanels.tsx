import { resolveApiErrorMessage } from '../api/errors'
import { BUILTIN_CATEGORIES, formatTaggingCategory } from './taggingSettingsModel'
import { TaggingSettingsController } from './useTaggingSettingsController'

type TaggingPanelProps = {
  controller: TaggingSettingsController
}

export function TaggingPageHeader({ controller }: TaggingPanelProps) {
  const { notice } = controller

  return (
    <>
      <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
        <p className="text-xs font-semibold uppercase text-slate dark:text-white/55">Automation</p>
        <h2 className="mt-1 font-display text-xl">Custom Tagging</h2>
        <p className="mt-1 text-sm text-slate dark:text-white/75">
          Tune the built-in auto-tagging behavior and add custom rules that create new tags from article content.
        </p>
      </section>

      {notice && (
        <p
          role={notice.tone === 'error' ? 'alert' : 'status'}
          aria-live={notice.tone === 'error' ? 'assertive' : 'polite'}
          aria-atomic="true"
          className={`rounded-lg border px-3 py-2 text-sm ${
            notice.tone === 'error'
              ? 'border-red-500/20 bg-red-500/10 text-red-700 dark:border-red-900/40 dark:bg-red-950/20 dark:text-red-200'
              : 'border-emerald-500/20 bg-emerald-500/10 text-emerald-700 dark:border-emerald-900/40 dark:bg-emerald-950/20 dark:text-emerald-300'
          }`}
        >
          {notice.message}
        </p>
      )}
    </>
  )
}

export function TaggingDefaultsPanel({ controller }: TaggingPanelProps) {
  const { onSaveSettings, saveSettings, setSettingsDraft, settingsDraft } = controller

  return (
    <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="font-display text-lg">Auto-Tag Defaults</h3>
          <p className="mt-1 text-sm text-slate dark:text-white/70">
            Control which built-in category tags can be applied automatically and how conservative the engine should be.
          </p>
        </div>
        <button
          className="rounded bg-ink px-3 py-2 text-sm font-semibold text-white dark:bg-cyan dark:text-[#053c2e]"
          disabled={saveSettings.isPending}
          onClick={onSaveSettings}
        >
          Save defaults
        </button>
      </div>

      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <div>
          <label htmlFor="tagging-auto-confidence" className="text-sm font-semibold">
            Minimum Auto-Tag Confidence
          </label>
          <input
            id="tagging-auto-confidence"
            className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
            type="number"
            min={0.05}
            max={0.995}
            step={0.01}
            value={settingsDraft.min_auto_tag_confidence}
            onChange={(event) =>
              setSettingsDraft((current) => ({ ...current, min_auto_tag_confidence: event.target.value }))
            }
          />
        </div>
        <div>
          <label htmlFor="tagging-secondary-tag-limit" className="text-sm font-semibold">
            Secondary Tag Limit
          </label>
          <select
            id="tagging-secondary-tag-limit"
            className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
            value={settingsDraft.secondary_tag_limit}
            onChange={(event) => setSettingsDraft((current) => ({ ...current, secondary_tag_limit: event.target.value }))}
          >
            <option value="0">0</option>
            <option value="1">1</option>
            <option value="2">2</option>
          </select>
        </div>
      </div>

      <div className="mt-4">
        <p className="text-sm font-semibold">Enabled Built-In Category Tags</p>
        <div className="mt-2 flex flex-wrap gap-2">
          {BUILTIN_CATEGORIES.map((category) => {
            const active = settingsDraft.enabled_categories.includes(category)
            return (
              <button
                type="button"
                key={category}
                aria-pressed={active}
                className={`rounded-full border px-3 py-1.5 text-sm ${
                  active ? 'tl-chip-filter-active' : 'tl-chip-neutral hover:border-slate/40 dark:hover:border-cyan-700/60'
                }`}
                onClick={() =>
                  setSettingsDraft((current) => ({
                    ...current,
                    enabled_categories: toggleCategory(current.enabled_categories, category),
                  }))
                }
              >
                {formatTaggingCategory(category)}
              </button>
            )
          })}
        </div>
      </div>

      {saveSettings.isError && (
        <p role="alert" aria-live="assertive" aria-atomic="true" className="mt-3 text-sm text-red-600">
          {resolveApiErrorMessage(saveSettings.error, 'Failed to update tagging settings.')}
        </p>
      )}
    </section>
  )
}

export function TaggingReapplyPanel({ controller }: TaggingPanelProps) {
  const {
    onRequestReapplyTagging,
    reapplyDays,
    reapplyLimit,
    reapplyRequestDraft,
    reapplyTagging,
    setReapplyDays,
    setReapplyLimit,
  } = controller

  return (
    <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
      <h3 className="font-display text-lg">Reapply Tagging</h3>
      <p className="mt-1 text-sm text-slate dark:text-white/70">
        Queue a background pass to re-tag recent items using the current settings and rules.
      </p>

      <div className="mt-4 space-y-3">
        <div>
          <label htmlFor="tagging-reapply-days" className="text-sm font-semibold">
            Days Back
          </label>
          <input
            id="tagging-reapply-days"
            className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
            type="number"
            min={1}
            max={365}
            value={reapplyDays}
            onChange={(event) => setReapplyDays(event.target.value)}
          />
        </div>
        <div>
          <label htmlFor="tagging-reapply-limit" className="text-sm font-semibold">
            Limit
          </label>
          <input
            id="tagging-reapply-limit"
            className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
            type="number"
            min={0}
            max={5000}
            value={reapplyLimit}
            onChange={(event) => setReapplyLimit(event.target.value)}
          />
          <p className="mt-1 text-xs text-slate dark:text-white/60">Use 0 to retag all items in the selected time window.</p>
        </div>
        <button
          className="rounded bg-ink px-3 py-2 text-sm font-semibold text-white dark:bg-cyan dark:text-[#053c2e]"
          disabled={reapplyTagging.isPending || !reapplyRequestDraft.request}
          onClick={onRequestReapplyTagging}
        >
          Queue retagging
        </button>
        {reapplyRequestDraft.error && (
          <p className="text-sm text-amber-700 dark:text-amber-300">{reapplyRequestDraft.error}</p>
        )}
        {reapplyTagging.isError && (
          <p role="alert" aria-live="assertive" aria-atomic="true" className="text-sm text-red-600">
            {resolveApiErrorMessage(reapplyTagging.error, 'Failed to queue retagging.')}
          </p>
        )}
      </div>
    </section>
  )
}

function toggleCategory(categories: string[], category: string): string[] {
  if (!categories.includes(category)) {
    return [...categories, category]
  }
  return categories.length === 1 ? categories : categories.filter((entry) => entry !== category)
}
