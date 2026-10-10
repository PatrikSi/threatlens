import { act, type ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { HuntDraftUnloadGuard } from '../components/HuntDraftUnloadGuard'

export { contextFixture, assessmentFixture, extractionFixture } from '../../tests/fixtures/articleIntelligence'

export function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: Error) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
export async function settle() { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 15)) }) }
export async function mountIntel(element: ReactNode, entry = '/?assessment_team=team-1') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } })
  const router = createMemoryRouter([{ path: '/', element }, { path: '/other', element: <p>Other page</p> }], { initialEntries: [entry] })
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  act(() => root.render(<QueryClientProvider client={client}><HuntDraftUnloadGuard /><RouterProvider router={router} /></QueryClientProvider>))
  await settle()
  return { host, root, client, router, close: () => { act(() => root.unmount()); router.dispose(); client.clear(); host.remove() } }
}
export function intelButton(host: ParentNode, text: string) {
  const button = [...host.querySelectorAll('button')].find((node) => node.textContent?.trim() === text)
  if (!button) throw new Error(`Button missing: ${text}`)
  return button
}
export function intelField(host: ParentNode, label: string) {
  const control = [...host.querySelectorAll('label')].find((node) => node.textContent?.trim().startsWith(label))?.control
  if (!(control instanceof HTMLTextAreaElement)) throw new Error(`Textarea missing: ${label}`)
  return control
}
export function editIntel(host: ParentNode, label: string, value: string) {
  const control = intelField(host, label)
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(control, value)
    control.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
