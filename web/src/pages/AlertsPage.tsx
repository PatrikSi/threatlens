import { useEffect, useRef, useState, type KeyboardEvent } from 'react'

import { useCurrentUser } from '../hooks/useCurrentUser'
import { AlertOperationsWorkspace } from './AlertOperationsWorkspace'
import { AlertOccurrencesWorkspace } from './AlertOccurrencesWorkspace'
import { AlertDeleteDialog, AlertEditorPanel, ConfiguredAlertsPanel } from './AlertsPagePanels'
import { useAlertsPageController } from './useAlertsPageController'

type AlertsView = 'rules' | 'occurrences' | 'operations'

export function AlertsPage() {
  const controller = useAlertsPageController()
  const currentUserQuery = useCurrentUser()
  const isAdmin = currentUserQuery.data?.role === 'admin'
  const [activeView, setActiveView] = useState<AlertsView>('rules')
  const [occurrencesVisited, setOccurrencesVisited] = useState(false)
  const [operationsVisited, setOperationsVisited] = useState(false)
  const tabRefs = useRef<Partial<Record<AlertsView, HTMLButtonElement | null>>>({})
  const views: AlertsView[] = isAdmin ? ['rules', 'occurrences', 'operations'] : ['rules', 'occurrences']

  useEffect(() => {
    if (!isAdmin && activeView === 'operations') setActiveView('rules')
  }, [activeView, isAdmin])

  const activateView = (view: AlertsView) => {
    if (view === 'occurrences') setOccurrencesVisited(true)
    if (view === 'operations') setOperationsVisited(true)
    setActiveView(view)
  }
  const handleTabKeyDown = (event: KeyboardEvent<HTMLButtonElement>, view: AlertsView) => {
    const currentIndex = views.indexOf(view)
    let nextIndex: number | null = null
    if (event.key === 'ArrowRight') nextIndex = (currentIndex + 1) % views.length
    if (event.key === 'ArrowLeft') nextIndex = (currentIndex - 1 + views.length) % views.length
    if (event.key === 'Home') nextIndex = 0
    if (event.key === 'End') nextIndex = views.length - 1
    if (nextIndex === null) return
    event.preventDefault()
    const nextView = views[nextIndex]
    activateView(nextView)
    tabRefs.current[nextView]?.focus()
  }

  return (
    <>
      <section className="mb-3 rounded-lg border border-slate/20 bg-white/80 px-3 py-3 dark:border-cyan-900/40 dark:bg-[#041612]/90 sm:px-4">
        <div>
          <h1 className="font-display text-xl sm:text-2xl">Alerts</h1>
          <p className="mt-0.5 text-sm text-slate dark:text-slate-300">
            Triage durable matches and maintain the rules that produce them.
          </p>
        </div>
        <nav
          className="mt-3 flex gap-1 overflow-x-auto border-t border-slate/15 pt-3 dark:border-white/10"
          role="tablist"
          aria-label="Alert views"
        >
          <AlertViewTab
            id="alert-rules-tab"
            controls="alert-rules-panel"
            active={activeView === 'rules'}
            label="Rules"
            tabRef={(node) => { tabRefs.current.rules = node }}
            onClick={() => activateView('rules')}
            onKeyDown={(event) => handleTabKeyDown(event, 'rules')}
          />
          <AlertViewTab
            id="alert-occurrences-tab"
            controls="alert-occurrences-panel"
            active={activeView === 'occurrences'}
            label="Occurrence triage"
            tabRef={(node) => { tabRefs.current.occurrences = node }}
            onClick={() => activateView('occurrences')}
            onKeyDown={(event) => handleTabKeyDown(event, 'occurrences')}
          />
          {isAdmin && (
            <AlertViewTab
              id="alert-operations-tab"
              controls="alert-operations-panel"
              active={activeView === 'operations'}
              label="Operations"
              tabRef={(node) => { tabRefs.current.operations = node }}
              onClick={() => activateView('operations')}
              onKeyDown={(event) => handleTabKeyDown(event, 'operations')}
            />
          )}
        </nav>
      </section>

      <div
        id="alert-rules-panel"
        role="tabpanel"
        aria-labelledby="alert-rules-tab"
        hidden={activeView !== 'rules'}
      >
        <div className="grid gap-4 xl:grid-cols-[480px_1fr]">
          <AlertEditorPanel controller={controller} />
          <ConfiguredAlertsPanel controller={controller} />
        </div>
      </div>
      <div
        id="alert-occurrences-panel"
        role="tabpanel"
        aria-labelledby="alert-occurrences-tab"
        hidden={activeView !== 'occurrences'}
      >
        {occurrencesVisited && <AlertOccurrencesWorkspace active={activeView === 'occurrences'} />}
      </div>
      {isAdmin && (
        <div
          id="alert-operations-panel"
          role="tabpanel"
          aria-labelledby="alert-operations-tab"
          hidden={activeView !== 'operations'}
        >
          {operationsVisited && <AlertOperationsWorkspace active={activeView === 'operations'} />}
        </div>
      )}
      <AlertDeleteDialog controller={controller} />
      {controller.confirmDiscardUnsavedAlertChanges.discardDialog}
    </>
  )
}

function AlertViewTab({
  id,
  controls,
  active,
  label,
  tabRef,
  onClick,
  onKeyDown,
}: {
  id: string
  controls: string
  active: boolean
  label: string
  tabRef: (node: HTMLButtonElement | null) => void
  onClick: () => void
  onKeyDown: (event: KeyboardEvent<HTMLButtonElement>) => void
}) {
  return (
    <button
      id={id}
      type="button"
      role="tab"
      aria-selected={active}
      aria-controls={controls}
      tabIndex={active ? 0 : -1}
      className={`min-h-10 shrink-0 rounded px-3 py-1.5 text-sm font-semibold ${
        active
          ? 'bg-ink text-white dark:bg-cyan dark:text-[#053c2e]'
          : 'border border-slate/20 text-slate-700 dark:border-white/10 dark:text-slate-200'
      }`}
      onClick={onClick}
      onKeyDown={onKeyDown}
      ref={tabRef}
    >
      {label}
    </button>
  )
}
