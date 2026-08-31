import { resolveApiErrorMessage } from '../api/errors'
import { Feed } from '../types/api'
import {
  BUILTIN_CATEGORIES,
  RULE_FIELDS,
  TaggingRuleDraft,
  formatTaggingCategory,
  formatTaggingField,
  formatTaggingTimestamp,
} from './taggingSettingsModel'
import { TaggingSettingsController } from './useTaggingSettingsController'

type TaggingPanelProps = {
  controller: TaggingSettingsController
}

export function TaggingRulesList({ controller }: TaggingPanelProps) {
  const { bundleQuery, canManageTagging, onCreateNewRule, onSelectRule, selectedRuleId } = controller

  return (
    <section className="rounded-xl border border-slate/20 bg-white/80 p-3 dark:border-cyan-900/40 dark:bg-[#041612]/90">
      <div className="flex items-center justify-between gap-3">
        <h2 className="font-display text-lg">Rules</h2>
        <button
          className="rounded border border-slate/30 px-3 py-1.5 text-sm font-semibold disabled:cursor-not-allowed disabled:opacity-50 dark:border-cyan-900/40"
          disabled={!canManageTagging}
          onClick={onCreateNewRule}
        >
          New rule
        </button>
      </div>

      <div className="mt-3 space-y-2">
        {(bundleQuery.data?.rules ?? []).map((rule) => {
          const selected = rule.id === selectedRuleId
          return (
            <button
              key={rule.id}
              type="button"
              aria-pressed={selected}
              className={`w-full rounded-lg border p-3 text-left transition ${
                selected
                  ? 'tl-row-selected'
                  : 'border-slate/20 hover:border-slate/40 dark:border-cyan-900/40 dark:hover:border-cyan-700/60'
              }`}
              onClick={() => onSelectRule(rule)}
            >
              <div className="flex items-start justify-between gap-2">
                <div>
                  <p className="font-semibold">{rule.name}</p>
                  <p className="mt-1 text-xs text-slate dark:text-white/65">
                    {rule.tag_name} • {rule.match_type}
                  </p>
                </div>
                <span className={`tl-chip ${rule.enabled ? 'tl-chip-success' : 'tl-chip-neutral'}`}>
                  {rule.enabled ? 'Enabled' : 'Disabled'}
                </span>
              </div>
              <p className="mt-2 truncate text-xs text-slate dark:text-white/65">{rule.pattern}</p>
            </button>
          )
        })}

        {bundleQuery.isLoading && <p className="text-sm text-slate dark:text-white/70">Loading tagging rules...</p>}
        {bundleQuery.isError && (
          <p className="text-sm text-red-600">
            {resolveApiErrorMessage(bundleQuery.error, 'Failed to load tagging settings.')}
          </p>
        )}
        {!bundleQuery.isLoading && (bundleQuery.data?.rules.length ?? 0) === 0 && (
          <p className="rounded-lg border border-dashed border-slate/25 p-3 text-sm text-slate dark:border-cyan-900/40 dark:text-white/70">
            No custom rules yet. Create one to add a new auto-tag based on article text or feed context.
          </p>
        )}
      </div>
    </section>
  )
}

