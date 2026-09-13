import { useCallback, useEffect, useMemo, useState, type Dispatch, type RefObject, type SetStateAction } from 'react'
import type { WorkspaceEffectiveResponse } from '../types/workspace'
import { getWindowContainerDimensions } from './dashboardPageUtils'
import { normalizeDashboardWindows, type DashboardWindow } from './dashboardSavedViews'
import { createDefaultDashboardWindows } from './dashboardWindowStorage'
import { workspaceTemplateWindows } from './workspaceDashboardTemplate'

/** A separate live arrangement leaves personal windows, local storage and edit drafts untouched. */
type EnforcedDashboardLayoutOptions = {
  policy: WorkspaceEffectiveResponse | undefined
  personalWindows: DashboardWindow[]
  setPersonalWindows: Dispatch<SetStateAction<DashboardWindow[]>>
  rootRef: RefObject<HTMLDivElement | null>
  defaultPanelIds: readonly DashboardWindow['type'][]
}

export function useEnforcedDashboardLayout({
  policy, personalWindows, setPersonalWindows, rootRef, defaultPanelIds,
}: EnforcedDashboardLayoutOptions) {
  const enforced = policy?.dashboard_mode === 'enforced'
  const revision = enforced ? `${policy.role}:${policy.policy_revision}` : null
  const template = policy?.dashboard_view_json
  const seed = useMemo(() => {
    const { width, height } = getWindowContainerDimensions(rootRef.current)
    return template
      ? workspaceTemplateWindows(template, width, height)
      : createDefaultDashboardWindows(width, height, defaultPanelIds)
  }, [rootRef, template, defaultPanelIds])
  const [working, setWorking] = useState<{ revision: string | null; windows: DashboardWindow[] }>({ revision: null, windows: [] })
  const organizationWindows = working.revision === revision ? working.windows : seed
  const windows = enforced ? organizationWindows : personalWindows
  const setWindows = useCallback<Dispatch<SetStateAction<DashboardWindow[]>>>((update) => {
    if (!enforced) {
      setPersonalWindows(update)
      return
    }
    setWorking((current) => {
      const previous = current.revision === revision ? current.windows : seed
      const next = typeof update === 'function' ? update(previous) : update
      if (next === previous && current.revision === revision) return current
      return { revision, windows: next }
    })
  }, [enforced, revision, seed, setPersonalWindows])
  useEffect(() => {
    if (!enforced) return
    const resize = () => {
      const { width, height } = getWindowContainerDimensions(rootRef.current)
      setWorking((current) => ({
        revision,
        windows: normalizeDashboardWindows(current.revision === revision ? current.windows : seed, width, height),
      }))
    }
    window.addEventListener('resize', resize)
    return () => window.removeEventListener('resize', resize)
  }, [enforced, revision, rootRef, seed])
  return { enforced, windows, setWindows }
}
