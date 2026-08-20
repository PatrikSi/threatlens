import { resolveApiErrorMessage } from '../api/errors'
import type { ArticleExportPreviewItem } from '../types/api'
import { formatDateTime } from '../utils/datetime'
import type { ExportPageController } from './useExportPageController'

export function ExportPreviewPanel({ controller }: { controller: ExportPageController }) {
  const { previewQuery } = controller
  const preview = previewQuery.data

  return (
    <section className="rounded-lg border border-slate/20 bg-white/80 dark:border-cyan-900/40 dark:bg-[#041612]/90">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-slate/15 px-3 py-3 dark:border-white/10 sm:px-4">
        <div>
          <h2 className="font-display text-lg">Matching articles</h2>
          {preview && (
            <p className="mt-0.5 text-xs text-slate dark:text-slate-400">
              Showing {preview.items.length.toLocaleString()} of {preview.total_matches.toLocaleString()}
            </p>
          )}
        </div>
        {previewQuery.isFetching && (
          <span role="status" className="text-xs font-semibold text-cyan-800 dark:text-cyan-200">
            Updating preview...
          </span>
        )}
      </header>

      {previewQuery.isLoading && !preview && <PreviewLoading />}
      {previewQuery.isError && (
        <div role="alert" className="m-3 rounded border border-red-300/70 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-800/60 dark:bg-red-950/30 dark:text-red-200 sm:m-4">
          {resolveApiErrorMessage(previewQuery.error, 'The export preview could not be loaded')}
        </div>
      )}
      {preview && preview.items.length === 0 && !previewQuery.isFetching && (
        <p className="px-3 py-8 text-center text-sm text-slate dark:text-slate-400">No articles match the current filters.</p>
      )}
      {preview && preview.items.length > 0 && (
        <>
          <div className="divide-y divide-slate/15 dark:divide-white/10 sm:hidden">
            {preview.items.map((item) => (
              <PreviewCard key={item.id} item={item} />
            ))}
          </div>
          <div className="hidden overflow-x-auto sm:block">
            <table className="min-w-full table-fixed text-left text-xs">
              <thead className="border-b border-slate/20 bg-slate/5 text-slate dark:border-white/10 dark:bg-white/[0.03] dark:text-slate-300">
                <tr>
                  <th className="w-[42%] px-3 py-2 font-semibold">Article</th>
                  <th className="w-[18%] px-3 py-2 font-semibold">Feed</th>
                  <th className="w-[17%] px-3 py-2 font-semibold">Published</th>
                  <th className="w-[13%] px-3 py-2 font-semibold">AI relevance</th>
                  <th className="w-[10%] px-3 py-2 text-right font-semibold">IOCs</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate/15 dark:divide-white/10">
                {preview.items.map((item) => (
                  <tr key={item.id} className="align-top hover:bg-slate/5 dark:hover:bg-white/[0.03]">
                    <td className="px-3 py-2.5">
                      <a
                        href={item.url}
                        target="_blank"
                        rel="noreferrer"
                        className="line-clamp-2 font-semibold text-ink hover:text-cyan dark:text-slate-100 dark:hover:text-cyan-100"
                      >
                        {item.title}
                      </a>
                      <div className="mt-1 flex flex-wrap gap-1">
                        {item.classification && <MetadataPill>{formatClassification(item.classification)}</MetadataPill>}
                        {item.tags.slice(0, 3).map((tag) => (
                          <MetadataPill key={tag}>{tag}</MetadataPill>
                        ))}
                        {item.tags.length > 3 && <MetadataPill>+{item.tags.length - 3}</MetadataPill>}
                      </div>
                    </td>
                    <td className="truncate px-3 py-2.5 text-slate dark:text-slate-300" title={item.feed_name}>
                      {item.feed_name}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2.5 text-slate dark:text-slate-300">
                      {formatDateTime(item.published_at ?? item.first_seen_at)}
                    </td>
                    <td className="px-3 py-2.5 text-slate dark:text-slate-300">{formatRelevance(item)}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums text-slate dark:text-slate-300">{item.ioc_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  )
}

function PreviewCard({ item }: { item: ArticleExportPreviewItem }) {
  return (
    <article className="px-3 py-3">
      <a href={item.url} target="_blank" rel="noreferrer" className="text-sm font-semibold leading-5 text-ink dark:text-slate-100">
        {item.title}
      </a>
      <div className="mt-1.5 grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-1 text-xs text-slate dark:text-slate-400">
        <span className="truncate">{item.feed_name}</span>
        <span className="whitespace-nowrap">{formatDateTime(item.published_at ?? item.first_seen_at)}</span>
        <span>{formatRelevance(item)}</span>
        <span className="text-right tabular-nums">{item.ioc_count} IOCs</span>
      </div>
      {(item.classification || item.tags.length > 0) && (
        <div className="mt-2 flex flex-wrap gap-1">
          {item.classification && <MetadataPill>{formatClassification(item.classification)}</MetadataPill>}
          {item.tags.slice(0, 4).map((tag) => (
            <MetadataPill key={tag}>{tag}</MetadataPill>
          ))}
          {item.tags.length > 4 && <MetadataPill>+{item.tags.length - 4}</MetadataPill>}
        </div>
      )}
    </article>
  )
}

function MetadataPill({ children }: { children: React.ReactNode }) {
  return <span className="rounded border border-slate/15 bg-slate/5 px-1.5 py-0.5 text-[10px] text-slate dark:border-white/10 dark:bg-white/[0.03] dark:text-slate-300">{children}</span>
}

function PreviewLoading() {
  return (
    <div role="status" className="space-y-2 p-3 sm:p-4">
      <p className="text-sm text-slate dark:text-slate-300">Loading matching articles...</p>
      <div className="h-10 animate-pulse rounded bg-slate/10 dark:bg-white/[0.05]" />
      <div className="h-10 animate-pulse rounded bg-slate/10 dark:bg-white/[0.05]" />
      <div className="h-10 animate-pulse rounded bg-slate/10 dark:bg-white/[0.05]" />
    </div>
  )
}

function formatRelevance(item: ArticleExportPreviewItem): string {
  if (item.ai_relevance_score === null) {
    return item.ai_relevance_label ? formatClassification(item.ai_relevance_label) : 'Not scored'
  }
  const score = `${Math.round(item.ai_relevance_score * 100)}%`
  return item.ai_relevance_label ? `${formatClassification(item.ai_relevance_label)} | ${score}` : score
}

function formatClassification(value: string): string {
  return value
    .split(/[_-]/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toLocaleUpperCase() + part.slice(1))
    .join(' ')
}
