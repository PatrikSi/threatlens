import { createContext } from 'react'

import type {
  WorkspaceEffectiveResponse,
  WorkspaceRegistryResponse,
  WorkspaceUserPreferenceResponse,
  WorkspaceUserPreferenceWriteRequest,
} from '../types/workspace'
import type { ResolvedWorkspaceModel, WorkspaceUserContext } from './workspaceModel'

export interface WorkspaceRefreshResult {
  effective: WorkspaceEffectiveResponse
  registry: WorkspaceRegistryResponse
  preferences: WorkspaceUserPreferenceResponse
}

export interface WorkspaceContextValue {
  model: ResolvedWorkspaceModel
  userContext: WorkspaceUserContext
  effective: WorkspaceEffectiveResponse | undefined
  registry: WorkspaceRegistryResponse | undefined
  preferences: WorkspaceUserPreferenceResponse | undefined
  isLoading: boolean
  isRefreshing: boolean
  isDegraded: boolean
  error: unknown
  preferenceError: unknown
  isSavingPreferences: boolean
  isResettingPreferences: boolean
  refresh: () => Promise<WorkspaceRefreshResult>
  savePreferences: (payload: WorkspaceUserPreferenceWriteRequest) => Promise<WorkspaceUserPreferenceResponse>
  resetPreferences: (expectedRevision: number) => Promise<WorkspaceUserPreferenceResponse>
}

export const WorkspaceContext = createContext<WorkspaceContextValue | null>(null)
