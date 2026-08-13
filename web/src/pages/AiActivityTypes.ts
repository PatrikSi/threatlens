import type { Dispatch, RefObject, SetStateAction } from 'react'
import { useQuery } from '@tanstack/react-query'

import type {
  AIDailyBriefSourceItemResponse,
  AILiveStatusResponse,
  AITaskRunDetailResponse,
  AITaskRunListResponse,
  AITaskRunResponse,
  Feed,
  ItemListEntry,
} from '../types/api'
import type { AIReprocessScopeValidation } from './aiReprocessQueueState'

export type RunFilters = {
  taskType: string
  status: string
  triggerSource: string
  onlyFailures: boolean
}

export type TaskRunListQuery = ReturnType<typeof useQuery<AITaskRunListResponse>>
export type TaskRunDetailQuery = ReturnType<typeof useQuery<AITaskRunDetailResponse>>

export type ActivityTabProps = {
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
  runsQuery: TaskRunListQuery
  selectedRunId: string | null
  onSelectRun: (runId: string) => void
  runDetailQuery: TaskRunDetailQuery
  briefSources: AIDailyBriefSourceItemResponse[]
  briefSourcesLoading: boolean
  briefSourcesErrorMessage: string
  selectedRunSectionRef: RefObject<HTMLDivElement | null>
  onCancelRun: (run: AITaskRunResponse) => void
  cancelingRunId: string | null
}
