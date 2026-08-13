import {
  type ComponentProps,
  type Dispatch,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
  type RefObject,
  type SetStateAction,
} from 'react'

import { ConfirmDialog } from '../components/ConfirmDialog'
import { AISettings, AITaskRunResponse } from '../types/api'
import { ActivityTab } from './AiSettingsActivityTab'
import { ConfigurationTab } from './AiSettingsConfigurationTab'
import { OverviewTab } from './AiSettingsOverviewTab'
import { StatusPill, TabButton } from './aiSettingsSupport'
import {
  cancelActionLabel,
  describeRunScope,
  formatRunTaskLabel,
  formatStatusLabel,
  formatTriggerLabel,
} from './aiSettingsUtils'

export type AiTab = 'overview' | 'activity' | 'configuration'

export type AiSettingsNotice = {
  tone: 'success' | 'error'
  message: string
}

export type AiOverviewTabProps = ComponentProps<typeof OverviewTab>
export type AiActivityTabProps = ComponentProps<typeof ActivityTab>
export type AiConfigurationTabProps = ComponentProps<typeof ConfigurationTab>

type AiSettingsPageViewProps = {
  activeTab: AiTab
  setActiveTab: Dispatch<SetStateAction<AiTab>>
  notice: AiSettingsNotice | null
  settings: AISettings | undefined
  overviewProps: AiOverviewTabProps
  activityProps: AiActivityTabProps
  configurationProps: AiConfigurationTabProps
  activityTabRef: RefObject<HTMLElement | null>
  pendingReprocessScopeClear: boolean
  setPendingReprocessScopeClear: Dispatch<SetStateAction<boolean>>
  confirmClearReprocessScope: () => void
  pendingCancelRun: AITaskRunResponse | null
  setPendingCancelRun: Dispatch<SetStateAction<AITaskRunResponse | null>>
  confirmRunCancellation: () => void
  cancelPending: boolean
  discardDialog: ReactNode
}

const AI_TABS: Array<{ value: AiTab; label: string }> = [
  { value: 'overview', label: 'Status' },
  { value: 'activity', label: 'Jobs' },
  { value: 'configuration', label: 'Configuration' },
]

function getAiTabButtonId(tab: AiTab) {
  return `ai-settings-tab-${tab}`
}

function getAiTabPanelId(tab: AiTab) {
  return `ai-settings-panel-${tab}`
}

function getAdjacentTab(currentTab: AiTab, key: string) {
  const currentIndex = AI_TABS.findIndex((tab) => tab.value === currentTab)
  if (key === 'ArrowRight' || key === 'ArrowDown') {
    return AI_TABS[(currentIndex + 1) % AI_TABS.length]?.value ?? currentTab
  }
  if (key === 'ArrowLeft' || key === 'ArrowUp') {
    return AI_TABS[(currentIndex - 1 + AI_TABS.length) % AI_TABS.length]?.value ?? currentTab
  }
  if (key === 'Home') {
    return AI_TABS[0]?.value ?? currentTab
  }
  if (key === 'End') {
    return AI_TABS[AI_TABS.length - 1]?.value ?? currentTab
  }
  return null
}

function AiSettingsHeader({ settings }: { settings: AISettings | undefined }) {
  return (
    <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
      <div className="flex flex-wrap items-start gap-3">
        <div>
          <p className="text-xs font-semibold uppercase text-slate dark:text-white/55">Automation</p>
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="font-display text-2xl">AI Settings</h2>
            <StatusPill tone={settings?.ai_enabled ? 'info' : 'neutral'} label={settings?.ai_enabled ? 'Enabled' : 'Disabled'} />
            {settings?.ai_configured ? (
              <StatusPill tone="success" label="Configured" />
            ) : (
              <StatusPill tone="warning" label="Needs setup" />
            )}
          </div>
          <p className="mt-1 text-sm text-slate dark:text-white/75">
            Manage local AI configuration, monitor health, and operate brief and enrichment jobs without leaving Settings.
          </p>
        </div>
      </div>
    </section>
  )
}

function AiSettingsNoticeBanner({ notice }: { notice: AiSettingsNotice | null }) {
  if (!notice) {
    return null
  }
  const accessibility = notice.tone === 'error'
    ? { role: 'alert' as const, live: 'assertive' as const }
    : { role: 'status' as const, live: 'polite' as const }
  const toneClass = notice.tone === 'success'
    ? 'border border-cyan/20 bg-cyan/10 text-cyan-900 dark:border-cyan-500/35 dark:bg-cyan/10 dark:text-cyan-100'
    : 'border border-red-500/20 bg-red-500/10 text-red-700 dark:border-red-900/40 dark:bg-red-950/20 dark:text-red-200'

  return (
    <p
      role={accessibility.role}
      aria-live={accessibility.live}
      aria-atomic="true"
      className={`rounded px-3 py-2 text-sm ${toneClass}`}
    >
      {notice.message}
    </p>
  )
}

