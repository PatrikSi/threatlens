import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'

const Children = createContext<ReactNode>(null)
let currentRouter: ReturnType<typeof createMemoryRouter> | undefined

function CurrentChildren() { return useContext(Children) }

/** Real router lifetime for component tests that update their rendered children. */
export function TestDataRouter({ children, initialEntries = ['/'] }: { children: ReactNode; initialEntries?: string[] }) {
  const [router] = useState(() => createMemoryRouter([
    { path: '/test-away', element: <p>Test navigation destination</p> },
    { path: '*', element: <CurrentChildren /> },
  ], { initialEntries }))
  currentRouter = router
  useEffect(() => () => { router.dispose(); if (currentRouter === router) currentRouter = undefined }, [router])
  return <Children.Provider value={children}><RouterProvider router={router} /></Children.Provider>
}

export function testRouter() {
  if (!currentRouter) throw new Error('Mount TestDataRouter before navigating.')
  return currentRouter
}
