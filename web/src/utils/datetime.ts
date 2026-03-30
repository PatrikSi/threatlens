const DATE_TIME_FORMATTER = new Intl.DateTimeFormat('en-GB', {
  day: '2-digit',
  month: '2-digit',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
})

const DATE_ONLY_FORMATTER = new Intl.DateTimeFormat('en-GB', {
  day: '2-digit',
  month: '2-digit',
  year: 'numeric',
})

function coerceDate(value: string | Date | null | undefined) {
  if (!value) {
    return null
  }

  const parsed = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(parsed.getTime())) {
    return null
  }
  return parsed
}

export function formatDateTime(value: string | Date | null | undefined) {
  const parsed = coerceDate(value)
  if (!parsed) {
    return typeof value === 'string' ? value : 'Unknown'
  }
  return DATE_TIME_FORMATTER.format(parsed)
}

export function formatDateOnly(value: string | Date | null | undefined) {
  const parsed = coerceDate(value)
  if (!parsed) {
    return typeof value === 'string' ? value : 'Unknown'
  }
  return DATE_ONLY_FORMATTER.format(parsed)
}
