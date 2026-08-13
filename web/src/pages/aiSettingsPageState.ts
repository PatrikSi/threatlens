import { resolveApiErrorMessage } from '../api/errors'
import { AISettings, AITaskRunDetailResponse, CurrentUser } from '../types/api'
import { AiTab } from './AiSettingsPageView'

export type AiQueryEnablement = {
  aiEnabled: boolean
  overview: boolean
  activity: boolean
  configuration: boolean
  workload: boolean
}

type QueryStatus = {
  data: unknown
  error: unknown
  isError: boolean
  isFetching: boolean
  isLoading: boolean
}

type SettingsAvailabilityArgs = {
  aiEnabled: boolean
  settings: AISettings | undefined
  isLoading: boolean
  isError: boolean
  draftDirty: boolean
  draftValidationError: string | null
}

export function deriveAiQueryEnablement(
  user: CurrentUser | undefined,
  activeTab: AiTab,
  settledActiveTab: AiTab,
): AiQueryEnablement {
  const aiEnabled = user?.features.ai_enabled ?? false
  const activeUsesWorkload = activeTab === 'activity' || activeTab === 'configuration'
  const settledUsesWorkload = settledActiveTab === 'activity' || settledActiveTab === 'configuration'
  return {
    aiEnabled,
    overview: aiEnabled && settledActiveTab === 'overview',
    activity: aiEnabled && settledActiveTab === 'activity',
    configuration: aiEnabled && settledActiveTab === 'configuration',
    workload: aiEnabled && (activeUsesWorkload || settledUsesWorkload),
  }
}

export function deriveAiSettingsAvailability({
  aiEnabled,
  settings,
  isLoading,
  isError,
  draftDirty,
  draftValidationError,
}: SettingsAvailabilityArgs) {
  const readyToSave = Boolean(settings) && !isLoading && !isError
  let saveBlockedReason: string | null = null
  if (isError) {
    saveBlockedReason = 'AI settings could not be loaded. Refresh before saving changes.'
  } else if (!readyToSave) {
    saveBlockedReason = 'AI settings are still loading. Wait for the saved configuration before saving changes.'
  } else if (draftValidationError) {
    saveBlockedReason = draftValidationError
  }

  let queueWorkBlockedReason: string | null = null
  if (aiEnabled && isError) {
    queueWorkBlockedReason = 'AI settings could not be loaded. Refresh the settings before queueing manual AI work.'
  } else if (aiEnabled && !settings) {
    queueWorkBlockedReason = 'AI settings are still loading. Wait for the saved provider configuration before queueing manual work.'
  } else if (draftDirty) {
    queueWorkBlockedReason = 'Save your AI settings changes before queueing manual AI work. Queued jobs use the last saved provider configuration.'
  } else if (settings && !settings.ai_configured) {
    queueWorkBlockedReason = 'AI is enabled, but the saved endpoint is not configured yet. Save the provider settings and test the connection before queueing manual work.'
  }

  return {
    aiConfigured: settings?.ai_configured ?? false,
    queueWorkBlockedReason,
    readyToSave,
    saveBlockedReason,
  }
}

export function isReprocessScopeDirty(rawDirty: boolean, queuedFingerprint: string | null, currentFingerprint: string) {
  return rawDirty && queuedFingerprint !== currentFingerprint
}

export function isCandidateItemSearchReady(search: string, feedCount: number, startTime: string, endTime: string) {
  return search.length >= 2 || feedCount > 0 || Boolean(startTime || endTime)
}

export function getDailyBriefId(detail: AITaskRunDetailResponse | undefined) {
  return detail?.run.daily_brief_id ?? null
}

function queryError(status: QueryStatus, fallback: string) {
  return status.isError ? resolveApiErrorMessage(status.error, fallback) : ''
}

export function deriveActiveTaskStatus(
  enabled: boolean,
  live: QueryStatus,
  queued: QueryStatus,
  running: QueryStatus,
) {
  const loading = enabled && (
    (live.isLoading && !live.data) ||
    (queued.isLoading && !queued.data) ||
    (running.isLoading && !running.data)
  )
  const refreshing = enabled && !loading && (live.isFetching || queued.isFetching || running.isFetching)
  const errorMessage = [
    queryError(live, 'AI live status could not be loaded'),
    queryError(queued, 'Queued AI tasks could not be loaded'),
    queryError(running, 'Running AI tasks could not be loaded'),
  ].filter(Boolean).join(' ')
  return { errorMessage, loading, refreshing }
}

export function deriveConfigurationSaveBlockedReason(
  settingsBlockedReason: string | null,
  testPending: boolean,
  draftDirty: boolean,
) {
  if (settingsBlockedReason) {
    return settingsBlockedReason
  }
  if (testPending) {
    return 'Wait for the saved connection test to finish before saving settings.'
  }
  return draftDirty ? null : 'No AI settings changes to save.'
}

export function deriveConnectionTestBlockedReason(
  configurationEnabled: boolean,
  activeTasksLoading: boolean,
  blockingRunCount: number,
) {
  if (!configurationEnabled) {
    return null
  }
  if (activeTasksLoading) {
    return 'Checking queued and running AI tasks before testing the saved provider.'
  }
  if (blockingRunCount === 0) {
    return null
  }
  const taskLabel = blockingRunCount === 1 ? '1 AI task is' : `${blockingRunCount} AI tasks are`
  return `${taskLabel} running or queued. Local providers such as Ollama usually process one generation at a time, so connection tests are paused until current work clears.`
}

export function getAiReadiness(settings: AISettings | undefined) {
  if (!settings) {
    return null
  }
  if (!settings.ai_configured) {
    return 'Complete the base URL and model to enable AI-generated output.'
  }
  if (!settings.api_key_configured) {
    return 'No API key is configured in the environment. That is fine for local endpoints that do not require auth.'
  }
  return 'AI endpoint settings are configured and ready to use.'
}

export function validateDailyBriefReprocessDays(value: string, retainedLimit: number | undefined) {
  const trimmed = value.trim()
  if (!/^\d+$/.test(trimmed)) {
    return 'Daily brief days must be a whole number.'
  }
  const days = Number(trimmed)
  if (!Number.isInteger(days) || days < 1 || days > 90) {
    return 'Daily brief days must be between 1 and 90.'
  }
  if (typeof retainedLimit === 'number' && days > retainedLimit) {
    return `Increase retained daily briefings before reprocessing more than ${retainedLimit} days.`
  }
  return null
}
