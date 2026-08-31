import { Navigate } from 'react-router-dom'

import { useWorkspace } from '../workspace/useWorkspace'

export function WorkspaceLandingRedirect() {
  const workspace = useWorkspace()

  if (workspace.isLoading) {
    return (
      <p role="status" className="px-4 py-6 text-sm text-slate dark:text-slate-300">
        Resolving your workspace...
      </p>
    )
  }

  return <Navigate to={workspace.model.landingPath} replace />
}
