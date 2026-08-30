import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, type ReactNode } from 'react'

import { useCurrentUser } from '../hooks/useCurrentUser'
import type { WorkspaceUserPreferenceWriteRequest } from '../types/workspace'
import {
  getEffectiveWorkspace,
  getWorkspacePreferences,
  getWorkspaceRegistry,
  resetWorkspacePreferences,
  updateWorkspacePreferences,
  workspaceQueryKeys,
} from '../workspace/workspaceApi'
import { WorkspaceContext, type WorkspaceContextValue } from '../workspace/workspaceContext'
import {
  effectiveWorkspaceForClientControls,
  resolveWorkspaceModel,
  type WorkspaceUserContext,
} from '../workspace/workspaceModel'

const EFFECTIVE_WORKSPACE_REFRESH_INTERVAL_MS = 60_000

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const meQuery = useCurrentUser()
  const user = meQuery.data
  const userId = user?.id ?? ''
  const enabled = Boolean(userId)

  const registryQuery = useQuery({
    queryKey: workspaceQueryKeys.registry,
    queryFn: getWorkspaceRegistry,
    enabled,
    staleTime: 300_000,
  })
  const effectiveQuery = useQuery({
    queryKey: workspaceQueryKeys.effective(userId),
    queryFn: getEffectiveWorkspace,
    enabled,
    staleTime: 30_000,
    refetchInterval: EFFECTIVE_WORKSPACE_REFRESH_INTERVAL_MS,
    refetchIntervalInBackground: false,
    refetchOnReconnect: 'always',
    refetchOnWindowFocus: 'always',
  })
  const preferencesQuery = useQuery({
    queryKey: workspaceQueryKeys.preferences(userId),
    queryFn: getWorkspacePreferences,
    enabled,
    staleTime: 30_000,
  })

  const updatePreferences = useMutation({
    mutationFn: (payload: WorkspaceUserPreferenceWriteRequest) => updateWorkspacePreferences(payload),
    onSuccess: (preferences) => {
      queryClient.setQueryData(workspaceQueryKeys.preferences(userId), preferences)
      void queryClient.invalidateQueries({ queryKey: workspaceQueryKeys.effective(userId) })
    },
  })
  const resetPreferences = useMutation({
    mutationFn: (expectedRevision: number) => resetWorkspacePreferences({ expected_revision: expectedRevision }),
    onSuccess: (preferences) => {
      queryClient.setQueryData(workspaceQueryKeys.preferences(userId), preferences)
      void queryClient.invalidateQueries({ queryKey: workspaceQueryKeys.effective(userId) })
    },
  })

  const userContext = useMemo<WorkspaceUserContext>(
    () => ({
      role: user?.role ?? 'viewer',
      permissions: user?.access?.permissions,
      features: user?.features ?? {
        ai_enabled: false,
        ai_configured: false,
        ai_summary_enabled: false,
        ai_relevance_enabled: false,
        ai_daily_brief_enabled: false,
      },
      accountEligible: user?.access?.account_eligible ?? Boolean(user?.is_active && user?.is_approved),
    }),
    [user],
  )
  const effective = useMemo(
    () => effectiveWorkspaceForClientControls(effectiveQuery.data),
    [effectiveQuery.data],
  )
  const model = useMemo(
    () => resolveWorkspaceModel(effective, registryQuery.data, userContext),
    [effective, registryQuery.data, userContext],
  )
  const queryError = effectiveQuery.error ?? registryQuery.error ?? preferencesQuery.error ?? null
  const isLoading = Boolean(
    enabled &&
    !effectiveQuery.data &&
    !effectiveQuery.error &&
    (effectiveQuery.isLoading || effectiveQuery.isPending),
  )

  const value = useMemo<WorkspaceContextValue>(
    () => ({
      model,
      userContext,
      effective,
      registry: registryQuery.data,
      preferences: preferencesQuery.data,
      isLoading,
      isRefreshing: effectiveQuery.isFetching || registryQuery.isFetching || preferencesQuery.isFetching,
      isDegraded: Boolean(queryError),
      error: queryError,
      preferenceError: updatePreferences.error ?? resetPreferences.error ?? preferencesQuery.error ?? null,
      isSavingPreferences: updatePreferences.isPending,
      isResettingPreferences: resetPreferences.isPending,
      refresh: async () => {
        const [effectiveResult, registryResult, preferencesResult] = await Promise.all([
          effectiveQuery.refetch({ throwOnError: true }),
          registryQuery.refetch({ throwOnError: true }),
          preferencesQuery.refetch({ throwOnError: true }),
        ])
        if (!effectiveResult.data || !registryResult.data || !preferencesResult.data) {
          throw new Error('The server did not return complete workspace configuration data.')
        }
        return {
          effective: effectiveResult.data,
          registry: registryResult.data,
          preferences: preferencesResult.data,
        }
      },
      savePreferences: (payload) => updatePreferences.mutateAsync(payload),
      resetPreferences: (expectedRevision) => resetPreferences.mutateAsync(expectedRevision),
    }),
    [
      effectiveQuery,
      effective,
      isLoading,
      model,
      preferencesQuery,
      queryError,
      registryQuery,
      resetPreferences,
      updatePreferences,
      userContext,
    ],
  )

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>
}
