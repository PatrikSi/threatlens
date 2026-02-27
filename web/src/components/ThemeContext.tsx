import { createContext, useContext, useEffect, useMemo, useState } from 'react'

export type DarkThemeId =
  | 'dark-emerald'
  | 'dark-cobalt'
  | 'dark-slate'
  | 'dark-carbon'
  | 'dark-amber'
  | 'dark-crimson'
  | 'dark-violet'
  | 'dark-ice'
  | 'dark-forest'
  | 'dark-solarized'

export type ThemeMode = 'light' | DarkThemeId

interface DarkThemeOption {
  id: DarkThemeId
  label: string
}

const DARK_THEME_OPTIONS: DarkThemeOption[] = [
  { id: 'dark-emerald', label: 'Dark Emerald' },
  { id: 'dark-cobalt', label: 'Dark Cobalt' },
  { id: 'dark-slate', label: 'Dark Slate' },
  { id: 'dark-carbon', label: 'Dark Carbon' },
  { id: 'dark-amber', label: 'Dark Amber' },
  { id: 'dark-crimson', label: 'Dark Crimson' },
  { id: 'dark-violet', label: 'Dark Violet' },
  { id: 'dark-ice', label: 'Dark Ice' },
  { id: 'dark-forest', label: 'Dark Forest' },
  { id: 'dark-solarized', label: 'Dark Solarized' },
]

const VALID_THEME_IDS = new Set<ThemeMode>(['light', ...DARK_THEME_OPTIONS.map((entry) => entry.id)])
const THEME_CLASS_NAMES = DARK_THEME_OPTIONS.map((entry) => `theme-${entry.id}`)

interface ThemeContextValue {
  mode: ThemeMode
  setMode: (mode: ThemeMode) => void
  isDark: boolean
  darkThemes: DarkThemeOption[]
}

const themeStorageKey = 'threatlens.theme'
const ThemeContext = createContext<ThemeContextValue | undefined>(undefined)

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [mode, setMode] = useState<ThemeMode>(() => {
    const stored = readStoredTheme()
    if (stored === 'dark') return 'dark-emerald'
    if (stored && VALID_THEME_IDS.has(stored as ThemeMode)) {
      return stored as ThemeMode
    }
    return 'light'
  })

  useEffect(() => {
    const root = document.documentElement

    root.classList.remove('theme-light', ...THEME_CLASS_NAMES)
    root.classList.toggle('dark', mode !== 'light')
    if (mode === 'light') {
      root.classList.add('theme-light')
    } else {
      root.classList.add(`theme-${mode}`)
    }

    persistTheme(mode)
  }, [mode])

  const value = useMemo(
    () => ({
      mode,
      setMode,
      isDark: mode !== 'light',
      darkThemes: DARK_THEME_OPTIONS,
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
