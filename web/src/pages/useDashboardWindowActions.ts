import {
  type Dispatch,
  type MouseEvent as ReactMouseEvent,
  type RefObject,
  type SetStateAction,
} from 'react'

import {
  applyDragMagnetSnap,
  clamp,
  clearItemFeedback,
  getWindowContainerDimensions,
} from './dashboardPageUtils'
import {
  createWindowLayout,
  getSnapRect,
  MAX_DASHBOARD_WINDOWS,
  resolveFloatingPanelRect,
  resolveWindowRect,
  withPanelRectPercentages,
  WINDOW_MIN_HEIGHT,
  WINDOW_MIN_WIDTH,
  type DashboardWindow,
  type DashboardWindowSnap,
  type DashboardWindowType,
} from './dashboardSavedViews'

type FeedbackByItemId = Record<string, { tone: 'success' | 'error'; message: string }>

type DashboardWindowActionsOptions = {
  closeAddWindowMenu: (restoreFocus?: boolean) => void
  expandedItemIdsByWindowId: Record<string, string>
  isWideLayout: boolean
  markItemReadIfNeeded: (itemId: string, isRead: boolean) => void
  renameWindowDraft: string
  renamingWindowId: string | null
  rootRef: RefObject<HTMLDivElement | null>
  setArticleRetryFeedbackByItemId: Dispatch<SetStateAction<FeedbackByItemId>>
  setExpandedItemIdsByWindowId: Dispatch<SetStateAction<Record<string, string>>>
  setItemActionFeedbackByItemId: Dispatch<SetStateAction<FeedbackByItemId>>
  setMobileWindowControlsOpenById: Dispatch<SetStateAction<Record<string, boolean>>>
  setOpenWindowMenuId: Dispatch<SetStateAction<string | null>>
  setRenameWindowDraft: Dispatch<SetStateAction<string>>
  setRenamingWindowId: Dispatch<SetStateAction<string | null>>
  setWindows: Dispatch<SetStateAction<DashboardWindow[]>>
  windows: DashboardWindow[]
}

