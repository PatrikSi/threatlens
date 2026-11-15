// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const exportPageDomMocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  apiDownload: vi.fn(),
  anchorClick: vi.fn(),
  createObjectURL: vi.fn(() => 'blob:threatlens-export'),
  revokeObjectURL: vi.fn(),
}))

vi.mock('../api/client', async (original) => ({
  ...(await original<object>()),
  apiFetch: exportPageDomMocks.apiFetch,
  apiDownload: exportPageDomMocks.apiDownload,
}))

import { ExportPage } from './ExportPage'
import { ApiError } from '../api/client'
import { triggerBrowserDownload } from './exportPageModel'
import { invalidateSession } from '../api/sessionLifecycle'
import { deferred } from './articleIntelligenceTestSupport'

const CAPABILITIES = {
  formats: [
    {
      id: 'csv',
      label: 'CSV',
      extension: '.csv',
      media_type: 'text/csv',
      description: 'Spreadsheet-ready article inventory.',
      supports_article_text: true,
      supports_iocs: true,
      supports_user_state: true,
    },
    {
      id: 'stix',
      label: 'STIX 2.1',
      extension: '.stix.json',
      media_type: 'application/stix+json',
      description: 'Standards-mapped cyber observables.',
      supports_article_text: false,
      supports_iocs: true,
      supports_user_state: false,
    },
    {
      id: 'misp',
      label: 'MISP',
      extension: '.misp.json',
      media_type: 'application/json',
      description: 'MISP event with extracted indicators.',
      supports_article_text: false,
      supports_iocs: true,
      supports_user_state: false,
    },
    {
      id: 'pdf_bundle',
      label: 'PDF bundle',
      extension: '.pdf.zip',
      media_type: 'application/zip',
      description: 'Readable article PDFs.',
      supports_article_text: true,
      supports_iocs: true,
      supports_user_state: true,
    },
  ],
  feeds: [
    { id: 'feed-1', name: 'CERT Advisories' },
    { id: 'feed-2', name: 'Vendor Research' },
  ],
  tags: [{ id: 'tag-1', name: 'Ransomware' }],
  classifications: ['malware'],
  max_items: 10_000,
  max_pdf_items: 500,
  max_uncompressed_bytes: 262_144_000,
  preview_limit: 25,
}

const PREVIEW = {
  total_matches: 2,
  articles_with_text: 1,
  items_with_iocs: 1,
  preview_limit: 25,
  exceeds_export_limit: false,
  exceeds_pdf_limit: false,
  items: [
    {
      id: 'article-1',
      title: 'Critical VPN vulnerability exploited in the wild',
      url: 'https://example.com/research',
      feed_name: 'CERT Advisories',
      published_at: '2026-08-12T10:00:00Z',
      first_seen_at: '2026-08-12T10:05:00Z',
      classification: 'vulnerability',
      ai_relevance_score: 0.92,
      ai_relevance_label: 'high',
      tags: ['VPN', 'Exploitation'],
      is_read: false,
      is_starred: true,
      has_article_text: true,
      ioc_count: 3,
    },
  ],
}

const BACKGROUND_JOB = {
  id: '00000000-0000-4000-8000-000000000011', format: 'csv', status: 'queued',
  created_at: '2026-09-08T08:00:00Z', expires_at: '2026-09-09T08:00:00Z',
  started_at: null, completed_at: null, attempts: 0, completed_items: 0,
  item_count: null, file_size: null, filename: null, error_code: null,
  message: null, download_available: false,
}

let queryClient: QueryClient | null = null
let root: Root | null = null
let container: HTMLDivElement | null = null

function renderPage() {
  queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => {
    root?.render(
      <QueryClientProvider client={queryClient!}>
        <ExportPage />
      </QueryClientProvider>,
    )
  })
  return container
}

async function waitForPreview(view: HTMLDivElement) {
  await act(async () => {
    await vi.waitFor(() => {
      expect(view.textContent).toContain('Critical VPN vulnerability')
    })
  })
}

