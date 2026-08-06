import {
  type Dispatch,
  type KeyboardEvent as ReactKeyboardEvent,
  type SetStateAction,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import {
  AIReprocessQueueRequest,
  resolveAiReprocessQueueState,
  toApiDateTime,
} from './aiReprocessQueueState'
import {
  AISettingsDraft,
  createDraftFromSettings,
  createRequestFromDraft,
  DEFAULT_DRAFT,
  getFirstAISettingsDraftValidationError,
  validateAISettingsDraft,
} from './aiSettingsDraft'
import { resolveVisibleRunSelection } from './aiRunSelection'
import { ActivityTab, type RunFilters } from './AiSettingsActivityTab'
import { ConfigurationTab } from './AiSettingsConfigurationTab'
import { OverviewTab } from './AiSettingsOverviewTab'
import { StatusPill, TabButton } from './aiSettingsSupport'
import {
  AI_RUN_PAGE_SIZE,
  cancelActionLabel,
  describeRunScope,
  formatRunTaskLabel,
  formatStatusLabel,
  formatTriggerLabel,
  invalidateAiQueries,
  markAiQueriesStale,
  parseTimestamp,
} from './aiSettingsUtils'
import {
  AIAuditEntryResponse,
  AIDailyBriefBackfillResponse,
  AIDailyBriefSourceItemResponse,
  AILiveStatusResponse,
  AIOpsOverviewResponse,
  AIReprocessResponse,
  AISettings,
  AISettingsUpdateRequest,
  AITaskRunDetailResponse,
  AITaskRunListResponse,
  AITaskRunResponse,
  AITestConnectionResponse,
  Feed,
  ItemListEntry,
  ItemListResponse,
} from '../types/api'

type AiTab = 'overview' | 'activity' | 'configuration'

type NoticeState = {
  tone: 'success' | 'error'
  message: string
}

const AI_QUERY_STALE_MS = 15_000
const AI_REFERENCE_STALE_MS = 60_000
const AI_CONNECTION_TEST_TIMEOUT_BUFFER_MS = 15_000
const CONNECTION_TEST_BLOCKING_TASK_TYPES = new Set(['item_enrichment', 'daily_brief', 'reprocess'])
const DEFAULT_RUN_FILTERS: RunFilters = {
  taskType: '',
  status: '',
  triggerSource: '',
  onlyFailures: false,
}
const DEFAULT_REPROCESS_DAYS = '7'
const DEFAULT_REPROCESS_LIMIT = '100'
const DEFAULT_DAILY_BRIEF_REPROCESS_DAYS = '1'

const AI_TABS: Array<{ value: AiTab; label: string }> = [
  { value: 'overview', label: 'Status' },
  { value: 'activity', label: 'Jobs' },
  { value: 'configuration', label: 'Configuration' },
]

type RunsQueryArgs = {
  days: number
  selectedModel: string
  runPage: number
  runFilters: RunFilters
}

function buildRunsPath({ days, selectedModel, runPage, runFilters }: RunsQueryArgs) {
  const params = new URLSearchParams()
  params.set('limit', String(AI_RUN_PAGE_SIZE))
  params.set('offset', String(runPage * AI_RUN_PAGE_SIZE))
  params.set('days', String(days))
  if (selectedModel !== 'all') {
    params.set('model', selectedModel)
  }
  if (runFilters.taskType) {
    params.set('task_type', runFilters.taskType)
  }
  if (runFilters.status) {
    params.set('status', runFilters.status)
  }
  if (runFilters.triggerSource) {
    params.set('trigger_source', runFilters.triggerSource)
  }
  if (runFilters.onlyFailures) {
    params.set('only_failures', 'true')
  }
  return `/ai/ops/runs?${params.toString()}`
}

function buildRunsQueryKey({ days, selectedModel, runPage, runFilters }: RunsQueryArgs) {
  return ['ai', 'ops', 'runs', days, selectedModel, runPage, runFilters] as const
}

function isConnectionTestBlockingRun(run: AITaskRunResponse) {
  return CONNECTION_TEST_BLOCKING_TASK_TYPES.has(run.task_type)
}

function connectionTestWorkloadMessage(count: number) {
  const taskLabel = count === 1 ? '1 AI task is' : `${count} AI tasks are`
  return `${taskLabel} running or queued. Local providers such as Ollama usually process one generation at a time, so connection tests are paused until current work clears.`
}

function getAiTabButtonId(tab: AiTab) {
  return `ai-settings-tab-${tab}`
}

function getAiTabPanelId(tab: AiTab) {
  return `ai-settings-panel-${tab}`
}

export function AiSettingsPage() {
  const queryClient = useQueryClient()
  const currentUserQuery = useCurrentUser()
  const [activeTab, setActiveTab] = useState<AiTab>('overview')
  const [days, setDays] = useState(30)
  const [draft, setDraftState] = useState<AISettingsDraft>(DEFAULT_DRAFT)
  const [draftDirty, setDraftDirty] = useState(false)
  const [notice, setNotice] = useState<NoticeState | null>(null)
  const [testResult, setTestResult] = useState<AITestConnectionResponse | null>(null)
  const [dailyBriefReprocessDays, setDailyBriefReprocessDays] = useState(DEFAULT_DAILY_BRIEF_REPROCESS_DAYS)
  const [reprocessDays, setReprocessDays] = useState(DEFAULT_REPROCESS_DAYS)
  const [reprocessLimit, setReprocessLimit] = useState(DEFAULT_REPROCESS_LIMIT)
  const [reprocessStartTime, setReprocessStartTime] = useState('')
  const [reprocessEndTime, setReprocessEndTime] = useState('')
  const [reprocessFeedIds, setReprocessFeedIds] = useState<string[]>([])
  const [reprocessItemSearch, setReprocessItemSearch] = useState('')
  const [selectedReprocessItems, setSelectedReprocessItems] = useState<ItemListEntry[]>([])
  const [queuedReprocessScopeFingerprint, setQueuedReprocessScopeFingerprint] = useState<string | null>(null)
  const [pendingReprocessScopeClear, setPendingReprocessScopeClear] = useState(false)
  const [cancelingRunId, setCancelingRunId] = useState<string | null>(null)
  const [pendingCancelRun, setPendingCancelRun] = useState<AITaskRunResponse | null>(null)
  const [selectedModel, setSelectedModel] = useState('all')
  const [runPage, setRunPage] = useState(0)
  const [runFilters, setRunFilters] = useState<RunFilters>(DEFAULT_RUN_FILTERS)
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [pinnedRunId, setPinnedRunId] = useState<string | null>(null)
  const [pendingRunNavigation, setPendingRunNavigation] = useState<string | null>(null)
  const [settledActiveTab, setSettledActiveTab] = useState<AiTab>('overview')
  const activityTabRef = useRef<HTMLElement | null>(null)
  const selectedRunSectionRef = useRef<HTMLDivElement | null>(null)

  const setDraft: Dispatch<SetStateAction<AISettingsDraft>> = (value) => {
    setDraftDirty(true)
    setTestResult(null)
    setDraftState(value)
  }
  const reprocessScopeFingerprint = useMemo(
    () =>
      JSON.stringify({
        days: reprocessDays.trim(),
        limit: reprocessLimit.trim(),
        startTime: reprocessStartTime.trim(),
        endTime: reprocessEndTime.trim(),
        feedIds: [...reprocessFeedIds].sort(),
        selectedItemIds: selectedReprocessItems.map((item) => item.id).sort(),
      }),
    [reprocessDays, reprocessEndTime, reprocessFeedIds, reprocessLimit, reprocessStartTime, selectedReprocessItems],
  )
  const rawReprocessScopeDirty = useMemo(
    () =>
      reprocessDays.trim() !== DEFAULT_REPROCESS_DAYS ||
      reprocessLimit.trim() !== DEFAULT_REPROCESS_LIMIT ||
      reprocessStartTime.trim() !== '' ||
      reprocessEndTime.trim() !== '' ||
      reprocessFeedIds.length > 0 ||
      selectedReprocessItems.length > 0,
    [reprocessDays, reprocessEndTime, reprocessFeedIds, reprocessLimit, reprocessStartTime, selectedReprocessItems],
  )
  const reprocessScopeDirty = rawReprocessScopeDirty && queuedReprocessScopeFingerprint !== reprocessScopeFingerprint
  const unsavedAiSettingsMessage = useMemo(() => {
    if (draftDirty && reprocessScopeDirty) {
      return 'You have unsaved AI settings changes and a reprocess scope in progress. Leave without saving or queueing that work?'
    }
    if (draftDirty) {
      return 'You have unsaved AI settings changes. Leave without saving?'
    }
    return 'You have a reprocess scope in progress. Leave without queueing or clearing it?'
  }, [draftDirty, reprocessScopeDirty])
  const confirmDiscardUnsavedAiSettingsChanges = useUnsavedChangesWarning(
    draftDirty || reprocessScopeDirty,
    unsavedAiSettingsMessage,
  )

  const aiEnabled = currentUserQuery.data?.features.ai_enabled ?? false
  const overviewQueriesEnabled = aiEnabled && settledActiveTab === 'overview'
  const activityQueriesEnabled = aiEnabled && settledActiveTab === 'activity'
  const configurationQueriesEnabled = aiEnabled && settledActiveTab === 'configuration'
  const workloadQueriesEnabled =
    aiEnabled &&
    (activeTab === 'activity' ||
      activeTab === 'configuration' ||
      settledActiveTab === 'activity' ||
      settledActiveTab === 'configuration')
  const deferredItemSearch = useDeferredValue(reprocessItemSearch.trim())

  useEffect(() => {
    setSettledActiveTab(activeTab)
  }, [activeTab])

  const handleAiTabKeyDown = (event: ReactKeyboardEvent<HTMLButtonElement>, currentTab: AiTab) => {
    let nextTab: AiTab | null = null
    const currentIndex = AI_TABS.findIndex((tab) => tab.value === currentTab)

    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
      nextTab = AI_TABS[(currentIndex + 1) % AI_TABS.length]?.value ?? currentTab
    } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
      nextTab = AI_TABS[(currentIndex - 1 + AI_TABS.length) % AI_TABS.length]?.value ?? currentTab
    } else if (event.key === 'Home') {
      nextTab = AI_TABS[0]?.value ?? currentTab
    } else if (event.key === 'End') {
      nextTab = AI_TABS[AI_TABS.length - 1]?.value ?? currentTab
    }

    if (!nextTab || nextTab === currentTab) {
      return
    }

    event.preventDefault()
    setActiveTab(nextTab)
    const focusedTab = nextTab
    window.requestAnimationFrame(() => {
      document.getElementById(getAiTabButtonId(focusedTab))?.focus()
    })
  }

  const settingsQuery = useQuery({
    queryKey: ['ai', 'settings'],
    queryFn: ({ signal }) => apiFetch<AISettings>('/ai/settings', { signal }),
    enabled: aiEnabled,
    staleTime: AI_REFERENCE_STALE_MS,
  })
  const draftValidation = useMemo(() => validateAISettingsDraft(draft), [draft])
  const draftValidationError = getFirstAISettingsDraftValidationError(draftValidation)
  const settingsReadyToSave = Boolean(settingsQuery.data) && !settingsQuery.isLoading && !settingsQuery.isError
  const settingsSaveBlockedReason = (() => {
    if (settingsQuery.isError) {
      return 'AI settings could not be loaded. Refresh before saving changes.'
    }
    if (!settingsReadyToSave) {
      return 'AI settings are still loading. Wait for the saved configuration before saving changes.'
    }
    if (draftValidationError) {
      return draftValidationError
    }
    return null
  })()
  const aiConfigured = settingsQuery.data?.ai_configured ?? false
  const queueWorkBlockedReason = (() => {
    if (aiEnabled && settingsQuery.isError) {
      return 'AI settings could not be loaded. Refresh the settings before queueing manual AI work.'
    }
    if (aiEnabled && !settingsQuery.data) {
      return 'AI settings are still loading. Wait for the saved provider configuration before queueing manual work.'
    }
    if (draftDirty) {
      return 'Save your AI settings changes before queueing manual AI work. Queued jobs use the last saved provider configuration.'
    }
    if (settingsQuery.data && !aiConfigured) {
      return 'AI is enabled, but the saved endpoint is not configured yet. Save the provider settings and test the connection before queueing manual work.'
    }
    return null
  })()

  const overviewQuery = useQuery({
    queryKey: ['ai', 'ops', 'overview', days],
    queryFn: ({ signal }) => apiFetch<AIOpsOverviewResponse>(`/ai/ops/overview?days=${days}`, { signal }),
    enabled: overviewQueriesEnabled,
    refetchInterval: 10000,
    staleTime: AI_QUERY_STALE_MS,
  })

  const liveStatusQuery = useQuery({
    queryKey: ['ai', 'ops', 'live'],
    queryFn: ({ signal }) => apiFetch<AILiveStatusResponse>('/ai/ops/live', { signal }),
    enabled: workloadQueriesEnabled,
    refetchInterval: 5000,
    staleTime: 2500,
  })

  const queuedRunsQuery = useQuery({
    queryKey: ['ai', 'ops', 'runs', 'queued-top'],
    queryFn: ({ signal }) => apiFetch<AITaskRunListResponse>('/ai/ops/runs?status=queued&limit=10&days=30', { signal }),
    enabled: workloadQueriesEnabled,
    refetchInterval: 5000,
    staleTime: 2500,
  })

  const runningRunsQuery = useQuery({
    queryKey: ['ai', 'ops', 'runs', 'running-top'],
    queryFn: ({ signal }) => apiFetch<AITaskRunListResponse>('/ai/ops/runs?status=running&limit=10&days=30', { signal }),
    enabled: workloadQueriesEnabled,
    refetchInterval: 5000,
    staleTime: 2500,
  })

  const feedsQuery = useQuery({
    queryKey: ['feeds', 'ai-reprocess'],
    queryFn: ({ signal }) => apiFetch<Feed[]>('/feeds', { signal }),
    enabled: activityQueriesEnabled,
    staleTime: AI_REFERENCE_STALE_MS,
  })

  const candidateItemsReady =
    deferredItemSearch.length >= 2 || reprocessFeedIds.length > 0 || Boolean(reprocessStartTime || reprocessEndTime)

  const candidateItemsPath = useMemo(() => {
    const params = new URLSearchParams()
    params.set('page', '1')
    params.set('page_size', '12')
    params.set('sort', 'published_at_desc')
    params.set('has_article', 'true')
    params.set('date_basis', 'published_at_or_first_seen_at')
    if (deferredItemSearch) {
      params.set('q', deferredItemSearch)
    }
    if (reprocessFeedIds.length) {
      params.set('feed_ids', reprocessFeedIds.join(','))
    }
    const startTime = toApiDateTime(reprocessStartTime)
    const endTime = toApiDateTime(reprocessEndTime)
    if (startTime) {
      params.set('since', startTime)
    }
    if (endTime) {
      params.set('until', endTime)
    }
    return `/items?${params.toString()}`
  }, [deferredItemSearch, reprocessEndTime, reprocessFeedIds, reprocessStartTime])

  const candidateItemsQuery = useQuery({
    queryKey: ['items', 'ai-reprocess-picker', deferredItemSearch, reprocessFeedIds, reprocessStartTime, reprocessEndTime],
    queryFn: ({ signal }) => apiFetch<ItemListResponse>(candidateItemsPath, { signal }),
    enabled: activityQueriesEnabled && candidateItemsReady,
    staleTime: AI_QUERY_STALE_MS,
  })

  const promptHistoryQuery = useQuery({
    queryKey: ['ai', 'ops', 'prompt-history'],
    queryFn: ({ signal }) => apiFetch<AIAuditEntryResponse[]>('/ai/ops/prompt-history?limit=12', { signal }),
    enabled: configurationQueriesEnabled,
    staleTime: AI_REFERENCE_STALE_MS,
  })

  const manualActionsQuery = useQuery({
    queryKey: ['ai', 'ops', 'manual-actions'],
    queryFn: ({ signal }) => apiFetch<AIAuditEntryResponse[]>('/ai/ops/manual-actions?limit=12', { signal }),
    enabled: configurationQueriesEnabled,
    staleTime: AI_REFERENCE_STALE_MS,
  })

  const runsPath = useMemo(
    () => buildRunsPath({ days, selectedModel, runPage, runFilters }),
    [days, runFilters, runPage, selectedModel],
  )
  const runsQueryKey = useMemo(
    () => buildRunsQueryKey({ days, selectedModel, runPage, runFilters }),
    [days, runFilters, runPage, selectedModel],
  )

  const runsQuery = useQuery({
    queryKey: runsQueryKey,
    queryFn: ({ signal }) => apiFetch<AITaskRunListResponse>(runsPath, { signal }),
    enabled: activityQueriesEnabled,
    refetchInterval: 10000,
    staleTime: 5000,
    placeholderData: keepPreviousData,
  })

  const runDetailQuery = useQuery({
    queryKey: ['ai', 'ops', 'run', selectedRunId],
    queryFn: ({ signal }) => apiFetch<AITaskRunDetailResponse>(`/ai/ops/runs/${selectedRunId}`, { signal }),
    enabled: activityQueriesEnabled && Boolean(selectedRunId),
    refetchInterval: 10000,
    staleTime: 5000,
  })

  const briefSourcesQuery = useQuery({
    queryKey: ['ai', 'daily-brief-sources', runDetailQuery.data?.run.daily_brief_id],
    queryFn: ({ signal }) =>
      apiFetch<AIDailyBriefSourceItemResponse[]>(
        `/ai/daily-briefs/${runDetailQuery.data?.run.daily_brief_id}/sources?limit=50`,
        { signal },
      ),
    enabled: activityQueriesEnabled && Boolean(runDetailQuery.data?.run.daily_brief_id),
    staleTime: AI_QUERY_STALE_MS,
  })

  useEffect(() => {
    if (!settingsQuery.data || draftDirty) {
      return
    }
    setDraftState(createDraftFromSettings(settingsQuery.data))
  }, [draftDirty, settingsQuery.data])

  useEffect(() => {
    if (!runsQuery.data || runsQuery.isPlaceholderData) {
      return
    }
    if (pinnedRunId && selectedRunId === pinnedRunId) {
      return
    }
    const nextSelectedRunId = resolveVisibleRunSelection(runsQuery.data?.items, selectedRunId)
    if (nextSelectedRunId !== selectedRunId) {
      setSelectedRunId(nextSelectedRunId)
    }
  }, [pinnedRunId, runsQuery.data, runsQuery.isPlaceholderData, selectedRunId])

  useEffect(() => {
    if (!activityQueriesEnabled || !runsQuery.data || runsQuery.isPlaceholderData) {
      return
    }
    const totalPages = Math.max(1, Math.ceil(runsQuery.data.total / AI_RUN_PAGE_SIZE))
    const pagesToPrefetch = [runPage + 1, runPage - 1].filter((page) => page >= 0 && page < totalPages)
    pagesToPrefetch.forEach((page) => {
      const prefetchArgs = { days, selectedModel, runPage: page, runFilters }
      void queryClient.prefetchQuery({
        queryKey: buildRunsQueryKey(prefetchArgs),
        queryFn: ({ signal }) => apiFetch<AITaskRunListResponse>(buildRunsPath(prefetchArgs), { signal }),
        staleTime: 5000,
      })
    })
  }, [
    activityQueriesEnabled,
    days,
    queryClient,
    runFilters,
    runPage,
    runsQuery.data,
    runsQuery.isPlaceholderData,
    selectedModel,
  ])

  const showActionError = (error: unknown, fallback: string) => {
    setNotice({ tone: 'error', message: resolveApiErrorMessage(error, fallback) })
  }

  const saveMutation = useMutation({
    mutationKey: ['ai', 'settings', 'save'],
    mutationFn: (payload: AISettingsUpdateRequest) =>
      apiFetch<AISettings>('/ai/settings', {
        method: 'PUT',
        body: JSON.stringify(payload),
      }),
    onSuccess: (saved) => {
      setDraftState(createDraftFromSettings(saved))
      setDraftDirty(false)
      setNotice({ tone: 'success', message: 'AI settings saved.' })
      setTestResult(null)
      invalidateAiQueries(queryClient)
      void queryClient.invalidateQueries({ queryKey: ['auth', 'me'] })
    },
    onError: (error) => {
      showActionError(error, 'Failed to save AI settings.')
    },
  })

  const testConnectionMutation = useMutation({
    mutationKey: ['ai', 'settings', 'test-connection'],
    mutationFn: () => {
      const timeoutMs =
        typeof settingsQuery.data?.request_timeout_seconds === 'number'
          ? settingsQuery.data.request_timeout_seconds * 1000 + AI_CONNECTION_TEST_TIMEOUT_BUFFER_MS
          : undefined
      return apiFetch<AITestConnectionResponse>('/ai/test-connection', {
        method: 'POST',
        timeoutMs,
      })
    },
    onSuccess: (result) => {
      setTestResult(result)
      setNotice({
        tone: result.success ? 'success' : 'error',
        message: result.success
          ? 'Saved AI connection test succeeded.'
          : result.skipped
            ? result.error ?? 'Saved AI connection test was paused because AI work is running or queued.'
            : 'Saved AI connection test failed.',
      })
      invalidateAiQueries(queryClient)
    },
    onError: (error) => {
      setTestResult(null)
      showActionError(error, 'Failed to test the saved AI connection.')
    },
  })

  const reprocessDailyBriefMutation = useMutation({
    mutationKey: ['ai', 'daily-brief', 'backfill'],
    mutationFn: (daysToReprocess: number) =>
      apiFetch<AIDailyBriefBackfillResponse>('/ai/daily-brief/backfill', {
        method: 'POST',
        body: JSON.stringify({ days: daysToReprocess }),
      }),
    onSuccess: (result) => {
      const rangeLabel = result.days === 1 ? 'today' : `the last ${result.days} days`
      setNotice({
        tone: 'success',
        message: `Queued daily brief reprocessing for ${rangeLabel} (${result.run_id ?? result.task_id}).`,
      })
      markAiQueriesStale(queryClient)
      void queryClient.invalidateQueries({ queryKey: ['auth', 'me'] })
    },
    onError: (error) => {
      showActionError(error, 'Failed to queue daily brief reprocessing.')
    },
  })

  const reprocessMutation = useMutation({
    mutationKey: ['ai', 'reprocess'],
    mutationFn: (payload: AIReprocessQueueRequest) =>
      apiFetch<AIReprocessResponse>('/ai/reprocess', {
        method: 'POST',
        body: JSON.stringify(payload),
      }),
    onSuccess: (result) => {
      clearReprocessScope()
      setNotice({ tone: 'success', message: `Queued AI reprocessing run ${result.run_id ?? result.task_id}.` })
      markAiQueriesStale(queryClient)
    },
    onError: (error) => {
      setQueuedReprocessScopeFingerprint(null)
      showActionError(error, 'Failed to queue AI reprocessing.')
    },
  })

  const cancelRunMutation = useMutation({
    mutationKey: ['ai', 'ops', 'runs', 'cancel'],
    mutationFn: (runId: string) =>
      apiFetch<AITaskRunResponse>(`/ai/ops/runs/${runId}/cancel`, {
        method: 'POST',
      }),
    onMutate: (runId) => {
      setCancelingRunId(runId)
    },
    onSuccess: (run) => {
      setNotice({
        tone: 'success',
        message: `${formatRunTaskLabel(run)} ${formatStatusLabel(run.status, run.reason).toLowerCase()}.`,
      })
      setPendingCancelRun(null)
      invalidateAiQueries(queryClient)
    },
    onError: (error) => {
      showActionError(error, 'Failed to cancel the AI task.')
    },
    onSettled: () => {
      setCancelingRunId(null)
    },
  })

  const requestRunCancellation = (run: AITaskRunResponse) => {
    setNotice(null)
    setPendingCancelRun(run)
  }

  const confirmRunCancellation = () => {
    if (!pendingCancelRun) {
      return
    }
    cancelRunMutation.mutate(pendingCancelRun.id)
  }

  const readiness = useMemo(() => {
    if (!settingsQuery.data) {
      return null
    }
    if (!settingsQuery.data.ai_configured) {
      return 'Complete the base URL and model to enable AI-generated output.'
    }
    if (!settingsQuery.data.api_key_configured) {
      return 'No API key is configured in the environment. That is fine for local endpoints that do not require auth.'
    }
    return 'AI endpoint settings are configured and ready to use.'
  }, [settingsQuery.data])

  const modelOptions = useMemo(() => {
    const values = new Set<string>()
    if (settingsQuery.data?.model) {
      values.add(settingsQuery.data.model)
    }
    for (const row of overviewQuery.data?.per_model ?? []) {
      values.add(row.model)
    }
    for (const run of runsQuery.data?.items ?? []) {
      if (run.model) {
        values.add(run.model)
      }
    }
    return ['all', ...Array.from(values)]
  }, [overviewQuery.data?.per_model, runsQuery.data?.items, settingsQuery.data?.model])

  const activeTopLevelRuns = useMemo(() => {
    const byId = new Map<string, AITaskRunResponse>()
    for (const run of [...(runningRunsQuery.data?.items ?? []), ...(queuedRunsQuery.data?.items ?? [])]) {
      if (run.parent_run_id || run.finished_at || (run.status !== 'queued' && run.status !== 'running')) {
        continue
      }
      byId.set(run.id, run)
    }
    return Array.from(byId.values()).sort((left, right) => {
      const leftRunning = left.status === 'running' ? 0 : 1
      const rightRunning = right.status === 'running' ? 0 : 1
      if (leftRunning !== rightRunning) {
        return leftRunning - rightRunning
      }
      return (parseTimestamp(right.updated_at)?.getTime() ?? 0) - (parseTimestamp(left.updated_at)?.getTime() ?? 0)
    })
  }, [queuedRunsQuery.data?.items, runningRunsQuery.data?.items])

  const connectionTestBlockingRuns = useMemo(
    () => activeTopLevelRuns.filter(isConnectionTestBlockingRun),
    [activeTopLevelRuns],
  )

  const candidateItems = useMemo(() => {
    const selectedIds = new Set(selectedReprocessItems.map((item) => item.id))
    return (candidateItemsQuery.data?.items ?? []).filter((item) => !selectedIds.has(item.id))
  }, [candidateItemsQuery.data?.items, selectedReprocessItems])

  const reprocessQueueState = useMemo(
    () =>
      resolveAiReprocessQueueState({
        days: reprocessDays,
        limit: reprocessLimit,
        startTime: reprocessStartTime,
        endTime: reprocessEndTime,
        feedIds: reprocessFeedIds,
        selectedItems: selectedReprocessItems,
        itemSearch: reprocessItemSearch,
      }),
    [
      reprocessDays,
      reprocessEndTime,
      reprocessFeedIds,
      reprocessItemSearch,
      reprocessLimit,
      reprocessStartTime,
      selectedReprocessItems,
    ],
  )
  const dailyBriefReprocessValidation = useMemo(() => {
    const trimmed = dailyBriefReprocessDays.trim()
    const retainedLimit = settingsQuery.data?.daily_brief_history_limit
    if (!/^\d+$/.test(trimmed)) {
      return 'Daily brief days must be a whole number.'
    }
    const value = Number(trimmed)
    if (!Number.isInteger(value) || value < 1 || value > 90) {
      return 'Daily brief days must be between 1 and 90.'
    }
    if (typeof retainedLimit === 'number' && value > retainedLimit) {
      return `Increase retained daily briefings before reprocessing more than ${retainedLimit} days.`
    }
    return null
  }, [dailyBriefReprocessDays, settingsQuery.data?.daily_brief_history_limit])

  const activeTasksLoading =
    workloadQueriesEnabled &&
    ((liveStatusQuery.isLoading && !liveStatusQuery.data) ||
      (queuedRunsQuery.isLoading && !queuedRunsQuery.data) ||
      (runningRunsQuery.isLoading && !runningRunsQuery.data))
  const activeTasksRefreshing =
    workloadQueriesEnabled &&
    !activeTasksLoading &&
    (liveStatusQuery.isFetching || queuedRunsQuery.isFetching || runningRunsQuery.isFetching)
  const activeTasksErrorMessage = [
    liveStatusQuery.isError ? resolveApiErrorMessage(liveStatusQuery.error, 'AI live status could not be loaded') : '',
    queuedRunsQuery.isError ? resolveApiErrorMessage(queuedRunsQuery.error, 'Queued AI tasks could not be loaded') : '',
    runningRunsQuery.isError ? resolveApiErrorMessage(runningRunsQuery.error, 'Running AI tasks could not be loaded') : '',
  ]
    .filter(Boolean)
    .join(' ')
  const configurationSaveBlockedReason =
    settingsSaveBlockedReason ??
    (testConnectionMutation.isPending
      ? 'Wait for the saved connection test to finish before saving settings.'
      : !draftDirty
        ? 'No AI settings changes to save.'
        : null)
  const connectionTestBlockedReason =
    configurationQueriesEnabled && activeTasksLoading
      ? 'Checking queued and running AI tasks before testing the saved provider.'
      : configurationQueriesEnabled && connectionTestBlockingRuns.length > 0
        ? connectionTestWorkloadMessage(connectionTestBlockingRuns.length)
        : null

  function clearReprocessScope() {
    setQueuedReprocessScopeFingerprint(null)
    setReprocessDays(DEFAULT_REPROCESS_DAYS)
    setReprocessLimit(DEFAULT_REPROCESS_LIMIT)
    setReprocessStartTime('')
    setReprocessEndTime('')
    setReprocessFeedIds([])
    setReprocessItemSearch('')
    setSelectedReprocessItems([])
  }

  function requestClearReprocessScope() {
    if (!reprocessScopeDirty) {
      clearReprocessScope()
      return
    }
    setPendingReprocessScopeClear(true)
  }

  function confirmClearReprocessScope() {
    setPendingReprocessScopeClear(false)
    clearReprocessScope()
  }

  function openRunInHistory(runId: string) {
    setSelectedModel('all')
    setRunFilters(DEFAULT_RUN_FILTERS)
    setRunPage(0)
    setPinnedRunId(runId)
    setSelectedRunId(runId)
    setPendingRunNavigation(runId)
    setActiveTab('activity')
    void queryClient.prefetchQuery({
      queryKey: ['ai', 'ops', 'run', runId],
      queryFn: ({ signal }) => apiFetch<AITaskRunDetailResponse>(`/ai/ops/runs/${runId}`, { signal }),
    })
  }

  useEffect(() => {
    if (activeTab !== 'activity' || !pendingRunNavigation) {
      return
    }
    const timer = window.setTimeout(() => {
      const target = selectedRunSectionRef.current ?? activityTabRef.current
      target?.scrollIntoView({ behavior: 'smooth', block: 'start' })
      setPendingRunNavigation(null)
    }, 90)
    return () => window.clearTimeout(timer)
  }, [activeTab, pendingRunNavigation])

  useEffect(() => {
    if (!reprocessScopeDirty) {
      setPendingReprocessScopeClear(false)
    }
  }, [reprocessScopeDirty])

  if (currentUserQuery.isLoading) {
    return (
      <div className="rounded-xl border border-slate/20 bg-white/80 p-4 text-sm dark:border-cyan-900/40 dark:bg-[#041612]/90">
        Loading AI settings...
      </div>
    )
  }

  if (!aiEnabled) {
    return (
      <div className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
        <p className="text-xs font-semibold uppercase text-slate dark:text-white/55">Automation</p>
        <h2 className="mt-1 font-display text-xl">AI Settings</h2>
        <p className="mt-2 text-sm text-slate dark:text-white/75">
          AI features are disabled by the deployment configuration. Enable `AI_ENABLED=true` and restart ThreatLens to use
          this section.
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
        <div className="flex flex-wrap items-start gap-3">
          <div>
            <p className="text-xs font-semibold uppercase text-slate dark:text-white/55">Automation</p>
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="font-display text-2xl">AI Settings</h2>
              <StatusPill tone={settingsQuery.data?.ai_enabled ? 'info' : 'neutral'} label={settingsQuery.data?.ai_enabled ? 'Enabled' : 'Disabled'} />
              {settingsQuery.data?.ai_configured ? (
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
      {notice && (
        <p
          role={notice.tone === 'error' ? 'alert' : 'status'}
          aria-live={notice.tone === 'error' ? 'assertive' : 'polite'}
          aria-atomic="true"
          className={`rounded px-3 py-2 text-sm ${
            notice.tone === 'success'
              ? 'border border-cyan/20 bg-cyan/10 text-cyan-900 dark:border-cyan-500/35 dark:bg-cyan/10 dark:text-cyan-100'
              : 'border border-red-500/20 bg-red-500/10 text-red-700 dark:border-red-900/40 dark:bg-red-950/20 dark:text-red-200'
          }`}
        >
          {notice.message}
        </p>
      )}

      <div className="grid gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
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
          <nav
            className="mt-3 hidden grid-cols-1 gap-1 lg:grid"
            role="tablist"
            aria-label="AI settings sections"
          >
            {AI_TABS.map((tab) => (
              <TabButton
                key={tab.value}
                id={getAiTabButtonId(tab.value)}
                controls={getAiTabPanelId(tab.value)}
                active={activeTab === tab.value}
                onClick={() => setActiveTab(tab.value)}
                onKeyDown={(event) => handleAiTabKeyDown(event, tab.value)}
                fullWidth
              >
                {tab.label}
              </TabButton>
            ))}
          </nav>

          <div className="mt-5 rounded border border-cyan/20 bg-cyan/10 p-3 text-xs dark:border-cyan-800/40 dark:bg-cyan-950/40">
            <p className="font-semibold">Current model</p>
            <p className="mt-1 text-cyan-800 dark:text-cyan-200">{settingsQuery.data?.model || 'Not configured'}</p>
            <p className="mt-3 font-semibold">Endpoint</p>
            <p className="mt-1 break-all text-cyan-800 dark:text-cyan-200">{settingsQuery.data?.base_url || 'Not configured'}</p>
          </div>
        </aside>

        <section className="space-y-4">
          {activeTab === 'overview' && (
            <section id={getAiTabPanelId('overview')} role="tabpanel" aria-labelledby={getAiTabButtonId('overview')}>
              <OverviewTab
                settings={settingsQuery.data}
                readiness={readiness}
                overview={overviewQuery.data}
                isLoading={overviewQuery.isLoading}
                isError={overviewQuery.isError}
                errorMessage={
                  overviewQuery.isError
                    ? resolveApiErrorMessage(overviewQuery.error, 'AI analytics could not be loaded')
                    : ''
                }
                days={days}
                setDays={setDays}
                onRefresh={() => invalidateAiQueries(queryClient)}
              />
            </section>
          )}

          {activeTab === 'activity' && (
            <section
              id={getAiTabPanelId('activity')}
              role="tabpanel"
              aria-labelledby={getAiTabButtonId('activity')}
              ref={activityTabRef}
            >
              <ActivityTab
                days={days}
                setDays={setDays}
                selectedModel={selectedModel}
                setSelectedModel={setSelectedModel}
                modelOptions={modelOptions}
                onRefresh={() => invalidateAiQueries(queryClient)}
                runs={activeTopLevelRuns}
                live={liveStatusQuery.data}
                activeTasksLoading={activeTasksLoading}
                activeTasksRefreshing={activeTasksRefreshing}
                activeTasksErrorMessage={activeTasksErrorMessage}
                onOpenRun={openRunInHistory}
                onCancelRun={requestRunCancellation}
                cancelingRunId={cancelingRunId}
                dailyBriefEnabled={draft.daily_brief_enabled}
                dailyBriefDays={dailyBriefReprocessDays}
                setDailyBriefDays={setDailyBriefReprocessDays}
                dailyBriefPending={reprocessDailyBriefMutation.isPending}
                dailyBriefValidation={dailyBriefReprocessValidation}
                retainedDailyBriefLimit={settingsQuery.data?.daily_brief_history_limit ?? null}
                onQueueDailyBrief={() => {
                  if (queueWorkBlockedReason) {
                    setNotice({ tone: 'error', message: queueWorkBlockedReason })
                    return
                  }
                  if (dailyBriefReprocessValidation) {
                    setNotice({ tone: 'error', message: dailyBriefReprocessValidation })
                    return
                  }
                  setNotice(null)
                  reprocessDailyBriefMutation.mutate(Number(dailyBriefReprocessDays.trim()))
                }}
                reprocessDays={reprocessDays}
                setReprocessDays={setReprocessDays}
                reprocessLimit={reprocessLimit}
                setReprocessLimit={setReprocessLimit}
                reprocessStartTime={reprocessStartTime}
                setReprocessStartTime={setReprocessStartTime}
                reprocessEndTime={reprocessEndTime}
                setReprocessEndTime={setReprocessEndTime}
                feeds={feedsQuery.data ?? []}
                selectedFeedIds={reprocessFeedIds}
                setSelectedFeedIds={setReprocessFeedIds}
                itemSearch={reprocessItemSearch}
                setItemSearch={setReprocessItemSearch}
                candidateItems={candidateItems}
                selectedItems={selectedReprocessItems}
                onAddItem={(item) => {
                  setSelectedReprocessItems((current) => {
                    if (current.some((entry) => entry.id === item.id)) {
                      return current
                    }
                    return [...current, item]
                  })
                }}
                onRemoveItem={(itemId) => {
                  setSelectedReprocessItems((current) => current.filter((item) => item.id !== itemId))
                }}
                onClearScope={requestClearReprocessScope}
                reprocessPending={reprocessMutation.isPending}
                reprocessValidation={reprocessQueueState.validation}
                reprocessQueueDisabled={!reprocessQueueState.payload || Boolean(queueWorkBlockedReason)}
                queueWorkBlockedReason={queueWorkBlockedReason}
                onQueueReprocess={() => {
                  if (queueWorkBlockedReason) {
                    setNotice({ tone: 'error', message: queueWorkBlockedReason })
                    return
                  }
                  if (!reprocessQueueState.payload) {
                    setNotice({ tone: 'error', message: 'Fix the reprocess scope inputs before queueing the job.' })
                    return
                  }
                  setNotice(null)
                  setQueuedReprocessScopeFingerprint(reprocessScopeFingerprint)
                  reprocessMutation.mutate(reprocessQueueState.payload)
                }}
                itemSearchLoading={candidateItemsQuery.isLoading}
                itemSearchError={
                  candidateItemsQuery.isError
                    ? resolveApiErrorMessage(candidateItemsQuery.error, 'Candidate items could not be loaded')
                    : ''
                }
                itemSearchReady={candidateItemsReady}
                filters={runFilters}
                setFilters={setRunFilters}
                runPage={runPage}
                setRunPage={setRunPage}
                runsQuery={runsQuery}
                selectedRunId={selectedRunId}
                onSelectRun={(runId) => {
                  setPinnedRunId(null)
                  setSelectedRunId(runId)
                }}
                runDetailQuery={runDetailQuery}
                briefSources={briefSourcesQuery.data ?? []}
                briefSourcesLoading={briefSourcesQuery.isLoading}
                briefSourcesErrorMessage={
                  briefSourcesQuery.isError
                    ? resolveApiErrorMessage(briefSourcesQuery.error, 'Daily brief sources could not be loaded')
                    : ''
                }
                selectedRunSectionRef={selectedRunSectionRef}
              />
            </section>
          )}

          {activeTab === 'configuration' && (
            <section
              id={getAiTabPanelId('configuration')}
              role="tabpanel"
              aria-labelledby={getAiTabButtonId('configuration')}
            >
              <ConfigurationTab
                draft={draft}
                setDraft={setDraft}
                draftDirty={draftDirty}
                settings={settingsQuery.data}
                readiness={readiness}
                isLoading={settingsQuery.isLoading}
                isError={settingsQuery.isError}
                errorMessage={
                  settingsQuery.isError
                    ? resolveApiErrorMessage(settingsQuery.error, 'AI settings could not be loaded')
                    : ''
                }
                savePending={saveMutation.isPending}
                saveDisabled={
                  !settingsReadyToSave ||
                  !draftDirty ||
                  testConnectionMutation.isPending ||
                  Boolean(draftValidationError)
                }
                saveDisabledReason={configurationSaveBlockedReason}
                validation={draftValidation}
                onSave={() => {
                  if (configurationSaveBlockedReason) {
                    setNotice({
                      tone: 'error',
                      message: configurationSaveBlockedReason,
                    })
                    return
                  }
                  setNotice(null)
                  saveMutation.mutate(createRequestFromDraft(draft))
                }}
                onTestConnection={() => {
                  if (connectionTestBlockedReason) {
                    setNotice({
                      tone: 'error',
                      message: connectionTestBlockedReason,
                    })
                    return
                  }
                  setNotice(null)
                  testConnectionMutation.mutate()
                }}
                testPending={testConnectionMutation.isPending}
                testDisabledReason={connectionTestBlockedReason}
                testResult={testResult}
                promptHistory={promptHistoryQuery.data ?? []}
                manualActions={manualActionsQuery.data ?? []}
              />
            </section>
          )}
        </section>
      </div>

      <ConfirmDialog
        open={pendingReprocessScopeClear}
        title="Clear reprocess scope?"
        description="This resets the reprocess scope to the default 7-day and 100-article window and removes any feed, time, search, or article targeting you have built."
        confirmLabel="Clear scope"
        onCancel={() => setPendingReprocessScopeClear(false)}
        onConfirm={confirmClearReprocessScope}
      />

      <ConfirmDialog
        open={Boolean(pendingCancelRun)}
        title="Cancel AI task?"
        description="This stops queued or running AI work. Use it when the current run should not continue."
        confirmLabel={pendingCancelRun ? cancelActionLabel(pendingCancelRun) : 'Cancel task'}
        onCancel={() => setPendingCancelRun(null)}
        onConfirm={confirmRunCancellation}
        isConfirming={cancelRunMutation.isPending}
        confirmDisabled={!pendingCancelRun}
      >
        {pendingCancelRun && (
          <div className="space-y-2">
            <p className="font-semibold text-ink dark:text-white">{formatRunTaskLabel(pendingCancelRun)}</p>
            <p className="text-xs text-slate dark:text-white/70">
              {formatTriggerLabel(pendingCancelRun.trigger_source)} · {describeRunScope(pendingCancelRun)}
            </p>
            <p className="text-xs text-slate dark:text-white/70">
              Status: {formatStatusLabel(pendingCancelRun.status, pendingCancelRun.reason)}
            </p>
          </div>
        )}
      </ConfirmDialog>
      {confirmDiscardUnsavedAiSettingsChanges.discardDialog}
    </div>
  )
}
