import { act } from 'react'

export { indicatorFixture, indicatorPageFixture } from '../../tests/fixtures/indicatorAutomation'

export function automationField(
  host: ParentNode,
  label: string,
): HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement {
  const control = [...host.querySelectorAll('label')].find((node) =>
    node.textContent?.trim().startsWith(label),
  )?.control
  if (
    !(
      control instanceof HTMLInputElement ||
      control instanceof HTMLSelectElement ||
      control instanceof HTMLTextAreaElement
    )
  )
    throw new Error(`Control missing: ${label}`)
  return control
}
export function editAutomation(host: ParentNode, label: string, value: string) {
  const control = automationField(host, label)
  act(() => {
    Object.getOwnPropertyDescriptor(
      Object.getPrototypeOf(control),
      'value',
    )!.set!.call(control, value)
    control.dispatchEvent(
      new Event(control instanceof HTMLSelectElement ? 'change' : 'input', {
        bubbles: true,
      }),
    )
  })
}
