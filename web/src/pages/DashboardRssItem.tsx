import { resolveApiErrorMessage } from '../api/errors'
import { sanitizeHref } from './dashboardContent'
import { RichContent } from './DashboardPageComponents'
import {
  aiRelevanceTone,
  formatAiRelevanceLabel,
  formatClassificationLabel,
  formatItemStatusLabel,
  formatPublishedAt,
  itemStatusTone,
} from './dashboardPageUtils'
import {
  resolveRssItemDetailClassName,
  selectVisibleItemTags,
} from './dashboardPanelPresentation'
import {
  HIDDEN_TAGS,
  type DashboardRssWindowFilters,
} from './dashboardSavedViews'
import type { DashboardPageController } from './useDashboardPageController'
import type { ItemDetail, ItemListEntry } from '../types/api'

type DashboardWindow = DashboardPageController['renderedWindows'][number]

interface DashboardRssItemProps {
  controller: DashboardPageController
  windowLayout: DashboardWindow
  rssFilters: DashboardRssWindowFilters
  item: ItemListEntry
}
export function DashboardRssItem({
  controller,
  windowLayout,
  rssFilters,
  item,
}: DashboardRssItemProps) {
  const {
    aiRelevanceEnabled, expandedItemIdsByWindowId, handleOpenArticlePreview,
    handleToggleItem, isWideLayout,
  } = controller
const expanded = expandedItemIdsByWindowId[windowLayout.id] === item.id
const compact = rssFilters.view_mode === 'compact'
const itemHref = sanitizeHref(item.canonical_url || item.url)
const displayableItemTags = item.tags.filter((tagName) => !HIDDEN_TAGS.has(tagName))
const visibleItemTags = selectVisibleItemTags(displayableItemTags, isWideLayout)
const hiddenItemTagCount = Math.max(0, displayableItemTags.length - visibleItemTags.length)

return (
  <article
    key={item.id}
    className={`tl-dashboard-rss-card relative rounded border text-slate-900 dark:text-slate-100 ${compact ? 'p-2' : 'p-2.5 sm:p-3'} transition ${
      expanded ? 'tl-row-selected' : 'tl-article-row'
    } ${item.is_read ? 'tl-article-row-read' : ''}`}
  >
    <div className="w-full text-left">
      <div className="flex flex-col gap-1.5 sm:flex-row sm:items-start sm:justify-between sm:gap-3">
        <h3 className={`${compact ? 'text-[14px]' : 'text-[14px] sm:text-[15px]'} line-clamp-2 pr-16 font-semibold leading-snug sm:pr-0`}>
          {itemHref ? (
            <a
              href={itemHref}
              target="_blank"
              rel="noreferrer"
              className="hover:text-cyan hover:underline"
              onClick={(event) => event.stopPropagation()}
            >
              {item.title}
            </a>
          ) : (
            <span>{item.title}</span>
          )}
        </h3>
        <div className="grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-2 sm:relative sm:flex sm:w-auto sm:shrink-0 sm:flex-nowrap sm:justify-end">
          <span className="hidden tl-source-text text-left text-xs font-semibold dark:text-slate-300 sm:inline sm:text-right">{item.feed_name}</span>
          <div className="min-w-0 pr-16 sm:hidden sm:pr-0">
            <p className="truncate text-[11px] leading-4">
              <span className="tl-source-text font-semibold dark:text-slate-300">{item.feed_name}</span>
              <span className="mx-1 text-slate/55" aria-hidden="true">·</span>
              <span className="text-slate dark:text-slate-300">{formatPublishedAt(item.published_at)}</span>
            </p>
          </div>
          {itemHref && (
            <button
              type="button"
              className="tl-dashboard-rss-preview absolute right-2 top-2 rounded border border-transparent bg-transparent px-1.5 py-1 text-xs font-semibold text-cyan hover:text-cyan dark:bg-transparent sm:right-0 sm:top-5 sm:whitespace-nowrap sm:border-slate/20 sm:bg-white sm:px-2 sm:font-normal sm:text-inherit sm:hover:border-cyan dark:sm:border-cyan-900/40 dark:sm:bg-[#041612]"
              onClick={() =>
                handleOpenArticlePreview(
                  {
                    itemId: item.id,
                    url: itemHref,
                    title: item.title,
                    sourceLabel: item.feed_name,
                  },
                  item.is_read,
                )
              }
            >
              <span className="sm:hidden">Preview</span>
              <span className="hidden sm:inline">Preview Original</span>
            </button>
          )}
        </div>
      </div>
      <button
        type="button"
        className="tl-dashboard-rss-toggle mt-1 w-full text-left text-slate-900 dark:text-slate-100"
        onClick={() => handleToggleItem(windowLayout.id, item.id, item.is_read)}
        aria-expanded={expanded}
        aria-controls={`rss-item-detail-${item.id}`}
      >
        <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-slate sm:gap-2 dark:text-slate-300">
          <span className="hidden sm:inline">Published {formatPublishedAt(item.published_at)}</span>
          {item.status !== 'content_fetched' && (
            <span className={`tl-chip ${itemStatusTone(item.status)}`}>{formatItemStatusLabel(item.status)}</span>
          )}
          {!item.is_read && <span className="tl-chip tl-chip-unread">Unread</span>}
          {item.is_starred && <span className="tl-chip tl-chip-starred">Starred</span>}
          {aiRelevanceEnabled && item.ai_relevance_label && (
            <span className={`tl-chip ${aiRelevanceTone(item.ai_relevance_label)}`}>
              AI {formatAiRelevanceLabel(item.ai_relevance_label)}
            </span>
          )}
          {visibleItemTags.map((tagName) => (
            <span
              key={`${item.id}-${tagName}`}
              className="tl-chip tl-chip-tag"
            >
              #{tagName}
            </span>
          ))}
          {!isWideLayout && hiddenItemTagCount > 0 && (
            <span className="tl-chip tl-chip-neutral">+{hiddenItemTagCount}</span>
          )}
        </div>
        {!compact && (
          <p className="mt-1 line-clamp-1 text-xs leading-[1.45] text-slate sm:mt-2 sm:line-clamp-2 sm:text-[13px] sm:leading-5 dark:text-slate-300">
            {item.summary || 'No summary available.'}
          </p>
        )}
      </button>
    </div>

    {expanded && (
      <DashboardRssItemDetail controller={controller} windowLayout={windowLayout} item={item} />
    )}
  </article>
)
}

