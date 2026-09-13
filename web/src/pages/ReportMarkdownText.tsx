import { useId } from 'react'
import Markdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { sanitizeHref } from './dashboardContent'
import { reportMarkdownCitations } from './reportMarkdownCitations'

const components: Components = {
  a: ({ href, children, 'aria-label': label }) => {
    if (!href) return <span>{children}</span>
    const internal = href.startsWith('#')
    return (
      <a href={href} aria-label={label} target={internal ? undefined : '_blank'} rel={internal ? undefined : 'noopener noreferrer'}
        className="rounded font-medium text-cyan-800 underline underline-offset-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 dark:text-cyan-200"
        onClick={internal ? (event) => {
          const target = document.getElementById(href.slice(1))
          target?.focus()
          if (target && document.activeElement === target) event.preventDefault()
        } : undefined}
      >{children}</a>
    )
  },
  img: ({ alt }) => <span className="italic text-slate dark:text-slate-300">[Image omitted{alt ? `: ${alt}` : ''}]</span>,
  table: ({ children }) => (
    <div role="region" aria-label="Report table" tabIndex={0} className="max-w-full overflow-x-auto rounded border border-slate/20 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 dark:border-white/20">
      <table className="w-full border-collapse text-left">{children}</table>
    </div>
  ),
  th: ({ children, style }) => <th scope="col" style={style} className="border-b border-slate/20 bg-slate/5 px-3 py-2 font-semibold dark:border-white/20 dark:bg-white/5">{children}</th>,
  td: ({ children, style }) => <td style={style} className="border-b border-slate/15 px-3 py-2 align-top dark:border-white/10">{children}</td>,
  pre: ({ children }) => <pre tabIndex={0} aria-label="Code block" className="max-w-full overflow-x-auto rounded border border-slate/20 bg-slate/5 p-3 text-xs dark:border-white/20 dark:bg-white/5">{children}</pre>,
  input: ({ checked }) => <span role="img" aria-label={checked ? 'Completed task' : 'Incomplete task'}>{checked ? '☑' : '☐'}</span>,
}

const markdownClassName = [
  'mt-2 min-w-0 space-y-3 break-words text-sm leading-6 text-slate-800 dark:text-slate-200',
  '[&_h3]:mt-4 [&_h3]:text-lg [&_h3]:font-semibold',
  '[&_h4]:mt-3 [&_h4]:text-base [&_h4]:font-semibold [&_h5]:font-semibold [&_h6]:font-semibold',
  '[&_ul]:list-disc [&_ul]:pl-6 [&_ol]:list-decimal [&_ol]:pl-6 [&_li]:my-1 [&_li>p]:my-1',
  '[&_blockquote]:border-l-4 [&_blockquote]:border-slate/30 [&_blockquote]:pl-4 [&_blockquote]:italic',
  '[&_code]:font-mono [&_code]:text-[0.9em] [&_hr]:border-slate/20',
].join(' ')

export function ReportMarkdownText({ value, citationTargets }: {
  value: string
  citationTargets: ReadonlyMap<string, string>
}) {
  const id = useId()
  return (
    <div className={markdownClassName}>
      <Markdown skipHtml remarkPlugins={[remarkGfm]} rehypePlugins={[[reportMarkdownCitations, citationTargets]]}
        remarkRehypeOptions={{ clobberPrefix: `report-markdown-${id}-` }}
        urlTransform={(url, key) => key === 'href' ? (url.startsWith('#') ? url : sanitizeHref(url) ?? undefined) : undefined}
        components={components}
      >{value}</Markdown>
    </div>
  )
}