export function TaggingRuleEditor({ controller }: TaggingPanelProps) {
  const {
    deleteRule,
    onPreviewRule,
    onRequestDeleteRule,
    onSaveRule,
    pendingRuleDelete,
    previewRule,
    ruleDraft,
    ruleValidationError,
    saveRule,
    selectedRule,
    setRuleDraft,
  } = controller

  return (
    <section className="rounded-xl border border-slate/20 bg-white/80 p-3 dark:border-cyan-900/40 dark:bg-[#041612]/90">
      <fieldset disabled={!controller.canManageTagging} className="m-0 min-w-0 border-0 p-0">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="font-display text-lg">{selectedRule ? 'Edit rule' : 'Create rule'}</h2>
            <p className="mt-1 text-sm text-slate dark:text-white/75">
              Add a new tag when article text, title, or feed context matches the conditions below.
            </p>
          </div>
          <label className="flex items-center gap-2 rounded-full border border-slate/20 px-3 py-1 text-sm dark:border-cyan-900/40">
            <input
              type="checkbox"
              checked={ruleDraft.enabled}
              onChange={(event) => setRuleDraft((current) => ({ ...current, enabled: event.target.checked }))}
            />
            Enabled
          </label>
        </div>

        <RuleIdentityFields ruleDraft={ruleDraft} setRuleDraft={setRuleDraft} />
        <RulePatternFields ruleDraft={ruleDraft} setRuleDraft={setRuleDraft} />
        <RuleSelectionGroup
          title="Fields to inspect"
          description="Choose which fields the pattern should inspect."
          entries={RULE_FIELDS}
          selectedValues={ruleDraft.applies_to}
          onToggle={(value) =>
            setRuleDraft((current) => ({
              ...current,
              applies_to: toggleValue(current.applies_to, value),
            }))
          }
        />
        <RuleSelectionGroup
          title="Required categories"
          description="Optional. If empty, the rule can match any classified item."
          entries={BUILTIN_CATEGORIES.map((category) => ({ value: category, label: formatTaggingCategory(category) }))}
          selectedValues={ruleDraft.required_categories}
          onToggle={(value) =>
            setRuleDraft((current) => ({
              ...current,
              required_categories: toggleValue(current.required_categories, value),
            }))
          }
        />
        <RuleFeedScope controller={controller} />

        <div className="mt-4 flex flex-wrap items-center gap-2">
          <button
            className="rounded bg-ink px-3 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50 dark:bg-cyan dark:text-[#053c2e]"
            disabled={saveRule.isPending || Boolean(ruleValidationError) || !controller.canManageTagging}
            onClick={onSaveRule}
          >
            {selectedRule ? 'Save rule' : 'Create rule'}
          </button>
          <button
            className="rounded border border-slate/30 px-3 py-2 text-sm font-semibold disabled:cursor-not-allowed disabled:opacity-50 dark:border-cyan-900/40"
            disabled={previewRule.isPending || Boolean(ruleValidationError) || !controller.canManageTagging}
            onClick={onPreviewRule}
          >
            Preview rule
          </button>
          {selectedRule && (
            <button
              className="rounded border border-red-300 px-3 py-2 text-sm font-semibold text-red-700 disabled:cursor-not-allowed disabled:opacity-50 dark:border-red-900/60 dark:text-red-300"
              disabled={deleteRule.isPending || Boolean(pendingRuleDelete) || !controller.canManageTagging}
              onClick={() => onRequestDeleteRule(selectedRule)}
            >
              Delete rule
            </button>
          )}
        </div>

        {ruleValidationError && <p className="mt-2 text-sm text-amber-700 dark:text-amber-300">{ruleValidationError}</p>}
        <RuleMutationErrors controller={controller} />
      </fieldset>
    </section>
  )
}

type RuleDraftFieldsProps = Pick<TaggingSettingsController, 'ruleDraft' | 'setRuleDraft'>

function RuleIdentityFields({ ruleDraft, setRuleDraft }: RuleDraftFieldsProps) {
  return (
    <div className="mt-3 grid gap-3 md:grid-cols-2">
      <div>
        <label htmlFor="tagging-rule-name" className="text-sm font-semibold">
          Rule name
        </label>
        <input
          id="tagging-rule-name"
          className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
          value={ruleDraft.name}
          onChange={(event) => setRuleDraft((current) => ({ ...current, name: event.target.value }))}
        />
      </div>
      <div>
        <label htmlFor="tagging-rule-tag-name" className="text-sm font-semibold">
          Tag name
        </label>
        <input
          id="tagging-rule-tag-name"
          className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
          value={ruleDraft.tag_name}
          onChange={(event) => setRuleDraft((current) => ({ ...current, tag_name: event.target.value }))}
        />
      </div>
      <div>
        <label htmlFor="tagging-rule-match-type" className="text-sm font-semibold">
          Match type
        </label>
        <select
          id="tagging-rule-match-type"
          className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
          value={ruleDraft.match_type}
          onChange={(event) =>
            setRuleDraft((current) => ({ ...current, match_type: event.target.value as TaggingRuleDraft['match_type'] }))
          }
        >
          <option value="contains">Contains text</option>
          <option value="regex">Regular expression</option>
        </select>
      </div>
      <div>
        <label htmlFor="tagging-rule-min-confidence" className="text-sm font-semibold">
          Minimum classification confidence
        </label>
        <input
          id="tagging-rule-min-confidence"
          className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
          type="number"
          min={0}
          max={1}
          step={0.01}
          value={ruleDraft.min_classification_confidence}
          onChange={(event) =>
            setRuleDraft((current) => ({ ...current, min_classification_confidence: event.target.value }))
          }
        />
      </div>
    </div>
  )
}

