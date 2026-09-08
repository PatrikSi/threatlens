type IsolationTarget = Pick<HTMLElement, 'children'>
type Snapshot = { ariaHidden: string | null; inert: boolean; hadInert: boolean }
type Layer = { root: HTMLElement; returnTargets: HTMLElement[] }
type Stack = {
  layers: Layer[]
  snapshots: Map<HTMLElement, Snapshot>
  observer?: MutationObserver
  overflow?: string
}

const stacks = new WeakMap<IsolationTarget, Stack>()

function restore(element: HTMLElement, snapshot: Snapshot) {
  if (snapshot.ariaHidden === null) element.removeAttribute('aria-hidden')
  else element.setAttribute('aria-hidden', snapshot.ariaHidden)
  element.inert = snapshot.inert
  element.toggleAttribute('inert', snapshot.hadInert)
}

function reconcile(target: IsolationTarget, stack: Stack) {
  const top = stack.layers.at(-1)
  if (!top) {
    stack.observer?.disconnect()
    for (const [element, snapshot] of stack.snapshots) restore(element, snapshot)
    if (stack.overflow !== undefined && target instanceof HTMLElement) target.style.overflow = stack.overflow
    stacks.delete(target)
    return
  }
  for (const element of Array.from(target.children)) {
    if (!(element instanceof HTMLElement)) continue
    if (!stack.snapshots.has(element)) {
      stack.snapshots.set(element, {
        ariaHidden: element.getAttribute('aria-hidden'),
        inert: Boolean(element.inert),
        hadInert: element.hasAttribute('inert'),
      })
    }
    if (element === top.root) {
      restore(element, stack.snapshots.get(element)!)
    } else {
      element.setAttribute('aria-hidden', 'true')
      element.inert = true
      element.setAttribute('inert', '')
    }
  }
}

/** One baseline per modal stack; individual layers never restore another layer's snapshot. */
export function registerDialogLayer(
  root: HTMLElement,
  target: IsolationTarget = document.body,
  returnFocus: HTMLElement | null = document.activeElement instanceof HTMLElement ? document.activeElement : null,
) {
  let stack = stacks.get(target)
  if (!stack) {
    stack = { layers: [], snapshots: new Map() }
    stacks.set(target, stack)
    if (target instanceof HTMLElement) {
      stack.overflow = target.style.overflow
      target.style.overflow = 'hidden'
      const currentStack = stack
      stack.observer = new MutationObserver(() => reconcile(target, currentStack))
      stack.observer.observe(target, { childList: true })
    }
  }
  const inheritedTargets = stack.layers.at(-1)?.returnTargets ?? []
  const layer: Layer = { root, returnTargets: [...new Set([...(returnFocus ? [returnFocus] : []), ...inheritedTargets])] }
  stack.layers.push(layer)
  reconcile(target, stack)
  let released = false
  return {
    isTop: () => !released && stack.layers.at(-1) === layer,
    release: (restoreFocus = true) => {
      if (released) return
      const wasTop = stack.layers.at(-1) === layer
      released = true
      stack.layers = stack.layers.filter((entry) => entry !== layer)
      reconcile(target, stack)
      if (!wasTop || !restoreFocus) return
      const top = stack.layers.at(-1)
      const candidate = layer.returnTargets.find((element) =>
        element.isConnected && !element.closest('[inert]') && (!top || top.root.contains(element)),
      )
      const fallback = top?.root.querySelector<HTMLElement>('[data-dialog-root]')
      ;(candidate ?? fallback)?.focus()
    },
  }
}
