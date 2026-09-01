import {
  TRUSTED_WORKSPACE_MODULE_BY_ID,
  type TrustedWorkspaceModuleId,
} from '../workspace/moduleRegistry'
import { PermissionRoute } from './PermissionRoute'

interface WorkspaceModuleRouteProps {
  moduleId: TrustedWorkspaceModuleId
  children: React.ReactNode
}

export function WorkspaceModuleRoute({
  moduleId,
  children,
}: WorkspaceModuleRouteProps) {
  const definition = TRUSTED_WORKSPACE_MODULE_BY_ID.get(moduleId)
  if (!definition) {
    throw new Error(`Unknown trusted workspace module: ${moduleId}`)
  }

  return (
    <PermissionRoute
      permissions={definition.requiredPermissions}
      roles={definition.requiredBaseRoles ?? undefined}
    >
      {children}
    </PermissionRoute>
  )
}