export function useDashboardWindowActions({
  closeAddWindowMenu,
  expandedItemIdsByWindowId,
  isWideLayout,
  markItemReadIfNeeded,
  renameWindowDraft,
  renamingWindowId,
  rootRef,
  setArticleRetryFeedbackByItemId,
  setExpandedItemIdsByWindowId,
  setItemActionFeedbackByItemId,
  setMobileWindowControlsOpenById,
  setOpenWindowMenuId,
  setRenameWindowDraft,
  setRenamingWindowId,
  setWindows,
  windows,
}: DashboardWindowActionsOptions) {
  const handleToggleItem = (windowId: string, itemId: string, isRead: boolean) => {
    const isOpening = expandedItemIdsByWindowId[windowId] !== itemId
    clearItemFeedback(setItemActionFeedbackByItemId, itemId)
    clearItemFeedback(setArticleRetryFeedbackByItemId, itemId)
    setExpandedItemIdsByWindowId((current) => {
      if (current[windowId] === itemId) {
        const next = { ...current }
        delete next[windowId]
        return next
      }
      return {
        ...current,
        [windowId]: itemId,
      }
    })
    if (isOpening) {
      markItemReadIfNeeded(itemId, isRead)
    }
  }

  const setWindowSnap = (windowId: string, snap: DashboardWindowSnap) => {
    if (!isWideLayout) return
    const { width, height } = getWindowContainerDimensions(rootRef.current)

    setWindows((current) =>
      current.map((window) => {
        if (window.id !== windowId) return window
        if (snap === 'free') {
          const resolvedRect = resolveWindowRect(window, width, height)
          return {
            ...window,
            snap,
            rect: withPanelRectPercentages(resolvedRect, width, height),
          }
        }

        return {
          ...window,
          snap,
          rect: getSnapRect(snap, width, height),
        }
      }),
    )
  }

  const addWindow = (type: DashboardWindowType) => {
    const { width, height } = getWindowContainerDimensions(rootRef.current)
    setWindows((current) => {
      if (current.length >= MAX_DASHBOARD_WINDOWS) {
        return current
      }
      const nextIndex = current.filter((window) => window.type === type).length + 1
      return [...current, createWindowLayout(type, nextIndex, width, height)]
    })
    closeAddWindowMenu(true)
  }

  const removeWindow = (windowId: string) => {
    setWindows((current) => {
      if (current.length <= 1) {
        return current
      }
      return current.filter((window) => window.id !== windowId)
    })
  }

  const openRenameWindow = (windowId: string) => {
    const target = windows.find((window) => window.id === windowId)
    if (!target) return

    setOpenWindowMenuId(null)
    setRenamingWindowId(windowId)
    setRenameWindowDraft(target.title)
  }

  const closeRenameWindow = () => {
    setRenamingWindowId(null)
    setRenameWindowDraft('')
  }

  const saveRenamedWindow = () => {
    if (!renamingWindowId) {
      return
    }

    const normalized = renameWindowDraft.trim().slice(0, 80)
    if (!normalized) {
      return
    }

    setWindows((current) =>
      current.map((window) => (window.id === renamingWindowId ? { ...window, title: normalized } : window)),
    )
    closeRenameWindow()
  }

  const toggleWindowControls = (windowId: string) => {
    setWindows((current) =>
      current.map((window) =>
        window.id === windowId ? { ...window, controls_collapsed: !window.controls_collapsed } : window,
      ),
    )
  }

  const toggleMobileWindowControls = (windowId: string) => {
    setMobileWindowControlsOpenById((current) => ({
      ...current,
      [windowId]: !current[windowId],
    }))
  }

  const updateWindowScratchNote = (windowId: string, scratchNote: string) => {
    setWindows((current) =>
      current.map((window) => (window.id === windowId ? { ...window, scratch_note: scratchNote } : window)),
    )
  }

  const bringWindowToFront = (windowId: string) => {
    setWindows((current) => {
      const target = current.find((entry) => entry.id === windowId)
      if (!target) return current
      const rest = current.filter((entry) => entry.id !== windowId)
      return [...rest, target]
    })
  }

  const startWindowDrag = (event: ReactMouseEvent<HTMLDivElement>, windowId: string) => {
    if (!isWideLayout) return

    const rootBounds = rootRef.current?.getBoundingClientRect()
    if (!rootBounds) return

    const targetWindow = windows.find((entry) => entry.id === windowId)
    if (!targetWindow || targetWindow.snap !== 'free') {
      return
    }

    event.preventDefault()

    const startMouseX = event.clientX
    const startMouseY = event.clientY
    const startRect = resolveFloatingPanelRect(targetWindow.rect, rootBounds.width, rootBounds.height)

    const onMove = (moveEvent: MouseEvent) => {
      const deltaX = moveEvent.clientX - startMouseX
      const deltaY = moveEvent.clientY - startMouseY

      const maxX = Math.max(0, rootBounds.width - startRect.width)
      const maxY = Math.max(0, rootBounds.height - startRect.height)
      const candidateX = clamp(startRect.x + deltaX, 0, maxX)
      const candidateY = clamp(startRect.y + deltaY, 0, maxY)
      setWindows((current) => {
        const otherRects = current
          .filter((layout) => layout.id !== windowId)
          .map((layout) => resolveWindowRect(layout, rootBounds.width, rootBounds.height))

        const snapped = applyDragMagnetSnap(
          {
            x: candidateX,
            y: candidateY,
            width: startRect.width,
            height: startRect.height,
          },
          otherRects,
          rootBounds.width,
          rootBounds.height,
          maxX,
          maxY,
        )

        return current.map((window) => {
          if (window.id !== windowId) return window
          return {
            ...window,
            rect: withPanelRectPercentages(
              {
                ...startRect,
                x: snapped.x,
                y: snapped.y,
              },
              rootBounds.width,
              rootBounds.height,
            ),
          }
        })
      })
    }

    const onUp = () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }

    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }

  const startWindowResize = (event: ReactMouseEvent<HTMLButtonElement>, windowId: string) => {
    if (!isWideLayout) return

    const rootBounds = rootRef.current?.getBoundingClientRect()
    if (!rootBounds) return

    const targetWindow = windows.find((entry) => entry.id === windowId)
    if (!targetWindow || targetWindow.snap !== 'free') {
      return
    }

    event.preventDefault()
    event.stopPropagation()

    const startMouseX = event.clientX
    const startMouseY = event.clientY
    const startRect = resolveFloatingPanelRect(targetWindow.rect, rootBounds.width, rootBounds.height)

    const onMove = (moveEvent: MouseEvent) => {
      const deltaX = moveEvent.clientX - startMouseX
      const deltaY = moveEvent.clientY - startMouseY

      const maxWidth = rootBounds.width - startRect.x
      const maxHeight = rootBounds.height - startRect.y
      setWindows((current) =>
        current.map((window) => {
          if (window.id !== windowId) return window
          return {
            ...window,
            rect: withPanelRectPercentages(
              {
                ...startRect,
                width: clamp(startRect.width + deltaX, WINDOW_MIN_WIDTH, maxWidth),
                height: clamp(startRect.height + deltaY, WINDOW_MIN_HEIGHT, maxHeight),
              },
              rootBounds.width,
              rootBounds.height,
            ),
          }
        }),
      )
    }

    const onUp = () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }

    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }

  return {
    addWindow,
    bringWindowToFront,
    closeRenameWindow,
    handleToggleItem,
    openRenameWindow,
    removeWindow,
    saveRenamedWindow,
    setWindowSnap,
    startWindowDrag,
    startWindowResize,
    toggleMobileWindowControls,
    toggleWindowControls,
    updateWindowScratchNote,
  }
}
