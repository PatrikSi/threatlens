// @vitest-environment jsdom
import { act, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { CopyableIdentifier } from '../components/CopyableIdentifier'
import { RecoveryCodesDialog, SecretValue } from './AccountSecurityControls'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

let root: Root | undefined
let host: HTMLDivElement | undefined
function render(element: ReactNode) {
  if (!host) {
    host = document.createElement('div')
    document.body.appendChild(host)
    root = createRoot(host)
  }
  act(() => root!.render(element))
}
function button(text: string) {
  const result = [...document.querySelectorAll('button')].find((entry) => entry.textContent?.trim() === text)
  if (!result) throw new Error(`Button missing: ${text}`)
  return result
}
function pendingCopy() {
  let resolve!: () => void
  let reject!: (error: Error) => void
  const promise = new Promise<void>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
function clipboard(writeText: ReturnType<typeof vi.fn>) {
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
}
async function finish(task: () => void) { await act(async () => { task(); await Promise.resolve() }) }
afterEach(() => {
  act(() => root?.unmount())
  host?.remove()
  root = undefined
  host = undefined
  Reflect.deleteProperty(navigator, 'clipboard')
  vi.clearAllMocks()
  vi.restoreAllMocks()
})

const fields = [
  { name: 'identifier', element: (value: string) => <CopyableIdentifier label="Identifier" value={value} /> },
  { name: 'MFA enrollment secret', element: (value: string) => <SecretValue label="Secret" value={value} /> },
]
describe.each(fields)('$name clipboard feedback', ({ element }) => {
  it('clears copied feedback when the displayed value changes', async () => {
    clipboard(vi.fn().mockResolvedValue(undefined))
    render(element('first-value'))
    await finish(() => button('Copy').click())
    expect(host!.textContent).toContain('Copied')
    render(element('second-value'))
    expect(host!.textContent).not.toContain('Copied')
    expect(button('Copy')).toBeDefined()
  })

  it.each(['success', 'failure'])('ignores a late %s for a replaced value', async (outcome) => {
    const pending = pendingCopy()
    const writeText = vi.fn(() => pending.promise)
    clipboard(writeText)
    render(element('first-value'))
    act(() => button('Copy').click())
    render(element('second-value'))
    await finish(() => { if (outcome === 'success') pending.resolve(); else pending.reject(new Error('Old request denied')) })
    expect(writeText).toHaveBeenCalledWith('first-value')
    expect(host!.textContent).not.toContain('Copied')
    expect(host!.querySelector('[role="alert"]')).toBeNull()
    expect(button('Copy')).toBeDefined()
  })

  it('retains the result of the latest copy attempt when an older attempt completes', async () => {
    const older = pendingCopy()
    const latest = pendingCopy()
    clipboard(vi.fn().mockReturnValueOnce(older.promise).mockReturnValueOnce(latest.promise))
    render(element('same-value'))
    act(() => button('Copy').click())
    act(() => button('Copy').click())
    await finish(() => latest.reject(new Error('Latest request denied')))
    expect(host!.querySelector('[role="alert"]')?.textContent).toContain('Clipboard access was denied')
    await finish(older.resolve)
    expect(host!.querySelector('[role="alert"]')?.textContent).toContain('Clipboard access was denied')
    expect(host!.textContent).not.toContain('Copied')
  })
})

describe('MFA recovery-code clipboard feedback', () => {
  const first = { recovery_codes: ['FIRST-CODE'], generated_at: '2026-11-15T09:00:00Z' }
  const second = { recovery_codes: ['SECOND-CODE'], generated_at: '2026-11-15T09:01:00Z' }
  const onDownload = vi.fn()
  const onDone = vi.fn()
  const dialog = (data: typeof first) => <RecoveryCodesDialog data={data} onDownload={onDownload} onDone={onDone} />

  it.each(['success', 'failure'])('ignores an old generation clipboard %s', async (outcome) => {
    const pending = pendingCopy()
    clipboard(vi.fn(() => pending.promise))
    render(dialog(first))
    act(() => button('Copy all codes').click())
    render(dialog(second))
    await finish(() => { if (outcome === 'success') pending.resolve(); else pending.reject(new Error('Old codes denied')) })
    expect(document.body.textContent).toContain('SECOND-CODE')
    expect(document.body.textContent).not.toContain('All recovery codes copied')
    expect(document.querySelector('[role="alert"]')).toBeNull()
    expect(onDone).not.toHaveBeenCalled()
  })

  it('resets feedback for replacement codes even when the timestamp is unchanged', async () => {
    clipboard(vi.fn().mockResolvedValue(undefined))
    render(dialog(first))
    await finish(() => button('Copy all codes').click())
    expect(document.body.textContent).toContain('All recovery codes copied')
    render(dialog({ ...second, generated_at: first.generated_at }))
    expect(document.body.textContent).not.toContain('All recovery codes copied')
  })

  it('preserves current codes and explicit acknowledgement after a clipboard denial', async () => {
    onDone.mockClear()
    clipboard(vi.fn().mockRejectedValue(new Error('Current request denied')))
    render(dialog(first))
    await finish(() => button('Copy all codes').click())
    expect(document.body.textContent).toContain('FIRST-CODE')
    expect(document.querySelector('[role="alert"]')?.textContent).toContain('Clipboard access was denied')
    expect(onDone).not.toHaveBeenCalled()
    act(() => button('I stored these codes').click())
    expect(onDone).toHaveBeenCalledTimes(1)
  })
})
