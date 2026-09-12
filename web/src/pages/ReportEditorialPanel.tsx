import { useEffect, useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import { captureSessionLease } from '../api/sessionLifecycle'
import type { ConfirmDiscardChanges } from '../hooks/useUnsavedChangesWarning'
import type { ReportDetail } from '../types/api'

const INPUT = 'mt-1 block w-full rounded border border-slate/30 bg-white p-2 text-sm dark:bg-[#072019]'
const BUTTON = 'rounded border border-slate/30 px-3 py-2 text-xs font-semibold disabled:opacity-50'
type Action = 'submit' | 'return_to_draft' | 'approve' | 'publish'
type Draft = ReturnType<typeof editableDraft>
function editableDraft(report: ReportDetail) {
  return { expected_version: report.editorial_version, title: report.title, summary_text: report.summary_text ?? '',
    sections: report.sections.map(({ key, title, body_markdown }) => ({ key, title, body_markdown })) }
}

export function ReportEditorialPanel({ report, canManage, canReview, onRefresh, onDirtyChange, discard }: {
  report: ReportDetail; canManage: boolean; canReview: boolean; onRefresh: () => void; onDirtyChange: (dirty: boolean) => void; discard: ConfirmDiscardChanges
}) {
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<Draft | null>(null)
  const [baseline, setBaseline] = useState('')
  const [note, setNote] = useState('')
  const [message, setMessage] = useState<string | null>(null)
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  const dirty = draft !== null && JSON.stringify(draft) !== baseline
  useEffect(() => { onDirtyChange(dirty || Boolean(note)); return () => onDirtyChange(false) }, [dirty, note, onDirtyChange])
  const mutation = useMutation({
    mutationFn: async (command: { action: Action } | { draft: Draft }) => {
      const lease = captureSessionLease()
      await queryClient.cancelQueries({ queryKey: ['reports', 'detail', report.id], exact: true })
      lease.assertCurrent()
      const updated = await apiFetch<ReportDetail>(`/reports/${report.id}/${'draft' in command ? 'draft' : 'editorial'}`, {
        method: 'draft' in command ? 'PUT' : 'POST',
        body: JSON.stringify('draft' in command ? command.draft : { action: command.action, expected_version: report.editorial_version, note: note.trim() || null }),
      })
      lease.assertCurrent()
      return { updated, lease }
    },
    onSuccess: async ({ updated, lease }) => {
      lease.assertCurrent()
      if (!mounted.current) return
      await queryClient.cancelQueries({ queryKey: ['reports', 'detail', report.id], exact: true })
      lease.assertCurrent()
      if (!mounted.current) return
      queryClient.setQueryData<ReportDetail>(['reports', 'detail', report.id], (current) =>
        (current?.editorial_version ?? 0) > (updated.editorial_version ?? 0) ? current : updated)
      void queryClient.invalidateQueries({ queryKey: ['reports', 'library'] })
      setDraft(null); setNote(''); setMessage('Report revision saved.')
    },
  })
  const state = report.publication_status
  if (!state || report.status !== 'ready') return null
  const staleDraft = draft !== null && draft.expected_version !== report.editorial_version
  function startEdit() {
    const snapshot = editableDraft(report)
    setDraft(snapshot); setBaseline(JSON.stringify(snapshot)); setMessage(null); mutation.reset()
  }
  function act(action: Action) { if (!mutation.isPending) { setMessage(null); mutation.mutate({ action }) } }
  return (
    <section aria-label="Report review and publication" className="rounded-lg border border-slate/20 bg-white/85 p-4 dark:border-cyan-900/40 dark:bg-[#041612]/90">
      <h2 className="font-display text-lg">Review and publication · <span className="capitalize">{state}</span></h2>
      <p className="mt-1 text-xs text-slate dark:text-slate-300">Revision {report.editorial_version}. {state === 'published' ? 'Published content is immutable.' : 'Draft → review → approved → published. Approval covers the exact content and retained evidence.'} {report.delivery_requested && state !== 'published' ? 'Requested deliveries wait for publication.' : ''}</p>
      {!report.review_required && <p className="mt-2 text-xs">Automatic publication is enabled for this report. A human approval is not required by its schedule or legacy policy.</p>}
      {report.approved_at && <p className="mt-2 text-xs">Approved {new Date(report.approved_at).toLocaleString()}{report.approval_self_review ? ' · Author self-review (recorded in audit history)' : ''}.</p>}
      {report.published_at && <p className="mt-1 text-xs">Published {new Date(report.published_at).toLocaleString()}.</p>}
      {report.editorial_note && <p className="mt-2 whitespace-pre-wrap text-sm">Latest review note: {report.editorial_note}</p>}
      {report.revision_current === false && <p role="alert" className="mt-2 text-sm text-red-700 dark:text-red-300">The content or evidence no longer matches its reviewed revision. Publication and delivery are blocked. Return it to draft for a fresh review if available.</p>}
      {mutation.isError && <div role="alert" className="mt-3 text-sm text-red-700 dark:text-red-300"><p>{resolveApiErrorMessage(mutation.error, 'The report revision could not be saved')}</p><button type="button" className={`${BUTTON} mt-2`} onClick={onRefresh}>Refresh current status</button><p className="mt-1 text-xs">Refresh preserves your open draft. If a request timed out, check the current status before repeating it.</p></div>}
      {message && <p role="status" className="mt-2 text-sm">{message}</p>}
      {staleDraft && <p role="alert" className="mt-2 text-sm text-amber-800 dark:text-amber-200">A newer revision is available. Your draft is preserved; discard it to load the new revision before saving.</p>}
      {draft ? <form className="mt-3" onSubmit={(event) => { event.preventDefault(); if (!mutation.isPending && !staleDraft) mutation.mutate({ draft }) }}>
        <fieldset disabled={mutation.isPending} className="space-y-3">
          <label className="block text-xs font-semibold">Report title<input className={INPUT} required maxLength={255} value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} /></label>
          <label className="block text-xs font-semibold">Delivery summary<textarea className={INPUT} rows={4} maxLength={100000} value={draft.summary_text} onChange={(event) => setDraft({ ...draft, summary_text: event.target.value })} /></label>
          {draft.sections.map((section, index) => <div key={section.key} className="space-y-2 rounded border border-slate/20 p-3">
            <label className="block text-xs font-semibold">Section {index + 1} title<input className={INPUT} required maxLength={255} value={section.title} onChange={(event) => setDraft({ ...draft, sections: draft.sections.map((entry, position) => position === index ? { ...entry, title: event.target.value } : entry) })} /></label>
            <label className="block text-xs font-semibold">{section.title} Markdown<textarea className={`${INPUT} font-mono`} rows={10} maxLength={400000} required value={section.body_markdown} onChange={(event) => setDraft({ ...draft, sections: draft.sections.map((entry, position) => position === index ? { ...entry, body_markdown: event.target.value } : entry) })} /></label>
          </div>)}
          <p className="text-xs">Keep source references such as [S1]. Edits retain the original evidence snapshots; review each claim against its sources before approval.</p>
          <div className="flex gap-2"><button className={BUTTON} type="submit" disabled={staleDraft || !dirty}>{mutation.isPending ? 'Saving…' : 'Save draft'}</button><button className={BUTTON} type="button" onClick={() => discard(() => setDraft(null))}>Discard editor</button></div>
        </fieldset>
      </form> : <fieldset disabled={mutation.isPending} className="mt-3 space-y-3">
        {canReview && state !== 'published' && <label className="block text-xs font-semibold">Review note (optional)<textarea className={INPUT} rows={2} maxLength={2000} value={note} onChange={(event) => setNote(event.target.value)} /></label>}
        <div className="flex flex-wrap gap-2">
          {canManage && state === 'draft' && <><button type="button" className={BUTTON} onClick={startEdit}>Edit draft</button><button type="button" className={BUTTON} onClick={() => act('submit')}>Submit for review</button></>}
          {canReview && state === 'review' && <button type="button" className={BUTTON} disabled={report.revision_current === false} onClick={() => act('approve')}>Approve this revision</button>}
          {canReview && (state === 'review' || state === 'approved') && <button type="button" className={BUTTON} onClick={() => act('return_to_draft')}>Return to draft</button>}
          {canManage && state === 'approved' && <button type="button" className={BUTTON} disabled={report.revision_current === false} onClick={() => act('publish')}>Publish approved revision</button>}
          {mutation.isPending && <span role="status" className="text-xs">Saving revision…</span>}
        </div>
        {canReview && state === 'review' && <p className="text-xs">Approval records your account and this evidence revision. Self-review is allowed and disclosed.</p>}
      </fieldset>}
    </section>
  )
}
