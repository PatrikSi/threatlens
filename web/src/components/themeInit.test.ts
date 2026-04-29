// @vitest-environment jsdom

import { afterEach, describe, expect, it } from 'vitest'

import themeInitScript from '../../public/theme-init.js?raw'

function runThemeInit(storedMode: string | null) {
  window.localStorage.clear()
  document.documentElement.className = 'theme-stale dark'
  document.documentElement.removeAttribute('data-color-mode')

  if (storedMode !== null) {
    window.localStorage.setItem('threatlens.theme', storedMode)
  }

  new Function(themeInitScript)()
}

afterEach(() => {
  window.localStorage.clear()
  document.documentElement.className = ''
  document.documentElement.removeAttribute('data-color-mode')
})

describe('theme-init.js', () => {
  it.each(['dark', 'dark-cobalt', 'theme-dark', 'theme-dark-cobalt'])('applies dark mode for %s', (storedMode) => {
    runThemeInit(storedMode)

    expect(document.documentElement.classList.contains('dark')).toBe(true)
    expect(document.documentElement.classList.contains('theme-dark')).toBe(true)
    expect(document.documentElement.classList.contains('theme-stale')).toBe(false)
    expect(document.documentElement.dataset.colorMode).toBe('dark')
  })

  it.each([null, 'light', 'theme-light', 'unexpected'])('applies light mode for %s', (storedMode) => {
    runThemeInit(storedMode)

    expect(document.documentElement.classList.contains('dark')).toBe(false)
    expect(document.documentElement.classList.contains('theme-light')).toBe(true)
    expect(document.documentElement.classList.contains('theme-stale')).toBe(false)
    expect(document.documentElement.dataset.colorMode).toBe('light')
  })
})
