import { useEffect, useRef } from 'react'

/** A preview is nonmodal: move focus once without trapping the dashboard. */
export function useArticlePreviewFocus() {
  const panelRef = useRef<HTMLElement>(null)
  const closeRef = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    const panel = panelRef.current
    const opener =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null
    const frame = window.requestAnimationFrame(() => {
      // Modal layers isolate the dashboard with inert. Their focus coordinator
      // remains authoritative when a modal is opened over a preview.
      if (panel?.isConnected && !panel.closest('[inert]'))
        closeRef.current?.focus()
    })
    return () => {
      window.cancelAnimationFrame(frame)
      const active = document.activeElement
      const focusLeftWithPreview =
        active === document.body || Boolean(active && panel?.contains(active))
      if (
        focusLeftWithPreview &&
        opener?.isConnected &&
        !opener.closest('[inert]')
      ) {
        opener.focus()
      }
    }
  }, [])
  return { panelRef, closeRef }
}
