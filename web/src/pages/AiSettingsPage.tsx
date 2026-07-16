import {
  type Dispatch,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
  type RefObject,
  type SetStateAction,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, apiFetch } from '../api/client'
import { ConfirmDialog, DialogSurface } from '../components/ConfirmDialog'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import { formatDateOnly, formatDateTime } from '../utils/datetime'
import {
  AIReprocessQueueRequest,
  AIReprocessScopeValidation,
  resolveAiReprocessQueueState,
  toApiDateTime,
} from './aiReprocessQueueState'
import {
  AISettingsDraft,
  AISettingsDraftValidation,
  createDraftFromSettings,
  createRequestFromDraft,
  DEFAULT_DRAFT,
  getFirstAISettingsDraftValidationError,
  validateAISettingsDraft,
} from './aiSettingsDraft'
import { resolveVisibleRunSelection } from './aiRunSelection'
import {
  AIAuditEntryResponse,
  AIDailyBriefBackfillResponse,
  AIDailyBriefSourceItemResponse,
  AILiveTaskResponse,
  AILiveStatusResponse,
  AIOpsOverviewResponse,
  AIReprocessResponse,
  AISettings,
  AISettingsUpdateRequest,
  AITaskEventResponse,
  AITaskRunDetailResponse,
  AITaskRunListResponse,
  AITaskRunResponse,
  AITestConnectionResponse,
  Feed,
  ItemListEntry,
  ItemListResponse,
} from '../types/api'

type AiTab = 'overview' | 'activity' | 'configuration'

type RunFilters = {
  taskType: string
  status: string
  triggerSource: string
  onlyFailures: boolean
}

type NoticeState = {
  tone: 'success' | 'error'
  message: string
}

const RUN_PAGE_SIZE = 20
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
  params.set('limit', String(RUN_PAGE_SIZE))
  params.set('offset', String(runPage * RUN_PAGE_SIZE))
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
    const totalPages = Math.max(1, Math.ceil(runsQuery.data.total / RUN_PAGE_SIZE))
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
    const message =
      error instanceof ApiError && error.message.trim()
        ? error.message
        : error instanceof Error && error.message.trim()
          ? error.message
          : fallback
    setNotice({ tone: 'error', message })
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
    liveStatusQuery.isError ? `Live status: ${(liveStatusQuery.error as Error | undefined)?.message ?? 'failed to load'}` : '',
    queuedRunsQuery.isError ? `Queued tasks: ${(queuedRunsQuery.error as Error | undefined)?.message ?? 'failed to load'}` : '',
    runningRunsQuery.isError ? `Running tasks: ${(runningRunsQuery.error as Error | undefined)?.message ?? 'failed to load'}` : '',
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
          <nav
            className="mt-3 grid grid-cols-2 gap-1 sm:grid-cols-3 lg:grid-cols-1"
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
                errorMessage={(overviewQuery.error as Error | undefined)?.message ?? ''}
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
                itemSearchError={(candidateItemsQuery.error as Error | undefined)?.message ?? ''}
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
                briefSourcesErrorMessage={(briefSourcesQuery.error as Error | undefined)?.message ?? ''}
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
                errorMessage={(settingsQuery.error as Error | undefined)?.message ?? ''}
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

