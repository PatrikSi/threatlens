import type { ExtractionCoverage } from '../types/articleIntelligence'

export function ArticleExtractionCoverage({ coverage }: { coverage: ExtractionCoverage }) {
  const completed = coverage.sections.filter((section) => section.status === 'completed').length
  return <details className="mt-2 text-sm">
    <summary className="cursor-pointer font-semibold">Extraction coverage: {completed} of {coverage.sections.length} planned sections</summary>
    <p className="mt-2">{coverage.processed_chars.toLocaleString()} of {coverage.normalized_text_chars.toLocaleString()} normalized article characters processed. {coverage.uncovered_chars.toLocaleString()} characters remain uncovered.</p>
    <p className="mt-1 text-xs">
      Coordinates use whitespace-normalized text. This run reserves up to {coverage.token_budget.toLocaleString()} estimated input and maximum output tokens across {coverage.call_limit} extraction calls, plus synthesis when it fits.
      {coverage.summary_scope === 'section_synthesis'
        ? ' The summary synthesizes processed sections and cites their section numbers; uncovered text remains outside its evidence.'
        : coverage.summary_scope === 'processed_sections'
          ? ' The summary includes labeled contributions from processed sections; relevance retains its initial assessment.'
          : ' Summary and relevance use the first section.'}
    </p>
    {(coverage.synthesis_status === 'failed' || coverage.synthesis_status === 'pending') && <p role="status">Combined summary unavailable. Verified extraction and labeled section summaries remain available. Review provider receipts before requesting synthesis recovery.</p>}
    {coverage.synthesis_status === 'budget_limited' && <p role="status">Combined summary did not fit the authorized token budget. Verified section results remain available.</p>}
    {coverage.summary_limited && <p role="status">Some section summaries exceeded their 900-character storage budget. Primary quotations and coverage remain available for review.</p>}
    {coverage.output_limited && <p role="status" className="mt-1">The combined output reached its entity or relationship limit. Review source evidence for additional findings.</p>}
    <ol className="mt-2 list-decimal pl-5">
      {coverage.sections.map((section) => <li key={section.index}>Characters {section.start.toLocaleString()}–{section.end.toLocaleString()}: {section.status === 'started' ? 'awaiting a durable result; delivery may require reconciliation' : section.status}</li>)}
    </ol>
  </details>
}
