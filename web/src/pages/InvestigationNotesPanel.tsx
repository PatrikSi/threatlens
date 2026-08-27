import { FormEvent, useState } from 'react'

import type { InvestigationNote } from '../types/investigations'
import { formatDateTime } from '../utils/datetime'
import { canEditInvestigationNote } from './investigationPageModel'
import { InvestigationConfirmDialog } from './InvestigationShared'
import type { InvestigationDetailController } from './useInvestigationDetail'

export function InvestigationNotesPanel({ controller }: { controller: InvestigationDetailController }) {
  const detail = controller.detailQuery.data
  const [pendingRemoval, setPendingRemoval] = useState<InvestigationNote | null>(null)
  if (!detail || !controller.access) return null

  const addNote = (event: FormEvent) => {
    event.preventDefault()
    const body = controller.noteDraft.trim()
    if (body) controller.mutation.mutate({ kind: 'add-note', body })
  }

  return (
    <section aria-labelledby="investigation-notes-heading" className="min-w-0">
      <div>
        <h2 id="investigation-notes-heading" className="text-base font-semibold">Analyst notes ({detail.note_count})</h2>
        <p className="mt-0.5 text-sm text-slate dark:text-slate-300">Record decisions, hypotheses, handoff context, and follow-up work in plain text.</p>
      </div>

      {controller.access.canWrite && (
        <form className="mt-4 border-y border-slate/15 py-3 dark:border-white/10" onSubmit={addNote}>
          <div className="flex items-center justify-between gap-2"><label htmlFor="investigation-new-note" className="text-sm font-semibold">Add note</label><span className="text-xs text-slate dark:text-slate-400">{controller.noteDraft.length.toLocaleString()} / 10,000</span></div>
          <textarea id="investigation-new-note" rows={4} maxLength={10_000} className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]" value={controller.noteDraft} onChange={(event) => controller.setNoteDraft(event.target.value)} placeholder="Record analyst context or a decision" />
          <button type="submit" className="mt-2 min-h-11 w-full rounded bg-ink px-3 py-2 text-sm font-semibold text-white disabled:opacity-50 sm:w-auto dark:bg-cyan dark:text-[#053c2e]" disabled={controller.mutation.isPending || !controller.noteDraft.trim()}>{controller.mutation.isPending ? 'Saving...' : 'Add note'}</button>
        </form>
      )}

      {detail.notes_truncated && <p role="status" className="mt-3 rounded border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-700/40 dark:bg-amber-950/30 dark:text-amber-100">Showing the 200 most recent notes out of {detail.note_count}. Older note events remain available in Activity.</p>}

      {detail.notes.length === 0 ? (
        <p className="py-8 text-center text-sm text-slate dark:text-slate-300">No analyst notes have been recorded.</p>
      ) : (
        <div className="mt-4 divide-y divide-slate/15 border-y border-slate/15 dark:divide-white/10 dark:border-white/10">
          {detail.notes.map((note) => {
            const editing = controller.editingNoteId === note.id
            const canEdit = controller.access?.canWrite
              && canEditInvestigationNote(note.author_user_id, controller.currentUserQuery.data?.id, detail.current_user_role)
            return (
              <article key={note.id} className="min-w-0 py-3">
                <div className="flex min-w-0 flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0 text-xs text-slate dark:text-slate-400">
                    <p className="break-all font-semibold text-ink dark:text-slate-200">{note.author_email ?? 'Deleted or system account'}</p>
                    <p className="mt-0.5">Created <time dateTime={note.created_at}>{formatDateTime(note.created_at)}</time>{note.updated_at !== note.created_at ? ` · edited ${formatDateTime(note.updated_at)}` : ''} · note version {note.version}</p>
                  </div>
                  {canEdit && !editing && (
                    <div className="flex gap-2">
                      <button type="button" className="min-h-11 rounded border border-slate/20 px-3 py-2 text-sm font-semibold disabled:opacity-50 md:min-h-0 md:py-1 dark:border-white/10" disabled={controller.mutation.isPending || controller.editingNoteId !== null} title={controller.editingNoteId !== null ? 'Finish or cancel the current note edit first.' : undefined} onClick={() => controller.beginNoteEdit(note.id, note.body)}>Edit</button>
                      <button type="button" className="min-h-11 rounded border border-slate/20 px-3 py-2 text-sm font-semibold text-red-700 disabled:opacity-50 md:min-h-0 md:py-1 dark:border-white/10 dark:text-red-300" disabled={controller.mutation.isPending || controller.editingNoteId !== null} title={controller.editingNoteId !== null ? 'Finish or cancel the current note edit first.' : undefined} onClick={() => setPendingRemoval(note)}>Remove</button>
                    </div>
                  )}
                </div>
                {editing ? (
                  <form className="mt-2" onSubmit={(event) => { event.preventDefault(); const body = controller.editingNoteBody.trim(); if (body) controller.mutation.mutate({ kind: 'update-note', noteId: note.id, body }) }}>
                    <label htmlFor={`investigation-note-edit-${note.id}`} className="sr-only">Edit note by {note.author_email ?? 'unknown author'}</label>
                    <textarea id={`investigation-note-edit-${note.id}`} rows={4} maxLength={10_000} className="w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]" value={controller.editingNoteBody} onChange={(event) => controller.setEditingNoteBody(event.target.value)} />
                    <div className="mt-2 grid grid-cols-2 gap-2 sm:flex"><button type="submit" className="min-h-11 rounded bg-ink px-3 py-2 text-sm font-semibold text-white disabled:opacity-50 dark:bg-cyan dark:text-[#053c2e]" disabled={controller.mutation.isPending || !controller.editingNoteBody.trim()}>{controller.mutation.isPending ? 'Saving...' : 'Save note'}</button><button type="button" className="min-h-11 rounded border border-slate/20 px-3 py-2 text-sm font-semibold dark:border-white/10" disabled={controller.mutation.isPending} onClick={() => { controller.setEditingNoteId(null); controller.setEditingNoteBody('') }}>Cancel</button></div>
                  </form>
                ) : (
                  <p className="mt-2 whitespace-pre-wrap break-words text-sm leading-relaxed">{note.body}</p>
                )}
              </article>
            )
          })}
        </div>
      )}

      <InvestigationConfirmDialog
        open={Boolean(pendingRemoval)}
        title="Remove analyst note?"
        description="Remove this note from the investigation? Its content will no longer be visible, but the removal remains recorded in Activity and audit logs."
        confirmLabel="Remove note"
        isConfirming={controller.mutation.isPending}
        onCancel={() => setPendingRemoval(null)}
        onConfirm={() => {
          if (!pendingRemoval) return
          controller.mutation.mutate(
            { kind: 'remove-note', noteId: pendingRemoval.id },
            { onSuccess: () => setPendingRemoval(null) },
          )
        }}
      />
    </section>
  )
}
