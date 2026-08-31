import { resolveApiErrorMessage } from '../api/errors'
import { SettingsPageHeader, SettingsReadOnlyNotice } from '../components/SettingsPageHeader'
import { BUILTIN_CATEGORIES, formatTaggingCategory } from './taggingSettingsModel'
import { TaggingSettingsController } from './useTaggingSettingsController'

type TaggingPanelProps = {
  controller: TaggingSettingsController
}

export function TaggingPageHeader({ controller }: TaggingPanelProps) {
  const { notice } = controller

  return (
    <>
      <SettingsPageHeader
        scope="Organization"
        title="Content tagging"
        description="Set automatic tagging defaults and rules for content across this organization."
      >
        {controller.accessNotice && (
          <div className="py-3">
            <SettingsReadOnlyNotice permission="permission to manage content tagging" />
          </div>
        )}
      </SettingsPageHeader>

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
  const { canManageTagging, onSaveSettings, saveSettings, setSettingsDraft, settingsDraft } = controller

  return (
    <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
      <fieldset disabled={!canManageTagging} className="m-0 min-w-0 border-0 p-0">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="font-display text-lg">Automatic tagging</h2>
            <p className="mt-1 text-sm text-slate dark:text-white/70">
              Control which built-in category tags can be applied automatically and how conservative the engine should be.
            </p>
          </div>
          <button
            className="rounded bg-ink px-3 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50 dark:bg-cyan dark:text-[#053c2e]"
            disabled={saveSettings.isPending || !canManageTagging}
            onClick={onSaveSettings}
          >
            Save defaults
          </button>
        </div>

        <div className="mt-4 grid gap-4 md:grid-cols-2">
          <div>
            <label htmlFor="tagging-auto-confidence" className="text-sm font-semibold">
              Minimum tagging confidence
            </label>
            <input
              id="tagging-auto-confidence"
              className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 disabled:bg-slate/5 disabled:text-slate/60 dark:border-cyan-900/40 dark:bg-[#072019] dark:disabled:bg-white/[0.03] dark:disabled:text-white/45"
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
              Secondary tag limit
            </label>
            <select
              id="tagging-secondary-tag-limit"
              className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 disabled:bg-slate/5 disabled:text-slate/60 dark:border-cyan-900/40 dark:bg-[#072019] dark:disabled:bg-white/[0.03] dark:disabled:text-white/45"
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
          <p className="text-sm font-semibold">Enabled built-in category tags</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {BUILTIN_CATEGORIES.map((category) => {
              const active = settingsDraft.enabled_categories.includes(category)
              return (
                <button
                  type="button"
                  key={category}
                  aria-pressed={active}
                  className={`rounded-full border px-3 py-1.5 text-sm disabled:cursor-not-allowed disabled:opacity-50 ${
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
      </fieldset>
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
      <fieldset disabled={!controller.canManageTagging} className="m-0 min-w-0 border-0 p-0">
        <h2 className="font-display text-lg">Retag existing content</h2>
        <p className="mt-1 text-sm text-slate dark:text-white/70">
          Queue a background pass to re-tag recent items using the current settings and rules.
        </p>

        <div className="mt-4 space-y-3">
          <div>
            <label htmlFor="tagging-reapply-days" className="text-sm font-semibold">
              Lookback period (days)
            </label>
            <input
              id="tagging-reapply-days"
              className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 disabled:bg-slate/5 disabled:text-slate/60 dark:border-cyan-900/40 dark:bg-[#072019] dark:disabled:bg-white/[0.03] dark:disabled:text-white/45"
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
              className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 disabled:bg-slate/5 disabled:text-slate/60 dark:border-cyan-900/40 dark:bg-[#072019] dark:disabled:bg-white/[0.03] dark:disabled:text-white/45"
              type="number"
              min={0}
              max={5000}
              value={reapplyLimit}
              onChange={(event) => setReapplyLimit(event.target.value)}
            />
            <p className="mt-1 text-xs text-slate dark:text-white/60">Use 0 to retag all items in the selected time window.</p>
          </div>
          <button
            className="rounded bg-ink px-3 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50 dark:bg-cyan dark:text-[#053c2e]"
            disabled={reapplyTagging.isPending || !reapplyRequestDraft.request || !controller.canManageTagging}
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
      </fieldset>
    </section>
  )
}

function toggleCategory(categories: string[], category: string): string[] {
  if (!categories.includes(category)) {
    return [...categories, category]
  }
  return categories.length === 1 ? categories : categories.filter((entry) => entry !== category)
}
