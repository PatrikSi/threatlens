export type ArticleBlock =
  | { kind: 'heading'; text: string }
  | { kind: 'paragraph'; text: string }
  | { kind: 'bullet-list'; items: string[] }
  | { kind: 'numbered-list'; items: string[] }
  | { kind: 'quote'; text: string }

const ALLOWED_HTML_TAGS = new Set([
  'a',
  'b',
  'blockquote',
  'br',
  'code',
  'em',
  'h1',
  'h2',
  'h3',
  'h4',
  'h5',
  'h6',
  'hr',
  'i',
  'li',
  'ol',
  'p',
  'pre',
  'strong',
  'u',
  'ul',
])

export function parseArticleBlocks(text: string): ArticleBlock[] {
  const lines = text.replace(/\r/g, '').split('\n')
  const blocks: ArticleBlock[] = []
  const nonEmptyCount = lines.filter((line) => line.trim()).length
  const blankCount = lines.length - nonEmptyCount
  const useLineOrientedMode = nonEmptyCount >= 10 && blankCount <= Math.ceil(nonEmptyCount * 0.12)

  let index = 0
  while (index < lines.length) {
    const raw = lines[index]
    const line = raw.trim()

    if (!line) {
      index += 1
      continue
    }

    if (isHeadingLine(line)) {
      blocks.push({ kind: 'heading', text: cleanHeading(line) })
      index += 1
      continue
    }

    if (looksLikeSectionHeading(line)) {
      blocks.push({ kind: 'heading', text: line })
      index += 1
      continue
    }

    if (isBulletLine(line)) {
      const items: string[] = []
      while (index < lines.length && isBulletLine(lines[index].trim())) {
        items.push(cleanBullet(lines[index].trim()))
        index += 1
      }
      if (items.length) {
        blocks.push({ kind: 'bullet-list', items })
      }
      continue
    }

    if (line.includes(' • ')) {
      const items: string[] = []
      while (index < lines.length) {
        const bulletLine = lines[index].trim()
        if (!bulletLine || !bulletLine.includes(' • ')) {
          break
        }
        items.push(bulletLine)
        index += 1
      }
      if (items.length) {
        blocks.push({ kind: 'bullet-list', items })
      }
      continue
    }

    if (isNumberedLine(line)) {
      const items: string[] = []
      while (index < lines.length && isNumberedLine(lines[index].trim())) {
        items.push(cleanNumbered(lines[index].trim()))
        index += 1
      }
      if (items.length) {
        blocks.push({ kind: 'numbered-list', items })
      }
      continue
    }

    if (line.startsWith('>')) {
      const quoteLines: string[] = []
      while (index < lines.length && lines[index].trim().startsWith('>')) {
        quoteLines.push(lines[index].trim().replace(/^>\s*/, ''))
        index += 1
      }
      const quoteText = quoteLines.join(' ').replace(/\s{2,}/g, ' ').trim()
      if (quoteText) {
        blocks.push({ kind: 'quote', text: quoteText })
      }
      continue
    }

    if (useLineOrientedMode) {
      blocks.push({ kind: 'paragraph', text: line })
      index += 1
      continue
    }

    const paragraphLines: string[] = []
    while (index < lines.length) {
      const paragraphLine = lines[index].trim()
      if (!paragraphLine || isHeadingLine(paragraphLine) || isBulletLine(paragraphLine) || isNumberedLine(paragraphLine) || paragraphLine.startsWith('>')) {
        break
      }
      paragraphLines.push(paragraphLine)
      index += 1
    }

    if (paragraphLines.length) {
      blocks.push({
        kind: 'paragraph',
        text: paragraphLines.join(' ').replace(/\s{2,}/g, ' ').trim(),
      })
      continue
    }

    index += 1
  }

  return blocks
}

export function looksLikeHtml(value: string): boolean {
  return /<([a-z][a-z0-9]*)\b[^>]*>/i.test(value)
}

export function sanitizeHtmlFragment(html: string): string {
  if (typeof window === 'undefined') {
    return ''
  }

  const parser = new DOMParser()
  const document = parser.parseFromString(html, 'text/html')
  return Array.from(document.body.childNodes)
    .map((node) => sanitizeNode(node))
    .join('')
    .trim()
}

export function sanitizeHref(rawHref: string | null): string | null {
  if (!rawHref) return null
  const href = rawHref.trim()
  if (/^https?:\/\//i.test(href)) return href
  return null
}

export function stripHtml(value: string): string {
  if (typeof window === 'undefined') return value
  const parser = new DOMParser()
  const document = parser.parseFromString(value, 'text/html')
  return document.body.textContent?.trim() ?? ''
}

export function formatPlainTextPreview(value: string | null | undefined, fallback: string): string {
  const text = stripHtml(value ?? '').replace(/\s+/g, ' ').trim()
  return text || fallback
}

function isHeadingLine(line: string): boolean {
  if (/^#{1,4}\s+/.test(line)) {
    return true
  }

  return /^[A-Z][A-Z0-9\s\-:]{8,}$/.test(line) && line === line.toUpperCase()
}

function cleanHeading(line: string): string {
  return line.replace(/^#{1,4}\s*/, '').trim()
}

function isBulletLine(line: string): boolean {
  return /^[-*•]\s+/.test(line)
}

function cleanBullet(line: string): string {
  return line.replace(/^[-*•]\s+/, '').trim()
}

function isNumberedLine(line: string): boolean {
  return /^\d+[.)]\s+/.test(line)
}

function cleanNumbered(line: string): string {
  return line.replace(/^\d+[.)]\s+/, '').trim()
}

function looksLikeSectionHeading(line: string): boolean {
  if (line.length < 3 || line.length > 72) return false
  if (/^https?:\/\//i.test(line)) return false
  if (isBulletLine(line) || isNumberedLine(line) || line.startsWith('>')) return false
  if (line.includes(' • ')) return false
  if (/[.!?]$/.test(line)) return false

  const words = line.split(/\s+/)
  if (words.length > 10) return false
  if (words.every((word) => word.length <= 2)) return false

  return true
}

function sanitizeNode(node: Node): string {
  if (node.nodeType === Node.TEXT_NODE) {
    return escapeHtml(node.textContent ?? '')
  }

  if (node.nodeType !== Node.ELEMENT_NODE) {
    return ''
  }

  const element = node as HTMLElement
  const tag = element.tagName.toLowerCase()
  const children = Array.from(element.childNodes)
    .map((child) => sanitizeNode(child))
    .join('')

  if (!ALLOWED_HTML_TAGS.has(tag)) {
    return children
  }

  if (tag === 'br' || tag === 'hr') {
    return `<${tag}>`
  }

  if (tag === 'a') {
    const href = sanitizeHref(element.getAttribute('href'))
    if (!href) {
      return children
    }
    return `<a href="${escapeAttribute(href)}" target="_blank" rel="noopener noreferrer">${children}</a>`
  }

  return `<${tag}>${children}</${tag}>`
}

function escapeHtml(value: string): string {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;')
}

function escapeAttribute(value: string): string {
  return escapeHtml(value)
}
