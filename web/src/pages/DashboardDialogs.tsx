import { resolveApiErrorMessage } from '../api/errors'
import {
  ConfirmDialog,
  DialogSurface,
} from '../components/ConfirmDialog'
import {
  formatDateTime,
} from '../utils/datetime'
import {
  SavedViewThumbnail,
} from './DashboardPageComponents'
import type { DashboardPageController } from './useDashboardPageController'

export function DashboardDialogs({ controller }: { controller: DashboardPageController }) {
  const {
    closeRenameWindow, confirmDiscardUnsavedDashboardChanges, deleteView, exportAllViews, hasProtectedEditSession,
    importViewsError, importViewsFile, importViewsInputRef, importViewsResult, isImportingViews,
    onConfirmDeleteView, onConfirmPendingSavedViewLoad, openImportViewsPicker, pendingSavedViewLoad, pendingViewDelete,
    renameWindowDraft, renameWindowInputRef, renamingWindowId, requestSavedViewLoad, saveRenamedWindow,
    savedViewPreviews, setPendingSavedViewLoad, setPendingViewDelete, setRenameWindowDraft, setShowManageViewsModal,
    setViewDeleteError, showManageViewsModal, viewDeleteError, viewsQuery,
  } = controller

  return (
    <>
      {renamingWindowId && (
        <DialogSurface
          open
          title="Rename panel"
          description="Rename this panel without leaving the dashboard."
          eyebrow="Panel settings"
          onClose={closeRenameWindow}
          initialFocusRef={renameWindowInputRef}
          panelClassName="max-w-md"
          footer={
            <>
              <button
                type="button"
                className="rounded border border-slate/20 px-3 py-2 text-xs font-semibold dark:border-cyan-900/40"
                onClick={closeRenameWindow}
              >
                Cancel
              </button>
              <button
                type="button"
                className="rounded bg-ink px-3 py-2 text-xs font-semibold text-white dark:bg-cyan dark:text-slate-950"
                onClick={saveRenamedWindow}
              >
                Save Panel Title
              </button>
            </>
          }
        >
          <div className="space-y-3">
            <div className="space-y-1">
              <label
                htmlFor="dashboard-panel-title-input"
                className="text-xs font-semibold uppercase text-slate dark:text-white/60"
              >
                Panel title
              </label>
              <input
                id="dashboard-panel-title-input"
                ref={renameWindowInputRef}
                value={renameWindowDraft}
                onChange={(event) => setRenameWindowDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    event.preventDefault()
                    saveRenamedWindow()
                  }
                }}
                maxLength={80}
                className="w-full rounded border border-slate/20 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
              />
            </div>
            <p className="text-xs text-slate dark:text-white/60">Up to 80 characters. Saved with this view.</p>
          </div>
        </DialogSurface>
      )}

      {showManageViewsModal && (
        <DialogSurface
          open
          title="Manage Saved Views"
          description="Load, import, export, or delete saved dashboard layouts without leaving your current workspace."
          onClose={() => setShowManageViewsModal(false)}
          panelClassName="max-h-[92vh] max-w-3xl overflow-auto"
          bodyClassName="mt-4 space-y-4 text-sm text-slate dark:text-white/75"
        >
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              className="rounded border border-slate/20 px-3 py-1.5 text-xs dark:border-cyan-900/40"
              onClick={exportAllViews}
            >
              Export JSON
            </button>
            <button
              type="button"
              className="rounded border border-slate/20 px-3 py-1.5 text-xs disabled:cursor-not-allowed disabled:opacity-60 dark:border-cyan-900/40"
              onClick={openImportViewsPicker}
              disabled={isImportingViews}
            >
              Import JSON
            </button>
            <input
              ref={importViewsInputRef}
              type="file"
              accept="application/json"
              className="hidden"
              tabIndex={-1}
              aria-label="Import saved dashboard views JSON"
              onChange={(event) => {
                void importViewsFile(event)
              }}
              disabled={isImportingViews}
            />
            {isImportingViews && <span className="text-xs text-slate dark:text-slate-300">Importing...</span>}
            {importViewsError && <span className="text-xs text-red-600">{importViewsError}</span>}
            {importViewsResult && <span className="text-xs text-emerald-600">{importViewsResult}</span>}
          </div>

          <div className="grid gap-2 md:grid-cols-2">
            {savedViewPreviews.map((view) => (
              <div
                key={view.id}
                className="rounded border border-slate/20 p-2 dark:border-cyan-900/40"
              >
                <div className="flex items-start gap-3">
                  <SavedViewThumbnail windows={view.windows} />
                  <div className="min-w-0 flex-1">
                    <p className="truncate font-semibold">{view.name}</p>
                    <p className="text-xs text-slate dark:text-slate-300">{formatDateTime(view.created_at)}</p>
                    <div className="mt-1 flex flex-wrap gap-1.5 text-[11px]">
                      <span className="rounded border border-slate/20 px-1.5 py-0.5 dark:border-cyan-900/40">
                        RSS {view.window_type_counts.rss}
                      </span>
                      <span className="rounded border border-slate/20 px-1.5 py-0.5 dark:border-cyan-900/40">
                        Alerts {view.window_type_counts.alerts}
                      </span>
                      <span className="rounded border border-slate/20 px-1.5 py-0.5 dark:border-cyan-900/40">
                        Notes {view.window_type_counts.notes}
                      </span>
                      <span className="rounded border border-slate/20 px-1.5 py-0.5 dark:border-cyan-900/40">
                        Daily Brief {view.window_type_counts.daily_brief}
                      </span>
                    </div>
                  </div>
                </div>
                <div className="mt-2 flex items-center gap-2">
                  <button
                    type="button"
                    className="rounded border border-slate/20 px-2 py-1 text-xs dark:border-cyan-900/40"
                    onClick={() => requestSavedViewLoad(view.id)}
                    aria-label={`Load saved view ${view.name}`}
                  >
                    Load
                  </button>
                  <button
                    type="button"
                    className="rounded border border-slate/20 px-2 py-1 text-xs text-red-600 dark:border-cyan-900/40"
                    onClick={() => {
                      setViewDeleteError('')
                      setPendingViewDelete(view)
                    }}
                    disabled={deleteView.isPending || Boolean(pendingViewDelete)}
                    aria-label={`Delete saved view ${view.name}`}
                  >
                    Delete
                  </button>
                </div>
              </div>
            ))}

            {viewsQuery.isLoading && <p className="text-sm text-slate dark:text-slate-300">Loading saved views...</p>}
            {viewsQuery.isError && (
              <p className="text-sm text-red-600">
                {resolveApiErrorMessage(viewsQuery.error, 'Saved dashboard views could not be loaded')}
              </p>
            )}
            {!viewsQuery.isLoading && !viewsQuery.data?.length && (
              <p className="text-sm text-slate dark:text-slate-300">No saved views available.</p>
            )}
          </div>
        </DialogSurface>
      )}

      <ConfirmDialog
        open={Boolean(pendingSavedViewLoad)}
        title={hasProtectedEditSession ? 'Discard the current edit session?' : 'Discard unsaved note drafts?'}
        description={
          hasProtectedEditSession
            ? 'Loading another saved view will replace the layout you are editing and clear the current cancel checkpoint.'
            : 'Loading another saved view can hide your in-progress note drafts before you save them.'
        }
        confirmLabel="Load saved view"
        onCancel={() => setPendingSavedViewLoad(null)}
        onConfirm={onConfirmPendingSavedViewLoad}
      >
        {pendingSavedViewLoad && (
          <div className="space-y-2">
            <p className="font-semibold text-ink dark:text-white">{pendingSavedViewLoad.name}</p>
            <p className="text-xs text-slate dark:text-white/70">
              {hasProtectedEditSession
                ? 'Save or cancel the current edit session first if you want to keep those unsaved layout changes.'
                : 'Save your item notes first if you want the current note drafts to remain safely persisted.'}
            </p>
          </div>
        )}
      </ConfirmDialog>

      <ConfirmDialog
        open={Boolean(pendingViewDelete)}
        title="Delete saved view?"
        description="This permanently removes the saved dashboard layout and filters."
        confirmLabel="Delete view"
        onCancel={() => {
          setPendingViewDelete(null)
          setViewDeleteError('')
        }}
        onConfirm={onConfirmDeleteView}
        confirmDisabled={deleteView.isPending}
        isConfirming={deleteView.isPending}
      >
        {pendingViewDelete && (
          <div className="space-y-2">
            <p className="font-semibold text-ink dark:text-white">{pendingViewDelete.name}</p>
            <p className="text-xs text-slate dark:text-white/70">
              Saved on {formatDateTime(pendingViewDelete.created_at)}
            </p>
            {viewDeleteError && (
              <p role="alert" className="text-sm text-red-600 dark:text-red-300">
                {viewDeleteError}
              </p>
            )}
          </div>
        )}
      </ConfirmDialog>
      {confirmDiscardUnsavedDashboardChanges.discardDialog}
    </>
  )
}
