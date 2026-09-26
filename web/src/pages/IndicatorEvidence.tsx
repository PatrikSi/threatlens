import type { Indicator } from '../types/indicators'

export function IndicatorEvidence({ indicator }: { indicator: Indicator }) {
  return (
    <div className="space-y-2 text-sm">
      {indicator.evidence_truncated && <p>Showing {indicator.evidence.length} retained passages from {indicator.occurrences} occurrences. Additional passages were not retained.</p>}
      <p>
        Extraction confidence:{' '}
        {Math.round(indicator.extraction_confidence * 100)}%. This measures
        extraction, not maliciousness.
      </p>
      {indicator.ai ? (
        <>
          <p>
            AI role: {indicator.ai.role.replaceAll('_', ' ')} ·{' '}
            {indicator.ai.assertion === 'reported'
              ? 'Reported by source'
              : 'AI inference'}
            {indicator.ai_current ? '' : ' · stale evidence'}
          </p>
          <p>
            Maliciousness confidence:{' '}
            {indicator.ai.maliciousness_confidence == null
              ? 'not scored'
              : `${Math.round(indicator.ai.maliciousness_confidence * 100)}%`}
            .
          </p>
        </>
      ) : (
        <p>No linked AI assessment is available.</p>
      )}
      {indicator.evidence.length === 0 && (
        <p>No retained passage is available for this extraction.</p>
      )}
      {indicator.evidence.map((entry, index) => (
        <figure key={index} className="border-l-2 border-cyan/40 pl-3">
          <blockquote className="whitespace-pre-wrap break-all">
            {entry.quote ?? entry.raw}
          </blockquote>
          <figcaption className="text-xs">
            {entry.source}
            {entry.start != null && entry.end != null
              ? ` · match characters ${entry.start}–${entry.end}`
              : ''}
            {entry.transformations.length > 0
              ? ` · ${entry.transformations.join(', ')}`
              : ''}
          </figcaption>
          {entry.raw !== indicator.value && (
            <p className="text-xs break-all">Original spelling: {entry.raw}</p>
          )}
        </figure>
      ))}
      {indicator.ai?.evidence.map((entry, index) => (
        <figure key={`ai-${index}`} className="border-l-2 border-slate/30 pl-3">
          <blockquote className="whitespace-pre-wrap break-words">
            {entry.quote}
          </blockquote>
          <figcaption className="text-xs">
            AI supporting passage · {entry.source}
          </figcaption>
        </figure>
      ))}
      {indicator.excluded && (
        <p role="status">
          Excluded from actionable indicators:{' '}
          {indicator.exclusion_reasons.join(', ')}.
        </p>
      )}
      {indicator.suppressed && (
        <p role="status">Suppressed for the selected team.</p>
      )}
    </div>
  )
}