function DashboardRssItemDetail({
  controller,
  windowLayout,
  item,
}: {
  controller: DashboardPageController
  windowLayout: DashboardWindow
  item: ItemListEntry
}) {
  const { detailQueriesByWindowId, handleToggleItem, isWideLayout } = controller
  const detailQuery = detailQueriesByWindowId[windowLayout.id]
  const detail = detailQuery?.data ?? null

  return (
      <div
        id={`rss-item-detail-${item.id}`}
        className={resolveRssItemDetailClassName(isWideLayout)}
      >
        <div className="sticky top-0 z-10 -mx-3 mb-3 flex items-center gap-3 border-b border-slate/20 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#03130f] lg:hidden">
            <button
              type="button"
              className="shrink-0 rounded border border-slate/20 px-3 py-1.5 text-xs font-semibold dark:border-cyan-900/40"
              onClick={() => handleToggleItem(windowLayout.id, item.id, true)}
            >
              Back
            </button>
            <div className="min-w-0">
              <p className="text-[11px] font-semibold uppercase text-slate dark:text-slate-300">RSS item</p>
              <p className="truncate text-sm font-semibold">{item.title}</p>
            </div>
          </div>
        {detailQuery?.isLoading && <p className="text-sm text-slate dark:text-slate-300">Loading article content...</p>}
        {detailQuery?.isError && (
          <p className="text-sm text-red-600">
            {resolveApiErrorMessage(detailQuery.error, 'Article details could not be loaded')}
          </p>
        )}

        {detail && detail.id === item.id && (
          <DashboardRssItemDetailContent controller={controller} detail={detail} />
        )}
      </div>
  )
}