function RulePatternFields({ ruleDraft, setRuleDraft }: RuleDraftFieldsProps) {
  return (
    <div className="mt-3">
      <label htmlFor="tagging-rule-pattern" className="text-sm font-semibold">
        Pattern
      </label>
      <textarea
        id="tagging-rule-pattern"
        className="mt-1 h-24 w-full rounded border border-slate/30 bg-white px-3 py-2 font-mono text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
        value={ruleDraft.pattern}
        onChange={(event) => setRuleDraft((current) => ({ ...current, pattern: event.target.value }))}
      />
      <label className="mt-2 flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={ruleDraft.case_sensitive}
          onChange={(event) => setRuleDraft((current) => ({ ...current, case_sensitive: event.target.checked }))}
        />
        Case sensitive
      </label>
    </div>
  )
}

function RuleSelectionGroup<T extends string>({
  title,
  description,
  entries,
  selectedValues,
  onToggle,
}: {
  title: string
  description: string
  entries: ReadonlyArray<{ value: T; label: string }>
  selectedValues: T[]
  onToggle: (value: T) => void
}) {
  return (
    <div className="mt-4">
      <h3 className="font-semibold">{title}</h3>
      <p className="mt-1 text-xs text-slate dark:text-white/65">{description}</p>
      <div role="group" aria-label={title} className="mt-2 flex flex-wrap gap-2">
        {entries.map((entry) => {
          const active = selectedValues.includes(entry.value)
          return (
            <button
              type="button"
              key={entry.value}
              aria-pressed={active}
              className={`rounded-full border px-3 py-1.5 text-sm ${
                active ? 'tl-chip-filter-active' : 'tl-chip-neutral hover:border-slate/40 dark:hover:border-cyan-700/60'
              }`}
              onClick={() => onToggle(entry.value)}
            >
              {entry.label}
            </button>
          )
        })}
      </div>
    </div>
  )
}