function setInputValue(input: HTMLInputElement, value: string) {
  const descriptor = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')
  descriptor?.set?.call(input, value)
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

beforeEach(() => {
  exportPageDomMocks.apiFetch.mockImplementation((path: string) => {
    if (path === '/exports/capabilities') {
      return Promise.resolve(CAPABILITIES)
    }
    if (path === '/exports/preview') {
      return Promise.resolve(PREVIEW)
    }
    if (path.startsWith('/exports/jobs?')) {
      return Promise.resolve({ items: [], has_more: false })
    }
    return Promise.reject(new Error(`Unexpected API path: ${path}`))
  })
  exportPageDomMocks.apiDownload.mockResolvedValue({
    blob: new Blob(['export']),
    filename: 'threatlens-research.csv',
    contentType: 'text/csv',
  })
  vi.stubGlobal('URL', {
    ...URL,
    createObjectURL: exportPageDomMocks.createObjectURL,
    revokeObjectURL: exportPageDomMocks.revokeObjectURL,
  })
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(exportPageDomMocks.anchorClick)
})

afterEach(async () => {
  vi.useRealTimers()
  await act(async () => {
    root?.unmount()
    await Promise.resolve()
  })
  queryClient?.clear()
  queryClient = null
  root = null
  container?.remove()
  container = null
  document.body.innerHTML = ''
  exportPageDomMocks.apiFetch.mockReset()
  exportPageDomMocks.apiDownload.mockReset()
  exportPageDomMocks.anchorClick.mockReset()
  exportPageDomMocks.createObjectURL.mockClear()
  exportPageDomMocks.revokeObjectURL.mockClear()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('ExportPage', () => {
  it('keeps an old preview hidden after capabilities recover until the preview is freshly authorized', async () => {
    const view = renderPage()
    await waitForPreview(view)
    const fallback = exportPageDomMocks.apiFetch.getMockImplementation()!
    exportPageDomMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => path === '/exports/capabilities'
      ? Promise.reject(new ApiError('Export access removed', 403, path)) : fallback(path, options))
    await act(async () => { await queryClient!.invalidateQueries({ queryKey: ['exports', 'capabilities'] }) })
    const held = deferred<typeof PREVIEW>()
    let requested = false
    exportPageDomMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => {
      if (path !== '/exports/preview') return fallback(path, options)
      requested = true
      return held.promise
    })
    await act(async () => { await queryClient!.invalidateQueries({ queryKey: ['exports', 'capabilities'] }) })
    await act(async () => { await vi.waitFor(() => expect(requested).toBe(true)) })
    expect(view.textContent).not.toContain('Critical VPN vulnerability')
    expect([...view.querySelectorAll<HTMLButtonElement>('button')].find((button) => button.textContent === 'Generate CSV')?.disabled).toBe(true)
    await act(async () => { held.resolve({ ...PREVIEW, items: [{ ...PREVIEW.items[0], title: 'Freshly authorized article' }] }); await Promise.resolve() })
    await act(async () => { await vi.waitFor(() => expect(view.textContent).toContain('Freshly authorized article')) })
  })

  it('retains a capabilities denial through an outage without restoring private option labels', async () => {
    const fallback = exportPageDomMocks.apiFetch.getMockImplementation()!
    const capabilities = { ...CAPABILITIES, feeds: [{ id: 'feed-1', name: 'Private export feed' }],
      tags: [{ id: 'tag-1', name: 'Private export tag' }] }
    exportPageDomMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => path === '/exports/capabilities'
      ? Promise.resolve(capabilities) : fallback(path, options))
    const view = renderPage()
    await waitForPreview(view)
    expect(view.textContent).toContain('Private export feed')
    expect(view.textContent).toContain('Private export tag')
    for (const status of [403, 503]) {
      exportPageDomMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => path === '/exports/capabilities'
        ? Promise.reject(new ApiError('Export options unavailable', status, path)) : fallback(path, options))
      await act(async () => { await queryClient!.invalidateQueries({ queryKey: ['exports', 'capabilities'] }) })
      expect(view.textContent).not.toContain('Private export feed')
      expect(view.textContent).not.toContain('Private export tag')
    }
    exportPageDomMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => path === '/exports/capabilities'
      ? Promise.resolve({ ...capabilities, feeds: [{ id: 'feed-1', name: 'Fresh authorized feed' }] }) : fallback(path, options))
    await act(async () => { await queryClient!.invalidateQueries({ queryKey: ['exports', 'capabilities'] }) })
    await act(async () => { await vi.waitFor(() => expect(view.textContent).toContain('Fresh authorized feed')) })
  })

  it('does not restore a denied preview through keepPreviousData when filters change', async () => {
    const view = renderPage()
    await waitForPreview(view)
    const fallback = exportPageDomMocks.apiFetch.getMockImplementation()!
    exportPageDomMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => path === '/exports/preview'
      ? Promise.reject(new ApiError('Preview access removed', 403, path)) : fallback(path, options))
    await act(async () => { await queryClient!.invalidateQueries({ queryKey: ['exports', 'preview'] }) })
    const held = deferred<typeof PREVIEW>()
    exportPageDomMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => path === '/exports/preview'
      ? held.promise : fallback(path, options))
    // The shared export denial also retires option metadata. A fresh metadata
    // read can restore filter controls, but cannot restore the old preview.
    await act(async () => { await queryClient!.invalidateQueries({ queryKey: ['exports', 'capabilities'] }) })
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    act(() => setInputValue(view.querySelector<HTMLInputElement>('#export-search')!, 'new-filters'))
    await act(async () => { await vi.advanceTimersByTimeAsync(450) })
    expect(exportPageDomMocks.apiFetch.mock.calls.some(([path, options]) => path === '/exports/preview'
      && JSON.parse(String(options?.body)).filters.q === 'new-filters')).toBe(true)
    vi.useRealTimers()
    expect(view.textContent).not.toContain('Critical VPN vulnerability')
    expect(view.querySelector('[aria-label="Export match summary"]')?.textContent).not.toContain('Matching2')
    expect([...view.querySelectorAll<HTMLButtonElement>('button')].find((button) => button.textContent === 'Generate CSV')?.disabled).toBe(true)
    await act(async () => { held.resolve({ ...PREVIEW, items: [{ ...PREVIEW.items[0], title: 'Freshly authorized article' }] }); await Promise.resolve() })
    await act(async () => { await vi.waitFor(() => expect(view.textContent).toContain('Freshly authorized article')) })
  })

  it.each(['preview', 'jobs'])('retains a %s denial through a same-key outage retry', async (kind) => {
    const fallback = exportPageDomMocks.apiFetch.getMockImplementation()!
    const ready = { ...BACKGROUND_JOB, status: 'ready', filename: 'private-saved-export.csv', download_available: true }
    const match = (path: string) => kind === 'preview' ? path === '/exports/preview' : path.startsWith('/exports/jobs?')
    exportPageDomMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => kind === 'jobs' && match(path)
      ? Promise.resolve({ items: [ready], has_more: false }) : fallback(path, options))
    const view = renderPage()
    await waitForPreview(view)
    const privateText = kind === 'preview' ? 'Critical VPN vulnerability' : 'private-saved-export.csv'
    await act(async () => { await vi.waitFor(() => expect(view.textContent).toContain(privateText)) })
    for (const status of [403, 503]) {
      exportPageDomMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => match(path)
        ? Promise.reject(new ApiError('Read unavailable', status, path)) : fallback(path, options))
      await act(async () => { await queryClient!.invalidateQueries({ queryKey: ['exports', kind] }) })
      expect(view.textContent).not.toContain(privateText)
    }
    exportPageDomMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => match(path)
      ? Promise.resolve(kind === 'preview' ? { ...PREVIEW, items: [{ ...PREVIEW.items[0], title: 'Freshly authorized article' }] }
        : { items: [{ ...ready, filename: 'freshly-authorized.csv' }], has_more: false }) : fallback(path, options))
    await act(async () => { await queryClient!.invalidateQueries({ queryKey: ['exports', kind] }) })
    await act(async () => { await queryClient!.invalidateQueries({ queryKey: ['exports', 'capabilities'] }) })
    await act(async () => { await vi.waitFor(() => expect(view.textContent).toContain(kind === 'preview' ? 'Freshly authorized article' : 'freshly-authorized.csv')) })
  })

  it('does not show a cached previous jobs page before a fresh authorized read', async () => {
    const held = deferred<unknown>()
    const fallback = exportPageDomMocks.apiFetch.getMockImplementation()!
    let phase = 'seed'
    exportPageDomMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => {
      if (!path.startsWith('/exports/jobs?')) return fallback(path, options)
      if (phase === 'denied') return Promise.reject(new ApiError('Jobs access removed', 403, path))
      if (phase === 'held') return held.promise
      const second = path.includes('offset=25')
      return Promise.resolve({ items: [{ ...BACKGROUND_JOB, status: 'ready', download_available: true,
        filename: second ? 'private-second.csv' : 'private-first.csv' }], has_more: !second })
    })
    const view = renderPage()
    await waitForPreview(view)
    act(() => [...view.querySelectorAll<HTMLButtonElement>('button')].find((button) => button.textContent === 'Next jobs')!.click())
    await act(async () => { await vi.waitFor(() => expect(view.textContent).toContain('private-second.csv')) })
    phase = 'denied'
    await act(async () => { await queryClient!.invalidateQueries({ queryKey: ['exports', 'jobs', 1], exact: true }) })
    phase = 'held'
    act(() => [...view.querySelectorAll<HTMLButtonElement>('button')].find((button) => button.textContent === 'Previous jobs')!.click())
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 15)) })
    expect(view.textContent).not.toContain('private-first.csv')
    expect([...view.querySelectorAll<HTMLButtonElement>('button')].some((button) => button.textContent === 'Download export')).toBe(false)
    await act(async () => { held.resolve({ items: [{ ...BACKGROUND_JOB, filename: 'freshly-authorized.csv' }], has_more: false }); await Promise.resolve() })
    await act(async () => { await vi.waitFor(() => expect(view.textContent).toContain('freshly-authorized.csv')) })
  })

  it.each([401, 403, 404])('hides retained preview rows and counts after a %s refresh', async (status) => {
    const view = renderPage()
    await waitForPreview(view)
    const fallback = exportPageDomMocks.apiFetch.getMockImplementation()!
    exportPageDomMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => path === '/exports/preview'
      ? Promise.reject(new ApiError('Article access removed', status, path)) : fallback(path, options))
    await act(async () => { await queryClient!.invalidateQueries({ queryKey: ['exports', 'preview'] }) })
    await act(async () => { await vi.waitFor(() => expect(view.textContent).toContain('Article access removed')) })
    expect(view.textContent).not.toContain('Critical VPN vulnerability')
    const summary = view.querySelector('[aria-label="Export match summary"]')!
    expect(summary.textContent).not.toContain('2')
    const generate = [...view.querySelectorAll<HTMLButtonElement>('button')].find((button) => button.textContent === 'Generate CSV')
    expect(generate === undefined || generate.disabled).toBe(true)
  })

  it('preserves preview context through an outage while pausing generation', async () => {
    const view = renderPage()
    await waitForPreview(view)
    const fallback = exportPageDomMocks.apiFetch.getMockImplementation()!
    exportPageDomMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => path === '/exports/preview'
      ? Promise.reject(new ApiError('Preview temporarily unavailable', 503, path)) : fallback(path, options))
    await act(async () => { await queryClient!.invalidateQueries({ queryKey: ['exports', 'preview'] }) })
    await act(async () => { await vi.waitFor(() => expect(view.textContent).toContain('Preview temporarily unavailable')) })
    expect(view.textContent).toContain('Critical VPN vulnerability')
    expect([...view.querySelectorAll<HTMLButtonElement>('button')].find((button) => button.textContent === 'Generate CSV')?.disabled).toBe(true)
  })

  it.each([403, 503])('pauses exports when capabilities refresh fails with %s', async (status) => {
    const view = renderPage()
    await waitForPreview(view)
    const fallback = exportPageDomMocks.apiFetch.getMockImplementation()!
    exportPageDomMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => path === '/exports/capabilities'
      ? Promise.reject(new ApiError('Export options unavailable', status, path)) : fallback(path, options))
    await act(async () => { await queryClient!.invalidateQueries({ queryKey: ['exports', 'capabilities'] }) })
    await act(async () => { await vi.waitFor(() => expect(view.textContent).toContain('Export options unavailable')) })
    if (status === 403) {
      expect(view.textContent).not.toContain('CERT Advisories')
      expect(view.querySelector('#export-search')).toBeNull()
    } else {
      expect(view.textContent).toContain('CERT Advisories')
      expect(view.querySelector('#export-search')).not.toBeNull()
    }
    const generate = [...view.querySelectorAll<HTMLButtonElement>('button')].find((button) => button.textContent === 'Generate CSV')
    const queue = [...view.querySelectorAll<HTMLButtonElement>('button')].find((button) => button.textContent === 'Generate in background')
    expect(generate === undefined || generate.disabled).toBe(true)
    expect(queue === undefined || queue.disabled).toBe(true)
    act(() => { generate?.click(); queue?.click() })
    expect(exportPageDomMocks.apiDownload).not.toHaveBeenCalled()
    expect(exportPageDomMocks.apiFetch.mock.calls.some(([path]) => path === '/exports/jobs')).toBe(false)
  })

  it.each([403, 503])('handles retained background jobs after a %s refresh', async (status) => {
    const ready = { ...BACKGROUND_JOB, status: 'ready', filename: 'private-report.csv', file_size: 1024, download_available: true }
    const fallback = exportPageDomMocks.apiFetch.getMockImplementation()!
    exportPageDomMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => path.startsWith('/exports/jobs?')
      ? Promise.resolve({ items: [ready], has_more: false }) : fallback(path, options))
    const view = renderPage()
    await waitForPreview(view)
    await act(async () => { await vi.waitFor(() => expect(view.textContent).toContain('private-report.csv')) })
    exportPageDomMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => path.startsWith('/exports/jobs?')
      ? Promise.reject(new ApiError('Export jobs unavailable', status, path)) : fallback(path, options))
    await act(async () => { await queryClient!.invalidateQueries({ queryKey: ['exports', 'jobs'] }) })
    await act(async () => { await vi.waitFor(() => expect(view.textContent).toContain('Export jobs unavailable')) })
    if (status === 403) expect(view.textContent).not.toContain('private-report.csv')
    else expect(view.textContent).toContain('private-report.csv')
    const download = [...view.querySelectorAll<HTMLButtonElement>('button')].find((button) => button.textContent === 'Download export')
    const remove = [...view.querySelectorAll<HTMLButtonElement>('button')].find((button) => button.textContent === 'Delete export')
    expect(download === undefined || download.disabled).toBe(true)
    expect(remove === undefined || remove.disabled).toBe(true)
    act(() => { download?.click(); remove?.click() })
    expect(exportPageDomMocks.apiDownload).not.toHaveBeenCalled()
    expect(exportPageDomMocks.apiFetch.mock.calls.some(([path]) => path.endsWith('/cancel'))).toBe(false)
  })

  it('cleans up the temporary download anchor and URL when the browser click fails', () => {
    vi.useFakeTimers()
    exportPageDomMocks.anchorClick.mockImplementationOnce(() => {
      throw new Error('Browser download blocked')
    })

    expect(() => triggerBrowserDownload(new Blob(['export']), 'export.csv')).toThrow('Browser download blocked')
    expect(document.querySelector('a[download]')).toBeNull()
    expect(exportPageDomMocks.revokeObjectURL).not.toHaveBeenCalled()

    vi.runOnlyPendingTimers()
    expect(exportPageDomMocks.revokeObjectURL).toHaveBeenCalledWith('blob:threatlens-export')
    vi.useRealTimers()
  })

  it('renders configurable filters and responsive article previews', async () => {
    const view = renderPage()
    await waitForPreview(view)

    expect(view.textContent).toContain('Export intelligence')
    expect(view.textContent).toContain('2 articles ready for CSV')
    expect(view.textContent).toContain('CERT Advisories')
    expect(view.querySelector('table')?.parentElement?.className).toContain('hidden')
    expect(view.querySelector('article')?.parentElement?.className).toContain('sm:hidden')
    expect(view.querySelector<HTMLInputElement>('#export-search')).not.toBeNull()
    expect(view.querySelectorAll('input[name="article-export-format"]')).toHaveLength(4)
    const generateButton = Array.from(view.querySelectorAll('button')).find((button) => button.textContent?.includes('Generate CSV'))
    const previewHeading = Array.from(view.querySelectorAll('h2')).find((heading) => heading.textContent === 'Matching articles')
    const documentPosition = generateButton && previewHeading ? generateButton.compareDocumentPosition(previewHeading) : 0
    expect(documentPosition & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('loads format-specific options and limits', async () => {
    const view = renderPage()
    await waitForPreview(view)
    const pdfOption = view.querySelector<HTMLInputElement>('input[value="pdf_bundle"]')

    act(() => {
      pdfOption?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(view.textContent).toContain('Limit: 500 articles')
    expect(view.textContent).toContain('Full article text')
    expect(view.textContent).not.toContain('Full article text in PDFs')
    expect(view.textContent).toContain('Generate PDF bundle')
  })

  it.each(['stix', 'misp'])('discloses raw extraction and unapplied team policy for %s', async (format) => {
    const view = renderPage()
    await waitForPreview(view)
    const warning = 'Raw extracted values; review before enabling detections. Team verdicts and suppressions are not applied.'
    expect(view.textContent).not.toContain(warning)
    act(() => view.querySelector<HTMLInputElement>(`input[value="${format}"]`)!.click())
    expect(view.querySelector('[role="note"]')?.textContent).toContain(warning)
  })

  it('validates filters immediately and pauses export while the preview is stale', async () => {
    const view = renderPage()
    await waitForPreview(view)
    const generateButton = Array.from(view.querySelectorAll('button')).find((button) => button.textContent?.includes('Generate CSV'))
    const search = view.querySelector<HTMLInputElement>('#export-search')
    const minimumScore = view.querySelector<HTMLInputElement>('input[placeholder="0.00"]')

    expect(generateButton?.disabled).toBe(false)
    act(() => setInputValue(search!, 'ransomware'))
    expect(generateButton?.disabled).toBe(true)
    expect(view.textContent).toContain('Wait for the matching article preview to update')

    act(() => setInputValue(minimumScore!, '1.2'))
    expect(view.textContent).toContain('Minimum AI score must be a number from 0 to 1')
    expect(generateButton?.disabled).toBe(true)
  })

  it('downloads the generated artifact with the server-provided filename', async () => {
    const view = renderPage()
    await waitForPreview(view)
    const generateButton = Array.from(view.querySelectorAll('button')).find((button) => button.textContent?.includes('Generate CSV'))
    const fullTextToggle = Array.from(view.querySelectorAll('label'))
      .find((label) => label.textContent?.trim() === 'Full article text')
      ?.querySelector<HTMLInputElement>('input')

    await act(async () => {
      fullTextToggle?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      generateButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      await vi.waitFor(() => expect(exportPageDomMocks.apiDownload).toHaveBeenCalledTimes(1))
    })

    const request = exportPageDomMocks.apiDownload.mock.calls[0]?.[1]
    const body = JSON.parse(String(request?.body))
    expect(body).toMatchObject({
      format: 'csv',
      options: { include_article_text: false, csv_include_article_text: true },
    })
    expect(body.filters.since).toBeTruthy()
    expect(exportPageDomMocks.anchorClick).toHaveBeenCalledTimes(1)
    expect(view.textContent).toContain('Export ready: threatlens-research.csv')
  })

  it('aborts an active export and suppresses a late download after unmount', async () => {
    let requestSignal: AbortSignal | undefined
    let resolveExport: ((result: {
      blob: Blob
      filename: string | null
      contentType: string | null
    }) => void) | undefined
    exportPageDomMocks.apiDownload.mockImplementation((_path: string, options?: RequestInit) => {
      requestSignal = options?.signal ?? undefined
      return new Promise((resolve) => {
        resolveExport = resolve
      })
    })
    const view = renderPage()
    await waitForPreview(view)
    const generateButton = Array.from(view.querySelectorAll('button'))
      .find((button) => button.textContent?.includes('Generate CSV'))

    act(() => generateButton?.click())
    await act(async () => {
      await vi.waitFor(() => expect(requestSignal).toBeDefined())
    })
    await act(async () => {
      root?.unmount()
      root = null
      await Promise.resolve()
    })

    expect(requestSignal?.aborted).toBe(true)
    await act(async () => {
      resolveExport?.({
        blob: new Blob(['late export']),
        filename: 'late-export.csv',
        contentType: 'text/csv',
      })
      await Promise.resolve()
    })
    expect(exportPageDomMocks.anchorClick).not.toHaveBeenCalled()
  })

  it('accepts a durable background job and restores it when the page remounts', async () => {
    let accepted = false
    const fallback = exportPageDomMocks.apiFetch.getMockImplementation()!
    exportPageDomMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/exports/jobs') {
        accepted = true
        const request = JSON.parse(String(options?.body))
        expect(request.idempotency_key).toMatch(/^[0-9a-f-]{36}$/)
        expect(request.format).toBe('csv')
        return Promise.resolve(BACKGROUND_JOB)
      }
      if (path.startsWith('/exports/jobs?')) return Promise.resolve({ items: accepted ? [BACKGROUND_JOB] : [], has_more: false })
      return fallback(path, options)
    })
    const view = renderPage()
    await waitForPreview(view)
    await act(async () => {
      Array.from(view.querySelectorAll('button')).find((button) => button.textContent === 'Generate in background')?.click()
      await vi.waitFor(() => expect(view.textContent).toContain('Background export accepted'))
    })
    expect(exportPageDomMocks.apiDownload).not.toHaveBeenCalled()
    await act(async () => { root?.unmount(); root = null })
    queryClient?.clear()
    view.remove()
    const restored = renderPage()
    await waitForPreview(restored)
    await act(async () => { await vi.waitFor(() => expect(restored.textContent).toContain('Waiting for a worker')) })
    expect(restored.textContent).toContain('Cancel export')
  })

  it('retries an ambiguous background acceptance with the same idempotency key', async () => {
    vi.stubGlobal('crypto', { getRandomValues: crypto.getRandomValues.bind(crypto) })
    const requests: Record<string, unknown>[] = []
    const fallback = exportPageDomMocks.apiFetch.getMockImplementation()!
    exportPageDomMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/exports/jobs') {
        requests.push(JSON.parse(String(options?.body)))
        return requests.length === 1 ? Promise.reject(new Error('Connection lost after acceptance')) : Promise.resolve(BACKGROUND_JOB)
      }
      return fallback(path, options)
    })
    const view = renderPage()
    await waitForPreview(view)
    const queue = Array.from(view.querySelectorAll('button')).find((button) => button.textContent === 'Generate in background')!
    await act(async () => {
      queue.click()
      await vi.waitFor(() => expect(view.textContent).toContain('Connection lost after acceptance'))
    })
    await act(async () => {
      queue.click()
      await vi.waitFor(() => expect(requests).toHaveLength(2))
    })
    expect(requests[1]).toEqual(requests[0])
  })

  it('shows a recoverable preparation error without sending an export', async () => {
    const originalCrypto = crypto
    vi.stubGlobal('crypto', undefined)
    const view = renderPage()
    await waitForPreview(view)
    const queue = [...view.querySelectorAll('button')].find((entry) => entry.textContent === 'Generate in background')!
    act(() => queue.click())
    expect(view.textContent).toContain('Secure random generation is unavailable')
    expect(view.textContent).toContain('No request was sent')
    expect(exportPageDomMocks.apiFetch.mock.calls.filter(([path]) => path === '/exports/jobs')).toHaveLength(0)
    const fallback = exportPageDomMocks.apiFetch.getMockImplementation()!
    exportPageDomMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => path === '/exports/jobs'
      ? Promise.resolve(BACKGROUND_JOB) : fallback(path, options))
    vi.stubGlobal('crypto', originalCrypto)
    await act(async () => {
      queue.click()
      await vi.waitFor(() => expect(view.textContent).toContain('Background export accepted'))
    })
    expect(view.textContent).not.toContain('Secure random generation is unavailable')
  })

  it('downloads a ready background artifact only on request and can delete it', async () => {
    const ready = { ...BACKGROUND_JOB, status: 'ready', filename: 'saved-export.csv', file_size: 1024, download_available: true }
    let deleted = false
    const fallback = exportPageDomMocks.apiFetch.getMockImplementation()!
    exportPageDomMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => {
      if (path.startsWith('/exports/jobs?')) return Promise.resolve({ items: [{ ...ready, ...(deleted ? { status: 'cancelled', filename: null, download_available: false } : {}) }], has_more: false })
      if (path.endsWith('/cancel')) { deleted = true; return Promise.resolve({ ...ready, status: 'cancelled' }) }
      return fallback(path, options)
    })
    const view = renderPage()
    await waitForPreview(view)
    expect(exportPageDomMocks.apiDownload).not.toHaveBeenCalled()
    await act(async () => {
      Array.from(view.querySelectorAll('button')).find((button) => button.textContent === 'Download export')?.click()
      await vi.waitFor(() => expect(exportPageDomMocks.anchorClick).toHaveBeenCalledTimes(1))
    })
    expect(exportPageDomMocks.apiDownload.mock.calls[0]?.[0]).toBe(`/exports/jobs/${BACKGROUND_JOB.id}/download`)
    await act(async () => {
      Array.from(view.querySelectorAll('button')).find((button) => button.textContent === 'Delete export')?.click()
      await vi.waitFor(() => expect(view.textContent).toContain('cancelled'))
    })
    expect(Array.from(view.querySelectorAll('button')).some((button) => button.textContent === 'Download export')).toBe(false)
  })

  it('does not download a background artifact returned after a session change', async () => {
    const fallback = exportPageDomMocks.apiFetch.getMockImplementation()!
    exportPageDomMocks.apiFetch.mockImplementation((path: string, options?: RequestInit) => path.startsWith('/exports/jobs?')
      ? Promise.resolve({ items: [{ ...BACKGROUND_JOB, status: 'ready', download_available: true }], has_more: false })
      : fallback(path, options))
    let finish!: (value: unknown) => void
    exportPageDomMocks.apiDownload.mockImplementation(() => new Promise((resolve) => { finish = resolve }))
    const view = renderPage()
    await waitForPreview(view)
    await act(async () => {
      Array.from(view.querySelectorAll('button')).find((button) => button.textContent === 'Download export')?.click()
      await vi.waitFor(() => expect(finish).toBeDefined())
      invalidateSession()
      finish({ blob: new Blob(['private export']), filename: 'private.csv', contentType: 'text/csv' })
      await vi.waitFor(() => expect(view.textContent).toContain('session changed'))
    })
    expect(exportPageDomMocks.anchorClick).not.toHaveBeenCalled()
  })
})
