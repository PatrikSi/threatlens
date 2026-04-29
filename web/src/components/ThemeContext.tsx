import { createContext, useContext, useEffect, useMemo, useState } from 'react'

export type ThemeMode = 'light' | 'dark'

interface ThemeContextValue {
  mode: ThemeMode
  setMode: (mode: ThemeMode) => void
  isDark: boolean
}

const themeStorageKey = 'threatlens.theme'
const ThemeContext = createContext<ThemeContextValue | undefined>(undefined)

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [mode, setMode] = useState<ThemeMode>(() => normalizeThemeMode(readStoredTheme()))

  useEffect(() => {
    const root = document.documentElement

    for (const className of Array.from(root.classList)) {
      if (className.startsWith('theme-')) {
        root.classList.remove(className)
      }
    }
    root.classList.toggle('dark', mode === 'dark')
    root.classList.add(`theme-${mode}`)
    root.dataset.colorMode = mode
    persistTheme(mode)
  }, [mode])

  const value = useMemo(
    () => ({
      mode,
      setMode,
      isDark: mode === 'dark',
    }),
    [mode],
  )

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

export function useTheme() {
  const context = useContext(ThemeContext)
  if (!context) {
    throw new Error('useTheme must be used within ThemeProvider')
  }
  return context
}

function readStoredTheme(): string | null {
  if (typeof window === 'undefined') {
    return null
  }
  try {
    return window.localStorage.getItem(themeStorageKey)
  } catch {
    return null
  }
}

function persistTheme(mode: ThemeMode) {
  if (typeof window === 'undefined') {
    return
  }
  try {
    window.localStorage.setItem(themeStorageKey, mode)
  } catch {
    // No-op when browser storage is unavailable (private mode / policy restrictions).
  }
}

function normalizeThemeMode(stored: string | null): ThemeMode {
  if (stored === 'dark' || stored?.startsWith('dark-') || stored?.startsWith('theme-dark')) {
    return 'dark'
  }
  return 'light'
}
