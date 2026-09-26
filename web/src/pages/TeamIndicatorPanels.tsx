import { hasRequiredPermissions } from '../workspace/workspaceModel'
import { TeamAIContextTab } from './TeamAIContext'
import { TeamIndicatorSuppressions } from './TeamIndicatorSuppressions'

export function TeamIndicatorPanels({
  teamId,
  panel,
  permissions,
  unavailable,
}: {
  teamId: string
  panel: string | null
  permissions: string[]
  unavailable: boolean
}) {
  const writable =
    !unavailable && hasRequiredPermissions(permissions, ['write:teams'])
  const suppressions = panel === 'indicator-suppressions'
  return (
    <>
      <TeamAIContextTab
        teamId={teamId}
        selected={panel === 'ai-context'}
        indicatorSelected={suppressions}
        writable={writable}
        unavailable={unavailable}
      />
      {suppressions && (
        <TeamIndicatorSuppressions
          key={teamId}
          teamId={teamId}
          writable={writable}
        />
      )}
    </>
  )
}
