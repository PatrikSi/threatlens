import { describe, expect, it } from 'vitest'

import { resolveVisibleRunSelection } from './aiRunSelection'

describe('resolveVisibleRunSelection', () => {
  it('keeps the current selection when it is still visible', () => {
    expect(
      resolveVisibleRunSelection(
        [
          { id: 'run-1' },
          { id: 'run-2' },
        ],
        'run-2',
      ),
    ).toBe('run-2')
  })

  it('falls back to the first visible run when filters remove the current selection', () => {
    expect(
      resolveVisibleRunSelection(
        [
          { id: 'run-3' },
          { id: 'run-4' },
        ],
        'run-2',
      ),
    ).toBe('run-3')
  })

  it('clears the selection when no runs match the current filters', () => {
    expect(resolveVisibleRunSelection([], 'run-2')).toBeNull()
    expect(resolveVisibleRunSelection(undefined, 'run-2')).toBeNull()
  })
})