function DashboardRssItemDetailContent({
  controller,
  detail,
}: {
  controller: DashboardPageController
  detail: ItemDetail
}) {
  return (
    <>
      <DashboardRssItemActions controller={controller} detail={detail} />
      <DashboardRssItemSummary detail={detail} />
      <DashboardRssItemAiInsight controller={controller} detail={detail} />
      <DashboardRssItemArticle controller={controller} detail={detail} />
      <DashboardRssItemNotes controller={controller} detail={detail} />
    </>
  )
}

function DashboardRssItemActions({
  controller,
  detail,
}: {
  controller: DashboardPageController
  detail: ItemDetail
}) {
  const { canManage, isItemActionPending, itemActionFeedbackByItemId, updateRead, updateStar } = controller
  const detailHref = sanitizeHref(detail.article?.final_url || detail.url || null)

  return (
    <>
<div className="flex flex-wrap items-center gap-2">
                                      {detailHref ? (
                                        <a
                                          className="rounded border border-slate/20 px-2 py-1 text-xs hover:border-cyan hover:text-cyan dark:border-cyan-900/40"
                                          href={detailHref}
                                          target="_blank"
                                          rel="noreferrer"
                                        >
                                          Open Source Link
                                        </a>
                                      ) : (
                                        <span className="rounded border border-slate/20 px-2 py-1 text-xs text-slate dark:border-cyan-900/40 dark:text-slate-400">
                                          Source link unavailable
                                        </span>
                                      )}
                                      <button
                                        className="rounded border border-slate/20 px-2 py-1 text-xs dark:border-cyan-900/40"
                                        disabled={!canManage || isItemActionPending('read', detail.id)}
                                        onClick={() =>
                                          updateRead.mutate({
                                            itemId: detail.id,
                                            isRead: !detail.state.is_read,
                                          })
                                        }
                                      >
                                        {isItemActionPending('read', detail.id)
                                          ? 'Saving...'
                                          : detail.state.is_read
                                            ? 'Mark Unread'
                                            : 'Mark Read'}
                                      </button>
                                      <button
                                        className="rounded border border-slate/20 px-2 py-1 text-xs dark:border-cyan-900/40"
                                        disabled={!canManage || isItemActionPending('star', detail.id)}
                                        onClick={() =>
                                          updateStar.mutate({
                                            itemId: detail.id,
                                            isStarred: !detail.state.is_starred,
                                          })
                                        }
                                      >
                                        {isItemActionPending('star', detail.id)
                                          ? 'Saving...'
                                          : detail.state.is_starred
                                            ? 'Unstar'
                                            : 'Star'}
                                      </button>
                                      {!canManage && <span className="text-xs text-amber-600 dark:text-amber-300">Viewer role is read-only.</span>}
                                    </div>
                                    {itemActionFeedbackByItemId[detail.id] && (
                                      <p
                                        role={itemActionFeedbackByItemId[detail.id]?.tone === 'error' ? 'alert' : 'status'}
                                        aria-live={itemActionFeedbackByItemId[detail.id]?.tone === 'error' ? 'assertive' : 'polite'}
                                        aria-atomic="true"
                                        className={`mt-2 text-xs ${
                                          itemActionFeedbackByItemId[detail.id]?.tone === 'success'
                                            ? 'text-emerald-700 dark:text-emerald-300'
                                            : 'text-red-600 dark:text-red-300'
                                        }`}
                                      >
                                        {itemActionFeedbackByItemId[detail.id]?.message}
                                      </p>
                                    )}
    </>
  )
}

