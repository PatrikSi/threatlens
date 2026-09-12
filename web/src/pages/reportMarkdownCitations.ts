import type { Element, ElementContent, Root, Text } from 'hast'

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

/** Transform parsed text only: never rewrite code, link destinations or raw HTML. */
export function reportMarkdownCitations(targets: ReadonlyMap<string, string>) {
  return (tree: Root) => {
    const headings: Element[] = []
    function transform(parent: Root | Element) {
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
