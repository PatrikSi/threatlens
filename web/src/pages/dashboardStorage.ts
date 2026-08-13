import { safeLocalStorage } from '../utils/safeStorage'

const WINDOW_STORAGE_KEY = 'threatlens.dashboard.windows.v2'
const WINDOW_SEEN_STORAGE_KEY = 'threatlens.dashboard.window-seen.v1'
const USER_LAST_OPEN_STORAGE_KEY = 'threatlens.user-last-open.v1'

export function getDashboardStorageKeys(userId: string) {
  return {
    windows: `${WINDOW_STORAGE_KEY}:${userId}`,
    windowSeenAt: `${WINDOW_SEEN_STORAGE_KEY}:${userId}`,
    lastOpenedAt: `${USER_LAST_OPEN_STORAGE_KEY}:${userId}`,
  } as const
}

export function migrateLegacyDashboardStorage(userId: string) {
  if (typeof window === 'undefined' || !userId) {
    return false
  }

  const storageKeys = getDashboardStorageKeys(userId)
  const migrations: Array<{ legacyKey: string; scopedKey: string }> = [
    { legacyKey: WINDOW_STORAGE_KEY, scopedKey: storageKeys.windows },
    { legacyKey: WINDOW_SEEN_STORAGE_KEY, scopedKey: storageKeys.windowSeenAt },
    { legacyKey: USER_LAST_OPEN_STORAGE_KEY, scopedKey: storageKeys.lastOpenedAt },
  ]

  let migrated = false
  for (const { legacyKey, scopedKey } of migrations) {
    if (safeLocalStorage.getItem(scopedKey) !== null) {
      continue
    }

    const legacyValue = safeLocalStorage.getItem(legacyKey)
    if (legacyValue === null) {
      continue
    }

    safeLocalStorage.setItem(scopedKey, legacyValue)
    migrated = true
  }

  return migrated
}
