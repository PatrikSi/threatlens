import { useQuery } from '@tanstack/react-query'

import { accessibleQueryData } from '../api/queryData'
import { useCurrentUser } from './useCurrentUser'
import {
  getWorkspacePreferences,
  workspaceQueryKeys,
} from '../workspace/workspaceApi'
import { hasRequiredPermissions } from '../workspace/workspaceModel'

export function useArticlePreviewPreferences() {
  const currentUser = useCurrentUser()
  const user = accessibleQueryData(currentUser)
  const userId = user?.id ?? ''
  const permissions = user?.access?.permissions ?? []
  const canRead = hasRequiredPermissions(permissions, ['read:workspace'])
  const canWrite = hasRequiredPermissions(permissions, [
    'write:workspace_preferences',
  ])
  const query = useQuery({
    queryKey: workspaceQueryKeys.preferences(userId),
    queryFn: getWorkspacePreferences,
    enabled: Boolean(userId) && canRead,
    staleTime: 30_000,
  })
  const candidate = accessibleQueryData(query)
  const preferences =
    canRead && candidate?.user_id === userId ? candidate : undefined
  return {
    userId,
    canRead,
    canWrite: canWrite && !currentUser.isError && !query.isError,
    preferences,
    query,
    defaultExternalResources:
      !query.isError &&
      preferences?.article_preview_external_resources === true,
  }
}
