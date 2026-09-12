import type { Element, ElementContent, Root, Text } from 'hast'
import { parseEntities } from 'parse-entities'

function citationNodes(text: string, targets: ReadonlyMap<string, string>): ElementContent[] {
  const nodes: ElementContent[] = []
  let offset = 0
  for (const match of text.matchAll(/\[(S\d+)\]/g)) {
    const target = targets.get(match[1])
    if (!target) continue
    if (match.index > offset) nodes.push({ type: 'text', value: text.slice(offset, match.index) })
    nodes.push({
      type: 'element', tagName: 'a', properties: { href: `#${target}`, ariaLabel: `Source ${match[1]}` },
      children: [{ type: 'text', value: match[0] }],
    })
    offset = match.index + match[0].length
  }
  if (offset < text.length) nodes.push({ type: 'text', value: text.slice(offset) })
  return nodes
}

/** GFM literal autolinks can split a marker between a link label and text.
 * Restore those literal spans before citation matching, as the CommonMark
 * backend sees them. Explicit Markdown links and <autolinks> remain links.
 */
function restoreLiteralCitationText(parent: Root | Element, source: string) {
  const children = parent.children.flatMap((child, index) => {
    if (child.type !== 'element' || child.tagName !== 'a' || !child.children.every((entry) => entry.type === 'text')) return [child]
    const offset = child.position?.start.offset
    if (offset == null || ['[', '<'].includes(source[offset])) return [child]
    const literal = child.children.map((entry) => (entry as Text).value).join('')
    const next = parent.children[index + 1]
    const suffix = next?.type === 'text' ? next.value : ''
    // GFM also leaves a final entity semicolon outside its literal link. Decode
    // that span once, without decoding the already-parsed following paragraph.
    const ending = suffix.startsWith(';') ? ';' : ''
    const label = parseEntities(literal + ending, { nonTerminated: false })
    const markerOffset = (label + suffix.slice(ending.length)).search(/\[S\d+\]/)
    if (markerOffset < 0 || markerOffset >= label.length) return [child]
    if (ending && next.type === 'text') next.value = suffix.slice(1)
    return [{ type: 'text' as const, value: label }]
  })
  const joined: Root['children'] = []
  for (const child of children) {
    const previous = joined.at(-1)
    if (child.type === 'text' && previous?.type === 'text') previous.value += child.value
    else joined.push(child)
  }
  parent.children = joined
}

/** Match parsed citation text without rewriting explicit links, code or raw HTML. */
export function reportMarkdownCitations(targets: ReadonlyMap<string, string>) {
  return (tree: Root, file: { value: unknown }) => {
    const source = String(file.value)
    const headings: Element[] = []
    function transform(parent: Root | Element) {
      restoreLiteralCitationText(parent, source)
      parent.children = parent.children.flatMap((child): Array<Element | Text | typeof child> => {
        if (child.type === 'text') return citationNodes(child.value, targets)
        if (child.type === 'element' && !['a', 'code', 'pre'].includes(child.tagName)) {
          if (/^h[1-6]$/.test(child.tagName)) headings.push(child)
          transform(child)
        }
        return [child]
      })
    }
    transform(tree)
    // The report title and section title own h1/h2. Preserve the Markdown's
    // relative heading hierarchy beneath them, even when it starts with ##.
    const firstLevel = headings.reduce((level, heading) => Math.min(level, Number(heading.tagName.slice(1))), 6)
    for (const heading of headings) {
      heading.tagName = `h${Math.min(6, Number(heading.tagName.slice(1)) - firstLevel + 3)}`
    }
  }
}
