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

const ISO_DATE_ONLY_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/

function coerceDate(value: string | Date | null | undefined) {
  if (!value) {
    return null
  }

  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? null : value
  }

  const dateOnlyMatch = ISO_DATE_ONLY_PATTERN.exec(value)
  if (dateOnlyMatch) {
    const [, year, month, day] = dateOnlyMatch
    const parsed = new Date(Number(year), Number(month) - 1, Number(day), 0, 0, 0, 0)
    return Number.isNaN(parsed.getTime()) ? null : parsed
  }

  const parsed = new Date(value)
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
