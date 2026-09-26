import { useEffect, useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '../api/client'
import { resolveApiErrorMessage } from '../api/errors'
import type { WebhookCredentialProfile } from '../types/webhookAutomation'

const INPUT =
  'mt-1 w-full rounded border border-slate/30 bg-white p-2 text-sm dark:bg-[#072019]'
const BUTTON =
  'rounded border border-slate/30 px-3 py-1.5 text-sm disabled:opacity-50'

function initialDraft(profile: WebhookCredentialProfile | null) {
  return {
    name: profile?.name ?? '',
    enabled: profile?.enabled ?? true,
    auth_type: profile?.auth_type ?? 'none',
    header_name: profile?.header_name ?? '',
    auth_secret: '',
    signing_secret: '',
    clear_auth_secret: false,
    clear_signing_secret: false,
  }
}

export function CredentialEditor({
  profile,
  current,
  writable,
  onDirtyChange,
  onBusyChange,
  onSaved,
}: {
  profile: WebhookCredentialProfile | null
  current?: WebhookCredentialProfile
  writable: boolean
  onDirtyChange: (dirty: boolean) => void
  onBusyChange: (busy: boolean) => void
  onSaved: (profile: WebhookCredentialProfile) => void
}) {
  const client = useQueryClient()
  const [baseline, setBaseline] = useState(profile)
  const [draft, setDraft] = useState(() => initialDraft(profile))
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])
  const dirty = JSON.stringify(draft) !== JSON.stringify(initialDraft(baseline))
  const save = useMutation({
    mutationFn: () =>
      apiFetch<WebhookCredentialProfile>(
        `/notifications/credential-profiles${baseline ? `/${baseline.id}` : ''}`,
        {
          method: baseline ? 'PATCH' : 'POST',
          body: JSON.stringify({
            ...draft,
            name: draft.name.trim(),
            header_name:
              draft.auth_type === 'header' ? draft.header_name.trim() : null,
            auth_secret: draft.auth_secret || undefined,
            signing_secret: draft.signing_secret || undefined,
            expected_revision: baseline?.revision,
          }),
        },
      ),
    onSuccess: (saved) => {
      if (!mounted.current) return
      setBaseline(saved)
      setDraft(initialDraft(saved))
      onDirtyChange(false)
      onSaved(saved)
      client.setQueryData<WebhookCredentialProfile[]>(
        ['notifications', 'credential-profiles'],
        (entries) => [
          ...(entries ?? []).filter((entry) => entry.id !== saved.id),
          saved,
        ],
      )
      void client.invalidateQueries({
        queryKey: ['notifications', 'credential-profiles'],
      })
    },
  })
  useEffect(() => {
    onDirtyChange(dirty)
  }, [dirty, onDirtyChange])
  useEffect(() => {
    onBusyChange(save.isPending)
    return () => onBusyChange(false)
  }, [save.isPending, onBusyChange])
  const changedElsewhere = Boolean(
    current && baseline && current.revision !== baseline.revision,
  )
  const invalid =
    !draft.name.trim() ||
    Boolean(draft.signing_secret && draft.signing_secret.length < 32) ||
    (draft.clear_signing_secret && Boolean(draft.signing_secret)) ||
    (draft.clear_auth_secret && Boolean(draft.auth_secret))
  return (
    <div className="mt-3 space-y-3">
      {baseline && (
        <p className="text-xs">
          Revision {baseline.revision} · Authentication{' '}
          {baseline.auth_configured ? 'configured' : 'not configured'} · Signing{' '}
          {baseline.signing_configured ? 'configured' : 'not configured'}
        </p>
      )}
      {changedElsewhere && (
        <p role="status">
          This profile changed elsewhere. Your draft still uses revision{' '}
          {baseline?.revision}. Close and reopen it after preserving or
          discarding your changes.
        </p>
      )}
      {save.isError && (
        <p role="alert">
          {resolveApiErrorMessage(
            save.error,
            'Credential profile could not be saved. Your input is preserved.',
          )}
        </p>
      )}
      <fieldset disabled={!writable || save.isPending} className="space-y-3">
        <label className="block text-sm">
          Profile name
          <input
            className={INPUT}
            maxLength={255}
            value={draft.name}
            onChange={(event) =>
              setDraft({ ...draft, name: event.target.value })
            }
          />
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={draft.enabled}
            onChange={(event) =>
              setDraft({ ...draft, enabled: event.target.checked })
            }
          />
          Profile enabled
        </label>
        {!draft.enabled && (
          <p className="text-xs">
            Webhooks using a disabled profile cannot send with its credentials.
          </p>
        )}
        <label className="block text-sm">
          Authentication
          <select
            className={INPUT}
            value={draft.auth_type}
            onChange={(event) =>
              setDraft({
                ...draft,
                auth_type: event.target.value as typeof draft.auth_type,
              })
            }
          >
            <option value="none">None</option>
            <option value="bearer">Bearer token</option>
            <option value="header">API key header</option>
          </select>
        </label>
        {draft.auth_type === 'header' && (
          <label className="block text-sm">
            Authentication header name
            <input
              className={INPUT}
              value={draft.header_name}
              placeholder="X-API-Key"
              maxLength={128}
              onChange={(event) =>
                setDraft({ ...draft, header_name: event.target.value })
              }
            />
          </label>
        )}
        {draft.auth_type !== 'none' && (
          <label className="block text-sm">
            {baseline?.auth_configured
              ? 'Replacement authentication secret'
              : 'Authentication secret'}
            <input
              className={INPUT}
              type="password"
              autoComplete="new-password"
              maxLength={8000}
              value={draft.auth_secret}
              onChange={(event) =>
                setDraft({ ...draft, auth_secret: event.target.value })
              }
            />
            <span className="text-xs">
              Leave blank to retain a saved secret.
            </span>
          </label>
        )}
        {baseline?.auth_configured && (
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={draft.clear_auth_secret}
              onChange={(event) =>
                setDraft({ ...draft, clear_auth_secret: event.target.checked })
              }
            />
            Clear stored authentication secret
          </label>
        )}
        <label className="block text-sm">
          {baseline?.signing_configured
            ? 'Replacement signing secret'
            : 'Signing secret'}
          <input
            className={INPUT}
            type="password"
            autoComplete="new-password"
            minLength={32}
            maxLength={4096}
            value={draft.signing_secret}
            onChange={(event) =>
              setDraft({ ...draft, signing_secret: event.target.value })
            }
          />
          <span className="text-xs">
            At least 32 characters. Configure the same secret at the receiver.
            Leave blank to retain the saved signing key.
          </span>
        </label>
        {baseline?.signing_configured && (
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={draft.clear_signing_secret}
              onChange={(event) =>
                setDraft({
                  ...draft,
                  clear_signing_secret: event.target.checked,
                })
              }
            />
            Remove request signing
          </label>
        )}
        <button
          type="button"
          className={BUTTON}
          disabled={!dirty || invalid || changedElsewhere}
          onClick={() => save.mutate()}
        >
          {save.isPending ? 'Saving profile…' : 'Save credential profile'}
        </button>
      </fieldset>
    </div>
  )
}
