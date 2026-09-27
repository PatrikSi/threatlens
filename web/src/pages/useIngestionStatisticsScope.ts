import { useCallback, useMemo, type SetStateAction } from 'react'
import { useSearchParams } from 'react-router-dom'

export function useIngestionStatisticsScope() {
  const [params, setParams] = useSearchParams()
  const requestedDays = Number(params.get('ingestion_days'))
  const days = [7, 30, 90, 180].includes(requestedDays) ? requestedDays : 30
  const rawFeeds = params.get('ingestion_feeds') ?? ''
  const selectedFeedIds = useMemo(() => parseFeedSelection(rawFeeds), [rawFeeds])
  const setDays = useCallback((value: number) => setParams((current) => {
    const next = new URLSearchParams(current)
    if (value === 30) next.delete('ingestion_days')
    else next.set('ingestion_days', String(value))
    return next
  }), [setParams])
  const setSelectedFeedIds = useCallback((update: SetStateAction<string[]>) => setParams((current) => {
    const previous = parseFeedSelection(current.get('ingestion_feeds') ?? '')
    const nextIds = typeof update === 'function' ? update(previous) : update
    const next = new URLSearchParams(current)
    if (nextIds.length) next.set('ingestion_feeds', [...new Set(nextIds)].sort().join(','))
    else next.delete('ingestion_feeds')
    return next
  }), [setParams])
  return { days, setDays, selectedFeedIds, setSelectedFeedIds }
}

function parseFeedSelection(value: string): string[] {
  return [...new Set(value.split(',').map((id) => id.trim()).filter(Boolean))].sort()
}
