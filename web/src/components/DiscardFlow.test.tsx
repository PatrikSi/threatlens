import { describe, expect, it, vi } from 'vitest'

import {
  confirmUnsavedChanges,
  createBeforeUnloadHandler,
  handleBlockedUnsavedChangesNavigation,
} from '../hooks/useUnsavedChangesWarning'

describe('unsaved change helpers', () => {
  it('routes confirmations through the provided confirm function', () => {
    const confirmWith = vi.fn(() => true)

    expect(confirmUnsavedChanges('Discard changes?', confirmWith)).toBe(true)
    expect(confirmWith).toHaveBeenCalledWith('Discard changes?')
  })

  it('proceeds blocked navigation only after confirmation', () => {
    const blocker = {
      state: 'blocked' as const,
      proceed: vi.fn(),
      reset: vi.fn(),
    }

    const confirmed = handleBlockedUnsavedChangesNavigation(blocker, 'Leave?', () => true)

    expect(confirmed).toBe(true)
    expect(blocker.proceed).toHaveBeenCalledTimes(1)
    expect(blocker.reset).not.toHaveBeenCalled()
  })

  it('resets blocked navigation when the operator declines', () => {
    const blocker = {
      state: 'blocked' as const,
      proceed: vi.fn(),
      reset: vi.fn(),
    }

    const confirmed = handleBlockedUnsavedChangesNavigation(blocker, 'Leave?', () => false)

    expect(confirmed).toBe(false)
    expect(blocker.reset).toHaveBeenCalledTimes(1)
    expect(blocker.proceed).not.toHaveBeenCalled()
  })

  it('builds a beforeunload handler that sets the browser warning text', () => {
    const handler = createBeforeUnloadHandler('Unsaved changes remain.')
    const event = {
      preventDefault: vi.fn(),
      returnValue: '',
    } as unknown as BeforeUnloadEvent

    expect(handler(event)).toBe('Unsaved changes remain.')
    expect(event.preventDefault).toHaveBeenCalledTimes(1)
    expect(event.returnValue).toBe('Unsaved changes remain.')
  })
})