function DashboardRssItemSummary({ detail }: { detail: ItemDetail }) {
  return (
<div className="tl-rss-detail-section tl-surface-muted mt-3 rounded p-3">
                                      <p className="text-xs font-semibold text-slate dark:text-slate-300">RSS summary</p>
                                      {detail.classification && (
                                        <p className="mt-1 text-xs text-slate dark:text-slate-300">
                                          Classification:{' '}
                                          <span className="font-semibold">
                                            {formatClassificationLabel(detail.classification.primary_category)}
                                          </span>{' '}
                                          ({Math.round(detail.classification.confidence * 100)}% confidence)
                                        </p>
                                      )}
                                      <div className="tl-rss-detail-reader rss-reader tl-reader-surface mt-2 rounded p-3">
                                        <RichContent content={detail.summary || 'No summary.'} itemId={detail.id} section="summary" />
                                      </div>
                                    </div>
  )
}

function DashboardRssItemAiInsight({
  controller,
  detail,
}: {
  controller: DashboardPageController
  detail: ItemDetail
}) {
  const { aiRelevanceEnabled, aiSummaryEnabled } = controller

  return (
    <>
{(aiSummaryEnabled || aiRelevanceEnabled) && detail.ai_insight?.status === 'ready' && (
                                      <div className="tl-rss-detail-section tl-surface-muted mt-3 rounded p-3">
                                        <p className="text-xs font-semibold text-slate dark:text-slate-300">AI insight</p>
                                        {aiRelevanceEnabled && detail.ai_insight.relevance_label && (
                                          <div className="mt-2 flex flex-wrap items-center gap-2">
                                            <span className={`tl-chip tl-chip-md ${aiRelevanceTone(detail.ai_insight.relevance_label)}`}>
                                              {formatAiRelevanceLabel(detail.ai_insight.relevance_label)} Relevance
                                            </span>
                                            {typeof detail.ai_insight.relevance_score === 'number' && (
                                              <span className="text-xs text-slate dark:text-white/65">
                                                Score {Math.round(detail.ai_insight.relevance_score * 100)}%
                                              </span>
                                            )}
                                          </div>
                                        )}
                                        {aiRelevanceEnabled && detail.ai_insight.relevance_reasons.length > 0 && (
                                          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-slate dark:text-white/75">
                                            {detail.ai_insight.relevance_reasons.map((reason, index) => (
                                              <li key={`${detail.id}-ai-reason-${index}`}>{reason}</li>
                                            ))}
                                          </ul>
                                        )}
                                        {aiSummaryEnabled && detail.ai_insight.summary_text && (
                                          <div className="tl-rss-detail-reader tl-reader-surface mt-3 rounded p-3">
                                            <RichContent
                                              content={detail.ai_insight.summary_text}
                                              itemId={detail.id}
                                              section="ai-summary"
                                            />
                                          </div>
                                        )}
                                        <p className="mt-2 text-xs text-slate dark:text-white/60">
                                          Generated {formatPublishedAt(detail.ai_insight.generated_at)}{detail.ai_insight.model ? ` via ${detail.ai_insight.model}` : ''}.
                                        </p>
                                      </div>
                                    )}
                                    {(aiSummaryEnabled || aiRelevanceEnabled) && detail.ai_insight?.status === 'error' && detail.ai_insight.error && (
                                      <div className="mt-3 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-950/20 dark:text-red-200">
                                        AI enrichment failed: {detail.ai_insight.error}
                                      </div>
                                    )}
    </>
  )
}

