type SearchableDashboardWindow = {
  type: string
  rss_filters?: { q?: string } | null
  alert_filters?: { q?: string } | null
}

export function summarizeGlobalSearchAcrossWindows(windows: SearchableDashboardWindow[]) {
  const searchValues = windows.flatMap((window) => {
    if (window.type === 'rss') {
      return [window.rss_filters?.q ?? '']
    }
    if (window.type === 'alerts') {
      return [window.alert_filters?.q ?? '']
    }
    return []
  })

  if (!searchValues.length) {
    return {
      value: '',
      isMixed: false,
    }
  }

  const firstValue = searchValues[0] ?? ''
  const isMixed = searchValues.some((value) => value !== firstValue)
  return {
    value: isMixed ? '' : firstValue,
    isMixed,
  }
}
