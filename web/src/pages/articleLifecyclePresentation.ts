import type { Article } from '../types/items'
import { formatDateTime } from '../utils/datetime'

export function articleUnavailableMessage(article: Article): string {
  if (article.content_purged_at) {
    return `Fetched article content was removed by data lifecycle on ${formatDateTime(article.content_purged_at)}; source metadata remains.`
  }
  return 'No extracted article text available yet.'
}

export function articleFetchActionLabel(
  article: Article | null,
  pending: boolean,
): string {
  if (pending) return 'Queueing...'
  if (article?.content_purged_at) return 'Fetch article again'
  if (article?.error) return 'Retry Article Fetch'
  return 'Queue Article Fetch'
}
