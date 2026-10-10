import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '../api/client'
import { accessibleQueryData } from '../api/queryData'
import { resolveApiErrorMessage } from '../api/errors'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import type {
  IndicatorHistoryPage,
  IndicatorSuppression,
  IndicatorSuppressionPage,
} from '../types/indicators'

const BUTTON =
  'rounded border border-slate/30 px-3 py-1.5 text-sm disabled:opacity-50'
const INPUT =
  'mt-1 w-full rounded border border-slate/30 bg-white p-2 dark:bg-[#072019]'
const TYPES = [
  'domain',
  'url',
  'email',
  'ipv4',
  'ipv6',
  'hash_sha256',
  'hash_sha1',
  'hash_md5',
  'cve',
  'vendor',
  'program',
]

function draftFrom(rule: IndicatorSuppression | null) {
  return {
    ioc_type: rule?.ioc_type ?? 'domain',
    value: rule?.value ?? '',
    reason: rule?.reason ?? '',
    expiry: rule?.expires_at?.slice(0, 16) ?? '',
    active: rule?.active ?? true,
  }
}

export function TeamIndicatorSuppressions({
  teamId,
  writable,
}: {
  teamId: string
  writable: boolean
}) {
  const [page, setPage] = useState(1)
  const [baseline, setBaseline] = useState<IndicatorSuppression | null>(null)
  const [draft, setDraft] = useState(() => draftFrom(null))
  const [notice, setNotice] = useState('')
  const client = useQueryClient()
  const base = `/teams/${teamId}/indicator-suppressions`
  const query = useQuery({
    queryKey: ['indicator-suppressions', teamId, page],
    queryFn: ({ signal }) =>
      apiFetch<IndicatorSuppressionPage>(`${base}?page=${page}&page_size=20`, {
        signal,
      }),
    refetchInterval: 30_000,
  })
  const data = accessibleQueryData(query)
  const dirty = JSON.stringify(draft) !== JSON.stringify(draftFrom(baseline))
  const discard = useUnsavedChangesWarning(
    dirty,
    'Discard unsaved indicator suppression changes?',
  )
  const save = useMutation({
    mutationFn: () =>
      apiFetch<IndicatorSuppression>(
        `${base}${baseline ? `/${baseline.id}` : ''}`,
        {
          method: baseline ? 'PATCH' : 'POST',
          body: JSON.stringify({
            ...(baseline
              ? { expected_version: baseline.version }
              : { ioc_type: draft.ioc_type, value: draft.value.trim() }),
            active: draft.active,
            reason: draft.reason.trim(),
            expires_at: draft.expiry
              ? new Date(`${draft.expiry}:00Z`).toISOString()
              : null,
          }),
        },
      ),
    onSuccess: (saved) => {
      setBaseline(saved)
      setDraft(draftFrom(saved))
      setNotice('Team suppression saved.')
      void client.invalidateQueries({
        queryKey: ['indicator-suppressions', teamId],
      })
      void client.invalidateQueries({ queryKey: ['item-indicators'] })
      void client.invalidateQueries({
        queryKey: ['suppression-history', saved.id],
      })
    },
  })
  const select = (rule: IndicatorSuppression | null) =>
    discard(() => {
      setBaseline(rule)
      setDraft(draftFrom(rule))
      setNotice('')
      save.reset()
    })
  const current = data?.items.find((rule) => rule.id === baseline?.id)
  const changedElsewhere = Boolean(
    current && current.version !== baseline?.version,
  )
  const expiryInvalid = Boolean(
    draft.expiry &&
      (!Number.isFinite(Date.parse(`${draft.expiry}:00Z`)) ||
        Date.parse(`${draft.expiry}:00Z`) <= Date.now()),
  )
  return (
    <section aria-label="Team indicator suppressions" className="space-y-3">
      <h2 className="font-semibold">Indicator suppressions</h2>
      <p className="text-sm">
        Exclude exact indicators for this team while preserving source evidence
        and other teams' assessments. Managers can create, expire, or deactivate
        rules.
      </p>
      {discard.discardDialog}
      {query.isPending && <p role="status">Loading suppressions…</p>}
      {query.isError && (
        <p role="alert">
          {resolveApiErrorMessage(
            query.error,
            'Suppressions could not be refreshed. Your draft is preserved.',
          )}{' '}
          <button className={BUTTON} onClick={() => void query.refetch()}>
            Retry suppressions
          </button>
        </p>
      )}
      {data && (
        <>
          <p className="text-xs">
            {data.total} saved suppressions · Page {page} of{' '}
            {Math.max(1, Math.ceil(data.total / 20))}
          </p>
          {data.items.map((rule) => (
            <article
              key={rule.id}
              className="space-y-1 rounded border border-slate/25 p-3 text-sm"
            >
              <p className="break-all font-mono">
                {rule.ioc_type}: {rule.value}
              </p>
              <p>
                {!rule.active
                  ? 'Inactive'
                  : rule.expired
                    ? 'Expired'
                    : 'Active'}{' '}
                · Revision {rule.version}
                {rule.expires_at
                  ? ` · Expires ${new Date(rule.expires_at).toLocaleString()}`
                  : ''}
              </p>
              <p className="whitespace-pre-wrap break-words">{rule.reason}</p>
              <button
                className={BUTTON}
                disabled={save.isPending}
                onClick={() => select(rule)}
              >
                {data.can_manage ? 'Edit suppression' : 'Inspect suppression'}
              </button>
            </article>
          ))}
          {!data.total && (
            <p>No team indicator suppressions have been created.</p>
          )}
          {data.total > 20 && (
            <div className="flex gap-2">
              <button
                className={BUTTON}
                disabled={page === 1 || query.isFetching || save.isPending}
                onClick={() => setPage(page - 1)}
              >
                Previous suppressions
              </button>
              <button
                className={BUTTON}
                disabled={
                  page * 20 >= data.total || query.isFetching || save.isPending
                }
                onClick={() => setPage(page + 1)}
              >
                Next suppressions
              </button>
            </div>
          )}
          {data.can_manage && (
            <button
              className={BUTTON}
              disabled={save.isPending}
              onClick={() => select(null)}
            >
              New suppression
            </button>
          )}
          {(data.can_manage || baseline) && (
            <div className="space-y-3 border-t border-slate/20 pt-3">
              <h3 className="font-semibold">
                {baseline ? 'Selected suppression' : 'Create suppression'}
              </h3>
              {changedElsewhere && (
                <p role="status">
                  This suppression changed elsewhere. Your draft remains at
                  revision {baseline?.version}. Select it again to reload after
                  preserving your changes.
                </p>
              )}
              {notice && <p role="status">{notice}</p>}
              {save.isError && (
                <p role="alert">
                  {resolveApiErrorMessage(
                    save.error,
                    'Suppression could not be saved. Your input is preserved.',
                  )}
                </p>
              )}
              <fieldset
                disabled={
                  !writable ||
                  !data.can_manage ||
                  query.isError ||
                  save.isPending
                }
                className="space-y-3"
              >
                <label className="block text-sm">
                  Indicator type
                  <select
                    className={INPUT}
                    disabled={Boolean(baseline)}
                    value={draft.ioc_type}
                    onChange={(event) =>
                      setDraft({ ...draft, ioc_type: event.target.value })
                    }
                  >
                    {TYPES.map((type) => (
                      <option key={type} value={type}>
                        {type}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="block text-sm">
                  Exact indicator value
                  <input
                    className={INPUT}
                    disabled={Boolean(baseline)}
                    maxLength={4096}
                    value={draft.value}
                    onChange={(event) =>
                      setDraft({ ...draft, value: event.target.value })
                    }
                  />
                </label>
                <label className="block text-sm">
                  Suppression reason
                  <textarea
                    className={INPUT}
                    maxLength={2000}
                    rows={3}
                    value={draft.reason}
                    onChange={(event) =>
                      setDraft({ ...draft, reason: event.target.value })
                    }
                  />
                </label>
                <label className="block text-sm">
                  Expires at (UTC, optional)
                  <input
                    className={INPUT}
                    type="datetime-local"
                    value={draft.expiry}
                    onChange={(event) =>
                      setDraft({ ...draft, expiry: event.target.value })
                    }
                  />
                </label>
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={draft.active}
                    onChange={(event) =>
                      setDraft({ ...draft, active: event.target.checked })
                    }
                  />
                  Suppression active
                </label>
                {expiryInvalid && (
                  <p role="alert">
                    Choose a future UTC expiry or leave it empty.
                  </p>
                )}
                <button
                  className={BUTTON}
                  disabled={
                    !dirty ||
                    !draft.value.trim() ||
                    !draft.reason.trim() ||
                    expiryInvalid ||
                    changedElsewhere
                  }
                  onClick={() => save.mutate()}
                >
                  {save.isPending ? 'Saving suppression…' : 'Save suppression'}
                </button>
              </fieldset>
              {baseline && (
                <SuppressionHistory
                  key={baseline.id}
                  base={base}
                  ruleId={baseline.id}
                />
              )}
            </div>
          )}
        </>
      )}
    </section>
  )
}

function SuppressionHistory({
  base,
  ruleId,
}: {
  base: string
  ruleId: string
}) {
  const [open, setOpen] = useState(false)
  const [page, setPage] = useState(1)
  const query = useQuery({
    queryKey: ['suppression-history', ruleId, page],
    queryFn: ({ signal }) =>
      apiFetch<IndicatorHistoryPage>(
        `${base}/${ruleId}/history?page=${page}&page_size=10`,
        { signal },
      ),
    enabled: open,
  })
  const data = accessibleQueryData(query)
  return (
    <details
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary className="cursor-pointer font-semibold">
        Suppression history
      </summary>
      {query.isPending && open && (
        <p role="status">Loading suppression history…</p>
      )}
      {query.isError && (
        <p role="alert">
          {resolveApiErrorMessage(query.error, 'History could not be loaded.')}{' '}
          <button className={BUTTON} onClick={() => void query.refetch()}>
            Retry history
          </button>
        </p>
      )}
      {data?.items.map((entry) => (
        <p
          key={entry.version}
          className="mt-2 whitespace-pre-wrap break-words text-sm"
        >
          Revision {entry.version} ·{' '}
          {new Date(entry.created_at).toLocaleString()} ·{' '}
          {String(entry.snapshot.reason ?? '')}
        </p>
      ))}
      {data && data.total > 10 && (
        <div className="mt-2 flex gap-2">
          <button
            className={BUTTON}
            disabled={page === 1 || query.isFetching}
            onClick={() => setPage(page - 1)}
          >
            Previous history
          </button>
          <span>
            Page {page} of {Math.ceil(data.total / 10)}
          </span>
          <button
            className={BUTTON}
            disabled={page * 10 >= data.total || query.isFetching}
            onClick={() => setPage(page + 1)}
          >
            Next history
          </button>
        </div>
      )}
    </details>
  )
}