function AiSettingsNavigation({
  activeTab,
  setActiveTab,
  settings,
}: Pick<AiSettingsPageViewProps, 'activeTab' | 'setActiveTab' | 'settings'>) {
  const handleKeyDown = (event: ReactKeyboardEvent<HTMLButtonElement>, currentTab: AiTab) => {
    const nextTab = getAdjacentTab(currentTab, event.key)
    if (!nextTab || nextTab === currentTab) {
      return
    }
    event.preventDefault()
    setActiveTab(nextTab)
    window.requestAnimationFrame(() => {
      document.getElementById(getAiTabButtonId(nextTab))?.focus()
    })
  }

  return (
    <aside className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
      <h3 className="font-display text-xl">Automation Console</h3>
      <p className="mt-1 text-sm text-slate dark:text-white/70">
        Review status, work with queued jobs, and manage provider settings without leaving the settings area.
      </p>
      <label htmlFor="mobile-ai-settings-section" className="mt-3 block text-xs font-semibold uppercase text-slate lg:hidden dark:text-slate-400">
        Section
        <select
          id="mobile-ai-settings-section"
          className="mt-1 w-full rounded border border-slate/20 bg-white px-3 py-2 text-sm font-semibold normal-case text-ink dark:border-cyan-900/40 dark:bg-[#041612] dark:text-slate-100"
          value={activeTab}
          onChange={(event) => setActiveTab(event.target.value as AiTab)}
        >
          {AI_TABS.map((tab) => (
            <option key={tab.value} value={tab.value}>{tab.label}</option>
          ))}
        </select>
      </label>
      <nav className="mt-3 hidden grid-cols-1 gap-1 lg:grid" role="tablist" aria-label="AI settings sections">
        {AI_TABS.map((tab) => (
          <TabButton
            key={tab.value}
            id={getAiTabButtonId(tab.value)}
            controls={getAiTabPanelId(tab.value)}
            active={activeTab === tab.value}
            onClick={() => setActiveTab(tab.value)}
            onKeyDown={(event) => handleKeyDown(event, tab.value)}
            fullWidth
          >
            {tab.label}
          </TabButton>
        ))}
      </nav>
      <div className="mt-5 rounded border border-cyan/20 bg-cyan/10 p-3 text-xs dark:border-cyan-800/40 dark:bg-cyan-950/40">
        <p className="font-semibold">Current model</p>
        <p className="mt-1 text-cyan-800 dark:text-cyan-200">{settings?.model || 'Not configured'}</p>
        <p className="mt-3 font-semibold">Endpoint</p>
        <p className="mt-1 break-all text-cyan-800 dark:text-cyan-200">{settings?.base_url || 'Not configured'}</p>
      </div>
    </aside>
  )
}

function AiSettingsTabContent(props: AiSettingsPageViewProps) {
  return (
    <section className="space-y-4">
      {props.activeTab === 'overview' && (
        <section id={getAiTabPanelId('overview')} role="tabpanel" aria-labelledby={getAiTabButtonId('overview')}>
          <OverviewTab {...props.overviewProps} />
        </section>
      )}
      {props.activeTab === 'activity' && (
        <section
          id={getAiTabPanelId('activity')}
          role="tabpanel"
          aria-labelledby={getAiTabButtonId('activity')}
          ref={props.activityTabRef}
        >
          <ActivityTab {...props.activityProps} />
        </section>
      )}
      {props.activeTab === 'configuration' && (
        <section
          id={getAiTabPanelId('configuration')}
          role="tabpanel"
          aria-labelledby={getAiTabButtonId('configuration')}
        >
          <ConfigurationTab {...props.configurationProps} />
        </section>
      )}
    </section>
  )
}

function AiSettingsDialogs(props: AiSettingsPageViewProps) {
  return (
    <>
      <ConfirmDialog
        open={props.pendingReprocessScopeClear}
        title="Clear reprocess scope?"
        description="This resets the reprocess scope to the default 7-day and 100-article window and removes any feed, time, search, or article targeting you have built."
        confirmLabel="Clear scope"
        onCancel={() => props.setPendingReprocessScopeClear(false)}
        onConfirm={props.confirmClearReprocessScope}
      />
      <ConfirmDialog
        open={Boolean(props.pendingCancelRun)}
        title="Cancel AI task?"
        description="This stops queued or running AI work. Use it when the current run should not continue."
        confirmLabel={props.pendingCancelRun ? cancelActionLabel(props.pendingCancelRun) : 'Cancel task'}
        onCancel={() => props.setPendingCancelRun(null)}
        onConfirm={props.confirmRunCancellation}
        isConfirming={props.cancelPending}
        confirmDisabled={!props.pendingCancelRun}
      >
        {props.pendingCancelRun && (
          <div className="space-y-2">
            <p className="font-semibold text-ink dark:text-white">{formatRunTaskLabel(props.pendingCancelRun)}</p>
            <p className="text-xs text-slate dark:text-white/70">
              {formatTriggerLabel(props.pendingCancelRun.trigger_source)} · {describeRunScope(props.pendingCancelRun)}
            </p>
            <p className="text-xs text-slate dark:text-white/70">
              Status: {formatStatusLabel(props.pendingCancelRun.status, props.pendingCancelRun.reason)}
            </p>
          </div>
        )}
      </ConfirmDialog>
      {props.discardDialog}
    </>
  )
}

export function AiSettingsPageView(props: AiSettingsPageViewProps) {
  return (
    <div className="space-y-4">
      <AiSettingsHeader settings={props.settings} />
      <AiSettingsNoticeBanner notice={props.notice} />
      <div className="grid gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
        <AiSettingsNavigation activeTab={props.activeTab} setActiveTab={props.setActiveTab} settings={props.settings} />
        <AiSettingsTabContent {...props} />
      </div>
      <AiSettingsDialogs {...props} />
    </div>
  )
}
