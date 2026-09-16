import type { ArticleEvidence, StructuredExtraction } from '../types/articleIntelligence'

const sourceLabels = { title: 'Article title', summary: 'Feed summary', article_text: 'Article text' }
function humanize(value: string) { return value.replaceAll('_', ' ') }

export function EvidencePassages({ evidence }: { evidence: ArticleEvidence[] }) {
  if (!evidence.length) return <p className="text-xs">No supporting passage was provided.</p>
  return <details className="mt-2 text-sm">
    <summary className="cursor-pointer font-semibold">Supporting passages ({evidence.length})</summary>
    {evidence.map((passage, index) => <figure key={`${passage.source}-${index}`} className="mt-2 border-l-2 border-cyan/40 pl-3">
      <blockquote className="whitespace-pre-wrap break-words">{passage.quote}</blockquote>
      <figcaption className="mt-1 text-xs text-slate dark:text-slate-300">{sourceLabels[passage.source]}{passage.start !== undefined && passage.end !== undefined ? ` · characters ${passage.start}–${passage.end}` : ''}</figcaption>
    </figure>)}
  </details>
}

export function IntelligenceList({ title, values }: { title: string; values: string[] }) {
  return <div className="mt-2">
    <h5 className="text-sm font-semibold">{title}</h5>
    {values.length ? <ul className="mt-1 list-disc space-y-1 pl-5 text-sm">{values.map((value, index) => <li key={index}>{value}</li>)}</ul> : <p className="text-sm text-slate dark:text-slate-300">Not provided.</p>}
  </div>
}

export function ArticleEvidenceView({ extraction, stale }: { extraction: StructuredExtraction; stale: boolean }) {
  const names = new Map(extraction.entities.map((entity) => [entity.id, entity.name]))
  return <section aria-label="Shared article evidence" className="tl-surface-muted mt-3 space-y-3 rounded p-3">
    <h3 className="font-semibold">Shared article evidence</h3>
    <p className="text-xs text-slate dark:text-slate-300">AI-extracted statements from this source, separate from team assessments. “Reported” describes what the source says; “AI inference” is a model interpretation, not an independently verified fact.</p>
    {stale && <p role="status" className="text-sm text-amber-700 dark:text-amber-300">This extraction is from an earlier article revision. Review the current article before using it.</p>}
    {extraction.truncated && <p role="status" className="text-sm">Only part of the article fit in the model input. Unseen content may change these findings.</p>}
    <details open>
      <summary className="cursor-pointer font-semibold">Entities and behaviors ({extraction.entities.length})</summary>
      {!extraction.entities.length && <p className="mt-2 text-sm">No supported entities or behaviors were extracted.</p>}
      <div className="mt-2 grid gap-3 lg:grid-cols-2">
        {extraction.entities.map((entity) => <article key={entity.id} className="min-w-0 rounded border border-slate/20 p-3">
          <h4 className="break-words font-semibold">{entity.name}</h4>
          <p className="mt-1 text-xs">{humanize(entity.kind)} · {entity.assertion === 'reported' ? 'Reported by source' : 'AI inference'}{entity.indicator_role ? ` · ${humanize(entity.indicator_role)}` : ''}</p>
          {entity.description && <p className="mt-2 text-sm">{entity.description}</p>}
          {entity.versions.length > 0 && <p className="mt-2 text-sm">Affected versions: {entity.versions.join(', ')}</p>}
          <EvidencePassages evidence={entity.evidence} />
        </article>)}
      </div>
    </details>
    {extraction.relationships.length > 0 && <details>
      <summary className="cursor-pointer font-semibold">Relationships ({extraction.relationships.length})</summary>
      {extraction.relationships.map((relation, index) => <article key={index} className="mt-2 rounded border border-slate/20 p-3">
        <h4 className="text-sm font-semibold">{names.get(relation.source_entity_id) ?? relation.source_entity_id} → {humanize(relation.relationship)} → {names.get(relation.target_entity_id) ?? relation.target_entity_id}</h4>
        <p className="mt-1 text-xs">{relation.assertion === 'reported' ? 'Reported by source' : 'AI inference'}</p>
        <p className="mt-2 text-sm">{relation.description}</p>
        <EvidencePassages evidence={relation.evidence} />
      </article>)}
    </details>}
    {extraction.information_gaps.length > 0 && <IntelligenceList title="Evidence gaps" values={extraction.information_gaps} />}
  </section>
}
