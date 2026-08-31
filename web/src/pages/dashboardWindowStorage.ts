import { safeLocalStorage } from '../utils/safeStorage'
import {
  createWindowLayout,
  MAX_DASHBOARD_WINDOWS,
  normalizeDashboardWindows,
  parseDashboardWindowCandidate,
  type DashboardWindow,
  type DashboardWindowSnap,
  type DashboardWindowType,
} from './dashboardSavedViews'

export function loadDashboardWindows(
  storageKey: string,
  containerWidth: number,
  containerHeight: number,
  defaultWindowTypes: readonly DashboardWindowType[] = ['rss'],
): DashboardWindow[] {
  if (typeof window === 'undefined') {
    return createDefaultDashboardWindows(containerWidth, containerHeight, defaultWindowTypes)
  }

  return loadStoredDashboardWindows(storageKey, containerWidth, containerHeight) ??
    createDefaultDashboardWindows(containerWidth, containerHeight, defaultWindowTypes)
}

export function loadStoredDashboardWindows(
  storageKey: string,
  containerWidth: number,
  containerHeight: number,
): DashboardWindow[] | null {
  if (typeof window === 'undefined') return null

  const raw = safeLocalStorage.getItem(storageKey)
  if (!raw) return null

  try {
    const parsed = JSON.parse(raw) as unknown
    if (!Array.isArray(parsed)) return null

    const windows = parsed
      .slice(0, MAX_DASHBOARD_WINDOWS)
      .map((entry, index) => parseDashboardWindowCandidate(entry, index + 1))
      .filter((entry): entry is DashboardWindow => entry !== null)

    if (!windows.length) return null

    return normalizeDashboardWindows(windows, containerWidth, containerHeight)
  } catch {
    return null
  }
}

function createDefaultDashboardWindows(
  containerWidth: number,
  containerHeight: number,
  requestedTypes: readonly DashboardWindowType[],
) {
  const types = [...new Set(requestedTypes)].slice(0, 4)
  if (types.length === 0) types.push('notes')
  const snaps: DashboardWindowSnap[][] = [
    ['full'],
    ['left', 'right'],
    ['top_left', 'top_right', 'bottom_left'],
    ['top_left', 'top_right', 'bottom_left', 'bottom_right'],
  ]
  const layoutSnaps = snaps[types.length - 1]
  return types.map((type, index) =>
    createWindowLayout(type, index + 1, containerWidth, containerHeight, layoutSnaps[index]),
  )
}
