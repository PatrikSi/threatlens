// @vitest-environment jsdom

import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it } from 'vitest'

import { ThemeProvider, useTheme } from './ThemeContext'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

let root: Root | null = null
let container: HTMLDivElement | null = null

function ThemeProbe() {
  const { isDark, setMode } = useTheme()

  return (
    <button type="button" onClick={() => setMode(isDark ? 'light' : 'dark')}>
      {isDark ? 'dark' : 'light'}
    </button>
  )
}

async function renderThemeProbe() {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)

  await act(async () => {
    root?.render(
      <ThemeProvider>
        <ThemeProbe />
      </ThemeProvider>,
    )
    await flushPromises()
  })

  return container
}

function flushPromises() {
  return new Promise((resolve) => window.setTimeout(resolve, 0))
}

afterEach(async () => {
  await act(async () => {
    root?.unmount()
    await flushPromises()
  })
  root = null
  container?.remove()
  container = null
  window.localStorage.clear()
  document.body.innerHTML = ''
  document.documentElement.className = ''
  document.documentElement.removeAttribute('data-color-mode')
})

describe('ThemeProvider', () => {
  it('normalizes legacy dark theme values to the single dark mode', async () => {
    window.localStorage.setItem('threatlens.theme', 'dark-cobalt')

    const view = await renderThemeProbe()

    expect(view.textContent).toContain('dark')
    expect(document.documentElement.classList.contains('dark')).toBe(true)
    expect(document.documentElement.classList.contains('theme-dark')).toBe(true)
    expect(document.documentElement.dataset.colorMode).toBe('dark')
    expect(window.localStorage.getItem('threatlens.theme')).toBe('dark')
  })

  it('toggles between light and dark root state', async () => {
    const view = await renderThemeProbe()
    const button = view.querySelector('button')
    expect(button).not.toBeNull()

    expect(document.documentElement.classList.contains('dark')).toBe(false)
    expect(document.documentElement.classList.contains('theme-light')).toBe(true)
    expect(window.localStorage.getItem('threatlens.theme')).toBe('light')

    await act(async () => {
      button?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      await flushPromises()
    })

    expect(view.textContent).toContain('dark')
    expect(document.documentElement.classList.contains('dark')).toBe(true)
    expect(document.documentElement.classList.contains('theme-dark')).toBe(true)
    expect(window.localStorage.getItem('threatlens.theme')).toBe('dark')
  })
})