function DashboardRssItemArticle({
  controller,
  detail,
}: {
  controller: DashboardPageController
  detail: ItemDetail
}) {
  const {
    articleRetryFeedbackByItemId, canManage, isItemActionPending, retryArticleFetch,
  } = controller

  return (
<div className="tl-rss-detail-section tl-surface-muted mt-3 rounded p-3">
                                      <p className="text-xs font-semibold text-slate dark:text-slate-300">Full article</p>
                                      {detail.article?.text && detail.article.extraction_method === 'rss_summary_fallback' && (
                                        <p className="mt-2 text-xs text-amber-700 dark:text-amber-200">
                                          Showing RSS summary because full article extraction returned {detail.article.error ?? 'an error'}.
                                        </p>
                                      )}
                                      {detail.article?.text ? (
                                        <div className="tl-rss-detail-reader rss-reader tl-reader-surface mt-2 rounded p-3">
                                          <RichContent content={detail.article.text} itemId={detail.id} section="article" />
                                        </div>
                                      ) : (
                                        <p className="mt-2 text-sm text-slate dark:text-slate-300">No extracted article text available yet.</p>
                                      )}
                                      {detail.article?.error && detail.article.extraction_method !== 'rss_summary_fallback' && (
                                        <p className="mt-2 text-sm text-red-600">Extraction error: {detail.article.error}</p>
                                      )}
                                      {(!detail.article?.text || detail.article?.error) && (
                                        <div className="mt-3 flex flex-wrap items-center gap-2">
                                          <button
                                            className="rounded border border-slate/20 px-2 py-1 text-xs dark:border-cyan-900/40 disabled:opacity-50"
                                            disabled={
                                              !canManage ||
                                              isItemActionPending('retry', detail.id)
                                            }
                                            onClick={() => retryArticleFetch.mutate({ itemId: detail.id })}
                                          >
                                            {isItemActionPending('retry', detail.id)
                                              ? 'Queueing...'
                                              : detail.article?.error
                                                ? 'Retry Article Fetch'
                                                : 'Queue Article Fetch'}
                                          </button>
                                          {articleRetryFeedbackByItemId[detail.id] && (
                                            <span
                                              role={articleRetryFeedbackByItemId[detail.id]?.tone === 'error' ? 'alert' : 'status'}
                                              aria-live={articleRetryFeedbackByItemId[detail.id]?.tone === 'error' ? 'assertive' : 'polite'}
                                              aria-atomic="true"
                                              className={`text-xs ${
                                                articleRetryFeedbackByItemId[detail.id]?.tone === 'success'
                                                  ? 'text-emerald-700 dark:text-emerald-300'
                                                  : 'text-red-600 dark:text-red-300'
                                              }`}
                                            >
                                              {articleRetryFeedbackByItemId[detail.id]?.message}
                                            </span>
                                          )}
                                          {!canManage && (
                                            <span className="text-xs text-slate dark:text-slate-300">Read-only for viewer role.</span>
                                          )}
                                        </div>
                                      )}
                                    </div>
  )
}

function DashboardRssItemNotes({
  controller,
  detail,
}: {
  controller: DashboardPageController
  detail: ItemDetail
}) {
  const {
    canManage, isItemActionPending, noteDraftsByItemId, setNoteDraftsByItemId, updateNote,
  } = controller

  return (
<div className="tl-rss-detail-section tl-surface-muted mt-3 rounded p-3">
                                      <label className="text-xs font-semibold text-slate dark:text-slate-300">Notes</label>
                                      <textarea
                                        className="mt-1 h-20 w-full rounded border border-slate/20 bg-white px-2 py-1.5 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
                                        value={noteDraftsByItemId[detail.id] ?? detail.state.note ?? ''}
                                        onChange={(event) =>
                                          setNoteDraftsByItemId((current) => ({
                                            ...current,
                                            [detail.id]: event.target.value,
                                          }))
                                        }
                                        disabled={!canManage}
                                        aria-label={`Analyst notes for ${detail.title}`}
                                      />
                                      <div className="mt-2 flex items-center gap-2">
                                        <button
                                          className="rounded bg-ink px-2.5 py-1 text-xs font-semibold text-white disabled:opacity-50 dark:border dark:border-cyan-500/35 dark:bg-[var(--tl-accent-bg-strong)] dark:text-[var(--tl-accent-soft)]"
                                          onClick={() =>
                                            updateNote.mutate({
                                              itemId: detail.id,
                                              note: (noteDraftsByItemId[detail.id] ?? detail.state.note ?? '') || null,
                                            })
                                          }
                                          disabled={!canManage || isItemActionPending('note', detail.id)}
                                        >
                                          {isItemActionPending('note', detail.id) ? 'Saving...' : 'Save Notes'}
                                        </button>
                                        {!canManage && <span className="text-xs text-slate dark:text-slate-300">Read-only for viewer role.</span>}
                                      </div>
                                    </div>
  )
}
