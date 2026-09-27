import { createElement, useContext, useId, useLayoutEffect, useState, useSyncExternalStore, type ContextType } from 'react'
import { UNSAFE_DataRouterContext, type Blocker, type BlockerFunction } from 'react-router-dom'

import { SharedNavigationWarning } from './SharedNavigationWarning'

type Warning = { dirty: boolean; message: string; ignoreSearchChanges?: boolean }
type DataRouter = NonNullable<ContextType<typeof UNSAFE_DataRouterContext>>['router']
const BLOCKER_KEY = 'threatlens-unsaved-forms'

// A data router supports one blocker. Forms register here instead of replacing
// each other's blockers; the first mounted form hosts the shared dialog.
export class NavigationWarnings {
  constructor(readonly router: DataRouter) {}
  blocker: Blocker | undefined
  private disconnectRouter?: () => void
  blocked = false
  readonly forms = new Map<string, Warning>()
  private readonly listeners = new Set<() => void>()
  private revision = 0
  subscribe = (listener: () => void) => {
    this.listeners.add(listener)
    return () => { this.listeners.delete(listener) }
  }
  leader = () => this.forms.keys().next().value ?? null
  snapshot = () => this.revision
  notify() { this.revision += 1; this.listeners.forEach((listener) => listener()) }
  blocks: BlockerFunction = ({ currentLocation, nextLocation }) =>
    [...this.forms.values()].some((form) => form.dirty && (
      !form.ignoreSearchChanges || currentLocation.pathname !== nextLocation.pathname
    ))
  register(id: string, warning: Warning) {
    this.forms.set(id, warning)
    if (!this.disconnectRouter) {
      // Keep the router registration independent of the component hosting its
      // dialog. Removing that component must not discard a blocked navigation.
      this.blocker = this.router.getBlocker(BLOCKER_KEY, this.blocks)
      this.disconnectRouter = this.router.subscribe((state) => {
        const blocker = state.blockers.get(BLOCKER_KEY)
        if (this.blocker === blocker) return
        this.blocker = blocker
        this.blocked = blocker?.state === 'blocked'
        this.notify()
      })
    }
    this.notify()
  }
  unregister(id: string) {
    this.forms.delete(id)
    if (!this.forms.size) {
      // A workspace/session teardown abandons its pending navigation. Never
      // resume that transition after the entire originating workspace is gone.
      this.disconnectRouter?.()
      this.disconnectRouter = undefined
      this.router.deleteBlocker(BLOCKER_KEY)
      this.blocker = undefined
      this.blocked = false
    }
    this.notify()
  }
}

const warningsByRouter = new WeakMap<object, NavigationWarnings>()

export function useSharedNavigationWarning(warning: Warning) {
  const context = useContext(UNSAFE_DataRouterContext)
  if (!context) throw new Error('Unsaved changes protection requires a data router.')
  const router = context.router
  const [warnings] = useState(() => {
    let shared = warningsByRouter.get(router)
    if (!shared) {
      shared = new NavigationWarnings(router)
      warningsByRouter.set(router, shared)
    }
    return shared
  })
  const id = useId()
  useSyncExternalStore(warnings.subscribe, warnings.snapshot, warnings.snapshot)
  const { dirty, message, ignoreSearchChanges } = warning
  useLayoutEffect(() => {
    warnings.register(id, { dirty, message, ignoreSearchChanges })
  }, [warnings, id, dirty, message, ignoreSearchChanges])
  useLayoutEffect(() => () => {
    warnings.unregister(id)
  }, [warnings, id])
  return {
    dialog: warnings.leader() === id ? createElement(SharedNavigationWarning, { warnings }) : null,
    open: warnings.blocked,
  }
}
