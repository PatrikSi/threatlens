import {
  type PointerEvent as ReactPointerEvent,
  useEffect,
  useRef,
  useState,
} from 'react'

import { type ArticlePreviewState } from './DashboardPageComponents'
import {
  clampArticlePreviewWidth,
  loadArticlePreviewWidth,
  persistArticlePreviewWidth,
} from './dashboardPageUtils'

export function useArticlePreview() {
  const [articlePreview, setArticlePreview] = useState<ArticlePreviewState | null>(null)
  const [articlePreviewFrameState, setArticlePreviewFrameState] = useState<'loading' | 'loaded' | 'possibly_blocked'>(
    'loading',
  )
  const [articlePreviewWidth, setArticlePreviewWidth] = useState(() => loadArticlePreviewWidth())
  const [isArticlePreviewResizing, setIsArticlePreviewResizing] = useState(false)
  const resizeCleanupRef = useRef<(() => void) | null>(null)

  const openArticlePreview = (preview: ArticlePreviewState) => {
    setArticlePreviewFrameState('loading')
    setArticlePreview(preview)
  }

  const closeArticlePreview = () => {
    setArticlePreview(null)
  }

  const updateArticlePreviewWidth = (width: number) => {
    const nextWidth = clampArticlePreviewWidth(width)
    setArticlePreviewWidth(nextWidth)
    persistArticlePreviewWidth(nextWidth)
  }

  const adjustArticlePreviewWidth = (delta: number) => {
    updateArticlePreviewWidth(articlePreviewWidth + delta)
  }

  const startArticlePreviewResize = (event: ReactPointerEvent<HTMLElement>) => {
    event.preventDefault()
    event.stopPropagation()

    resizeCleanupRef.current?.()

    const resizeHandle = event.currentTarget
    const pointerId = event.pointerId
    const startX = event.clientX
    const startWidth = articlePreviewWidth
    const previousCursor = document.body.style.cursor
    const previousUserSelect = document.body.style.userSelect

    try {
      resizeHandle.setPointerCapture(pointerId)
    } catch {
      // Pointer capture is best-effort in tests and older browser engines.
    }

    document.body.style.cursor = 'ew-resize'
    document.body.style.userSelect = 'none'
    setIsArticlePreviewResizing(true)

    const handlePointerMove = (moveEvent: PointerEvent) => {
      if (moveEvent.pointerId !== pointerId) return
      moveEvent.preventDefault()
      updateArticlePreviewWidth(startWidth + startX - moveEvent.clientX)
    }

    const cleanup = () => {
      document.removeEventListener('pointermove', handlePointerMove, true)
      document.removeEventListener('pointerup', handlePointerEnd, true)
      document.removeEventListener('pointercancel', handlePointerEnd, true)
      resizeHandle.removeEventListener('lostpointercapture', cleanup)
      window.removeEventListener('blur', cleanup)
      try {
        if (resizeHandle.hasPointerCapture(pointerId)) {
          resizeHandle.releasePointerCapture(pointerId)
        }
      } catch {
        // The browser may already have released capture by this point.
      }
      document.body.style.cursor = previousCursor
      document.body.style.userSelect = previousUserSelect
      setIsArticlePreviewResizing(false)
      resizeCleanupRef.current = null
    }

    const handlePointerEnd = (endEvent: PointerEvent) => {
      if (endEvent.pointerId === pointerId) {
        cleanup()
      }
    }

    resizeCleanupRef.current = cleanup
    document.addEventListener('pointermove', handlePointerMove, true)
    document.addEventListener('pointerup', handlePointerEnd, true)
    document.addEventListener('pointercancel', handlePointerEnd, true)
    resizeHandle.addEventListener('lostpointercapture', cleanup)
    window.addEventListener('blur', cleanup)
  }

  useEffect(() => () => resizeCleanupRef.current?.(), [])

  useEffect(() => {
    if (!articlePreview) {
      return
    }

    const blockedNoticeTimeout = window.setTimeout(() => {
      setArticlePreviewFrameState((current) => (current === 'loading' ? 'possibly_blocked' : current))
    }, 5000)
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setArticlePreview(null)
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => {
      window.clearTimeout(blockedNoticeTimeout)
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [articlePreview])

  return {
    adjustArticlePreviewWidth,
    articlePreview,
    articlePreviewFrameState,
    articlePreviewWidth,
    closeArticlePreview,
    isArticlePreviewResizing,
    openArticlePreview,
    setArticlePreviewFrameState,
    setArticlePreviewWidth,
    startArticlePreviewResize,
  }
}
