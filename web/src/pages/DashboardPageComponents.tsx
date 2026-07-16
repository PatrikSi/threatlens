import { type PointerEvent as ReactPointerEvent } from 'react'

import { buildApiUrl } from '../api/client'
import { looksLikeHtml, parseArticleBlocks, sanitizeHtmlFragment, stripHtml } from './dashboardContent'
import { resolveWindowRect, type DashboardWindow, type DashboardWindowType } from './dashboardSavedViews'

export interface ArticlePreviewState {
  itemId: string
  url: string
  title: string
  sourceLabel: string
}

const SAVED_VIEW_THUMBNAIL_WIDTH = 148
const SAVED_VIEW_THUMBNAIL_HEIGHT = 96

export function ArticlePreviewDrawer({
  preview,
  frameState,
  width,
  minWidth,
  maxWidth,
  onResizeStart,
  onResizeBy,
  isResizing,
  onFrameLoad,
  onClose,
}: {
  preview: ArticlePreviewState
  frameState: 'loading' | 'loaded' | 'possibly_blocked'
  width: number
  minWidth: number
  maxWidth: number
  onResizeStart: (event: ReactPointerEvent<HTMLElement>) => void
  onResizeBy: (delta: number) => void
  isResizing: boolean
  onFrameLoad: () => void
  onClose: () => void
}) {
  const previewFrameUrl = buildApiUrl(`/items/${encodeURIComponent(preview.itemId)}/article-preview`)

  return (
    <aside
      role="dialog"
      aria-labelledby="article-preview-title"
      className="fixed inset-y-0 right-0 z-50 flex max-w-full flex-col border-l border-slate/20 bg-white shadow-2xl dark:border-cyan-900/50 dark:bg-[#03130f]"
      style={{ width: `${width}px` }}
    >
      <div
        role="separator"
        aria-label="Resize article preview width"
        aria-orientation="vertical"
        aria-valuemin={minWidth}
        aria-valuemax={maxWidth}
        aria-valuenow={width}
        tabIndex={0}
        className="absolute left-0 top-1/2 z-10 flex h-24 w-4 -translate-x-1/2 -translate-y-1/2 cursor-ew-resize touch-none items-center justify-center rounded-full border border-slate/20 bg-white shadow-md hover:border-cyan hover:text-cyan focus-visible:ring-2 focus-visible:ring-cyan/50 dark:border-cyan-900/50 dark:bg-[#062019] dark:hover:border-cyan-500/60"
        onPointerDown={onResizeStart}
        onKeyDown={(event) => {
          if (event.key === 'ArrowLeft') {
            event.preventDefault()
            onResizeBy(32)
          } else if (event.key === 'ArrowRight') {
            event.preventDefault()
            onResizeBy(-32)
          } else if (event.key === 'Home') {
            event.preventDefault()
            onResizeBy(minWidth - width)
          } else if (event.key === 'End') {
            event.preventDefault()
            onResizeBy(maxWidth - width)
          }
        }}
      >
        <span className="h-12 w-1 rounded-full bg-slate/35 dark:bg-cyan-700/70" />
      </div>
      <div className="flex items-start justify-between gap-3 border-b border-slate/20 px-4 py-3 dark:border-cyan-900/40">
        <div className="min-w-0">
          <p className="text-xs font-semibold uppercase text-slate dark:text-slate-400">{preview.sourceLabel}</p>
          <h2 id="article-preview-title" className="truncate font-display text-lg font-semibold text-ink dark:text-slate-100">
            Original article
          </h2>
          <p className="mt-0.5 truncate text-sm text-slate dark:text-slate-300">{preview.title}</p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <a
            href={preview.url}
            target="_blank"
            rel="noreferrer"
            className="rounded border border-slate/20 px-2.5 py-1.5 text-xs font-semibold hover:border-cyan hover:text-cyan dark:border-cyan-900/40"
          >
            Open Original
          </a>
          <button
            type="button"
            className="rounded border border-slate/20 px-2.5 py-1.5 text-xs font-semibold dark:border-cyan-900/40"
            onClick={onClose}
            aria-label="Close original article preview"
          >
            Close
          </button>
        </div>
      </div>

      {frameState !== 'loaded' && (
        <div
          role={frameState === 'possibly_blocked' ? 'status' : undefined}
          className={`border-b px-4 py-2 text-xs ${
            frameState === 'possibly_blocked'
              ? 'border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900/40 dark:bg-amber-950/20 dark:text-amber-200'
              : 'border-slate/20 bg-slate-50 text-slate dark:border-cyan-900/40 dark:bg-white/[0.03] dark:text-slate-300'
          }`}
        >
          {frameState === 'possibly_blocked'
            ? 'Preview is still loading. Open the original source if it does not render here.'
            : 'Loading original site...'}
        </div>
      )}

      <div className={`min-h-0 flex-1 bg-white dark:bg-[#020b09] ${isResizing ? 'cursor-ew-resize select-none' : ''}`}>
        <iframe
          key={preview.url}
          title={`Original article preview: ${preview.title}`}
          src={previewFrameUrl}
          className={`h-full w-full border-0 bg-white ${isResizing ? 'pointer-events-none' : ''}`}
          sandbox="allow-popups allow-popups-to-escape-sandbox"
          referrerPolicy="no-referrer"
          onLoad={onFrameLoad}
        />
      </div>
    </aside>
  )
}


