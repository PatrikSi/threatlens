import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ApiError, apiFetch } from '../api/client'
import { accessibleQueryData } from '../api/queryData'
import { resolveApiErrorMessage } from '../api/errors'
import { ConfirmDialog, DialogSurface } from '../components/ConfirmDialog'
import type { WebhookCredentialProfile } from '../types/webhookAutomation'
import { CredentialEditor } from './WebhookCredentialEditor'

const INPUT =
  'mt-1 w-full rounded border border-slate/30 bg-white p-2 text-sm dark:bg-[#072019]'
const BUTTON =
  'rounded border border-slate/30 px-3 py-1.5 text-sm disabled:opacity-50'

export function WebhookCredentialProfiles({
  writable,
  onClose,
  onDirtyChange,
}: {
  writable: boolean
  onClose: () => void
  onDirtyChange: (dirty: boolean) => void
}) {
  const [selected, setSelected] = useState<WebhookCredentialProfile | null>(
    null,
  )
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState(false)
  const [pending, setPending] = useState<(() => void) | null>(null)
  const [notice, setNotice] = useState('')
  const query = useQuery({
    queryKey: ['notifications', 'credential-profiles'],
    queryFn: ({ signal }) =>
      apiFetch<WebhookCredentialProfile[]>(
        '/notifications/credential-profiles',
        { signal },
      ),
  })
  const profiles = accessibleQueryData(query) ?? []
  const denied =
    query.error instanceof ApiError &&
    [401, 403, 404].includes(query.error.status)
  useEffect(() => {
    if (denied) {
      setDirty(false)
      setPending(null)
      setSelected(null)
      setNotice('')
    }
  }, [denied])
  useEffect(() => {
    onDirtyChange(dirty)
    return () => onDirtyChange(false)
  }, [dirty, onDirtyChange])
  const request = (action: () => void) => {
    if (busy) return
    if (dirty) setPending(() => action)
    else action()
  }
  return (
    <>
      <DialogSurface
        open
        title="Webhook credential profiles"
        description="Personal reusable authentication and signing credentials. Saved secret values are never returned to the browser."
        onClose={() => request(onClose)}
        dismissDisabled={busy}
        ariaBusy={busy}
        panelClassName="max-w-3xl"
        describeBody={false}
      >
        {query.isPending && <p role="status">Loading credential profiles…</p>}
        {query.isError && (
          <p role="alert">
            {resolveApiErrorMessage(
              query.error,
              'Credential profiles could not be loaded.',
            )}{' '}
            <button className={BUTTON} onClick={() => void query.refetch()}>
              Retry profiles
            </button>
          </p>
        )}
        {!denied && (
          <>
            <label className="block text-sm">
              Saved profile
              <select
                className={INPUT}
                value={selected?.id ?? ''}
                disabled={busy}
                onChange={(event) => {
                  const next =
                    profiles.find(
                      (profile) => profile.id === event.target.value,
                    ) ?? null
                  if (next?.id !== selected?.id)
                    request(() => {
                      setDirty(false)
                      setNotice('')
                      setSelected(next)
                    })
                }}
              >
                <option value="">Create a new profile</option>
                {selected &&
                  !profiles.some((profile) => profile.id === selected.id) && (
                    <option value={selected.id}>
                      {selected.name} (not in current list)
                    </option>
                  )}
                {profiles.map((profile) => (
                  <option key={profile.id} value={profile.id}>
                    {profile.name}
                    {profile.enabled ? '' : ' (disabled)'}
                  </option>
                ))}
              </select>
            </label>
            {notice && <p role="status">{notice}</p>}
            <CredentialEditor
              key={selected?.id ?? 'new'}
              profile={selected}
              current={profiles.find((profile) => profile.id === selected?.id)}
              writable={writable && !query.isError}
              onDirtyChange={setDirty}
              onBusyChange={setBusy}
              onSaved={(profile) => {
                setSelected(profile)
                setNotice(
                  'Credential profile saved. Secrets have been cleared from this form.',
                )
              }}
            />
          </>
        )}
        <button
          className={`${BUTTON} mt-3`}
          disabled={busy}
          onClick={() => request(onClose)}
        >
          Close profiles
        </button>
      </DialogSurface>
      <ConfirmDialog
        open={pending !== null}
        title="Discard credential changes?"
        description="Unsaved credential changes will be lost. Existing saved profiles are unchanged."
        confirmLabel="Discard changes"
        onCancel={() => setPending(null)}
        onConfirm={() => {
          const action = pending
          setPending(null)
          setDirty(false)
          action?.()
        }}
      />
    </>
  )
}
