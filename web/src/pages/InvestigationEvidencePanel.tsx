import { FormEvent, useState } from 'react'

import type { InvestigationEvidence, InvestigationEvidenceType } from '../types/investigations'
import { formatDateTime } from '../utils/datetime'
import { formatEvidenceType, INVESTIGATION_EVIDENCE_TYPES, safeInvestigationExternalUrl } from './investigationPageModel'
import { InvestigationConfirmDialog, InvestigationInlineMessage } from './InvestigationShared'
import type { InvestigationDetailController } from './useInvestigationDetail'

const UUID_PATTERN = '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}'

export function InvestigationEvidencePanel({ controller }: { controller: InvestigationDetailController }) {
  const detail = controller.detailQuery.data
  const [pendingRemoval, setPendingRemoval] = useState<InvestigationEvidence | null>(null)
  if (!detail || !controller.access) return null
  const draft = controller.evidenceDraft
  const selectedType = INVESTIGATION_EVIDENCE_TYPES.find((entry) => entry.value === draft.sourceType) ?? INVESTIGATION_EVIDENCE_TYPES[0]
  const selectedAlertUnavailable = draft.sourceType === 'alert_occurrence' && controller.alertOccurrenceUnavailable

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!draft.sourceId.trim() || selectedAlertUnavailable) return
    controller.mutation.mutate({
      kind: 'add-evidence',
      sourceType: draft.sourceType,
      sourceId: draft.sourceId.trim(),
      note: draft.note,
    })
  }

  return (
    <section aria-labelledby="investigation-evidence-heading" className="min-w-0">
      <div>
        <h2 id="investigation-evidence-heading" className="text-base font-semibold">Evidence ({detail.evidence_count})</h2>
        <p className="mt-0.5 text-sm text-slate dark:text-slate-300">Evidence keeps a point-in-time snapshot so the investigation remains understandable as source records change.</p>
      </div>

      {controller.access.canWrite && (
        <form className="mt-4 border-y border-slate/15 py-3 dark:border-white/10" onSubmit={submit}>
          <div className="grid min-w-0 gap-3 sm:grid-cols-2">
            <div><label htmlFor="investigation-evidence-type" className="text-sm font-semibold">Evidence type</label><select id="investigation-evidence-type" className="mt-1 min-h-11 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]" value={draft.sourceType} onChange={(event) => controller.setEvidenceDraft((current) => ({ ...current, sourceType: event.target.value as InvestigationEvidenceType, sourceId: '' }))}>{INVESTIGATION_EVIDENCE_TYPES.map((type) => <option key={type.value} value={type.value}>{type.label}{type.value === 'alert_occurrence' && controller.alertOccurrenceUnavailable ? ' (unavailable)' : ''}</option>)}</select></div>
            <div><label htmlFor="investigation-evidence-source-id" className="text-sm font-semibold">{selectedType.idLabel}</label><input id="investigation-evidence-source-id" required pattern={UUID_PATTERN} title="Enter a valid UUID." className="mt-1 min-h-11 w-full rounded border border-slate/30 bg-white px-3 py-2 font-mono text-sm dark:border-cyan-900/40 dark:bg-[#072019]" value={draft.sourceId} onChange={(event) => controller.setEvidenceDraft((current) => ({ ...current, sourceId: event.target.value }))} placeholder="00000000-0000-0000-0000-000000000000" /></div>
            <div className="sm:col-span-2"><div className="flex items-center justify-between gap-2"><label htmlFor="investigation-evidence-note" className="text-sm font-semibold">Context note (optional)</label><span className="text-xs text-slate dark:text-slate-400">{draft.note.length.toLocaleString()} / 2,000</span></div><textarea id="investigation-evidence-note" maxLength={2_000} rows={3} className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]" value={draft.note} onChange={(event) => controller.setEvidenceDraft((current) => ({ ...current, note: event.target.value }))} placeholder="Why this evidence matters to the investigation" /></div>
          </div>
          {draft.sourceType === 'alert_occurrence' && !controller.alertOccurrenceUnavailable && <p className="mt-2 text-xs text-slate dark:text-slate-400">The server will verify whether durable Alerting v2 occurrences are available on this deployment.</p>}
          {selectedAlertUnavailable && <div className="mt-2"><InvestigationInlineMessage tone="warning">Alert occurrence evidence is unavailable until durable Alerting v2 is enabled.</InvestigationInlineMessage></div>}
          <button type="submit" className="mt-3 min-h-11 w-full rounded bg-ink px-3 py-2 text-sm font-semibold text-white disabled:opacity-50 sm:w-auto dark:bg-cyan dark:text-[#053c2e]" disabled={controller.mutation.isPending || !draft.sourceId.trim() || selectedAlertUnavailable}>{controller.mutation.isPending ? 'Adding...' : 'Add evidence'}</button>
        </form>
      )}

      {detail.evidence.length === 0 ? (
        <p className="py-8 text-center text-sm text-slate dark:text-slate-300">No evidence has been added to this investigation.</p>
      ) : (
        <div className="mt-4 divide-y divide-slate/15 border-y border-slate/15 dark:divide-white/10 dark:border-white/10">
          {detail.evidence.map((evidence) => (
            <EvidenceEntry key={evidence.id} evidence={evidence} canRemove={controller.access?.canWrite ?? false} pending={controller.mutation.isPending} onRemove={setPendingRemoval} />
          ))}
        </div>
      )}

      <InvestigationConfirmDialog open={Boolean(pendingRemoval)} title="Remove evidence?" description={pendingRemoval ? `Remove the saved snapshot “${pendingRemoval.title_snapshot}” from this investigation? The source record will not be deleted.` : undefined} confirmLabel="Remove evidence" isConfirming={controller.mutation.isPending} onCancel={() => setPendingRemoval(null)} onConfirm={() => { if (!pendingRemoval) return; controller.mutation.mutate({ kind: 'remove-evidence', evidenceId: pendingRemoval.id }, { onSuccess: () => setPendingRemoval(null) }) }} />
    </section>
  )
}