function OverviewTab({
  settings,
  readiness,
  overview,
  isLoading,
  isError,
  errorMessage,
  days,
  setDays,
  onRefresh,
}: {
  settings: AISettings | undefined
  readiness: string | null
  overview: AIOpsOverviewResponse | undefined
  isLoading: boolean
  isError: boolean
  errorMessage: string
  days: number
  setDays: Dispatch<SetStateAction<number>>
  onRefresh: () => void
}) {
  if (isLoading && !overview) {
    return <Panel title="Overview">Loading AI analytics...</Panel>
  }

  if (isError && !overview) {
    return <Panel title="Overview">Failed to load AI analytics. {errorMessage}</Panel>
  }

  if (!overview) {
    return null
  }

  return (
    <div className="space-y-4">
      <Panel title="Overview" subtitle="Start here to see whether AI is healthy, how much it is being used, and where it needs attention.">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <MiniStat label="Model" value={settings?.model || 'Not configured'} />
            <MiniStat label="Requests" value={overview.kpis.total_requests.toLocaleString()} />
            <MiniStat label="Success Rate" value={`${overview.kpis.success_rate_pct.toFixed(1)}%`} />
            <MiniStat label="Queued" value={overview.live.queued_count} />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <label className="sr-only" htmlFor="ai-overview-window-days">
              Overview time window
            </label>
            <select
              id="ai-overview-window-days"
              value={days}
              onChange={(event) => setDays(Number(event.target.value))}
              aria-label="Overview time window"
              className="rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
            >
              <option value={1}>Last 24h</option>
              <option value={7}>Last 7d</option>
              <option value={30}>Last 30d</option>
              <option value={90}>Last 90d</option>
            </select>
            <button
              type="button"
              className="rounded border border-slate/30 px-3 py-2 text-sm font-semibold dark:border-cyan-900/40"
              onClick={onRefresh}
            >
              Refresh
            </button>
          </div>
        </div>
      </Panel>

      <OverviewSection
        title="Health"
        description="Use this section to confirm the endpoint is configured, the queue is moving, and problems are visible quickly."
      >
        <div className="grid gap-4 xl:grid-cols-2">
          <Panel title="AI Status" subtitle={readiness ?? 'Loading runtime state...'}>
            <dl className="space-y-2 text-sm">
              <Metric label="Configured" value={settings?.ai_configured ? 'Yes' : 'No'} />
              <Metric label="API Key In Env" value={settings?.api_key_configured ? 'Yes' : 'No / Optional'} />
              <Metric label="Model" value={settings?.model || 'Not configured'} />
              <Metric label="Retry attempts" value={settings?.request_max_retries ?? 0} />
              <Metric label="Last success" value={overview.endpoint_health.last_success_at ? formatTimestamp(overview.endpoint_health.last_success_at) : 'Never'} />
              <Metric label="Failure rate" value={`${overview.endpoint_health.rolling_failure_rate_pct.toFixed(1)}%`} />
              <Metric label="Median latency" value={`${overview.endpoint_health.median_latency_ms.toFixed(1)} ms`} />
            </dl>
            <div className="mt-3 space-y-2">
              {overview.feature_health.map((row) => (
                <div key={row.feature_key} className="flex items-center justify-between gap-3 rounded-lg border border-slate/10 px-3 py-2 text-sm dark:border-cyan-900/30">
                  <div>
                    <p className="font-semibold">{formatFeatureKey(row.feature_key)}</p>
                    <p className="text-xs text-slate dark:text-white/60">
                      Last success {row.last_success_at ? formatTimestamp(row.last_success_at) : 'never'}
                    </p>
                  </div>
                  <StatusPill tone={row.enabled ? 'success' : 'neutral'} label={row.enabled ? 'Enabled' : 'Disabled'} />
                </div>
              ))}
            </div>
          </Panel>

          <Panel title="Queue Snapshot" subtitle="Database-backed snapshot of AI task runs.">
            <dl className="space-y-2 text-sm">
              <Metric label="Known workers" value={overview.live.worker_count} />
              <Metric label="Running" value={overview.live.active_count} />
              <Metric label="Queued" value={overview.live.queued_count} />
              <Metric
                label="Oldest queued age"
                value={overview.live.oldest_queued_age_seconds != null ? formatAgeSeconds(overview.live.oldest_queued_age_seconds) : 'n/a'}
              />
            </dl>
            <div className="mt-3 space-y-2">
              {overview.live.active_tasks.slice(0, 4).map((task) => (
                <LiveTaskCard key={`${task.worker_name}:${task.celery_task_id}`} task={task} />
              ))}
              {!overview.live.active_tasks.length && <EmptyInline>No running AI tasks right now.</EmptyInline>}
            </div>
          </Panel>
        </div>
      </OverviewSection>

      <OverviewSection
        title="Usage"
        description="Volume, token cost, and model performance for the selected time window."
      >
        <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          <StatCard label="Requests" value={overview.kpis.total_requests.toLocaleString()} />
          <StatCard label="Success Rate" value={`${overview.kpis.success_rate_pct.toFixed(1)}%`} />
          <StatCard label="Total Tokens" value={overview.kpis.total_tokens.toLocaleString()} />
          <StatCard label="Avg Latency" value={`${overview.kpis.average_latency_ms.toFixed(1)} ms`} />
          <StatCard label="P95 Latency" value={`${overview.kpis.p95_latency_ms.toFixed(1)} ms`} />
          <StatCard
            label="Last Success"
            value={overview.kpis.last_successful_run_at ? formatTimestamp(overview.kpis.last_successful_run_at) : 'Never'}
          />
        </section>

        <div className="grid gap-4 xl:grid-cols-2">
          <Panel title="Requests & Failures Over Time" subtitle="Recent request volume and failure pressure across the selected window.">
            <TimeSeriesBars
              points={overview.time_series}
              valueKey="requests"
              accentClass="bg-cyan"
              secondaryKey="failures"
              secondaryClass="bg-red-400/80"
            />
          </Panel>

          <Panel title="Per-Model Usage" subtitle="Requests, success rate, latency, and token footprint by model.">
            <div className="overflow-x-auto">
              <table className="min-w-full text-sm">
                <thead className="text-left text-xs uppercase text-slate dark:text-white/55">
                  <tr>
                    <th className="pb-2">Model</th>
                    <th className="pb-2">Requests</th>
                    <th className="pb-2">Success</th>
                    <th className="pb-2">Avg Latency</th>
                    <th className="pb-2">Tokens</th>
                  </tr>
                </thead>
                <tbody>
                  {overview.per_model.map((row) => (
                    <tr key={row.model} className="border-t border-slate/10 text-slate dark:border-cyan-900/30 dark:text-white/80">
                      <td className="py-2 font-semibold">{row.model}</td>
                      <td className="py-2">{row.total_requests}</td>
                      <td className="py-2">{row.success_rate_pct.toFixed(1)}%</td>
                      <td className="py-2">{row.average_latency_ms.toFixed(1)} ms</td>
                      <td className="py-2">{row.total_tokens.toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!overview.per_model.length && <EmptyInline>No model usage has been recorded yet.</EmptyInline>}
          </Panel>
        </div>

        <div className="grid gap-4 xl:grid-cols-2">
          <Panel title="Token Usage Over Time" subtitle="Total tokens by day.">
            <TimeSeriesBars points={overview.time_series} valueKey="total_tokens" accentClass="bg-emerald-500" />
          </Panel>

          <Panel title="Token Efficiency" subtitle="Average AI cost profile across successful requests.">
            <div className="grid gap-3 sm:grid-cols-2">
              <MiniStat label="Avg Prompt" value={overview.token_efficiency.average_prompt_tokens.toFixed(1)} />
              <MiniStat label="Avg Completion" value={overview.token_efficiency.average_completion_tokens.toFixed(1)} />
              <MiniStat label="Avg Total" value={overview.token_efficiency.average_total_tokens.toFixed(1)} />
              <MiniStat label="Prompt/Completion" value={overview.token_efficiency.prompt_to_completion_ratio.toFixed(2)} />
            </div>
            <p className="mt-4 text-sm text-slate dark:text-white/70">
              Top expensive feature: {formatTaskTypeLabel(overview.token_efficiency.top_expensive_feature || 'n/a')} (
              {overview.token_efficiency.top_expensive_feature_avg_tokens.toFixed(1)} avg tokens)
            </p>
          </Panel>
        </div>
      </OverviewSection>

      <OverviewSection
        title="Quality & Coverage"
        description="How complete the enrichment pipeline is, what the relevance output looks like, and how much data the AI subsystem retains."
      >
        <div className="grid gap-4 xl:grid-cols-2">
          <Panel title="Coverage & Freshness" subtitle="How much content is enriched and whether the pipeline is keeping up.">
            <dl className="space-y-2 text-sm">
              <Metric label="Eligible items" value={overview.coverage.eligible_items} />
              <Metric label="Enriched" value={overview.coverage.enriched_items} />
              <Metric label="Pending" value={overview.coverage.pending_items} />
              <Metric label="Failed" value={overview.coverage.failed_items} />
              <Metric label="No article" value={overview.coverage.skipped_no_article_count} />
              <Metric label="AI disabled skips" value={overview.coverage.skipped_ai_disabled_count} />
              <Metric label="Config skips" value={overview.coverage.skipped_not_configured_count} />
              <Metric label="Auto-enrich off skips" value={overview.coverage.skipped_auto_enrich_disabled_count} />
              <Metric label="Unchanged skips" value={overview.coverage.skipped_unchanged_count} />
              <Metric label="Oldest pending" value={overview.coverage.oldest_pending_at ? formatTimestamp(overview.coverage.oldest_pending_at) : 'n/a'} />
              <Metric label="Last enrichment" value={overview.coverage.last_successful_enrichment_at ? formatTimestamp(overview.coverage.last_successful_enrichment_at) : 'Never'} />
              <Metric label="Last daily brief" value={overview.coverage.last_successful_daily_brief_at ? formatTimestamp(overview.coverage.last_successful_daily_brief_at) : 'Never'} />
            </dl>
          </Panel>
        </div>

        <div className="grid gap-4 xl:grid-cols-2">
          <Panel title="Relevance Distribution" subtitle="Current relevance labels and the feeds producing them.">
            <div className="grid gap-3 sm:grid-cols-4">
              <MiniStat label="High" value={overview.relevance_distribution.high_count} />
              <MiniStat label="Medium" value={overview.relevance_distribution.medium_count} />
              <MiniStat label="Low" value={overview.relevance_distribution.low_count} />
              <MiniStat label="Avg Score" value={overview.relevance_distribution.average_score.toFixed(2)} />
            </div>
            <div className="mt-4 space-y-2">
              {overview.relevance_distribution.by_feed.slice(0, 6).map((feed) => (
                <div key={feed.feed_name} className="rounded-lg border border-slate/10 px-3 py-2 text-sm dark:border-cyan-900/30">
                  <div className="flex items-center justify-between gap-3">
                    <span className="font-semibold">{feed.feed_name}</span>
                    <span className="text-xs text-slate dark:text-white/60">{feed.total_items} items</span>
                  </div>
                  <p className="mt-1 text-xs text-slate dark:text-white/60">
                    High {feed.high_count} · Medium {feed.medium_count} · Low {feed.low_count} · Avg {feed.average_score.toFixed(2)}
                  </p>
                </div>
              ))}
            </div>
          </Panel>
        </div>

        <div className="grid gap-4 xl:grid-cols-2">
          <Panel title="Cache / No-op">
            <dl className="space-y-2 text-sm">
              <Metric label="Reused" value={overview.cache.reused_count} />
              <Metric label="Recomputed" value={overview.cache.recomputed_count} />
              <Metric label="No-op rate" value={`${overview.cache.no_op_rate_pct.toFixed(1)}%`} />
            </dl>
          </Panel>

          <Panel title="Storage / Retention">
            <dl className="space-y-2 text-sm">
              <Metric label="Retained briefs" value={`${overview.storage.retained_daily_briefs}/${overview.storage.daily_brief_history_limit}`} />
              <Metric label="Enrichment rows" value={overview.storage.enrichment_rows} />
              <Metric label="Usage rows" value={overview.storage.usage_event_rows} />
              <Metric label="Task history rows" value={overview.storage.task_history_rows} />
              <Metric label="Growth 7d" value={overview.storage.growth_last_7d} />
              <Metric label="Growth 30d" value={overview.storage.growth_last_30d} />
            </dl>
          </Panel>
        </div>
      </OverviewSection>
    </div>
  )
}

function ActiveTasksPanel({
  runs,
  live,
  isLoading,
  isRefreshing,
  errorMessage,
  onOpenRun,
  onCancelRun,
  cancelingRunId,
}: {
  runs: AITaskRunResponse[]
  live: AILiveStatusResponse | undefined
  isLoading: boolean
  isRefreshing: boolean
  errorMessage: string
  onOpenRun: (runId: string) => void
  onCancelRun: (run: AITaskRunResponse) => void
  cancelingRunId: string | null
}) {
  return (
    <Panel title="Active Tasks" subtitle="Queued and running top-level AI work plus the current Celery queue snapshot.">
      <div aria-busy={isLoading || isRefreshing}>
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
          <MiniStat label="Workers" value={live?.worker_count ?? 0} />
          <MiniStat label="Active" value={live?.active_count ?? 0} />
          <MiniStat label="Reserved" value={live?.reserved_count ?? 0} />
          <MiniStat label="Scheduled" value={live?.scheduled_count ?? 0} />
          <MiniStat
            label="Oldest Queued"
            value={live?.oldest_queued_age_seconds != null ? formatAgeSeconds(live.oldest_queued_age_seconds) : 'n/a'}
          />
        </div>

        <div className="mt-4 space-y-3">
          {errorMessage && (
            <p className="rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-sm text-red-700 dark:text-red-300">
              {errorMessage}
            </p>
          )}
          {isLoading && !runs.length && (
            <div className="rounded-xl border border-slate/20 bg-white/70 p-4 text-sm text-slate dark:border-cyan-900/40 dark:bg-[#072019]/80 dark:text-white/70">
              Checking queued and running AI tasks...
            </div>
          )}
          {runs.map((run) => (
            <div
              key={run.id}
              className="rounded-xl border border-slate/20 bg-white/70 p-3 dark:border-cyan-900/40 dark:bg-[#072019]/80"
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="text-sm font-semibold">{formatRunTaskLabel(run)}</p>
                    <StatusPill tone={statusTone(run.status)} label={formatStatusLabel(run.status, run.reason)} />
                  </div>
                  <p className="mt-1 text-xs text-slate dark:text-white/65">
                    {formatTriggerLabel(run.trigger_source)} · queued {formatTimestamp(run.queued_at)}
                    {run.worker_name ? ` · ${run.worker_name}` : ''}
                    {run.model ? ` · ${run.model}` : ''}
                  </p>
                  <p className="mt-2 text-sm text-slate dark:text-white/70">{describeRunScope(run)}</p>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    className="rounded border border-slate/30 px-3 py-2 text-xs font-semibold dark:border-cyan-900/40"
                    onClick={() => onOpenRun(run.id)}
                  >
                    Open Run
                  </button>
                  {canCancelRun(run) && (
                    <button
                      type="button"
                      className="rounded border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs font-semibold text-red-700 disabled:opacity-50 dark:text-red-300"
                      onClick={() => onCancelRun(run)}
                      disabled={cancelingRunId === run.id}
                    >
                      {cancelingRunId === run.id ? 'Working...' : cancelActionLabel(run)}
                    </button>
                  )}
                </div>
              </div>
              {run.task_type === 'reprocess' && (
                <div className="mt-3">
                  <ProgressBar
                    value={run.processed_count}
                    max={run.target_count || Math.max(run.processed_count, 1)}
                  />
                  <p className="mt-2 text-xs text-slate dark:text-white/60">
                    Processed {run.processed_count}/{run.target_count ?? '?'} · Success {run.success_count} · Errors{' '}
                    {run.error_count} · Skipped {run.skipped_count} · Remaining {remainingCount(run)}
                  </p>
                </div>
              )}
            </div>
          ))}
          {!isLoading && !errorMessage && !runs.length && <EmptyInline>No queued or running top-level AI tasks right now.</EmptyInline>}
        </div>
      </div>
    </Panel>
  )
}

function QueueWorkPanel({
  dailyBriefEnabled,
  dailyBriefDays,
  setDailyBriefDays,
  dailyBriefPending,
  dailyBriefValidation,
  retainedDailyBriefLimit,
  onQueueDailyBrief,
  reprocessDays,
  setReprocessDays,
  reprocessLimit,
  setReprocessLimit,
  reprocessStartTime,
  setReprocessStartTime,
  reprocessEndTime,
  setReprocessEndTime,
  feeds,
  selectedFeedIds,
  setSelectedFeedIds,
  itemSearch,
  setItemSearch,
  candidateItems,
  selectedItems,
  onAddItem,
  onRemoveItem,
  onClearScope,
  reprocessPending,
  reprocessValidation,
  reprocessQueueDisabled,
  queueWorkBlockedReason,
  onQueueReprocess,
  itemSearchLoading,
  itemSearchError,
  itemSearchReady,
}: {
  dailyBriefEnabled: boolean
  dailyBriefDays: string
  setDailyBriefDays: Dispatch<SetStateAction<string>>
  dailyBriefPending: boolean
  dailyBriefValidation: string | null
  retainedDailyBriefLimit: number | null
  onQueueDailyBrief: () => void
  reprocessDays: string
  setReprocessDays: Dispatch<SetStateAction<string>>
  reprocessLimit: string
  setReprocessLimit: Dispatch<SetStateAction<string>>
  reprocessStartTime: string
  setReprocessStartTime: Dispatch<SetStateAction<string>>
  reprocessEndTime: string
  setReprocessEndTime: Dispatch<SetStateAction<string>>
  feeds: Feed[]
  selectedFeedIds: string[]
  setSelectedFeedIds: Dispatch<SetStateAction<string[]>>
  itemSearch: string
  setItemSearch: Dispatch<SetStateAction<string>>
  candidateItems: ItemListEntry[]
  selectedItems: ItemListEntry[]
  onAddItem: (item: ItemListEntry) => void
  onRemoveItem: (itemId: string) => void
  onClearScope: () => void
  reprocessPending: boolean
  reprocessValidation: AIReprocessScopeValidation
  reprocessQueueDisabled: boolean
  queueWorkBlockedReason: string | null
  onQueueReprocess: () => void
  itemSearchLoading: boolean
  itemSearchError: string
  itemSearchReady: boolean
}) {
  const usingExplicitScope = !shouldUseLookbackWindow(reprocessStartTime, reprocessEndTime, selectedItems)
  const hasReprocessValidationError = Boolean(
    reprocessValidation.days ||
      reprocessValidation.limit ||
      reprocessValidation.timeRange ||
      reprocessValidation.itemSelection,
  )

  return (
    <Panel title="Queue AI Work" subtitle="Launch daily brief and reprocess jobs from one place, with optional feed, time, and item targeting.">
      <div className="space-y-4">
        {queueWorkBlockedReason && (
          <div
            role="status"
            aria-live="polite"
            aria-atomic="true"
            className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-900/50 dark:bg-amber-950/35 dark:text-amber-200"
          >
            {queueWorkBlockedReason}
          </div>
        )}
        <div className="rounded-xl border border-slate/20 bg-white/70 p-4 dark:border-cyan-900/40 dark:bg-[#072019]/80">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="text-sm font-semibold">Daily Brief</p>
              <p className="mt-1 text-sm text-slate dark:text-white/70">
                Reprocess daily briefs for the last X days, ending today. The batch runs sequentially so local models are not flooded.
              </p>
            </div>
            <button
              type="button"
              className="rounded border border-slate/30 px-3 py-2 text-sm font-semibold disabled:opacity-50 dark:border-cyan-900/40"
              onClick={onQueueDailyBrief}
              disabled={dailyBriefPending || !dailyBriefEnabled || Boolean(dailyBriefValidation) || Boolean(queueWorkBlockedReason)}
            >
              {dailyBriefPending ? 'Queueing...' : 'Queue Daily Brief'}
            </button>
          </div>
          <div className="mt-4 grid gap-3 md:grid-cols-[minmax(0,220px)_minmax(0,1fr)]">
            <Field label="Last X Days">
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#041612]/90"
                value={dailyBriefDays}
                onChange={(event) => setDailyBriefDays(event.target.value)}
                inputMode="numeric"
                aria-invalid={Boolean(dailyBriefValidation)}
              />
              {dailyBriefValidation && <p className="mt-1 text-xs text-red-600">{dailyBriefValidation}</p>}
            </Field>
            <div className="rounded-lg border border-slate/15 bg-slate/5 px-3 py-2 text-xs text-slate dark:border-cyan-900/30 dark:bg-white/[0.03] dark:text-white/60">
              {retainedDailyBriefLimit == null
                ? 'Retention limit is still loading. Increase retained daily briefings in Configuration before queueing a larger daily brief range.'
                : `Retention allows ${retainedDailyBriefLimit} brief${retainedDailyBriefLimit === 1 ? '' : 's'}. Increase retained daily briefings in Configuration before queueing a larger daily brief range.`}
            </div>
          </div>
        </div>

        <div className="rounded-xl border border-slate/20 bg-white/70 p-4 dark:border-cyan-900/40 dark:bg-[#072019]/80">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="text-sm font-semibold">Reprocess Scope</p>
              <p className="mt-1 text-sm text-slate dark:text-white/70">
                Use a recent lookback, narrow it to feeds or a time range, or select exact articles to re-enrich.
              </p>
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                className="rounded border border-slate/30 px-3 py-2 text-sm font-semibold dark:border-cyan-900/40"
                onClick={onClearScope}
              >
                Clear Scope
              </button>
              <button
                type="button"
                className="rounded bg-ink px-3 py-2 text-sm font-semibold text-white disabled:opacity-50 dark:bg-cyan dark:text-slate-950"
                onClick={onQueueReprocess}
                disabled={reprocessPending || reprocessQueueDisabled || Boolean(queueWorkBlockedReason)}
              >
                {reprocessPending ? 'Queueing...' : 'Queue Reprocess'}
              </button>
            </div>
          </div>

          <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <Field label="Lookback Days">
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#041612]/90"
                value={reprocessDays}
                onChange={(event) => setReprocessDays(event.target.value)}
                inputMode="numeric"
              />
              {reprocessValidation.days && <p className="mt-1 text-xs text-red-600">{reprocessValidation.days}</p>}
            </Field>
            <Field label="Last X Articles">
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#041612]/90"
                value={reprocessLimit}
                onChange={(event) => setReprocessLimit(event.target.value)}
                inputMode="numeric"
              />
              {reprocessValidation.limit && <p className="mt-1 text-xs text-red-600">{reprocessValidation.limit}</p>}
            </Field>
            <Field label="Start Time">
              <input
                type="datetime-local"
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#041612]/90"
                value={reprocessStartTime}
                onChange={(event) => setReprocessStartTime(event.target.value)}
              />
            </Field>
            <Field label="End Time">
              <input
                type="datetime-local"
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#041612]/90"
                value={reprocessEndTime}
                onChange={(event) => setReprocessEndTime(event.target.value)}
              />
            </Field>
          </div>
          <div className="space-y-1">
            <p className="text-xs text-slate dark:text-white/60">
              Blank or <code>0</code> values are rejected so the queued job cannot widen beyond the scope you review here.
            </p>
            {reprocessValidation.timeRange && <p className="text-xs text-red-600">{reprocessValidation.timeRange}</p>}
            {!hasReprocessValidationError && (
              <p className="text-xs text-slate dark:text-white/60">
                {usingExplicitScope
                  ? 'Explicit time or article scope is active, so lookback days are ignored for this run.'
                  : 'Lookback days use article publication time, falling back to first-seen time only when a feed has no publication date.'}
              </p>
            )}
          </div>

          <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,280px)_minmax(0,1fr)]">
            <div>
              <p className="text-xs font-semibold uppercase text-slate dark:text-white/55">Feeds</p>
              <div className="mt-2 max-h-56 space-y-2 overflow-y-auto rounded-lg border border-slate/15 bg-slate/5 p-2 dark:border-cyan-900/30 dark:bg-white/[0.03]">
                {feeds.map((feed) => (
                  <label
                    key={feed.id}
                    className="flex items-start gap-2 rounded border border-transparent px-2 py-2 text-sm transition hover:border-slate/15 dark:hover:border-cyan-900/30"
                  >
                    <input
                      type="checkbox"
                      checked={selectedFeedIds.includes(feed.id)}
                      onChange={(event) =>
                        setSelectedFeedIds((current) =>
                          event.target.checked
                            ? [...current, feed.id]
                            : current.filter((candidateId) => candidateId !== feed.id),
                        )
                      }
                    />
                    <span>
                      <span className="block font-semibold">{feed.name}</span>
                      <span className="block text-xs text-slate dark:text-white/60">{feed.url}</span>
                    </span>
                  </label>
                ))}
                {!feeds.length && <EmptyInline>No feeds available to scope.</EmptyInline>}
              </div>
            </div>

            <div>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <p className="text-xs font-semibold uppercase text-slate dark:text-white/55">Specific Articles</p>
                <span className="text-xs text-slate dark:text-white/60">
                  {selectedItems.length
                    ? `${selectedItems.length} selected article${selectedItems.length === 1 ? '' : 's'}`
                    : usingExplicitScope
                      ? 'Explicit scope active'
                      : 'Using lookback window'}
                </span>
              </div>
              <input
                className="mt-2 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#041612]/90"
                value={itemSearch}
                onChange={(event) => setItemSearch(event.target.value)}
                placeholder="Search items by title, summary, or URL"
                aria-invalid={Boolean(reprocessValidation.itemSelection)}
              />
              {reprocessValidation.itemSelection && (
                <p className="mt-1 text-xs text-red-600">{reprocessValidation.itemSelection}</p>
              )}

              {selectedItems.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-2">
                  {selectedItems.map((item) => (
                    <button
                      key={item.id}
                      type="button"
                      className="rounded-full border border-cyan/20 bg-cyan/10 px-3 py-1 text-left text-xs text-cyan-900 dark:border-cyan/30 dark:text-cyan-100"
                      onClick={() => onRemoveItem(item.id)}
                    >
                      {truncate(item.title, 56)} · remove
                    </button>
                  ))}
                </div>
              )}

              <div className="mt-3 max-h-72 space-y-2 overflow-y-auto">
                {!itemSearchReady && (
                  <EmptyInline>Search by title, summary, or URL, or narrow by feed/time to preview matching articles.</EmptyInline>
                )}
                {itemSearchReady && itemSearchLoading && <EmptyInline>Loading matching items...</EmptyInline>}
                {itemSearchReady &&
                  !itemSearchLoading &&
                  candidateItems.map((item) => (
                    <button
                      key={item.id}
                      type="button"
                      className="w-full rounded-lg border border-slate/15 bg-white/80 px-3 py-3 text-left transition hover:border-cyan/30 dark:border-cyan-900/30 dark:bg-[#041612]/90"
                      onClick={() => onAddItem(item)}
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <p className="text-sm font-semibold">{item.title}</p>
                          <p className="mt-1 text-xs text-slate dark:text-white/60">
                            {item.feed_name}
                            {item.published_at ? ` · published ${formatTimestamp(item.published_at)}` : ''}
                            {item.first_seen_at ? ` · first seen ${formatTimestamp(item.first_seen_at)}` : ''}
                          </p>
                        </div>
                        <span className="rounded-full border border-slate/20 px-2 py-1 text-[11px] font-semibold uppercase text-slate dark:border-cyan-900/40 dark:text-white/65">
                          Add
                        </span>
                      </div>
                    </button>
                  ))}
                {itemSearchReady && !itemSearchLoading && !candidateItems.length && !itemSearchError && (
                  <EmptyInline>No recent items matched the current scope.</EmptyInline>
                )}
                {itemSearchReady && itemSearchError && <p className="text-sm text-red-600">Failed to load items. {itemSearchError}</p>}
              </div>
            </div>
          </div>

          <p className="mt-4 text-xs text-slate dark:text-white/60">
            Selected articles override the lookback window. Without selected articles, ThreatLens uses the time range and feed
            filters against the last X articles by publication time, falling back to first-seen time for undated feed items.
          </p>
        </div>
      </div>
    </Panel>
  )
}

