import { resolveApiErrorMessage } from '../api/errors'
import { sanitizeHref } from './dashboardContent'
import {
  aiRelevanceTone,
  formatAiRelevanceLabel,
  formatDailyBriefOptionLabel,
  formatPublishedAt,
} from './dashboardPageUtils'
import { WINDOW_TYPE_META } from './dashboardPanelPresentation'
import type { DashboardPageController } from './useDashboardPageController'

type DashboardWindow = DashboardPageController['renderedWindows'][number]

export function DashboardDailyBriefPanel({
  controller,
  windowLayout,
}: {
  controller: DashboardPageController
  windowLayout: DashboardWindow
}) {
  const { dailyBriefHistoryQuery, updateWindowDailyBriefSelection } = controller
  const windowMeta = WINDOW_TYPE_META[windowLayout.type]

  return (
                <div className={`flex min-h-0 flex-1 flex-col p-2 sm:p-3 ${windowMeta.panelClassName}`}>
                  {dailyBriefHistoryQuery.isLoading && <p className="text-sm text-slate dark:text-white/75">Loading daily brief...</p>}
                  {dailyBriefHistoryQuery.isError && (
                    <p className="text-sm text-red-600">
                      {resolveApiErrorMessage(dailyBriefHistoryQuery.error, 'The daily brief could not be loaded')}
                    </p>
                  )}
                  {!dailyBriefHistoryQuery.isLoading && !(dailyBriefHistoryQuery.data?.length ?? 0) && (
                    <p className="text-sm text-slate dark:text-white/75">
                      No AI daily brief is available yet. An admin can generate one from the AI page after the endpoint is configured.
                    </p>
                  )}
                  {(dailyBriefHistoryQuery.data?.length ?? 0) > 0 && (() => {
                    const availableBriefs = dailyBriefHistoryQuery.data ?? []
                    const selectedBrief =
                      availableBriefs.find((brief) => brief.id === windowLayout.selected_daily_brief_id) ?? availableBriefs[0]

                    if (!selectedBrief) {
                      return null
                    }

                    return (
                    <div className="min-h-0 flex-1 space-y-3 overflow-auto">
                      <div className="rounded border border-slate/20 bg-white/92 p-2.5 sm:p-3 dark:border-cyan-900/40 dark:bg-[#041612]/92">
                        <div className="flex flex-wrap items-center justify-between gap-3">
                          <label className="flex min-w-0 flex-1 flex-col items-stretch gap-1 text-sm sm:min-w-[220px] sm:flex-row sm:items-center sm:gap-2">
                            <span className="text-xs font-semibold text-slate dark:text-white/55">Briefing</span>
                            <select
                              className="w-full rounded border border-slate/20 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#041612]"
                              value={selectedBrief.id}
                              onChange={(event) => updateWindowDailyBriefSelection(windowLayout.id, event.target.value)}
                              aria-label={`${windowLayout.title} briefing selection`}
                            >
                              {availableBriefs.map((brief) => (
                                <option key={brief.id} value={brief.id}>
                                  {formatDailyBriefOptionLabel(brief)}
                                </option>
                              ))}
                            </select>
                          </label>
                        </div>
                        <p className="mt-3 text-xs text-slate dark:text-white/60">
                          Generated {formatPublishedAt(selectedBrief.generated_at)} for {selectedBrief.item_count} items covering{' '}
                          {formatPublishedAt(selectedBrief.window_start)} to {formatPublishedAt(selectedBrief.window_end)}.
                        </p>
                      </div>

                      {selectedBrief.key_points.length > 0 && (
                        <div className="tl-daily-brief-section rounded border border-slate/20 bg-white/90 p-2.5 sm:p-3 dark:border-cyan-900/40 dark:bg-[#041612]/92">
                          <p className="text-xs font-semibold text-slate dark:text-slate-300">Key points</p>
                          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-slate dark:text-white/75">
                            {selectedBrief.key_points.map((point, index) => (
                              <li key={`${windowLayout.id}-brief-point-${index}`}>{point}</li>
                            ))}
                          </ul>
                        </div>
                      )}

                      {selectedBrief.recommended_actions.length > 0 && (
                        <div className="tl-daily-brief-section rounded border border-slate/20 bg-white/90 p-2.5 sm:p-3 dark:border-cyan-900/40 dark:bg-[#041612]/92">
                          <p className="text-xs font-semibold text-slate dark:text-slate-300">Recommended actions</p>
                          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-slate dark:text-white/75">
                            {selectedBrief.recommended_actions.map((action, index) => (
                              <li key={`${windowLayout.id}-brief-action-${index}`}>{action}</li>
                            ))}
                          </ul>
                        </div>
                      )}

                      {selectedBrief.items.length > 0 && (
                        <div className="tl-daily-brief-section rounded border border-slate/20 bg-white/90 p-2.5 sm:p-3 dark:border-cyan-900/40 dark:bg-[#041612]/92">
                          <p className="text-xs font-semibold text-slate dark:text-slate-300">Referenced items</p>
                          <div className="mt-2 space-y-2">
                            {selectedBrief.items.map((item) => (
                              <article key={item.id} className="tl-daily-brief-item rounded border border-slate/20 p-2 dark:border-cyan-900/40">
                                <div className="flex items-start justify-between gap-2">
                                  {sanitizeHref(item.url) ? (
                                    <a
                                      href={sanitizeHref(item.url) ?? undefined}
                                      target="_blank"
                                      rel="noreferrer"
                                      className="text-sm font-semibold hover:text-cyan hover:underline dark:hover:text-cyan-200"
                                    >
                                      {item.title}
                                    </a>
                                  ) : (
                                    <span className="text-sm font-semibold">{item.title}</span>
                                  )}
                                  {item.relevance_label && (
                                    <span className={`tl-chip shrink-0 ${aiRelevanceTone(item.relevance_label)}`}>
                                      {formatAiRelevanceLabel(item.relevance_label)}
                                    </span>
                                  )}
                                </div>
                                <p className="mt-1 text-xs text-slate dark:text-white/65">
                                  {item.feed_name} • Published {formatPublishedAt(item.published_at)}
                                </p>
                              </article>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                    )
                  })()}
                </div>

  )
}