export function SavedViewThumbnail({ windows }: { windows: DashboardWindow[] }) {
  const previewContainerWidth = 1120
  const previewContainerHeight = 680

  return (
    <div
      className="relative shrink-0 overflow-hidden rounded border border-slate/20 bg-white/90 dark:border-cyan-900/40 dark:bg-[#041612]"
      style={{ width: SAVED_VIEW_THUMBNAIL_WIDTH, height: SAVED_VIEW_THUMBNAIL_HEIGHT }}
    >
      {windows.slice(0, 14).map((windowLayout) => {
        const rect = resolveWindowRect(windowLayout, previewContainerWidth, previewContainerHeight)
        const left = Math.max(0, (rect.x / previewContainerWidth) * SAVED_VIEW_THUMBNAIL_WIDTH)
        const top = Math.max(0, (rect.y / previewContainerHeight) * SAVED_VIEW_THUMBNAIL_HEIGHT)
        const width = Math.max(6, (rect.width / previewContainerWidth) * SAVED_VIEW_THUMBNAIL_WIDTH)
        const height = Math.max(6, (rect.height / previewContainerHeight) * SAVED_VIEW_THUMBNAIL_HEIGHT)

        return (
          <div
            key={windowLayout.id}
            className={`absolute overflow-hidden rounded-[3px] border ${thumbnailWindowTone(windowLayout.type)}`}
            style={{ left, top, width, height }}
            title={windowLayout.title}
          />
        )
      })}
      {windows.length > 14 && (
        <div className="absolute bottom-1 right-1 rounded border border-slate/40 bg-white/85 px-1 text-[10px] font-semibold text-slate-700 dark:border-cyan-900/40 dark:bg-[#041612]/90 dark:text-slate-200">
          +{windows.length - 14}
        </div>
      )}
    </div>
  )
}

function thumbnailWindowTone(type: DashboardWindowType): string {
  if (type === 'rss') return 'border-cyan-500/40 bg-cyan-400/30 dark:bg-cyan-500/35'
  if (type === 'alerts') return 'border-amber-500/40 bg-amber-300/35 dark:bg-amber-500/35'
  if (type === 'daily_brief') return 'border-slate-400/45 bg-slate-200/70 dark:border-cyan-900/40 dark:bg-cyan-500/20'
  return 'border-slate-400/40 bg-slate-300/45 dark:border-slate-600/45 dark:bg-slate-500/30'
}


export function RichContent({
  content,
  itemId,
  section,
}: {
  content: string
  itemId: string
  section: 'summary' | 'article' | 'ai-summary'
}) {
  const trimmed = content.trim()
  if (!trimmed) {
    return <p>No content.</p>
  }

  if (!looksLikeHtml(trimmed)) {
    return renderArticleBlocks(trimmed, `${itemId}-${section}`)
  }

  const sanitized = sanitizeHtmlFragment(trimmed)
  if (!sanitized) {
    return renderArticleBlocks(stripHtml(trimmed), `${itemId}-${section}`)
  }

  return <div className="rss-rich" dangerouslySetInnerHTML={{ __html: sanitized }} />
}

function renderArticleBlocks(text: string, itemId: string) {
  const blocks = parseArticleBlocks(text)

  return blocks.map((block, index) => {
    if (block.kind === 'heading') {
      return (
        <h4 key={`${itemId}-heading-${index}`} className="rss-heading">
          {block.text}
        </h4>
      )
    }

    if (block.kind === 'bullet-list') {
      return (
        <ul key={`${itemId}-ul-${index}`} className="rss-list">
          {block.items.map((entry, entryIndex) => (
            <li key={`${itemId}-ul-${index}-${entryIndex}`}>{entry}</li>
          ))}
        </ul>
      )
    }

    if (block.kind === 'numbered-list') {
      return (
        <ol key={`${itemId}-ol-${index}`} className="rss-list rss-list-ordered">
          {block.items.map((entry, entryIndex) => (
            <li key={`${itemId}-ol-${index}-${entryIndex}`}>{entry}</li>
          ))}
        </ol>
      )
    }

    if (block.kind === 'quote') {
      return (
        <blockquote key={`${itemId}-quote-${index}`} className="rss-quote">
          {block.text}
        </blockquote>
      )
    }

    return <p key={`${itemId}-paragraph-${index}`}>{block.text}</p>
  })
}

