/** Reveal a saved-budget or validation target without unmounting editor drafts. */
export function revealAiConfigurationTarget(target: HTMLElement | null) {
  if (!target) return
  for (let parent = target.parentElement; parent; parent = parent.parentElement) {
    if (parent instanceof HTMLDetailsElement) parent.open = true
  }
  target.focus()
  target.scrollIntoView?.({ block: 'center' })
}
