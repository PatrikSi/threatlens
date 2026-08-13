// @vitest-environment jsdom

import { afterEach, describe, expect, it } from 'vitest'

import { safeLocalStorage } from './safeStorage'

const originalStorageDescriptor = Object.getOwnPropertyDescriptor(window, 'localStorage')

afterEach(() => {
  if (originalStorageDescriptor) {
    Object.defineProperty(window, 'localStorage', originalStorageDescriptor)
  }
})

describe('safeLocalStorage', () => {
  it('keeps session state when browser storage is denied', () => {
    const deniedStorage = {
      getItem: () => {
        throw new DOMException('Denied', 'SecurityError')
      },
      setItem: () => {
        throw new DOMException('Denied', 'SecurityError')
      },
      removeItem: () => {
        throw new DOMException('Denied', 'SecurityError')
      },
    }
    Object.defineProperty(window, 'localStorage', { configurable: true, value: deniedStorage })

    safeLocalStorage.setItem('safe-storage-denied', 'dashboard-state')
    expect(safeLocalStorage.getItem('safe-storage-denied')).toBe('dashboard-state')

    safeLocalStorage.removeItem('safe-storage-denied')
    expect(safeLocalStorage.getItem('safe-storage-denied')).toBeNull()
  })

  it('falls back when quota prevents a write even though reads remain available', () => {
    const quotaStorage = {
      getItem: () => null,
      setItem: () => {
        throw new DOMException('Full', 'QuotaExceededError')
      },
      removeItem: () => undefined,
    }
    Object.defineProperty(window, 'localStorage', { configurable: true, value: quotaStorage })

    safeLocalStorage.setItem('safe-storage-quota', 'pending-layout')
    expect(safeLocalStorage.getItem('safe-storage-quota')).toBe('pending-layout')
  })
})
