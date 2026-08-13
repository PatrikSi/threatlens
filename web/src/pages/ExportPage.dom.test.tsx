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

vi.mock('../api/client', () => ({
  apiFetch: exportPageDomMocks.apiFetch,
  apiDownload: exportPageDomMocks.apiDownload,
}))

import { ExportPage } from './ExportPage'

const CAPABILITIES = {
  formats: [
    {
      id: 'csv',
      label: 'CSV',
      extension: '.csv',
      media_type: 'text/csv',
      description: 'Spreadsheet-ready article inventory.',
      supports_article_text: false,
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
  it('renders configurable filters and responsive article previews', async () => {
    const view = renderPage()
    await waitForPreview(view)

    expect(view.textContent).toContain('Export intelligence')
    expect(view.textContent).toContain('2 articles ready for CSV')
    expect(view.textContent).toContain('CERT Advisories')
    expect(view.querySelector('table')?.parentElement?.className).toContain('hidden')
    expect(view.querySelector('article')?.parentElement?.className).toContain('sm:hidden')
    expect(view.querySelector<HTMLInputElement>('#export-search')).not.toBeNull()
    expect(view.querySelectorAll('input[name="article-export-format"]')).toHaveLength(3)
  })

  it('loads format-specific options and limits', async () => {
    const view = renderPage()
    await waitForPreview(view)
    const pdfOption = view.querySelector<HTMLInputElement>('input[value="pdf_bundle"]')

    act(() => {
      pdfOption?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(view.textContent).toContain('Limit: 500 articles')
    expect(view.textContent).toContain('Full article text in PDFs')
    expect(view.textContent).toContain('Generate PDF bundle')
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

    await act(async () => {
      generateButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      await vi.waitFor(() => expect(exportPageDomMocks.apiDownload).toHaveBeenCalledTimes(1))
    })

    const request = exportPageDomMocks.apiDownload.mock.calls[0]?.[1]
    const body = JSON.parse(String(request?.body))
    expect(body).toMatchObject({ format: 'csv', options: { include_article_text: false } })
    expect(body.filters.since).toBeTruthy()
    expect(exportPageDomMocks.anchorClick).toHaveBeenCalledTimes(1)
    expect(view.textContent).toContain('Export ready: threatlens-research.csv')
  })
})
