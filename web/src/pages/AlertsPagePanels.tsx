import { resolveApiErrorMessage } from '../api/errors'
import { ConfirmDialog } from '../components/ConfirmDialog'
import {
  ALERT_CATEGORIES,
  ALERT_SEVERITIES,
  describeAlertCategory,
  formatAlertPreviewSummary,
  formatAlertTimestamp,
  shouldShowSaveGuidance,
} from './alertPageModel'
import { AlertSeverityChip } from './AlertOccurrenceShared'
import { AlertsPageController } from './useAlertsPageController'

type AlertsPanelProps = {
  controller: AlertsPageController
}

export function AlertEditorPanel({ controller }: AlertsPanelProps) {
  const {
    category,
    editingAlertId,
    hasUnsavedAlertDraftChanges,
    keywordsText,
    name,
    onSave,
    resetForm,
    revisionConflict,
    saveAlert,
    saveDisabledReason,
    setCategory,
    setKeywordsText,
    setName,
    setSeverity,
    setSuppressionEnabled,
    setSuppressionReason,
    setSuppressionUntil,
    severity,
    suppressionEnabled,
    suppressionReason,
    suppressionUntil,
    updateAlertError,
  } = controller
  const savePendingLabel = editingAlertId ? 'Saving alert changes...' : 'Adding alert interest...'

  return (
    <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-display text-xl">
            {editingAlertId ? 'Edit Alert Interest' : 'Alert Interests'}
          </h2>
          <p className="mt-1 text-sm text-slate dark:text-slate-300">
            Define focused interests by category. Dashboard alert windows match item text against
            these keywords.
          </p>
        </div>
        {editingAlertId && (
          <button
            type="button"
            className="min-h-11 rounded border border-slate/30 px-3 py-1.5 text-sm font-semibold sm:min-h-0 dark:border-cyan-900/40"
            onClick={() => resetForm()}
          >
            Cancel edit
          </button>
        )}
      </div>

      <form className="mt-4 space-y-3" onSubmit={onSave}>
        <div>
          <label htmlFor="alert-interest-name" className="text-sm font-semibold">
            Interest Name
          </label>
          <input
            id="alert-interest-name"
            className="mt-1 min-h-11 w-full rounded border border-slate/30 bg-white px-3 py-2 sm:min-h-0 dark:border-cyan-900/40 dark:bg-[#072019]"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Microsoft Security Updates"
            required
          />
        </div>

        <div>
          <label htmlFor="alert-interest-category" className="text-sm font-semibold">
            Category
          </label>
          <select
            id="alert-interest-category"
            className="mt-1 min-h-11 w-full rounded border border-slate/30 bg-white px-3 py-2 sm:min-h-0 dark:border-cyan-900/40 dark:bg-[#072019]"
            value={category}
            onChange={(event) => setCategory(event.target.value)}
          >
            {ALERT_CATEGORIES.map((entry) => (
              <option key={entry.value} value={entry.value}>
                {entry.label}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label htmlFor="alert-interest-severity" className="text-sm font-semibold">
            Severity
          </label>
          <select
            id="alert-interest-severity"
            className="mt-1 min-h-11 w-full rounded border border-slate/30 bg-white px-3 py-2 sm:min-h-10 dark:border-cyan-900/40 dark:bg-[#072019]"
            value={severity}
            onChange={(event) => setSeverity(event.target.value as typeof severity)}
          >
            {ALERT_SEVERITIES.map((entry) => (
              <option key={entry.value} value={entry.value}>
                {entry.label}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label htmlFor="alert-interest-keywords" className="text-sm font-semibold">
            Keywords (comma-separated)
          </label>
          <textarea
            id="alert-interest-keywords"
            className="mt-1 h-24 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
            value={keywordsText}
            onChange={(event) => setKeywordsText(event.target.value)}
            placeholder="microsoft, exchange, entra id"
            required
          />
        </div>

        <fieldset className="rounded border border-slate/20 p-3 dark:border-cyan-900/40">
          <legend className="px-1 text-sm font-semibold">Notification suppression</legend>
          <label className="flex min-h-11 items-center gap-2 text-sm sm:min-h-10">
            <input
              type="checkbox"
              className="accent-cyan"
              checked={suppressionEnabled}
              onChange={(event) => setSuppressionEnabled(event.target.checked)}
            />
            Suppress notifications for this rule
          </label>
          {suppressionEnabled && (
            <div className="mt-2 grid gap-3 sm:grid-cols-2">
              <div>
                <label htmlFor="alert-interest-suppression-until" className="text-xs font-semibold">
                  Suppress until
                </label>
                <input
                  id="alert-interest-suppression-until"
                  type="datetime-local"
                  className="mt-1 min-h-11 w-full rounded border border-slate/30 bg-white px-2 py-1.5 text-sm sm:min-h-10 dark:border-cyan-900/40 dark:bg-[#072019]"
                  value={suppressionUntil}
                  onChange={(event) => setSuppressionUntil(event.target.value)}
                  required
                />
              </div>
              <div>
                <label
                  htmlFor="alert-interest-suppression-reason"
                  className="text-xs font-semibold"
                >
                  Reason
                </label>
                <input
                  id="alert-interest-suppression-reason"
                  className="mt-1 min-h-11 w-full rounded border border-slate/30 bg-white px-2 py-1.5 text-sm sm:min-h-10 dark:border-cyan-900/40 dark:bg-[#072019]"
                  value={suppressionReason}
                  onChange={(event) => setSuppressionReason(event.target.value)}
                  maxLength={500}
                  required
                />
              </div>
            </div>
          )}
        </fieldset>

        {revisionConflict && (
          <div
            role="alert"
            className="rounded border border-amber-300/70 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-700/50 dark:bg-amber-950/25 dark:text-amber-200"
          >
            <p className="font-semibold">This rule changed on the server.</p>
            <p className="mt-1">
              Your draft was not saved
              {revisionConflict.currentRowVersion
                ? `; the current version is ${revisionConflict.currentRowVersion}`
                : ''}
              .
            </p>
            <button
              type="button"
              className="mt-2 min-h-11 rounded border border-amber-500/50 px-2.5 py-1 text-xs font-semibold sm:min-h-9"
              onClick={() => void controller.reloadAlertAfterConflict()}
              disabled={controller.alertsQuery.isFetching}
            >
              {controller.alertsQuery.isFetching ? 'Loading latest rule...' : 'Reload latest rule'}
            </button>
          </div>
        )}

        {updateAlertError && revisionConflict && (
          <p role="alert" className="text-sm text-red-600 dark:text-red-300">
            {updateAlertError}
          </p>
        )}

        <div className="grid gap-2 sm:flex sm:flex-wrap">
          <button
            className="min-h-11 w-full rounded bg-ink px-3 py-2 text-white disabled:opacity-50 sm:min-h-0 sm:w-auto dark:bg-cyan dark:text-[#053c2e]"
            type="submit"
            disabled={saveAlert.isPending || Boolean(saveDisabledReason)}
            title={saveDisabledReason ?? undefined}
          >
            {saveAlert.isPending ? savePendingLabel : editingAlertId ? 'Save changes' : 'Add Interest'}
          </button>
          {editingAlertId && (
            <button
              className="min-h-11 w-full rounded border border-slate/30 px-3 py-2 text-sm font-semibold sm:min-h-0 sm:w-auto dark:border-cyan-900/40"
              type="button"
              onClick={() => resetForm()}
            >
              Reset
            </button>
          )}
        </div>
        {saveAlert.isPending && (
          <p role="status" aria-live="polite" aria-atomic="true" className="sr-only">
            {savePendingLabel}
          </p>
        )}
        {shouldShowSaveGuidance(saveDisabledReason, hasUnsavedAlertDraftChanges) && (
          <p
            role="status"
            aria-live="polite"
            aria-atomic="true"
            className="text-sm text-slate dark:text-white/70"
          >
            {saveDisabledReason}
          </p>
        )}
        {saveAlert.isError && !revisionConflict && (
          <p role="alert" className="text-sm text-red-600">
            {resolveApiErrorMessage(saveAlert.error, 'Alert interest could not be saved')}
          </p>
        )}
      </form>

      <AlertPreviewPanel controller={controller} />
    </section>
  )
}

function AlertPreviewPanel({ controller }: AlertsPanelProps) {
  const { category, previewEnabled, previewQuery } = controller

  return (
    <section className="mt-4 border-t border-slate/20 pt-4 sm:mt-5 sm:rounded-xl sm:border sm:bg-white/70 sm:p-4 dark:border-cyan-900/40 dark:sm:bg-white/[0.03]">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="font-display text-lg">Current Match Preview</h3>
          <p className="mt-1 text-sm text-slate dark:text-white/70">
            See how this alert would behave against the current corpus before you save it.
          </p>
        </div>
        {previewQuery.data && (
          <span className="tl-chip tl-chip-md tl-chip-info">
            {previewQuery.data.total} current match{previewQuery.data.total === 1 ? '' : 'es'}
          </span>
        )}
      </div>

      {!previewEnabled && (
        <p className="mt-3 text-sm text-slate dark:text-white/70">
          Add at least one keyword to preview recent matches.
        </p>
      )}
      {previewEnabled && previewQuery.isLoading && (
        <p role="status" className="mt-3 text-sm text-slate dark:text-white/70">Looking up current matches...</p>
      )}
      {previewEnabled && previewQuery.isError && (
        <p className="mt-3 text-sm text-red-600">
          {resolveApiErrorMessage(previewQuery.error, 'Alert preview could not be loaded')}
        </p>
      )}
      {previewEnabled && previewQuery.data && (
        <div className="mt-4 space-y-3">
          {previewQuery.data.items.length > 0 ? (
            previewQuery.data.items.map((item) => {
              const previewMatch = item.matches[0]
              return (
                <article
                  key={item.id}
                  className="rounded-lg border border-slate/20 bg-white/80 p-2.5 sm:p-3 dark:border-cyan-900/40 dark:bg-[#072019]/70"
                >
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div>
                      <p className="font-semibold">{item.title}</p>
                      <p className="mt-1 text-xs text-slate dark:text-white/60">
                        {item.feed_name} • {formatAlertTimestamp(item.first_seen_at)}
                      </p>
                    </div>
                    <span className="tl-chip tl-chip-neutral">
                      {describeAlertCategory(previewMatch?.category ?? category)}
                    </span>
                  </div>
                  {item.summary && (
                    <p className="mt-2 text-sm text-slate dark:text-white/75">
                      {formatAlertPreviewSummary(item.summary)}
                    </p>
                  )}
                  {previewMatch && (
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {previewMatch.matched_keywords.map((keyword) => (
                        <span key={`${item.id}-${keyword}`} className="tl-chip tl-chip-neutral">
                          {keyword}
                        </span>
                      ))}
                    </div>
                  )}
                </article>
              )
            })
          ) : (
            <p className="text-sm text-slate dark:text-white/70">
              This alert would not match any current items yet.
            </p>
          )}
          {previewQuery.data.total > previewQuery.data.items.length && (
            <p className="text-xs text-slate dark:text-white/60">
              Showing the {previewQuery.data.items.length} most recent matches out of{' '}
              {previewQuery.data.total}.
            </p>
          )}
        </div>
      )}
    </section>
  )
}

export function ConfiguredAlertsPanel({ controller }: AlertsPanelProps) {
  const {
    alertsQuery,
    deleteAlert,
    editingAlertId,
    groupedAlerts,
    onEdit,
    onRequestDeleteAlert,
    pendingDeleteAlert,
    setShowDisabled,
    showDisabled,
    toggleAlertState,
    updateAlert,
    updateAlertError,
  } = controller
  const pendingStateAlert = updateAlert.isPending
    ? alertsQuery.data?.find((alert) => alert.id === updateAlert.variables?.id) ?? null
    : null
  const pendingEnabledState = updateAlert.variables?.body.enabled
  const pendingStateVerb = pendingEnabledState === false
    ? 'Disabling'
    : pendingEnabledState === true
      ? 'Enabling'
      : 'Updating'

  return (
    <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="font-display text-xl">Configured Alerts</h2>
        <label className="flex min-h-11 items-center gap-2 text-sm sm:min-h-0">
          <input
            type="checkbox"
            checked={showDisabled}
            onChange={(event) => setShowDisabled(event.target.checked)}
            className="accent-cyan"
          />
          Include disabled
        </label>
      </div>

      {updateAlertError && (
        <p
          role="alert"
          className="mt-3 rounded border border-red-300/60 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-800/50 dark:bg-red-950/25 dark:text-red-200"
        >
          {updateAlertError}
        </p>
      )}

      {pendingStateAlert && (
        <p role="status" aria-live="polite" aria-atomic="true" className="sr-only">
          {pendingStateVerb} alert rule {pendingStateAlert.name}...
        </p>
      )}

      <div className="mt-4 space-y-4">
        {ALERT_CATEGORIES.map((categoryOption) => {
          const entries = groupedAlerts.get(categoryOption.value) ?? []
          if (!entries.length) {
            return null
          }
          return (
            <div
              key={categoryOption.value}
              className="rounded border border-slate/20 bg-white/70 p-2.5 sm:p-3 dark:border-cyan-900/40 dark:bg-white/[0.02]"
            >
              <h3 className="text-sm font-semibold uppercase text-slate dark:text-slate-300">
                {categoryOption.label}
              </h3>
              <div className="mt-2 space-y-2">
                {entries.map((alert) => (
                  <article
                    key={alert.id}
                    className={`rounded border p-2 ${
                      editingAlertId === alert.id
                        ? 'border-cyan bg-cyan/10 dark:border-cyan-500/35 dark:bg-cyan-950/30'
                        : 'border-slate/20 bg-white/75 dark:border-cyan-900/40 dark:bg-[#072019]/45'
                    }`}
                  >
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between sm:gap-2">
                      <div className="min-w-0">
                        <p className="font-semibold">{alert.name}</p>
                        <div className="mt-1 flex flex-wrap items-center gap-1.5">
                          <AlertSeverityChip severity={alert.severity ?? 'medium'} />
                          <span className="tl-chip tl-chip-neutral">
                            Rule revision {alert.revision ?? 1}
                          </span>
                          {alert.row_version && (
                            <span className="tl-chip tl-chip-neutral">
                              Version {alert.row_version}
                            </span>
                          )}
                          {!alert.enabled && (
                            <span className="tl-chip tl-chip-neutral">Disabled</span>
                          )}
                          {alert.suppression_until && (
                            <span className="tl-chip tl-chip-warning">
                              {new Date(alert.suppression_until) > new Date()
                                ? 'Suppressed until '
                                : 'Suppression expired '}
                              {formatAlertTimestamp(alert.suppression_until)}
                            </span>
                          )}
                        </div>
                        {alert.suppression_reason && (
                          <p className="mt-1 break-words text-xs text-slate dark:text-white/60">
                            {alert.suppression_reason}
                          </p>
                        )}
                      </div>
                      <div className="grid grid-cols-3 gap-2 sm:flex sm:items-center">
                        <button
                          type="button"
                          className="min-h-11 rounded border border-slate/30 px-2 py-1 text-xs sm:min-h-0 dark:border-cyan-900/40"
                          aria-label={`Edit alert rule ${alert.name}`}
                          onClick={() => onEdit(alert)}
                        >
                          Edit
                        </button>
                        <button
                          type="button"
                          className="min-h-11 rounded border border-slate/30 px-2 py-1 text-xs sm:min-h-0 dark:border-cyan-900/40"
                          aria-label={`${alert.enabled ? 'Disable' : 'Enable'} alert rule ${alert.name}`}
                          onClick={() => toggleAlertState(alert)}
                          disabled={updateAlert.isPending}
                        >
                          {pendingStateAlert?.id === alert.id
                            ? `${pendingStateVerb}...`
                            : alert.enabled
                              ? 'Disable'
                              : 'Enable'}
                        </button>
                        <button
                          type="button"
                          className="min-h-11 rounded border border-slate/30 px-2 py-1 text-xs text-red-600 sm:min-h-0 dark:border-cyan-900/40"
                          aria-label={`Delete alert rule ${alert.name}`}
                          onClick={() => onRequestDeleteAlert(alert)}
                          disabled={deleteAlert.isPending || Boolean(pendingDeleteAlert)}
                        >
                          Delete
                        </button>
                      </div>
                    </div>
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {alert.keywords.map((keyword) => (
                        <span key={`${alert.id}-${keyword}`} className="tl-chip tl-chip-neutral">
                          {keyword}
                        </span>
                      ))}
                    </div>
                  </article>
                ))}
              </div>
            </div>
          )
        })}

        {alertsQuery.isLoading && (
          <p role="status" className="text-sm text-slate dark:text-slate-300">Loading alert interests...</p>
        )}
        {alertsQuery.isError && (
          <div
            role="alert"
            className="rounded border border-red-300/60 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-800/50 dark:bg-red-950/25 dark:text-red-200"
          >
            <p>
              {resolveApiErrorMessage(alertsQuery.error, 'Alert interests could not be loaded')}
            </p>
            {alertsQuery.data && (
              <p className="mt-1">The last loaded alert rules remain visible.</p>
            )}
            <button
              type="button"
              className="mt-2 min-h-11 rounded border border-current px-3 py-2 font-semibold sm:min-h-0 sm:py-1.5"
              onClick={() => void alertsQuery.refetch()}
              disabled={alertsQuery.isFetching}
            >
              {alertsQuery.isFetching ? 'Retrying...' : 'Retry alert rules'}
            </button>
          </div>
        )}
        {!alertsQuery.isLoading && !alertsQuery.isError && (alertsQuery.data?.length ?? 0) === 0 && (
          <p className="text-sm text-slate dark:text-slate-300">
            No alert interests configured yet.
          </p>
        )}
      </div>
    </section>
  )
}

export function AlertDeleteDialog({ controller }: AlertsPanelProps) {
  const {
    confirmDeleteAlert,
    deleteAlert,
    deleteConflictNeedsRefresh,
    deleteAlertError,
    editingAlertId,
    hasUnsavedAlertDraftChanges,
    pendingDeleteAlert,
    reloadPendingDeleteAfterConflict,
    setDeleteAlertError,
    setPendingDeleteAlert,
  } = controller

  return (
    <ConfirmDialog
      open={Boolean(pendingDeleteAlert)}
      title="Delete alert interest?"
      description="This permanently removes the alert interest and stops future item matching for these keywords."
      confirmLabel="Delete alert"
      onCancel={() => {
        setPendingDeleteAlert(null)
        setDeleteAlertError(null)
      }}
      onConfirm={confirmDeleteAlert}
      confirmDisabled={!pendingDeleteAlert || deleteConflictNeedsRefresh}
      isConfirming={deleteAlert.isPending}
      confirmingLabel="Deleting alert..."
    >
      {pendingDeleteAlert && (
        <div className="space-y-3">
          <div className="space-y-1">
            <p className="font-semibold text-ink dark:text-white">{pendingDeleteAlert.name}</p>
            <p className="text-xs text-slate dark:text-white/70">
              Category: {describeAlertCategory(pendingDeleteAlert.category)} ·{' '}
              {pendingDeleteAlert.keywords.length} keyword
              {pendingDeleteAlert.keywords.length === 1 ? '' : 's'} ·{' '}
              {pendingDeleteAlert.enabled ? 'Enabled' : 'Disabled'}
            </p>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {pendingDeleteAlert.keywords.map((keyword) => (
              <span key={`${pendingDeleteAlert.id}-${keyword}`} className="tl-chip tl-chip-neutral">
                {keyword}
              </span>
            ))}
          </div>
          {pendingDeleteAlert.id === editingAlertId && hasUnsavedAlertDraftChanges && (
            <p className="rounded-lg border border-amber-300/70 bg-amber-100/80 px-3 py-2 text-xs text-amber-900 dark:border-amber-800/50 dark:bg-amber-950/30 dark:text-amber-200">
              Your current unsaved edits for this alert will be discarded too.
            </p>
          )}
          {deleteAlertError && (
            <div role="alert" className="text-sm text-red-600 dark:text-red-300">
              <p>{deleteAlertError}</p>
              {deleteConflictNeedsRefresh && (
                <button
                  type="button"
                  className="mt-2 min-h-11 rounded border border-current px-3 py-2 font-semibold sm:min-h-10"
                  onClick={() => void reloadPendingDeleteAfterConflict()}
                  disabled={controller.alertsQuery.isFetching}
                >
                  {controller.alertsQuery.isFetching ? 'Reloading...' : 'Reload latest rule'}
                </button>
              )}
            </div>
          )}
          {deleteAlert.isPending && (
            <p role="status" aria-live="polite" aria-atomic="true" className="sr-only">
              Deleting alert rule {pendingDeleteAlert.name}...
            </p>
          )}
        </div>
      )}
    </ConfirmDialog>
  )
}
