import { ItemListEntry } from '../types/api'

export type AIReprocessQueueRequest = {
  days: number | null
  limit: number
  start_time: string | null
  end_time: string | null
  feed_ids: string[]
  item_ids: string[]
}

export type AIReprocessScopeValidation = {
  days: string | null
  limit: string | null
  timeRange: string | null
  itemSelection: string | null
}

function parsePositiveWholeNumber(value: string) {
  const trimmed = value.trim()
  if (!trimmed || !/^\d+$/.test(trimmed)) {
    return null
  }

  const parsed = Number(trimmed)
  if (!Number.isSafeInteger(parsed) || parsed <= 0) {
    return null
  }

  return parsed
}

export function toApiDateTime(value: string) {
  if (!value) {
    return null
  }
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) {
    return null
  }
  return parsed.toISOString()
}

export function resolveAiReprocessQueueState({
  days,
  limit,
  startTime,
  endTime,
  feedIds,
  selectedItems,
  itemSearch = '',
}: {
  days: string
  limit: string
  startTime: string
  endTime: string
  feedIds: string[]
  selectedItems: Array<Pick<ItemListEntry, 'id'>>
  itemSearch?: string
}): {
  payload: AIReprocessQueueRequest | null
  validation: AIReprocessScopeValidation
} {
  const parsedDays = parsePositiveWholeNumber(days)
  const parsedLimit = parsePositiveWholeNumber(limit)
  const startTimeIso = toApiDateTime(startTime)
  const endTimeIso = toApiDateTime(endTime)
  const usingLookbackWindow = !startTimeIso && !endTimeIso && selectedItems.length === 0

  let daysError: string | null = null
  if (usingLookbackWindow) {
    if (parsedDays == null) {
      daysError = 'Lookback Days must be a whole number greater than 0 when no explicit time or article scope is selected.'
    } else if (parsedDays > 365) {
      daysError = 'Lookback Days cannot exceed 365.'
    }
  }

  let limitError: string | null = null
  if (parsedLimit == null) {
    limitError = 'Last X Articles must be a whole number greater than 0.'
  } else if (parsedLimit > 1000) {
    limitError = 'Last X Articles cannot exceed 1000.'
  }

  let timeRangeError: string | null = null
  if (startTime.trim() && !startTimeIso) {
    timeRangeError = 'Start Time is invalid.'
  } else if (endTime.trim() && !endTimeIso) {
    timeRangeError = 'End Time is invalid.'
  } else if (startTimeIso && endTimeIso && new Date(startTimeIso).getTime() > new Date(endTimeIso).getTime()) {
    timeRangeError = 'Start Time must be earlier than End Time.'
  }

  let itemSelectionError: string | null = null
  if (itemSearch.trim() && selectedItems.length === 0) {
    itemSelectionError = 'Add a matching article or clear the search before queueing. Search text is only used for picking articles.'
  } else if (parsedLimit != null && selectedItems.length > parsedLimit) {
    itemSelectionError = 'Selected articles cannot exceed Last X Articles. Increase the limit or remove selected articles.'
  }

  if (daysError || limitError || timeRangeError || itemSelectionError || parsedLimit == null) {
    return {
      payload: null,
      validation: {
        days: daysError,
        limit: limitError,
        timeRange: timeRangeError,
        itemSelection: itemSelectionError,
      },
    }
  }

  return {
    payload: {
      days: usingLookbackWindow ? parsedDays : null,
      limit: parsedLimit,
      start_time: startTimeIso,
      end_time: endTimeIso,
      feed_ids: [...feedIds],
      item_ids: selectedItems.map((item) => item.id),
    },
    validation: {
      days: null,
      limit: null,
      timeRange: null,
      itemSelection: null,
    },
  }
}