function RunArticlesSection({
  parentRun,
  childRunsQuery,
  visibleCount,
  onInspectRun,
  onShowMore,
  onShowLess,
}: {
  parentRun: AITaskRunResponse
  childRunsQuery: ReturnType<typeof useQuery<AITaskRunListResponse>>
  visibleCount: number
  onInspectRun: (runId: string) => void
  onShowMore: () => void
  onShowLess: () => void
}) {
  const childRuns = childRunsQuery.data?.items ?? []
  const totalChildRuns = childRunsQuery.data?.total ?? 0
  const canShowMore = totalChildRuns > childRuns.length
  const canShowLess = visibleCount > 8 && childRuns.length > 8
  const isBackfill = isDailyBriefBackfillRun(parentRun)
  const sectionTitle = isBackfill ? 'Daily Brief Runs' : 'Article Runs'
  const childRunNounPlural = isBackfill ? 'daily brief runs' : 'article runs'
  const targetNoun = isBackfill ? 'day' : 'article'
  const targetNounPlural = isBackfill ? 'days' : 'articles'

  return (
    <div>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase text-slate dark:text-white/55">{sectionTitle}</p>
          <p className="mt-1 text-xs text-slate dark:text-white/60">
            {totalChildRuns
              ? `Showing ${childRuns.length} of ${totalChildRuns} queued ${childRunNounPlural}${parentRun.target_count ? ` out of ${parentRun.target_count} target ${targetNounPlural}` : ''}.`
              : parentRun.target_count
                ? `No child ${childRunNounPlural} are visible yet. Target size: ${parentRun.target_count} ${targetNoun}${parentRun.target_count === 1 ? '' : 's'}.`
                : `No child ${childRunNounPlural} are visible yet.`}
          </p>
        </div>
        <div className="flex flex-wrap gap-2 text-xs">
          <StatusPill tone="success" label={`Ready ${parentRun.success_count}`} />
          <StatusPill tone="danger" label={`Errors ${parentRun.error_count}`} />
          <StatusPill tone="neutral" label={`Skipped ${parentRun.skipped_count}`} />
          <StatusPill tone="info" label={`Remaining ${remainingCount(parentRun)}`} />
        </div>
      </div>

      {childRunsQuery.isLoading && !childRuns.length && (
        <p className="mt-3 text-sm text-slate dark:text-white/70">Loading {childRunNounPlural}...</p>
      )}
      {childRunsQuery.isError && (
        <p className="mt-3 text-sm text-red-600">
          Failed to load {childRunNounPlural}. {(childRunsQuery.error as Error | undefined)?.message ?? ''}
        </p>
      )}

      {!childRunsQuery.isLoading && !childRuns.length && !childRunsQuery.isError && (
        <EmptyInline>Child {childRunNounPlural} have not been queued yet.</EmptyInline>
      )}

      {!!childRuns.length && (
        <div className={`mt-3 space-y-2 ${visibleCount > 8 ? 'max-h-96 overflow-y-auto pr-1' : ''}`}>
          {childRuns.map((run) => (
            <div
              key={run.id}
              className="rounded-lg border border-slate/10 px-3 py-3 text-sm dark:border-cyan-900/30"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="font-semibold">
                    {isBackfill ? formatDailyBriefChildRunTitle(run) : run.item_title || run.item_id || 'Unknown article'}
                  </p>
                  <p className="mt-1 text-xs text-slate dark:text-white/60">
                    {isBackfill ? (
                      formatDailyBriefChildRunMeta(run)
                    ) : (
                      <>
                        {run.feed_name || 'Unknown feed'}
                        {run.item_published_at ? ` · published ${formatTimestamp(run.item_published_at)}` : ''}
                        {run.item_first_seen_at ? ` · first seen ${formatTimestamp(run.item_first_seen_at)}` : ''}
                      </>
                    )}
                  </p>
                </div>
                <StatusPill tone={statusTone(run.status)} label={formatStatusLabel(run.status, run.reason)} />
              </div>
              <p className="mt-2 text-xs text-slate dark:text-white/60">
                Queued {formatTimestamp(run.queued_at)}
                {run.started_at ? ` · started ${formatTimestamp(run.started_at)}` : ''}
                {run.finished_at ? ` · finished ${formatTimestamp(run.finished_at)}` : ''}
                {run.duration_ms != null ? ` · ${formatDuration(run.duration_ms)}` : ''}
                {run.total_tokens != null ? ` · ${run.total_tokens.toLocaleString()} tokens` : ''}
              </p>
              {(run.error || run.reason) && (
                <p className="mt-2 text-xs text-slate dark:text-white/70">{run.error || run.reason}</p>
              )}
              {canInspectProviderExchange(run) && (
                <div className="mt-3">
                  <button
                    type="button"
                    className="rounded border border-slate/30 px-3 py-2 text-xs font-semibold dark:border-cyan-900/40"
                    onClick={() => onInspectRun(run.id)}
                  >
                    View Request / Response
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {(canShowMore || canShowLess) && (
        <div className="mt-3 flex flex-wrap gap-2">
          {canShowMore && (
            <button
              type="button"
              className="rounded border border-slate/30 px-3 py-2 text-xs font-semibold dark:border-cyan-900/40"
              onClick={onShowMore}
            >
              Show {Math.min(20, totalChildRuns - childRuns.length)} More
            </button>
          )}
          {canShowLess && (
            <button
              type="button"
              className="rounded border border-slate/30 px-3 py-2 text-xs font-semibold dark:border-cyan-900/40"
              onClick={onShowLess}
            >
              Show Less
            </button>
          )}
        </div>
      )}
    </div>
  )
}

function ProviderExchangeModal({
  run,
  event,
  isLoading,
  errorMessage,
  onClose,
}: {
  run: AITaskRunResponse | null
  event: AITaskEventResponse | null
  isLoading: boolean
  errorMessage: string
  onClose: () => void
}) {
  if (!run && !isLoading && !errorMessage) {
    return null
  }

  const payload = event?.payload ?? {}
  const requestPayload = payload.request_payload
  const requestUrl = typeof event?.payload?.request_url === 'string' ? event.payload.request_url : null
  const responseBody = typeof payload.response_body === 'string' ? payload.response_body : null
  const responseJson = payload.response_json
  const responseJsonSummary = payload.response_json_summary
  const statusCode = typeof payload.status_code === 'number' ? payload.status_code : null
  const requestSummary = buildProviderRequestSummary(payload)
  const responseSummary = buildProviderResponseSummary(payload)

  return (
    <DialogSurface
      open
      title="Provider Exchange"
      description={run ? `${formatRunTaskLabel(run)}${run.item_title ? ` · ${run.item_title}` : ''}` : 'Loading run detail'}
      onClose={onClose}
      panelClassName="max-h-[85vh] max-w-5xl overflow-y-auto"
      bodyClassName="mt-4 space-y-4 text-sm text-slate dark:text-white/75"
    >
      {isLoading && <p>Loading request/response details...</p>}
      {!isLoading && errorMessage && <p className="text-red-600">Failed to load run detail. {errorMessage}</p>}
      {!isLoading && !errorMessage && !event && (
        <p>No provider request/response was captured for this run.</p>
      )}

      {!isLoading && !errorMessage && event && (
        <div className="space-y-4">
          <div className="grid gap-3 md:grid-cols-3">
            <MiniStat label="Event" value={event.event_type} />
            <MiniStat label="Captured" value={formatTimestamp(event.created_at)} />
            <MiniStat label="HTTP Status" value={statusCode ?? 'n/a'} />
          </div>

          {event.message && (
            <div className="rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-sm text-red-700 dark:text-red-300">
              {event.message}
            </div>
          )}

          <div className="grid gap-4 xl:grid-cols-2">
            <Panel title="Request">
              {requestUrl && <p className="mb-3 break-all text-xs text-slate dark:text-white/60">{requestUrl}</p>}
              {requestPayload != null ? (
                <pre className="overflow-x-auto rounded-lg border border-slate/15 bg-slate/5 p-3 text-xs dark:border-cyan-900/30 dark:bg-white/[0.03]">
                  {formatDebugPayload(requestPayload)}
                </pre>
              ) : (
                <>
                  <p className="mb-3 text-xs text-slate dark:text-white/60">
                    Raw prompt payload is redacted; the persisted exchange keeps the operational request summary below.
                  </p>
                  <ExchangeSummaryList entries={requestSummary} emptyMessage="No request summary was recorded." />
                </>
              )}
            </Panel>

            <Panel title="Response">
              {responseBody ? (
                <pre className="overflow-x-auto rounded-lg border border-slate/15 bg-slate/5 p-3 text-xs dark:border-cyan-900/30 dark:bg-white/[0.03]">
                  {responseBody}
                </pre>
              ) : (
                <>
                  <p className="mb-3 text-xs text-slate dark:text-white/60">
                    Raw provider response is redacted; the persisted exchange keeps response size, status, and parsed-shape summary.
                  </p>
                  <ExchangeSummaryList entries={responseSummary} emptyMessage="No response summary was recorded." />
                </>
              )}
              {responseJson != null && (
                <>
                  <p className="mt-3 text-xs font-semibold uppercase text-slate dark:text-white/55">
                    Parsed Response JSON
                  </p>
                  <pre className="mt-2 overflow-x-auto rounded-lg border border-slate/15 bg-slate/5 p-3 text-xs dark:border-cyan-900/30 dark:bg-white/[0.03]">
                    {formatDebugPayload(responseJson)}
                  </pre>
                </>
              )}
              {responseJson == null && responseJsonSummary != null && (
                <>
                  <p className="mt-3 text-xs font-semibold uppercase text-slate dark:text-white/55">
                    Parsed Response Summary
                  </p>
                  <pre className="mt-2 overflow-x-auto rounded-lg border border-slate/15 bg-slate/5 p-3 text-xs dark:border-cyan-900/30 dark:bg-white/[0.03]">
                    {formatDebugPayload(responseJsonSummary)}
                  </pre>
                </>
              )}
            </Panel>
          </div>
        </div>
      )}
    </DialogSurface>
  )
}

type ExchangeSummaryEntry = {
  label: string
  value: string | number
}

function ExchangeSummaryList({
  entries,
  emptyMessage,
}: {
  entries: ExchangeSummaryEntry[]
  emptyMessage: string
}) {
  if (!entries.length) {
    return <EmptyInline>{emptyMessage}</EmptyInline>
  }

  return (
    <dl className="space-y-2 rounded-lg border border-slate/15 bg-slate/5 p-3 text-xs dark:border-cyan-900/30 dark:bg-white/[0.03]">
      {entries.map((entry) => (
        <div key={entry.label} className="grid gap-1 sm:grid-cols-[140px_minmax(0,1fr)]">
          <dt className="font-semibold uppercase text-slate dark:text-white/55">{entry.label}</dt>
          <dd className="min-w-0 break-words text-slate-900 dark:text-white/80">{entry.value}</dd>
        </div>
      ))}
    </dl>
  )
}

function buildProviderRequestSummary(payload: Record<string, unknown>): ExchangeSummaryEntry[] {
  return compactExchangeEntries([
    { label: 'Host', value: stringPayloadValue(payload.request_host) },
    { label: 'Path', value: stringPayloadValue(payload.request_path) },
    { label: 'Model', value: stringPayloadValue(payload.request_model) },
    { label: 'Messages', value: numberPayloadValue(payload.request_message_count) },
    { label: 'Roles', value: arrayPayloadValue(payload.request_message_roles) },
    { label: 'Prompt chars', value: numberPayloadValue(payload.request_prompt_chars) },
    { label: 'Temperature', value: numberPayloadValue(payload.request_temperature) },
    { label: 'Max tokens', value: numberPayloadValue(payload.request_max_tokens) },
    { label: 'Attempt', value: formatAttemptSummary(payload) },
  ])
}

function buildProviderResponseSummary(payload: Record<string, unknown>): ExchangeSummaryEntry[] {
  return compactExchangeEntries([
    { label: 'Body chars', value: numberPayloadValue(payload.response_body_chars) },
    { label: 'Body SHA-256', value: stringPayloadValue(payload.response_body_sha256) },
    { label: 'Finish reason', value: stringPayloadValue(payload.finish_reason) },
    { label: 'Attempt', value: formatAttemptSummary(payload) },
  ])
}

function compactExchangeEntries(entries: Array<{ label: string; value: string | number | null }>): ExchangeSummaryEntry[] {
  return entries.filter((entry): entry is ExchangeSummaryEntry => entry.value !== null)
}

function stringPayloadValue(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null
}

function numberPayloadValue(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function arrayPayloadValue(value: unknown): string | null {
  if (!Array.isArray(value)) {
    return null
  }
  const entries = value.filter((entry): entry is string | number => typeof entry === 'string' || typeof entry === 'number')
  return entries.length ? entries.join(', ') : null
}

function formatAttemptSummary(payload: Record<string, unknown>): string | null {
  const attempt = numberPayloadValue(payload.attempt)
  const maxAttempts = numberPayloadValue(payload.max_attempts)
  if (attempt === null) {
    return null
  }
  return maxAttempts === null ? String(attempt) : `${attempt} / ${maxAttempts}`
}

function ActivityTab({
  days,
  setDays,
  selectedModel,
  setSelectedModel,
  modelOptions,
  onRefresh,
  runs,
  live,
  activeTasksLoading,
  activeTasksRefreshing,
  activeTasksErrorMessage,
  onOpenRun,
  dailyBriefEnabled,
  dailyBriefDays,
  setDailyBriefDays,
  dailyBriefPending,
  dailyBriefValidation,
  retainedDailyBriefLimit,
  onQueueDailyBrief,
  reprocessDays,
  setReprocessDays,
  reprocessLimit,
  setReprocessLimit,
  reprocessStartTime,
  setReprocessStartTime,
  reprocessEndTime,
  setReprocessEndTime,
  feeds,
  selectedFeedIds,
  setSelectedFeedIds,
  itemSearch,
  setItemSearch,
  candidateItems,
  selectedItems,
  onAddItem,
  onRemoveItem,
  onClearScope,
  reprocessPending,
  reprocessValidation,
  reprocessQueueDisabled,
  queueWorkBlockedReason,
  onQueueReprocess,
  itemSearchLoading,
  itemSearchError,
  itemSearchReady,
  filters,
  setFilters,
  runPage,
  setRunPage,
  runsQuery,
  selectedRunId,
  onSelectRun,
  runDetailQuery,
  briefSources,
  briefSourcesLoading,
  briefSourcesErrorMessage,
  selectedRunSectionRef,
  onCancelRun,
  cancelingRunId,
}: {
  days: number
  setDays: Dispatch<SetStateAction<number>>
  selectedModel: string
  setSelectedModel: Dispatch<SetStateAction<string>>
  modelOptions: string[]
  onRefresh: () => void
  runs: AITaskRunResponse[]
  live: AILiveStatusResponse | undefined
  activeTasksLoading: boolean
  activeTasksRefreshing: boolean
  activeTasksErrorMessage: string
  onOpenRun: (runId: string) => void
  dailyBriefEnabled: boolean
  dailyBriefDays: string
  setDailyBriefDays: Dispatch<SetStateAction<string>>
  dailyBriefPending: boolean
  dailyBriefValidation: string | null
  retainedDailyBriefLimit: number | null
  onQueueDailyBrief: () => void
  reprocessDays: string
  setReprocessDays: Dispatch<SetStateAction<string>>
  reprocessLimit: string
  setReprocessLimit: Dispatch<SetStateAction<string>>
  reprocessStartTime: string
  setReprocessStartTime: Dispatch<SetStateAction<string>>
  reprocessEndTime: string
  setReprocessEndTime: Dispatch<SetStateAction<string>>
  feeds: Feed[]
  selectedFeedIds: string[]
  setSelectedFeedIds: Dispatch<SetStateAction<string[]>>
  itemSearch: string
  setItemSearch: Dispatch<SetStateAction<string>>
  candidateItems: ItemListEntry[]
  selectedItems: ItemListEntry[]
  onAddItem: (item: ItemListEntry) => void
  onRemoveItem: (itemId: string) => void
  onClearScope: () => void
  reprocessPending: boolean
  reprocessValidation: AIReprocessScopeValidation
  reprocessQueueDisabled: boolean
  queueWorkBlockedReason: string | null
  onQueueReprocess: () => void
  itemSearchLoading: boolean
  itemSearchError: string
  itemSearchReady: boolean
  filters: RunFilters
  setFilters: Dispatch<SetStateAction<RunFilters>>
  runPage: number
  setRunPage: Dispatch<SetStateAction<number>>
  runsQuery: ReturnType<typeof useQuery<AITaskRunListResponse>>
  selectedRunId: string | null
  onSelectRun: (runId: string) => void
  runDetailQuery: ReturnType<typeof useQuery<AITaskRunDetailResponse>>
  briefSources: AIDailyBriefSourceItemResponse[]
  briefSourcesLoading: boolean
  briefSourcesErrorMessage: string
  selectedRunSectionRef: RefObject<HTMLDivElement | null>
  onCancelRun: (run: AITaskRunResponse) => void
  cancelingRunId: string | null
}) {
  const selectedRun = runDetailQuery.data?.run
  const runTotal = runsQuery.data?.total ?? 0
  const runOffset = runPage * RUN_PAGE_SIZE
  const visibleRunOffset = runsQuery.isPlaceholderData ? (runsQuery.data?.offset ?? runOffset) : runOffset
  const runCount = runsQuery.data?.items.length ?? 0
  const totalPages = Math.max(1, Math.ceil(runTotal / RUN_PAGE_SIZE))
  const runListLoading = runsQuery.isLoading && !runsQuery.data
  const runListPageLoading = runsQuery.isFetching && runsQuery.isPlaceholderData
  const runListRefreshing = runsQuery.isFetching && Boolean(runsQuery.data) && !runsQuery.isPlaceholderData
  const runListStatusMessage = runListLoading
    ? 'Loading AI run history...'
    : runListPageLoading
      ? `Loading page ${runPage + 1}...`
      : runListRefreshing
        ? 'Refreshing run history...'
        : null
  const [articlePreviewLimit, setArticlePreviewLimit] = useState(8)
  const [inspectedRunId, setInspectedRunId] = useState<string | null>(null)

  useEffect(() => {
    setArticlePreviewLimit(8)
  }, [selectedRunId])

  useEffect(() => {
    if (!runsQuery.data || runsQuery.isPlaceholderData || runPage === 0) {
      return
    }
    if (runsQuery.data.total > runOffset) {
      return
    }
    setRunPage(Math.max(0, Math.ceil(runsQuery.data.total / RUN_PAGE_SIZE) - 1))
  }, [runOffset, runPage, runsQuery.data, runsQuery.isPlaceholderData, setRunPage])

  const inspectedRunDetailQuery = useQuery({
    queryKey: ['ai', 'ops', 'inspect-run', inspectedRunId],
    queryFn: ({ signal }) => apiFetch<AITaskRunDetailResponse>(`/ai/ops/runs/${inspectedRunId}`, { signal }),
    enabled: Boolean(inspectedRunId),
    staleTime: 5000,
  })

  const inspectedRun = inspectedRunDetailQuery.data?.run ?? null
  const inspectedProviderEvent = useMemo(
    () => findLatestProviderExchangeEvent(inspectedRunDetailQuery.data?.events ?? []),
    [inspectedRunDetailQuery.data?.events],
  )

  const childRunsQuery = useQuery({
    queryKey: ['ai', 'ops', 'child-runs', selectedRunId, articlePreviewLimit],
    queryFn: ({ signal }) =>
      apiFetch<AITaskRunListResponse>(
        `/ai/ops/runs?parent_run_id=${selectedRunId}&limit=${articlePreviewLimit}`,
        { signal },
      ),
    enabled: Boolean(selectedRunId && selectedRun?.task_type === 'reprocess'),
    refetchInterval:
      selectedRun && (selectedRun.status === 'queued' || selectedRun.status === 'running') ? 10000 : false,
    staleTime: 5000,
  })

  return (
    <div className="space-y-4">
      <Panel title="Operations" subtitle="Queue AI work, monitor current jobs, and inspect the full run history in one place.">
        <div className="flex flex-wrap items-center gap-2">
          <label className="sr-only" htmlFor="ai-activity-window-days">
            Activity window
          </label>
          <select
            id="ai-activity-window-days"
            value={days}
            onChange={(event) => {
              setDays(Number(event.target.value))
              setRunPage(0)
            }}
            aria-label="Activity window"
            className="rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
          >
            <option value={1}>Last 24h</option>
            <option value={7}>Last 7d</option>
            <option value={30}>Last 30d</option>
            <option value={90}>Last 90d</option>
          </select>
          <label className="sr-only" htmlFor="ai-activity-model-filter">
            Model filter
          </label>
          <select
            id="ai-activity-model-filter"
            value={selectedModel}
            onChange={(event) => {
              setSelectedModel(event.target.value)
              setRunPage(0)
            }}
            aria-label="Model filter"
            className="rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
          >
            {modelOptions.map((model) => (
              <option key={model} value={model}>
                {model === 'all' ? 'All models' : model}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="rounded border border-slate/30 px-3 py-2 text-sm font-semibold dark:border-cyan-900/40"
            onClick={onRefresh}
          >
            Refresh
          </button>
        </div>
        <p className="mt-3 text-xs text-slate dark:text-white/60">
          These filters apply to the operations and run-history views below.
        </p>
      </Panel>

      <OverviewSection
        title="Live Operations"
        description="Use this section to see what is running right now and to queue new brief or reprocess work."
      >
        <div className="space-y-4">
          <ActiveTasksPanel
            runs={runs}
            live={live}
            isLoading={activeTasksLoading}
            isRefreshing={activeTasksRefreshing}
            errorMessage={activeTasksErrorMessage}
            onOpenRun={onOpenRun}
            onCancelRun={onCancelRun}
            cancelingRunId={cancelingRunId}
          />
          <QueueWorkPanel
            dailyBriefEnabled={dailyBriefEnabled}
            dailyBriefDays={dailyBriefDays}
            setDailyBriefDays={setDailyBriefDays}
            dailyBriefPending={dailyBriefPending}
            dailyBriefValidation={dailyBriefValidation}
            retainedDailyBriefLimit={retainedDailyBriefLimit}
            onQueueDailyBrief={onQueueDailyBrief}
            reprocessDays={reprocessDays}
            setReprocessDays={setReprocessDays}
            reprocessLimit={reprocessLimit}
            setReprocessLimit={setReprocessLimit}
            reprocessStartTime={reprocessStartTime}
            setReprocessStartTime={setReprocessStartTime}
            reprocessEndTime={reprocessEndTime}
            setReprocessEndTime={setReprocessEndTime}
            feeds={feeds}
            selectedFeedIds={selectedFeedIds}
            setSelectedFeedIds={setSelectedFeedIds}
            itemSearch={itemSearch}
            setItemSearch={setItemSearch}
            candidateItems={candidateItems}
            selectedItems={selectedItems}
            onAddItem={onAddItem}
            onRemoveItem={onRemoveItem}
            onClearScope={onClearScope}
            reprocessPending={reprocessPending}
            reprocessValidation={reprocessValidation}
            reprocessQueueDisabled={reprocessQueueDisabled}
            queueWorkBlockedReason={queueWorkBlockedReason}
            onQueueReprocess={onQueueReprocess}
            itemSearchLoading={itemSearchLoading}
            itemSearchError={itemSearchError}
            itemSearchReady={itemSearchReady}
          />
        </div>
      </OverviewSection>

      <OverviewSection
        title="Run History"
        description="Review every AI task across enrichment, daily briefs, connection tests, and reprocess jobs."
      >
        <Panel title="Task History" subtitle="Filter by type, status, trigger source, and model to find the runs you need.">
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
            <label className="sr-only" htmlFor="ai-history-task-type-filter">
              Task type filter
            </label>
            <select
              id="ai-history-task-type-filter"
              value={filters.taskType}
              onChange={(event) => {
                setRunPage(0)
                setFilters((current) => ({ ...current, taskType: event.target.value }))
              }}
              aria-label="Task type filter"
              className="rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
            >
              <option value="">All task types</option>
              <option value="item_enrichment">Item Enrichment</option>
              <option value="daily_brief">Daily Brief</option>
              <option value="connection_test">Connection Test</option>
              <option value="reprocess">Reprocess</option>
            </select>
            <label className="sr-only" htmlFor="ai-history-status-filter">
              Status filter
            </label>
            <select
              id="ai-history-status-filter"
              value={filters.status}
              onChange={(event) => {
                setRunPage(0)
                setFilters((current) => ({ ...current, status: event.target.value }))
              }}
              aria-label="Status filter"
              className="rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
            >
              <option value="">All statuses</option>
              <option value="queued">Queued</option>
              <option value="running">Running</option>
              <option value="ready">Ready</option>
              <option value="error">Error</option>
              <option value="skipped">Skipped</option>
            </select>
            <label className="sr-only" htmlFor="ai-history-trigger-filter">
              Trigger source filter
            </label>
            <select
              id="ai-history-trigger-filter"
              value={filters.triggerSource}
              onChange={(event) => {
                setRunPage(0)
                setFilters((current) => ({ ...current, triggerSource: event.target.value }))
              }}
              aria-label="Trigger source filter"
              className="rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
            >
              <option value="">All triggers</option>
              <option value="auto">Auto</option>
              <option value="manual">Manual</option>
              <option value="scheduled">Scheduled</option>
            </select>
            <label className="flex items-center gap-2 rounded border border-slate/20 bg-white/70 px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]/80">
              <input
                type="checkbox"
                checked={filters.onlyFailures}
                onChange={(event) => {
                  setRunPage(0)
                  setFilters((current) => ({ ...current, onlyFailures: event.target.checked }))
                }}
              />
              Failures only
            </label>
            <div className="rounded border border-slate/20 bg-slate/5 px-3 py-2 text-sm text-slate dark:border-cyan-900/40 dark:bg-white/[0.03] dark:text-white/65">
              Window {days}d{selectedModel !== 'all' ? ` · ${selectedModel}` : ' · all models'}
            </div>
          </div>

          <div
            className="mt-3 flex min-h-5 items-center text-xs font-semibold uppercase text-slate dark:text-white/55"
            aria-live="polite"
          >
            {runListStatusMessage ?? <span aria-hidden="true">&nbsp;</span>}
          </div>
          {runsQuery.isError && (
            <p className="mt-3 text-sm text-red-600">
              Failed to load AI runs. {(runsQuery.error as Error | undefined)?.message ?? ''}
            </p>
          )}

          <div className="mt-4 overflow-x-auto">
            <table
              className={`min-w-full text-sm transition-opacity ${runListPageLoading ? 'opacity-70' : ''}`}
              aria-busy={runListLoading || runListRefreshing || runListPageLoading}
            >
              <caption className="sr-only">AI task history. Select a run to inspect its details below.</caption>
              <thead className="text-left text-xs uppercase text-slate dark:text-white/55">
                <tr>
                  <th scope="col" className="pb-2">
                    <span className="sr-only">Select</span>
                  </th>
                  <th scope="col" className="pb-2">Type</th>
                  <th scope="col" className="pb-2">Article</th>
                  <th scope="col" className="pb-2">Trigger</th>
                  <th scope="col" className="pb-2">Timing</th>
                  <th scope="col" className="pb-2">Status</th>
                  <th scope="col" className="pb-2">Execution</th>
                  <th scope="col" className="pb-2">Tokens</th>
                  <th scope="col" className="pb-2">Error</th>
                  <th scope="col" className="pb-2">Inspect</th>
                </tr>
              </thead>
              <tbody>
                {runsQuery.data?.items.map((run) => (
                  <tr
                    key={run.id}
                    className={`cursor-pointer border-t border-slate/10 text-slate dark:border-cyan-900/30 dark:text-white/80 ${
                      selectedRunId === run.id ? 'bg-cyan/5 dark:bg-cyan/10' : ''
                    }`}
                    onClick={() => onSelectRun(run.id)}
                  >
                    <td className="py-2 pr-2 align-top">
                      <input
                        type="radio"
                        name="ai-selected-run"
                        className="mt-1 h-4 w-4"
                        aria-label={formatRunSelectionLabel(run)}
                        checked={selectedRunId === run.id}
                        onChange={() => onSelectRun(run.id)}
                      />
                    </td>
                    <td className="py-2">
                      <div className="font-semibold">{formatRunTaskLabel(run)}</div>
                      {run.feed_name && <div className="text-xs text-slate dark:text-white/55">{run.feed_name}</div>}
                    </td>
                    <td className="py-2">
                      {run.item_title ? (
                        <div className="max-w-xs">
                          <div className="font-semibold">{truncate(run.item_title, 72)}</div>
                          <div className="text-xs text-slate dark:text-white/55">
                            {run.item_published_at ? `Published ${formatTimestamp(run.item_published_at)}` : 'Article-linked run'}
                          </div>
                        </div>
                      ) : (
                        <span className="text-xs text-slate dark:text-white/55">—</span>
                      )}
                    </td>
                    <td className="py-2">{formatTriggerLabel(run.trigger_source)}</td>
                    <td className="py-2">
                      <div>{formatTimestamp(run.queued_at)}</div>
                      <div className="text-xs text-slate dark:text-white/55">
                        {run.finished_at ? `Finished ${formatTimestamp(run.finished_at)}` : 'In progress'} · {formatDuration(run.duration_ms)}
                      </div>
                    </td>
                    <td className="py-2">
                      <StatusPill tone={statusTone(run.status)} label={formatStatusLabel(run.status, run.reason)} />
                    </td>
                    <td className="py-2">
                      <div>{run.worker_name || 'api'}</div>
                      <div className="text-xs text-slate dark:text-white/55">{run.model || 'n/a'}</div>
                    </td>
                    <td className="py-2">{run.total_tokens?.toLocaleString() || 'n/a'}</td>
                    <td className="py-2 text-xs text-slate dark:text-white/60">{truncate(run.error || run.reason || '', 36) || '—'}</td>
                    <td className="py-2">
                      {canInspectProviderExchange(run) ? (
                        <button
                          type="button"
                          className="rounded border border-slate/30 px-2 py-1 text-xs font-semibold dark:border-cyan-900/40"
                          onClick={(event) => {
                            event.stopPropagation()
                            setInspectedRunId(run.id)
                          }}
                        >
                          Request / Response
                        </button>
                      ) : (
                        <span className="text-xs text-slate dark:text-white/55">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {!runListLoading && !runListPageLoading && !runsQuery.isError && !runsQuery.data?.items.length && (
            <EmptyInline>No AI runs matched the current filters.</EmptyInline>
          )}

          <div className="mt-4 flex items-center justify-between gap-3 text-sm">
            <span className="text-slate dark:text-white/60">
              {runCount > 0
                ? `Showing ${visibleRunOffset + 1}-${visibleRunOffset + runCount} of ${runTotal}`
                : `Showing 0 of ${runTotal}`}
            </span>
            <div className="flex items-center gap-2">
              <button
                type="button"
                className="rounded border border-slate/30 px-3 py-2 disabled:opacity-50 dark:border-cyan-900/40"
                onClick={() => setRunPage((current) => Math.max(0, current - 1))}
                disabled={runPage === 0 || runListLoading}
              >
                Previous
              </button>
              <span>
                Page {runPage + 1} / {totalPages}
              </span>
              <button
                type="button"
                className="rounded border border-slate/30 px-3 py-2 disabled:opacity-50 dark:border-cyan-900/40"
                onClick={() => setRunPage((current) => Math.min(totalPages - 1, current + 1))}
                disabled={runListLoading || runPage >= totalPages - 1}
              >
                Next
              </button>
            </div>
          </div>
        </Panel>
      </OverviewSection>

      <div ref={selectedRunSectionRef}>
        <OverviewSection
          title="Selected Run"
          description="Inspect the currently selected run, its event timeline, request metadata, and any related article or daily-brief context."
        >
          <Panel title="Run Detail" subtitle="Selected run timeline, metadata, and related sources.">
          {runDetailQuery.isLoading && <p className="text-sm text-slate dark:text-white/70">Loading run detail...</p>}
          {runDetailQuery.isError && (
            <p className="text-sm text-red-600">
              Failed to load run detail. {(runDetailQuery.error as Error | undefined)?.message ?? ''}
            </p>
          )}
          {!selectedRun && <EmptyInline>Select a run to inspect it.</EmptyInline>}
          {selectedRun && (
            <div className="space-y-4">
              <div className="rounded-xl border border-slate/20 bg-white/70 p-3 dark:border-cyan-900/40 dark:bg-[#072019]/80">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <p className="text-sm font-semibold">{formatRunTaskLabel(selectedRun)}</p>
                    <p className="text-xs text-slate dark:text-white/60">
                      {formatTriggerLabel(selectedRun.trigger_source)} · {selectedRun.actor_email || selectedRun.worker_name || 'system'}
                    </p>
                    {selectedRun.task_type === 'reprocess' && (
                      <p className="mt-1 text-xs text-slate dark:text-white/60">{describeRunScope(selectedRun)}</p>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    {canInspectProviderExchange(selectedRun) && (
                      <button
                        type="button"
                        className="rounded border border-slate/30 px-3 py-2 text-xs font-semibold dark:border-cyan-900/40"
                        onClick={() => setInspectedRunId(selectedRun.id)}
                      >
                        View Request / Response
                      </button>
                    )}
                    {canCancelRun(selectedRun) && (
                      <button
                        type="button"
                        className="rounded border border-slate/30 px-3 py-2 text-xs font-semibold disabled:opacity-50 dark:border-cyan-900/40"
                        onClick={() => onCancelRun(selectedRun)}
                        disabled={cancelingRunId === selectedRun.id}
                      >
                        {cancelingRunId === selectedRun.id ? 'Working...' : cancelActionLabel(selectedRun)}
                      </button>
                    )}
                    <StatusPill
                      tone={statusTone(selectedRun.status)}
                      label={formatStatusLabel(selectedRun.status, selectedRun.reason)}
                    />
                  </div>
                </div>
                <dl className="mt-3 space-y-2 text-sm">
                  <Metric label="Queued" value={formatTimestamp(selectedRun.queued_at)} />
                  <Metric label="Started" value={selectedRun.started_at ? formatTimestamp(selectedRun.started_at) : 'n/a'} />
                  <Metric label="Finished" value={selectedRun.finished_at ? formatTimestamp(selectedRun.finished_at) : 'n/a'} />
                  <Metric label="Duration" value={formatDuration(selectedRun.duration_ms)} />
                  <Metric label="Worker" value={selectedRun.worker_name || 'api'} />
                  <Metric label="Model" value={selectedRun.model || 'n/a'} />
                  <Metric label="Prompt size" value={selectedRun.prompt_char_count ?? 'n/a'} />
                  <Metric label="Response size" value={selectedRun.response_char_count ?? 'n/a'} />
                  <Metric label="Input text chars" value={selectedRun.input_text_chars ?? 'n/a'} />
                  <Metric label="Tokens" value={selectedRun.total_tokens ?? 'n/a'} />
                </dl>
                {selectedRun.reason && (
                  <p className="mt-3 rounded border border-slate/20 bg-slate/5 px-3 py-2 text-xs text-slate dark:border-cyan-900/40 dark:bg-white/[0.03] dark:text-white/70">
                    Reason: {selectedRun.reason}
                  </p>
                )}
                {selectedRun.error && (
                  <p className="mt-3 rounded border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-700 dark:text-red-300">
                    {selectedRun.error}
                  </p>
                )}
                {selectedRun.task_type === 'reprocess' && (
                  <div className="mt-3">
                    <p className="text-xs font-semibold uppercase text-slate dark:text-white/55">Progress</p>
                    <ProgressBar
                      className="mt-2"
                      value={selectedRun.processed_count}
                      max={selectedRun.target_count || Math.max(selectedRun.processed_count, 1)}
                    />
                    <p className="mt-2 text-xs text-slate dark:text-white/60">
                      Processed {selectedRun.processed_count}/{selectedRun.target_count ?? '?'} · Success {selectedRun.success_count} ·
                      Errors {selectedRun.error_count} · Skipped {selectedRun.skipped_count}
                    </p>
                  </div>
                )}
              </div>

              {selectedRun.task_type === 'reprocess' && (
                <RunArticlesSection
                  parentRun={selectedRun}
                  childRunsQuery={childRunsQuery}
                  visibleCount={articlePreviewLimit}
                  onInspectRun={(runId) => setInspectedRunId(runId)}
                  onShowMore={() =>
                    setArticlePreviewLimit((current) => {
                      const total = childRunsQuery.data?.total ?? current
                      return Math.min(total, current + 20)
                    })
                  }
                  onShowLess={() => setArticlePreviewLimit(8)}
                />
              )}

              <div>
                <p className="text-xs font-semibold uppercase text-slate dark:text-white/55">Event Timeline</p>
                <div className="mt-2 space-y-2">
                  {runDetailQuery.data?.events.map((event) => (
                    <div key={event.id} className="rounded-lg border border-slate/10 px-3 py-2 text-sm dark:border-cyan-900/30">
                      <div className="flex items-center justify-between gap-3">
                        <span className="font-semibold">{event.event_type}</span>
                        <span className="text-xs text-slate dark:text-white/60">{formatTimestamp(event.created_at)}</span>
                      </div>
                      {event.message && <p className="mt-1 text-sm text-slate dark:text-white/70">{event.message}</p>}
                    </div>
                  ))}
                </div>
              </div>

              {Object.keys(selectedRun.metadata || {}).length > 0 && (
                <div>
                  <p className="text-xs font-semibold uppercase text-slate dark:text-white/55">Request / Response Summary</p>
                  <div className="mt-2 rounded-xl border border-slate/20 bg-white/70 p-3 text-xs dark:border-cyan-900/40 dark:bg-[#072019]/80">
                    <dl className="space-y-2">
                      {Object.entries(selectedRun.metadata).map(([key, value]) => (
                        <Metric key={key} label={humanizeKey(key)} value={formatMetadataValue(value)} />
                      ))}
                    </dl>
                  </div>
                </div>
              )}

              {selectedRun.daily_brief_id && (
                <div>
                  <p className="text-xs font-semibold uppercase text-slate dark:text-white/55">Daily Brief Source Items</p>
                  <div className="mt-2 space-y-2">
                    {briefSourcesLoading && <EmptyInline>Loading source log for this brief...</EmptyInline>}
                    {!briefSourcesLoading && briefSourcesErrorMessage && (
                      <p className="text-sm text-red-600">Failed to load the source log. {briefSourcesErrorMessage}</p>
                    )}
                    {briefSources.map((source) => (
                      <div key={source.id} className="rounded-lg border border-slate/10 px-3 py-2 text-sm dark:border-cyan-900/30">
                        <div className="flex items-start justify-between gap-3">
                          <div>
                            <p className="font-semibold">{source.title_snapshot}</p>
                            <p className="mt-1 text-xs text-slate dark:text-white/60">
                              {source.feed_name_snapshot || 'Unknown feed'}
                              {source.classification_snapshot ? ` · ${source.classification_snapshot}` : ''}
                            </p>
                          </div>
                          <StatusPill tone={source.included ? 'success' : 'neutral'} label={source.included ? 'Included' : 'Excluded'} />
                        </div>
                        {!source.included && source.exclusion_reason && (
                          <p className="mt-2 text-xs text-slate dark:text-white/60">Reason: {source.exclusion_reason}</p>
                        )}
                      </div>
                    ))}
                    {!briefSourcesLoading && !briefSourcesErrorMessage && !briefSources.length && (
                      <EmptyInline>No source log recorded for this brief.</EmptyInline>
                    )}
                  </div>
                </div>
              )}
            </div>
          )}
          </Panel>

          <ProviderExchangeModal
            run={inspectedRun}
            event={inspectedProviderEvent}
            isLoading={inspectedRunDetailQuery.isLoading}
            errorMessage={(inspectedRunDetailQuery.error as Error | undefined)?.message ?? ''}
            onClose={() => setInspectedRunId(null)}
          />
        </OverviewSection>
      </div>
    </div>
  )
}

function ConfigurationTab({
  draft,
  setDraft,
  draftDirty,
  settings,
  readiness,
  isLoading,
  isError,
  errorMessage,
  savePending,
  saveDisabled,
  saveDisabledReason,
  validation,
  onSave,
  onTestConnection,
  testPending,
  testDisabledReason,
  testResult,
  promptHistory,
  manualActions,
}: {
  draft: AISettingsDraft
  setDraft: Dispatch<SetStateAction<AISettingsDraft>>
  draftDirty: boolean
  settings: AISettings | undefined
  readiness: string | null
  isLoading: boolean
  isError: boolean
  errorMessage: string
  savePending: boolean
  saveDisabled: boolean
  saveDisabledReason: string | null
  validation: AISettingsDraftValidation
  onSave: () => void
  onTestConnection: () => void
  testPending: boolean
  testDisabledReason: string | null
  testResult: AITestConnectionResponse | null
  promptHistory: AIAuditEntryResponse[]
  manualActions: AIAuditEntryResponse[]
}) {
  const testSavedConnectionDisabled = testPending || draftDirty || !settings?.ai_configured || Boolean(testDisabledReason)
  const providerTestMessage = draftDirty
    ? 'Save your draft changes first. Test Saved Connection only checks the last saved provider settings.'
    : testDisabledReason
      ? testDisabledReason
      : settings?.ai_configured
        ? 'Test the saved provider configuration. Unsaved draft changes are not included.'
        : 'Save the provider settings before testing the saved connection.'

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(320px,1fr)]">
      <div className="space-y-4">
        {isLoading && (
          <div className="rounded-xl border border-slate/20 bg-white/80 p-4 text-sm dark:border-cyan-900/40 dark:bg-[#041612]/90">
            Loading AI settings...
          </div>
        )}
        {isError && (
          <div className="rounded-xl border border-red-500/20 bg-red-500/10 p-4 text-sm text-red-700 dark:text-red-300">
            Failed to load AI settings. {errorMessage}
          </div>
        )}

        <Panel title="Provider" subtitle="ThreatLens currently speaks to one OpenAI-compatible chat endpoint. Secrets stay in the environment.">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate/15 bg-slate/5 px-3 py-3 dark:border-cyan-900/30 dark:bg-white/[0.03]">
            <div className="text-sm text-slate dark:text-white/70">
              {providerTestMessage}
            </div>
            <button
              type="button"
              className="rounded border border-slate/30 px-3 py-2 text-sm font-semibold disabled:opacity-50 dark:border-cyan-900/40"
              onClick={onTestConnection}
              disabled={testSavedConnectionDisabled}
            >
              {testPending ? 'Testing...' : 'Test Saved Connection'}
            </button>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            <Field label="Base URL">
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={draft.base_url}
                onChange={(event) => updateDraft(setDraft, 'base_url', event.target.value)}
                aria-invalid={Boolean(validation.base_url)}
              />
              <FieldError message={validation.base_url} />
            </Field>
            <Field label="Model">
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={draft.model}
                onChange={(event) => updateDraft(setDraft, 'model', event.target.value)}
                aria-invalid={Boolean(validation.model)}
              />
              <FieldError message={validation.model} />
            </Field>
            <Field label="Temperature">
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={draft.temperature}
                onChange={(event) => updateDraft(setDraft, 'temperature', event.target.value)}
                inputMode="decimal"
                aria-invalid={Boolean(validation.temperature)}
              />
              <FieldError message={validation.temperature} />
            </Field>
            <Field label="Max Completion Tokens">
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={draft.max_completion_tokens}
                onChange={(event) => updateDraft(setDraft, 'max_completion_tokens', event.target.value)}
                inputMode="numeric"
                aria-invalid={Boolean(validation.max_completion_tokens)}
              />
              <FieldError message={validation.max_completion_tokens} />
            </Field>
            <Field label="Request Timeout Seconds" className="md:col-span-2">
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={draft.request_timeout_seconds}
                onChange={(event) => updateDraft(setDraft, 'request_timeout_seconds', event.target.value)}
                inputMode="numeric"
                aria-invalid={Boolean(validation.request_timeout_seconds)}
              />
              <FieldError message={validation.request_timeout_seconds} />
            </Field>
            <Field label="Max Retry Attempts" className="md:col-span-2">
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={draft.request_max_retries}
                onChange={(event) => updateDraft(setDraft, 'request_max_retries', event.target.value)}
                inputMode="numeric"
                aria-invalid={Boolean(validation.request_max_retries)}
              />
              <FieldError message={validation.request_max_retries} />
              <span className="mt-1 block text-xs text-slate dark:text-white/60">
                Retry malformed or failed AI responses up to X additional times before marking the run as failed.
              </span>
            </Field>
          </div>
          {testResult && (
            <div className="mt-4 rounded-xl border border-slate/20 bg-white/70 p-3 text-sm dark:border-cyan-900/40 dark:bg-[#072019]/80">
              <p className="font-semibold">
                {testResult.skipped ? 'Connection test paused' : testResult.success ? 'Connection succeeded' : 'Connection failed'}
              </p>
              <p className="mt-1 text-slate dark:text-white/70">
                Model: {testResult.model || 'unknown'}
                {typeof testResult.latency_ms === 'number' ? `, ${testResult.latency_ms} ms` : ''}
              </p>
              {testResult.skipped && (
                <p className="mt-1 text-slate dark:text-white/70">
                  Running: {testResult.running_task_count ?? 0}, queued: {testResult.queued_task_count ?? 0}
                </p>
              )}
              {testResult.error && <p className="mt-1 text-red-600">{testResult.error}</p>}
            </div>
          )}
        </Panel>

        <Panel title="Feature Controls" subtitle="Enable the AI features that should run and tune the relevance thresholds they rely on.">
          <div className="grid gap-3 md:grid-cols-2">
            <CheckboxRow label="AI article summaries" checked={draft.summary_enabled} onChange={(checked) => updateDraft(setDraft, 'summary_enabled', checked)} />
            <CheckboxRow label="AI relevance scoring" checked={draft.relevance_enabled} onChange={(checked) => updateDraft(setDraft, 'relevance_enabled', checked)} />
            <CheckboxRow
              label="Daily brief generation and panel"
              checked={draft.daily_brief_enabled}
              onChange={(checked) => updateDraft(setDraft, 'daily_brief_enabled', checked)}
            />
            <CheckboxRow label="Auto-enrich new items" checked={draft.auto_enrich_new_items} onChange={(checked) => updateDraft(setDraft, 'auto_enrich_new_items', checked)} />
            <Field label="Medium Relevance Threshold">
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={draft.relevance_medium_threshold}
                onChange={(event) => updateDraft(setDraft, 'relevance_medium_threshold', event.target.value)}
                inputMode="decimal"
                aria-invalid={Boolean(validation.relevance_medium_threshold)}
              />
              <FieldError message={validation.relevance_medium_threshold} />
            </Field>
            <Field label="High Relevance Threshold">
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={draft.relevance_high_threshold}
                onChange={(event) => updateDraft(setDraft, 'relevance_high_threshold', event.target.value)}
                inputMode="decimal"
                aria-invalid={Boolean(validation.relevance_high_threshold)}
              />
              <FieldError message={validation.relevance_high_threshold} />
            </Field>
          </div>
        </Panel>

        <Panel title="Daily Brief Settings" subtitle="Control when the scheduled daily brief runs, how much content it reviews, and how much history to keep.">
          <div className="grid gap-3 md:grid-cols-2">
            <Field label="Daily Brief Run Time (UTC)">
              <input
                type="time"
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={draft.daily_brief_run_time_utc}
                onChange={(event) => updateDraft(setDraft, 'daily_brief_run_time_utc', event.target.value || '09:00')}
                aria-invalid={Boolean(validation.daily_brief_run_time_utc)}
              />
              <FieldError message={validation.daily_brief_run_time_utc} />
              <span className="mt-1 block text-xs text-slate dark:text-white/60">
                Scheduled checks run every 5 minutes and fire after this UTC time. Manual queueing stays available in Operations.
              </span>
            </Field>
            <Field label="Daily Brief Window Hours">
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={draft.daily_brief_window_hours}
                onChange={(event) => updateDraft(setDraft, 'daily_brief_window_hours', event.target.value)}
                inputMode="numeric"
                aria-invalid={Boolean(validation.daily_brief_window_hours)}
              />
              <FieldError message={validation.daily_brief_window_hours} />
              <span className="mt-1 block text-xs text-slate dark:text-white/60">
                How far back ThreatLens looks when building the scheduled brief.
              </span>
            </Field>
            <Field label="Daily Brief Max Articles">
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={draft.daily_brief_max_items}
                onChange={(event) => updateDraft(setDraft, 'daily_brief_max_items', event.target.value)}
                inputMode="numeric"
                aria-invalid={Boolean(validation.daily_brief_max_items)}
              />
              <FieldError message={validation.daily_brief_max_items} />
              <span className="mt-1 block text-xs text-slate dark:text-white/60">
                Cap how many articles are handed to the model for a single brief.
              </span>
            </Field>
            <Field label="Retained Daily Briefings">
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={draft.daily_brief_history_limit}
                onChange={(event) => updateDraft(setDraft, 'daily_brief_history_limit', event.target.value)}
                inputMode="numeric"
                aria-invalid={Boolean(validation.daily_brief_history_limit)}
              />
              <FieldError message={validation.daily_brief_history_limit} />
              <span className="mt-1 block text-xs text-slate dark:text-white/60">
                Keep only the most recent X daily briefings for dashboard selection.
              </span>
            </Field>
          </div>
        </Panel>

        <Panel title="Company Context" subtitle="This context is global so relevance scoring stays consistent across users.">
          <div className="grid gap-3 md:grid-cols-2">
            <Field label="Company Name">
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={draft.company_name}
                onChange={(event) => updateDraft(setDraft, 'company_name', event.target.value)}
                aria-invalid={Boolean(validation.company_name)}
              />
              <FieldError message={validation.company_name} />
            </Field>
            <Field label="Industry">
              <input
                className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
                value={draft.company_industry}
                onChange={(event) => updateDraft(setDraft, 'company_industry', event.target.value)}
                aria-invalid={Boolean(validation.company_industry)}
              />
              <FieldError message={validation.company_industry} />
            </Field>
            <TextAreaList label="Regions" value={draft.company_regions} onChange={(value) => updateDraft(setDraft, 'company_regions', value)} />
            <TextAreaList label="Technology Stack" value={draft.company_stack} onChange={(value) => updateDraft(setDraft, 'company_stack', value)} />
            <TextAreaList label="Priority Topics" value={draft.company_priority_topics} onChange={(value) => updateDraft(setDraft, 'company_priority_topics', value)} />
            <TextAreaList label="Keywords" value={draft.company_keywords} onChange={(value) => updateDraft(setDraft, 'company_keywords', value)} />
            <TextAreaList label="Exclusions" value={draft.company_exclusions} onChange={(value) => updateDraft(setDraft, 'company_exclusions', value)} />
            <Field label="Additional Company Context" className="md:col-span-2">
              <textarea
                className="mt-1 h-28 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                value={draft.company_profile_text}
                onChange={(event) => updateDraft(setDraft, 'company_profile_text', event.target.value)}
                aria-invalid={Boolean(validation.company_profile_text)}
              />
              <FieldError message={validation.company_profile_text} />
            </Field>
          </div>
        </Panel>

        <Panel title="Prompt Tuning" subtitle="Built-in defaults stay visible here, but you can edit and save them directly.">
          <div className="grid gap-3">
            <PromptArea
              label="Item Enrichment System Prompt"
              value={draft.item_enrichment_system_prompt}
              onChange={(value) => updateDraft(setDraft, 'item_enrichment_system_prompt', value)}
              error={validation.item_enrichment_system_prompt}
            />
            <PromptArea
              label="Daily Brief System Prompt"
              value={draft.daily_brief_system_prompt}
              onChange={(value) => updateDraft(setDraft, 'daily_brief_system_prompt', value)}
              error={validation.daily_brief_system_prompt}
            />
            <PromptArea
              label="Global Instructions"
              value={draft.global_instructions}
              onChange={(value) => updateDraft(setDraft, 'global_instructions', value)}
              error={validation.global_instructions}
            />
            <PromptArea
              label="Item Summary Instructions"
              value={draft.item_summary_instructions}
              onChange={(value) => updateDraft(setDraft, 'item_summary_instructions', value)}
              error={validation.item_summary_instructions}
            />
            <PromptArea
              label="Relevance Instructions"
              value={draft.relevance_instructions}
              onChange={(value) => updateDraft(setDraft, 'relevance_instructions', value)}
              error={validation.relevance_instructions}
            />
            <PromptArea
              label="Daily Brief Instructions"
              value={draft.daily_brief_instructions}
              onChange={(value) => updateDraft(setDraft, 'daily_brief_instructions', value)}
              error={validation.daily_brief_instructions}
            />
          </div>
        </Panel>

        <div className="grid gap-4 lg:grid-cols-2">
          <Panel title="Prompt History" subtitle="Recent AI configuration and prompt changes.">
            <AuditPreviewList entries={promptHistory} emptyLabel="No AI prompt changes yet." />
          </Panel>
          <Panel title="Manual Actions" subtitle="Recent admin-triggered AI actions.">
            <AuditPreviewList entries={manualActions} emptyLabel="No manual actions yet." />
          </Panel>
        </div>
      </div>

      <div className="space-y-4">
        <Panel title="Configuration Status" subtitle={readiness ?? 'Loading runtime state...'}>
          <dl className="space-y-2 text-sm">
            <Metric label="Configured" value={settings?.ai_configured ? 'Yes' : 'No'} />
            <Metric label="API Key In Env" value={settings?.api_key_configured ? 'Yes' : 'No / Optional'} />
            <Metric label="Model" value={settings?.model || 'Not configured'} />
            <Metric label="Retry attempts" value={settings?.request_max_retries ?? 0} />
            <Metric
              label="Daily brief schedule"
              value={settings ? formatUtcTime(settings.daily_brief_schedule_hour_utc, settings.daily_brief_schedule_minute_utc) : '09:00 UTC'}
            />
            <Metric label="Created" value={settings?.created_at ? formatTimestamp(settings.created_at) : 'n/a'} />
            <Metric label="Updated" value={settings?.updated_at ? formatTimestamp(settings.updated_at) : 'n/a'} />
          </dl>
        </Panel>

        <div className="sticky top-4 rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
          <h3 className="font-display text-lg">Save Changes</h3>
          <p className="mt-1 text-sm text-slate dark:text-white/70">
            Provider, feature, company-context, and prompt changes affect future AI runs and are recorded in prompt history.
          </p>
          <button
            type="button"
            className="mt-4 w-full rounded bg-ink px-3 py-2 text-sm font-semibold text-white disabled:opacity-50 dark:bg-cyan dark:text-slate-950"
            onClick={onSave}
            disabled={savePending || saveDisabled}
            title={saveDisabledReason ?? undefined}
          >
            {savePending ? 'Saving...' : 'Save Settings'}
          </button>
          {saveDisabledReason && (
            <p role="status" aria-live="polite" aria-atomic="true" className="mt-2 text-xs text-amber-700 dark:text-amber-300">
              {saveDisabledReason}
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

function Panel({
  title,
  subtitle,
  children,
}: {
  title: string
  subtitle?: string
  children: ReactNode
}) {
  return (
    <section className="rounded-xl border border-slate/20 bg-white/80 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
      <h3 className="font-display text-lg">{title}</h3>
      {subtitle && <p className="mt-1 text-sm text-slate dark:text-white/70">{subtitle}</p>}
      <div className="mt-3">{children}</div>
    </section>
  )
}

function FieldError({ message }: { message?: string }) {
  if (!message) {
    return null
  }
  return <p className="mt-1 text-xs text-red-600 dark:text-red-300">{message}</p>
}

function OverviewSection({
  title,
  description,
  children,
}: {
  title: string
  description: string
  children: ReactNode
}) {
  return (
    <section className="rounded-2xl border border-slate/20 bg-white/70 p-5 dark:border-cyan-900/40 dark:bg-[#03130f]/80">
      <div className="border-b border-slate/15 pb-3 dark:border-cyan-900/30">
        <h3 className="font-display text-xl">{title}</h3>
        <p className="mt-1 text-sm text-slate dark:text-white/70">{description}</p>
      </div>
      <div className="mt-4 space-y-4">{children}</div>
    </section>
  )
}

function TabButton({
  id,
  controls,
  active,
  onClick,
  onKeyDown,
  children,
  fullWidth = false,
}: {
  id: string
  controls: string
  active: boolean
  onClick: () => void
  onKeyDown: (event: ReactKeyboardEvent<HTMLButtonElement>) => void
  children: ReactNode
  fullWidth?: boolean
}) {
  return (
    <button
      type="button"
      id={id}
      role="tab"
      aria-selected={active}
      aria-controls={controls}
      tabIndex={active ? 0 : -1}
      onClick={onClick}
      onKeyDown={onKeyDown}
      className={
        fullWidth
          ? `block rounded px-3 py-2 text-center text-sm transition lg:text-left ${
              active
                ? 'bg-cyan/15 text-cyan dark:bg-cyan-900/35 dark:text-cyan-300'
                : 'text-slate hover:bg-slate/10 dark:text-slate-200 dark:hover:bg-white/[0.06]'
            }`
          : `rounded-full px-3 py-2 text-sm font-semibold transition ${
              active
                ? 'bg-ink text-white dark:bg-cyan dark:text-slate-950'
                : 'border border-slate/20 bg-white/70 text-slate dark:border-cyan-900/40 dark:bg-[#072019]/80 dark:text-white/75'
            }`
      }
    >
      {children}
    </button>
  )
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-slate/20 bg-white/80 px-4 py-3 dark:border-cyan-900/40 dark:bg-[#041612]/90">
      <p className="text-xs uppercase text-slate dark:text-white/55">{label}</p>
      <p className="mt-1 text-xl font-semibold">{value}</p>
    </div>
  )
}

function MiniStat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg border border-slate/10 bg-slate/5 px-3 py-2 dark:border-cyan-900/30 dark:bg-white/[0.03]">
      <p className="text-xs uppercase text-slate dark:text-white/55">{label}</p>
      <p className="mt-1 text-base font-semibold">{value}</p>
    </div>
  )
}

function Metric({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <dt className="text-slate dark:text-white/65">{label}</dt>
      <dd className="text-right font-semibold">{value}</dd>
    </div>
  )
}

function StatusPill({ label, tone }: { label: string; tone: 'success' | 'warning' | 'danger' | 'neutral' | 'info' }) {
  const toneClass =
    tone === 'success'
      ? 'tl-chip-success'
      : tone === 'warning'
        ? 'tl-chip-warning'
        : tone === 'danger'
          ? 'tl-chip-danger'
          : tone === 'info'
            ? 'tl-chip-info'
            : 'tl-chip-neutral'
  return <span className={`tl-chip uppercase ${toneClass}`}>{label}</span>
}

function ProgressBar({ value, max, className = '' }: { value: number; max: number; className?: string }) {
  const pct = max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0
  return (
    <div className={`h-2 rounded-full bg-slate-200 dark:bg-[#072019] ${className}`}>
      <div className="h-2 rounded-full bg-cyan" style={{ width: `${pct}%` }} />
    </div>
  )
}

function TimeSeriesBars({
  points,
  valueKey,
  accentClass,
  secondaryKey,
  secondaryClass,
}: {
  points: AIOpsOverviewResponse['time_series']
  valueKey: 'requests' | 'total_tokens'
  accentClass: string
  secondaryKey?: 'failures'
  secondaryClass?: string
}) {
  const maxPrimary = Math.max(...points.map((point) => Number(point[valueKey]) || 0), 1)
  const maxSecondary = secondaryKey ? Math.max(...points.map((point) => Number(point[secondaryKey]) || 0), 1) : 1

  return (
    <div className="space-y-2">
      <div className="flex h-36 items-end gap-1">
        {points.map((point) => {
          const primaryHeight = `${Math.max(4, ((Number(point[valueKey]) || 0) / maxPrimary) * 100)}%`
          const secondaryHeight = secondaryKey ? `${Math.max(0, ((Number(point[secondaryKey]) || 0) / maxSecondary) * 38)}%` : '0%'
          return (
            <div key={String(point.bucket)} className="flex min-w-0 flex-1 flex-col justify-end gap-1">
              {secondaryKey && secondaryClass && <div className={`rounded-t ${secondaryClass}`} style={{ height: secondaryHeight }} />}
              <div className={`rounded-t ${accentClass}`} style={{ height: primaryHeight }} />
            </div>
          )
        })}
      </div>
      <div className="flex justify-between gap-2 text-[11px] text-slate dark:text-white/55">
        <span>{points[0]?.bucket ? formatDateOnly(String(points[0].bucket)) : ''}</span>
        <span>{points[points.length - 1]?.bucket ? formatDateOnly(String(points[points.length - 1].bucket)) : ''}</span>
      </div>
    </div>
  )
}

function LiveTaskCard({ task }: { task: AILiveTaskResponse }) {
  return (
    <div className="rounded-lg border border-slate/10 px-3 py-2 text-sm dark:border-cyan-900/30">
      <div className="flex items-center justify-between gap-3">
        <span className="font-semibold">{formatTaskTypeLabel(task.task_name)}</span>
        <span className="text-xs text-slate dark:text-white/60">{task.worker_name}</span>
      </div>
      <p className="mt-1 text-xs text-slate dark:text-white/60">
        {task.state}
        {task.eta ? ` · eta ${task.eta}` : ''}
        {task.received_at ? ` · received ${task.received_at}` : ''}
      </p>
    </div>
  )
}

function AuditPreviewList({ entries, emptyLabel }: { entries: AIAuditEntryResponse[]; emptyLabel: string }) {
  if (!entries.length) {
    return <EmptyInline>{emptyLabel}</EmptyInline>
  }
  return (
    <div className="space-y-2">
      {entries.map((entry) => (
        <div key={entry.id} className="rounded-lg border border-slate/10 px-3 py-2 text-sm dark:border-cyan-900/30">
          {(() => {
            const changedFields = Array.isArray(entry.metadata.changed_fields)
              ? (entry.metadata.changed_fields as unknown[]).filter(
                  (field): field is string => typeof field === 'string' && field.trim().length > 0,
                )
              : []
            return (
              <>
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="font-semibold">{entry.action}</p>
              <p className="mt-1 text-xs text-slate dark:text-white/60">{entry.actor_email || 'system'}</p>
            </div>
            <span className="text-xs text-slate dark:text-white/60">{formatTimestamp(entry.created_at)}</span>
          </div>
          {changedFields.length > 0 && (
            <p className="mt-2 text-xs text-slate dark:text-white/60">
              Changed: {changedFields.join(', ')}
            </p>
          )}
              </>
            )
          })()}
        </div>
      ))}
    </div>
  )
}

function CheckboxRow({
  label,
  checked,
  onChange,
}: {
  label: string
  checked: boolean
  onChange: (checked: boolean) => void
}) {
  return (
    <label className="flex items-center justify-between gap-3 rounded border border-slate/20 bg-white/60 px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]/80">
      <span>{label}</span>
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
    </label>
  )
}

function TextAreaList({
  label,
  value,
  onChange,
}: {
  label: string
  value: string
  onChange: (value: string) => void
}) {
  return (
    <Field label={label}>
      <textarea
        className="mt-1 h-24 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </Field>
  )
}

function PromptArea({
  label,
  value,
  onChange,
  error,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  error?: string
}) {
  return (
    <Field label={label}>
      <textarea
        className="mt-1 h-28 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        aria-invalid={Boolean(error)}
      />
      <FieldError message={error} />
    </Field>
  )
}

function Field({
  label,
  children,
  className = '',
}: {
  label: string
  children: ReactNode
  className?: string
}) {
  return (
    <label className={`text-sm ${className}`}>
      <span className="font-semibold">{label}</span>
      {children}
    </label>
  )
}

function EmptyInline({ children }: { children: ReactNode }) {
  return <p className="text-sm text-slate dark:text-white/60">{children}</p>
}

function invalidateAiQueries(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: ['ai'] })
}

function markAiQueriesStale(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: ['ai'], refetchType: 'none' })
}

function updateDraft<K extends keyof AISettingsDraft>(
  setter: Dispatch<SetStateAction<AISettingsDraft>>,
  key: K,
  value: AISettingsDraft[K],
) {
  setter((current) => ({ ...current, [key]: value }))
}

function formatTimestamp(value: string | null | undefined) {
  return value ? formatDateTime(value) : 'unknown'
}

function formatTaskTypeLabel(value: string) {
  if (value === 'item_enrichment') return 'Item Enrichment'
  if (value === 'daily_brief') return 'Daily Brief'
  if (value === 'connection_test') return 'Connection Test'
  if (value === 'reprocess') return 'Reprocess'
  return value
}

function isDailyBriefBackfillRun(run: AITaskRunResponse) {
  return run.task_type === 'reprocess' && run.metadata.scope === 'daily_brief_backfill'
}

function formatRunTaskLabel(run: AITaskRunResponse) {
  return isDailyBriefBackfillRun(run) ? 'Daily Brief' : formatTaskTypeLabel(run.task_type)
}

function formatTriggerLabel(value: string) {
  if (value === 'auto') return 'Auto'
  if (value === 'manual') return 'Manual'
  if (value === 'scheduled') return 'Scheduled'
  return value
}

function formatStatusLabel(value: string, reason?: string | null) {
  if (value === 'skipped' && reason === 'canceled') return 'Canceled'
  if (value === 'ready') return 'Ready'
  if (value === 'error') return 'Error'
  if (value === 'queued') return 'Queued'
  if (value === 'running') return 'Running'
  if (value === 'skipped') return 'Skipped'
  return value
}

function formatFeatureKey(value: string) {
  if (value === 'daily_brief') return 'Daily Brief'
  if (value === 'auto_enrichment') return 'Auto-Enrichment'
  return value.replace(/_/g, ' ').replace(/\b\w/g, (char) => char.toUpperCase())
}

function statusTone(value: string): 'success' | 'warning' | 'danger' | 'neutral' | 'info' {
  if (value === 'ready') return 'success'
  if (value === 'error') return 'danger'
  if (value === 'running' || value === 'queued') return 'info'
  return 'neutral'
}

function formatAgeSeconds(value: number) {
  if (value < 60) return `${value}s`
  if (value < 3600) return `${Math.round(value / 60)}m`
  return `${(value / 3600).toFixed(1)}h`
}

function formatUtcTime(hour: number, minute: number) {
  return `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')} UTC`
}

function formatDuration(value: number | null) {
  if (value == null) return 'n/a'
  if (value < 1000) return `${value} ms`
  if (value < 60_000) return `${(value / 1000).toFixed(1)} s`
  return `${(value / 60_000).toFixed(1)} min`
}

function remainingCount(run: AITaskRunResponse) {
  if (!run.target_count) {
    return '?'
  }
  return Math.max(0, run.target_count - run.processed_count)
}

function shouldUseLookbackWindow(
  startTime: string,
  endTime: string,
  selectedItems: ItemListEntry[],
) {
  return !startTime && !endTime && selectedItems.length === 0
}

function canCancelRun(run: AITaskRunResponse) {
  return !run.finished_at && (run.status === 'queued' || run.status === 'running')
}

function cancelActionLabel(run: AITaskRunResponse) {
  return run.status === 'queued' ? 'Remove From Queue' : 'Stop Task'
}

function canInspectProviderExchange(run: AITaskRunResponse) {
  return run.task_type === 'item_enrichment' || run.task_type === 'daily_brief' || run.task_type === 'connection_test'
}

function formatRunSelectionLabel(run: AITaskRunResponse) {
  const scope = run.item_title?.trim() || run.feed_name?.trim() || run.model?.trim() || formatTimestamp(run.queued_at)
  return `Select ${formatRunTaskLabel(run)} run ${scope}`
}

function describeRunScope(run: AITaskRunResponse) {
  if (run.task_type === 'daily_brief') {
    return run.status === 'queued' || run.status === 'running'
      ? 'Manual daily brief run queued for generation.'
      : 'Daily brief run.'
  }
  if (run.task_type !== 'reprocess') {
    return run.reason || 'AI task in progress.'
  }

  if (isDailyBriefBackfillRun(run)) {
    const days = asNumber(run.metadata.days) ?? run.target_count
    if (days === 1) {
      return 'Reprocessing today\'s daily brief.'
    }
    return days
      ? `Reprocessing daily briefs for the last ${days} days, ending today.`
      : 'Reprocessing daily briefs for recent days.'
  }

  const days = asNumber(run.metadata.days)
  const limit = asNumber(run.metadata.limit)
  const explicitItemCount = asNumber(run.metadata.explicit_item_count)
  const feedIds = Array.isArray(run.metadata.feed_ids) ? (run.metadata.feed_ids as unknown[]) : []
  const startTime = typeof run.metadata.start_time === 'string' ? run.metadata.start_time : null
  const endTime = typeof run.metadata.end_time === 'string' ? run.metadata.end_time : null

  if (explicitItemCount && explicitItemCount > 0) {
    return `Reprocessing ${explicitItemCount} selected article${explicitItemCount === 1 ? '' : 's'}.`
  }

  const parts: string[] = []
  if (days) {
    parts.push(`last ${days} day${days === 1 ? '' : 's'}`)
  }
  if (limit) {
    parts.push(`up to ${limit} articles`)
  }
  if (feedIds.length) {
    parts.push(`${feedIds.length} feed${feedIds.length === 1 ? '' : 's'}`)
  }
  if (startTime || endTime) {
    parts.push(`time range ${formatTimestamp(startTime)} to ${formatTimestamp(endTime)}`)
  }

  return parts.length ? `Reprocessing ${parts.join(' · ')}.` : 'Reprocessing recent eligible articles.'
}

function metadataString(value: unknown) {
  return typeof value === 'string' && value.trim().length > 0 ? value : null
}

function formatDailyBriefChildRunTitle(run: AITaskRunResponse) {
  const briefDate = metadataString(run.metadata.brief_date)
  return briefDate ? `Daily brief for ${formatDateOnly(briefDate)}` : 'Daily brief run'
}

function formatDailyBriefChildRunMeta(run: AITaskRunResponse) {
  const referenceTime = metadataString(run.metadata.reference_time)
  const parts: string[] = []
  if (referenceTime) {
    parts.push(`reference ${formatTimestamp(referenceTime)}`)
  }
  if (run.model) {
    parts.push(run.model)
  }
  return parts.join(' · ') || 'Queued daily brief generation'
}

function asNumber(value: unknown) {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value
  }
  if (typeof value === 'string') {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

function truncate(value: string, max: number) {
  if (!value) return ''
  if (value.length <= max) return value
  return `${value.slice(0, max - 1)}…`
}

function findLatestProviderExchangeEvent(events: AITaskEventResponse[]) {
  const exchanges = events.filter(
    (event) =>
      event.event_type === 'provider_exchange' ||
      event.event_type === 'provider_exchange_failed' ||
      event.event_type === 'provider_exchange_retry',
  )
  return exchanges.length ? exchanges[exchanges.length - 1] : null
}

function humanizeKey(value: string) {
  return value.replace(/_/g, ' ').replace(/\b\w/g, (char) => char.toUpperCase())
}

function parseTimestamp(value: string | null | undefined) {
  if (!value) {
    return null
  }
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

function formatMetadataValue(value: unknown) {
  if (typeof value === 'boolean') {
    return value ? 'Yes' : 'No'
  }
  if (Array.isArray(value)) {
    return value.join(', ')
  }
  if (value == null) {
    return 'n/a'
  }
  if (typeof value === 'object') {
    return JSON.stringify(value)
  }
  return String(value)
}

function formatDebugPayload(value: unknown) {
  if (typeof value === 'string') {
    return value
  }
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}
