import { ActivityFiltersPanel } from './AiActivityFilters'
import { ActiveTasksPanel, QueueWorkPanel } from './AiActivityOperations'
import type { ActivityTabProps } from './AiActivityTypes'
import { SelectedRunSection } from './AiTaskRunDetail'
import { TaskHistoryPanel } from './AiTaskHistoryPanel'
import { OverviewSection } from './aiSettingsSupport'
import { useAiActivityRunState } from './useAiActivityRunState'

export type { RunFilters } from './AiActivityTypes'

export function ActivityTab(props: ActivityTabProps) {
  const runState = useAiActivityRunState({
    runPage: props.runPage,
    setRunPage: props.setRunPage,
    runsQuery: props.runsQuery,
    selectedRunId: props.selectedRunId,
    runDetailQuery: props.runDetailQuery,
  })

  return (
    <div className="space-y-4">
      <ActivityFiltersPanel
        days={props.days}
        setDays={props.setDays}
        selectedModel={props.selectedModel}
        setSelectedModel={props.setSelectedModel}
        modelOptions={props.modelOptions}
        setRunPage={props.setRunPage}
        onRefresh={props.onRefresh}
      />

      <OverviewSection
        title="Live Operations"
        description="Use this section to see what is running right now and to queue new brief or reprocess work."
      >
        <div className="space-y-4">
          <ActiveTasksPanel
            runs={props.runs}
            live={props.live}
            isLoading={props.activeTasksLoading}
            isRefreshing={props.activeTasksRefreshing}
            errorMessage={props.activeTasksErrorMessage}
            onOpenRun={props.onOpenRun}
            onCancelRun={props.onCancelRun}
            cancelingRunId={props.cancelingRunId}
          />
          <QueueWorkPanel {...props} />
        </div>
      </OverviewSection>

      <TaskHistoryPanel
        days={props.days}
        selectedModel={props.selectedModel}
        filters={props.filters}
        setFilters={props.setFilters}
        runPage={props.runPage}
        setRunPage={props.setRunPage}
        runsQuery={props.runsQuery}
        selectedRunId={props.selectedRunId}
        onSelectRun={props.onSelectRun}
        onInspectRun={runState.setInspectedRunId}
        history={runState.history}
      />

      <SelectedRunSection
        selectedRunSectionRef={props.selectedRunSectionRef}
        runDetailQuery={props.runDetailQuery}
        briefSources={props.briefSources}
        briefSourcesLoading={props.briefSourcesLoading}
        briefSourcesErrorMessage={props.briefSourcesErrorMessage}
        onCancelRun={props.onCancelRun}
        cancelingRunId={props.cancelingRunId}
        runState={runState}
      />
    </div>
  )
}