function EvidenceEntry({ evidence, canRemove, pending, onRemove }: { evidence: InvestigationEvidence; canRemove: boolean; pending: boolean; onRemove: (evidence: InvestigationEvidence) => void }) {
  const metadata = Object.entries(evidence.metadata_snapshot)
  const sourceUrl = safeInvestigationExternalUrl(evidence.url_snapshot)
  return (
    <article className="min-w-0 py-3">
      <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2"><span className="tl-chip tl-chip-neutral">{formatEvidenceType(evidence.source_type)}</span><span className="break-all font-mono text-[11px] text-slate dark:text-slate-400">{evidence.source_id}</span></div>
          <h3 className="mt-1 break-words font-semibold">{evidence.title_snapshot}</h3>
          {evidence.description_snapshot && <p className="mt-1 whitespace-pre-wrap break-words text-sm text-slate dark:text-slate-300">{evidence.description_snapshot}</p>}
          {sourceUrl && <a href={sourceUrl} target="_blank" rel="noreferrer" className="mt-2 inline-flex min-h-11 items-center break-all text-sm font-semibold text-cyan hover:underline md:min-h-0">Open captured source URL</a>}
          {evidence.url_snapshot && !sourceUrl && <p className="mt-2 text-xs text-amber-800 dark:text-amber-200">The captured source URL uses an unsupported or unsafe scheme and cannot be opened here.</p>}
        </div>
        {canRemove && <button type="button" className="min-h-11 shrink-0 rounded border border-slate/20 px-3 py-2 text-sm font-semibold text-red-700 disabled:opacity-50 md:min-h-0 md:py-1.5 dark:border-white/10 dark:text-red-300" disabled={pending} onClick={() => onRemove(evidence)}>Remove</button>}
      </div>
      {evidence.note && <div className="mt-2 border-l-2 border-cyan/40 pl-3"><p className="text-xs font-semibold text-slate dark:text-slate-400">Analyst context</p><p className="mt-0.5 whitespace-pre-wrap break-words text-sm">{evidence.note}</p></div>}
      {metadata.length > 0 && <details className="mt-2 text-xs"><summary className="min-h-11 cursor-pointer py-2 font-semibold text-slate md:min-h-0 md:py-1 dark:text-slate-300">Snapshot metadata</summary><dl className="mt-1 grid min-w-0 gap-x-4 gap-y-2 sm:grid-cols-2">{metadata.map(([key, value]) => <div key={key} className="min-w-0"><dt className="break-words text-slate dark:text-slate-400">{humanizeKey(key)}</dt><dd className="mt-0.5 break-all font-mono">{formatMetadataValue(value)}</dd></div>)}</dl></details>}
      <p className="mt-2 text-xs text-slate dark:text-slate-400">Snapshot added <time dateTime={evidence.created_at}>{formatDateTime(evidence.created_at)}</time></p>
    </article>
  )
}

function humanizeKey(key: string): string {
  const value = key.replaceAll('_', ' ')
  return `${value.charAt(0).toUpperCase()}${value.slice(1)}`
}

function formatMetadataValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return 'Not recorded'
  if (Array.isArray(value)) return value.map((entry) => String(entry)).join(', ') || 'None'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}
