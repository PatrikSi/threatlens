const memoryFallback = new Map<string, string>()
const fallbackOnlyKeys = new Set<string>()

function getLocalStorage(): Storage | null {
  if (typeof window === 'undefined') {
    return null
  }

  try {
    return window.localStorage
  } catch {
    return null
  }
}

export const safeLocalStorage = {
  getItem(key: string): string | null {
    const storage = getLocalStorage()
    if (!storage) {
      return memoryFallback.get(key) ?? null
    }

    try {
      const stored = storage.getItem(key)
      if (stored !== null) {
        memoryFallback.set(key, stored)
        fallbackOnlyKeys.delete(key)
        return stored
      }
      if (!fallbackOnlyKeys.has(key)) {
        memoryFallback.delete(key)
      }
      return memoryFallback.get(key) ?? null
    } catch {
      return memoryFallback.get(key) ?? null
    }
  },

  setItem(key: string, value: string): void {
    memoryFallback.set(key, value)
    fallbackOnlyKeys.add(key)

    const storage = getLocalStorage()
    if (!storage) {
      return
    }

    try {
      storage.setItem(key, value)
      fallbackOnlyKeys.delete(key)
    } catch {
      // The in-memory value keeps the current session usable when storage is denied or full.
    }
  },

  removeItem(key: string): void {
    memoryFallback.delete(key)
    fallbackOnlyKeys.delete(key)

    try {
      getLocalStorage()?.removeItem(key)
    } catch {
      // The fallback has already been cleared.
    }
  },
}
