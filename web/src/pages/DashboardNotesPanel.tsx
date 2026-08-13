import { WINDOW_TYPE_META } from './dashboardPanelPresentation'
import type { DashboardPageController } from './useDashboardPageController'

type DashboardWindow = DashboardPageController['renderedWindows'][number]

export function DashboardNotesPanel({
  controller,
  windowLayout,
}: {
  controller: DashboardPageController
  windowLayout: DashboardWindow
}) {
  const { updateWindowScratchNote } = controller
  const windowMeta = WINDOW_TYPE_META[windowLayout.type]

  return (
                <div className={`flex flex-1 flex-col p-2.5 sm:p-3 ${windowMeta.panelClassName}`}>
                  <label className="text-xs font-semibold text-slate dark:text-slate-300">Scratch notes</label>
                  <textarea
                    className="tl-dashboard-notes-editor mt-2 h-full min-h-[180px] w-full flex-1 rounded border border-slate/20 bg-white px-3 py-2 text-sm leading-6 dark:border-cyan-900/40 dark:bg-[#072019]"
                    placeholder="Use this space for quick notes, pivots, and hypotheses..."
                    value={windowLayout.scratch_note}
                    onChange={(event) => updateWindowScratchNote(windowLayout.id, event.target.value)}
                    aria-label={`${windowLayout.title} scratch notes`}
                  />
                  <p className="mt-2 text-xs text-slate dark:text-slate-300">Saved in this panel and in saved views.</p>
                </div>

  )
}
