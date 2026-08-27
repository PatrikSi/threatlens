import { FormEvent } from 'react'

import type { InvestigationSeverity } from '../types/investigations'
import { formatDateTime } from '../utils/datetime'
import { INVESTIGATION_SEVERITIES } from './investigationPageModel'
import type { InvestigationDetailController } from './useInvestigationDetail'

export function InvestigationOverviewPanel({
  controller,
}: {
  controller: InvestigationDetailController
}) {
  const detail = controller.detailQuery.data
  if (!detail || !controller.access) return null
  const draft = controller.overviewDraft
  const assignableMembers = detail.members.filter(
    (member) => member.role === 'owner' || member.role === 'editor',
  )

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const changes: Record<string, unknown> = {}
    const title = draft.title.trim()
    const description = draft.description.trim()
    if (title !== controller.overviewBaseline.title) changes.title = title
    if (description !== controller.overviewBaseline.description) changes.description = description
    if (draft.severity !== controller.overviewBaseline.severity) changes.severity = draft.severity
    if (draft.visibility !== controller.overviewBaseline.visibility)
      changes.visibility = draft.visibility
    if (draft.assigneeUserId !== controller.overviewBaseline.assigneeUserId)
      changes.assignee_user_id = draft.assigneeUserId || null
    if (Object.keys(changes).length > 0) controller.mutation.mutate({ kind: 'update', changes })
  }

  return (
    <section aria-labelledby="investigation-overview-heading" className="min-w-0">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 id="investigation-overview-heading" className="text-base font-semibold">
            Overview
          </h2>
          <p className="mt-0.5 text-sm text-slate dark:text-slate-300">
            Keep scope, severity, visibility, and accountability current.
          </p>
        </div>
      </div>

      {controller.access.canWrite ? (
        <form className="mt-4 grid min-w-0 gap-3 lg:grid-cols-2" onSubmit={submit}>
          <div className="lg:col-span-2">
            <label htmlFor="investigation-title" className="text-sm font-semibold">
              Title
            </label>
            <input
              id="investigation-title"
              required
              maxLength={255}
              className="mt-1 min-h-11 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
              value={draft.title}
              onChange={(event) =>
                controller.setOverviewDraft((current) => ({
                  ...current,
                  title: event.target.value,
                }))
              }
            />
          </div>
          <div className="lg:col-span-2">
            <div className="flex items-center justify-between gap-2">
              <label htmlFor="investigation-description" className="text-sm font-semibold">
                Description
              </label>
              <span className="text-xs text-slate dark:text-slate-400">
                {draft.description.length.toLocaleString()} / 10,000
              </span>
            </div>
            <textarea
              id="investigation-description"
              maxLength={10_000}
              rows={5}
              className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
              value={draft.description}
              onChange={(event) =>
                controller.setOverviewDraft((current) => ({
                  ...current,
                  description: event.target.value,
                }))
              }
            />
          </div>
          <div>
            <label htmlFor="investigation-severity" className="text-sm font-semibold">
              Severity
            </label>
            <select
              id="investigation-severity"
              className="mt-1 min-h-11 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
              value={draft.severity}
              onChange={(event) =>
                controller.setOverviewDraft((current) => ({
                  ...current,
                  severity: event.target.value as InvestigationSeverity,
                }))
              }
            >
              {INVESTIGATION_SEVERITIES.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="investigation-visibility" className="text-sm font-semibold">
              Visibility
            </label>
            <select
              id="investigation-visibility"
              className="mt-1 min-h-11 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
              value={draft.visibility}
              onChange={(event) =>
                controller.setOverviewDraft((current) => ({
                  ...current,
                  visibility: event.target.value as 'private' | 'team',
                }))
              }
            >
              <option value="private">Private to members</option>
              <option value="team">Visible to the team</option>
            </select>
          </div>
          <div className="lg:col-span-2">
            <label htmlFor="investigation-assignee" className="text-sm font-semibold">
              Assignee
            </label>
            <select
              id="investigation-assignee"
              className="mt-1 min-h-11 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
              value={draft.assigneeUserId}
              onChange={(event) =>
                controller.setOverviewDraft((current) => ({
                  ...current,
                  assigneeUserId: event.target.value,
                }))
              }
            >
              <option value="">Unassigned</option>
              {assignableMembers.map((member) => (
                <option key={member.user_id} value={member.user_id}>
                  {member.email} ({member.role})
                </option>
              ))}
            </select>
            <p className="mt-1 text-xs text-slate dark:text-slate-400">
              Only owners and editors can be assigned.
            </p>
          </div>
          <div className="grid grid-cols-2 gap-2 lg:col-span-2 sm:flex">
            <button
              type="submit"
              className="min-h-11 rounded bg-ink px-3 py-2 text-sm font-semibold text-white disabled:opacity-50 dark:bg-cyan dark:text-[#053c2e]"
              disabled={
                controller.mutation.isPending || !controller.overviewDirty || !draft.title.trim()
              }
            >
              {controller.mutation.isPending ? 'Saving...' : 'Save changes'}
            </button>
            <button
              type="button"
              className="min-h-11 rounded border border-slate/20 px-3 py-2 text-sm font-semibold disabled:opacity-50 dark:border-white/10"
              disabled={!controller.overviewDirty || controller.mutation.isPending}
              onClick={() => controller.setOverviewDraft(controller.overviewBaseline)}
            >
              Reset
            </button>
          </div>
        </form>
      ) : (
        <div className="mt-4 min-w-0">
          <p className="whitespace-pre-wrap break-words text-sm text-slate dark:text-slate-200">
            {detail.description || 'No description has been recorded.'}
          </p>
          <dl className="mt-4 grid gap-x-6 gap-y-3 border-t border-slate/15 pt-3 text-sm sm:grid-cols-2 dark:border-white/10">
            <div>
              <dt className="text-xs text-slate dark:text-slate-400">Visibility</dt>
              <dd className="mt-0.5 capitalize">
                {detail.visibility === 'team' ? 'Visible to the team' : 'Private to members'}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-slate dark:text-slate-400">Assignee</dt>
              <dd className="mt-0.5 break-all">{detail.assignee_email ?? 'Unassigned'}</dd>
            </div>
          </dl>
        </div>
      )}

      <dl className="mt-5 grid gap-x-6 gap-y-3 border-t border-slate/15 pt-3 text-sm sm:grid-cols-2 lg:grid-cols-4 dark:border-white/10">
        <div>
          <dt className="text-xs text-slate dark:text-slate-400">Created</dt>
          <dd className="mt-0.5">
            <time dateTime={detail.created_at}>{formatDateTime(detail.created_at)}</time>
          </dd>
        </div>
        <div>
          <dt className="text-xs text-slate dark:text-slate-400">Closed</dt>
          <dd className="mt-0.5">
            {detail.closed_at ? (
              <time dateTime={detail.closed_at}>{formatDateTime(detail.closed_at)}</time>
            ) : (
              'Not closed'
            )}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-slate dark:text-slate-400">Archived</dt>
          <dd className="mt-0.5">
            {detail.archived_at ? (
              <time dateTime={detail.archived_at}>{formatDateTime(detail.archived_at)}</time>
            ) : (
              'Not archived'
            )}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-slate dark:text-slate-400">Disposition</dt>
          <dd className="mt-0.5 break-words">{detail.disposition ?? 'Not recorded'}</dd>
        </div>
      </dl>
    </section>
  )
}