function RuleFeedScope({ controller }: TaggingPanelProps) {
  const { feeds, feedsQuery, ruleDraft, setRuleDraft } = controller

  return (
    <div className="mt-4 rounded-lg border border-slate/20 p-3 dark:border-cyan-900/40">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="font-semibold">Feed scope</h3>
          <p className="mt-1 text-xs text-slate dark:text-white/65">Target all feeds or limit this rule to selected feeds.</p>
        </div>
        <div role="group" aria-label="Rule feed scope" className="flex rounded-lg border border-slate/20 p-1 dark:border-cyan-900/40">
          <button
            type="button"
            aria-pressed={ruleDraft.feed_scope === 'all'}
            className={`rounded px-3 py-1 text-sm ${
              ruleDraft.feed_scope === 'all'
                ? 'bg-ink text-white dark:bg-cyan dark:text-[#053c2e]'
                : 'text-slate dark:text-white/75'
            }`}
            onClick={() => setRuleDraft((current) => ({ ...current, feed_scope: 'all', feed_ids: [] }))}
          >
            Any feed
          </button>
          <button
            type="button"
            aria-pressed={ruleDraft.feed_scope === 'selected'}
            className={`rounded px-3 py-1 text-sm ${
              ruleDraft.feed_scope === 'selected'
                ? 'bg-ink text-white dark:bg-cyan dark:text-[#053c2e]'
                : 'text-slate dark:text-white/75'
            }`}
            onClick={() => setRuleDraft((current) => ({ ...current, feed_scope: 'selected' }))}
          >
            Selected feeds
          </button>
        </div>
      </div>

      {ruleDraft.feed_scope === 'selected' && (
        <div className="mt-3">
          {feedsQuery.isLoading && <p className="text-sm text-slate dark:text-white/70">Loading feeds...</p>}
          {feedsQuery.isError && (
            <p role="alert" className="text-sm text-red-600">
              {resolveApiErrorMessage(feedsQuery.error, 'Failed to load feeds for rule scope.')}
            </p>
          )}
          {!feedsQuery.isLoading && !feedsQuery.isError && (
            <div className="grid gap-2 md:grid-cols-2">
              {feeds.map((feed) => (
                <FeedScopeOption
                  key={feed.id}
                  feed={feed}
                  checked={ruleDraft.feed_ids.includes(feed.id)}
                  onToggle={() =>
                    setRuleDraft((current) => ({
                      ...current,
                      feed_scope: 'selected',
                      feed_ids: toggleValue(current.feed_ids, feed.id),
                    }))
                  }
                />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function FeedScopeOption({ feed, checked, onToggle }: { feed: Feed; checked: boolean; onToggle: () => void }) {
  return (
    <label className="flex items-start gap-3 rounded border border-slate/20 p-3 text-sm dark:border-cyan-900/40">
      <input className="mt-1" type="checkbox" checked={checked} onChange={onToggle} />
      <span>
        <span className="block font-semibold">{feed.name}</span>
        <span className="text-xs text-slate dark:text-white/60">{feed.url}</span>
      </span>
    </label>
  )
}

function RuleMutationErrors({ controller }: TaggingPanelProps) {
  const { deleteRule, previewRule, saveRule } = controller
  const errors = [
    { mutation: saveRule, fallback: 'Failed to save tagging rule.' },
    { mutation: deleteRule, fallback: 'Failed to delete tagging rule.' },
    { mutation: previewRule, fallback: 'Failed to preview tagging rule.' },
  ]

  return (
    <>
      {errors.map(({ mutation, fallback }) =>
        mutation.isError ? (
          <p key={fallback} role="alert" aria-live="assertive" aria-atomic="true" className="mt-2 text-sm text-red-600">
            {resolveApiErrorMessage(mutation.error, fallback)}
          </p>
        ) : null,
      )}
    </>
  )
}

export function TaggingRulePreview({ controller }: TaggingPanelProps) {
  const { previewResult } = controller

  return (
    <section className="rounded-xl border border-slate/20 bg-white/80 p-3 dark:border-cyan-900/40 dark:bg-[#041612]/90">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="font-display text-lg">Rule preview</h2>
          <p className="mt-1 text-sm text-slate dark:text-white/75">
            See how this rule would match the current corpus before you save it.
          </p>
        </div>
        {previewResult && (
          <span className="tl-chip tl-chip-md tl-chip-info">
            {previewResult.total} current match{previewResult.total === 1 ? '' : 'es'}
          </span>
        )}
      </div>

      {!previewResult && (
        <p className="mt-3 text-sm text-slate dark:text-white/70">Run a preview to inspect recent matches and affected items.</p>
      )}
      {previewResult && (
        <div className="mt-3 space-y-3">
          {previewResult.items.length > 0 ? (
            previewResult.items.map((item) => (
              <article
                key={item.id}
                className="rounded-lg border border-slate/20 bg-white/70 p-3 dark:border-cyan-900/40 dark:bg-[#072019]/70"
              >
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <p className="font-semibold">{item.title}</p>
                    <p className="mt-1 text-xs text-slate dark:text-white/60">
                      {item.feed_name} • {formatTaggingTimestamp(item.first_seen_at)}
                    </p>
                  </div>
                  {item.classification && <span className="tl-chip tl-chip-neutral">{formatTaggingCategory(item.classification)}</span>}
                </div>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {item.matched_sections.map((section) => (
                    <span key={`${item.id}-${section}`} className="tl-chip tl-chip-neutral">
                      matched in {formatTaggingField(section)}
                    </span>
                  ))}
                  {item.current_tags.map((tagName) => (
                    <span
                      key={`${item.id}-${tagName}`}
                      className="rounded-full border border-slate/25 bg-slate/10 px-2 py-0.5 text-[11px] text-slate-700 dark:border-white/10 dark:bg-white/5 dark:text-white/70"
                    >
                      current: {tagName}
                    </span>
                  ))}
                </div>
              </article>
            ))
          ) : (
            <p className="text-sm text-slate dark:text-white/70">No current items would match this rule.</p>
          )}
          {previewResult.total > previewResult.items.length && (
            <p className="text-xs text-slate dark:text-white/60">
              Showing the {previewResult.items.length} most recent matches out of {previewResult.total}.
            </p>
          )}
        </div>
      )}
    </section>
  )
}

function toggleValue<T>(values: T[], value: T): T[] {
  return values.includes(value) ? values.filter((entry) => entry !== value) : [...values, value]
}
