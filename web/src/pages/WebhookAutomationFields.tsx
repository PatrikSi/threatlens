import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../api/client'
import { accessibleQueryData } from '../api/queryData'
import { resolveApiErrorMessage } from '../api/errors'
import type { WebhookCredentialProfile } from '../types/webhookAutomation'
import type { NotificationWebhooksController } from './useNotificationWebhooksController'
import { WebhookArticleTextOption } from './WebhookArticleTextOption'
import { WebhookConditionBuilder } from './WebhookConditionBuilder'
import { WebhookEventPreview } from './WebhookEventPreview'

const INPUT =
  'mt-1 w-full rounded border border-slate/30 bg-white p-2 text-sm dark:bg-[#072019]'
const BUTTON =
  'rounded border border-slate/30 px-3 py-1.5 text-sm disabled:opacity-50'

export function WebhookAutomationFields({
  controller,
}: {
  controller: NotificationWebhooksController
}) {
  const { draft, setDraft, canManageWebhooks, saveWebhook } = controller
  const [open, setOpen] = useState(false)
  const profilesQuery = useQuery({
    queryKey: ['notifications', 'credential-profiles'],
    queryFn: ({ signal }) =>
      apiFetch<WebhookCredentialProfile[]>(
        '/notifications/credential-profiles',
        { signal },
      ),
    enabled: open,
  })
  const profiles = accessibleQueryData(profilesQuery) ?? []
  const disabled =
    !canManageWebhooks ||
    controller.currentUserQuery.isError ||
    saveWebhook.isPending
  return (
    <details
      className="mt-4 space-y-3 rounded border border-slate/25 p-3"
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary className="cursor-pointer font-semibold">
        Automation and event conditions
      </summary>
      <p className="text-xs">
        Choose typed intelligence for machine consumers, then preview the exact
        event selection before enabling delivery.
      </p>
      <fieldset disabled={disabled} className="grid gap-3 md:grid-cols-2">
        <label className="text-sm">
          Payload format
          <select
            className={INPUT}
            value={draft.payload_mode ?? 'template'}
            onChange={(event) =>
              setDraft((current) => ({
                ...current,
                method: event.target.value === 'automation_v1' ? 'POST' : current.method,
                payload_mode: event.target.value as
                  | 'template'
                  | 'automation_v1',
              }))
            }
          >
            <option value="template">Existing notification template</option>
            <option value="automation_v1">Structured automation JSON v1</option>
          </select>
        </label>
        <label className="text-sm">
          Credential profile
          <select
            className={INPUT}
            value={draft.credential_profile_id ?? ''}
            onChange={(event) =>
              setDraft((current) => ({
                ...current,
                credential_profile_id: event.target.value || null,
              }))
            }
          >
            <option value="">Use configured request headers</option>
            {draft.credential_profile_id &&
              !profiles.some(
                (profile) => profile.id === draft.credential_profile_id,
              ) && (
                <option value={draft.credential_profile_id}>
                  Selected profile (unavailable)
                </option>
              )}
            {profiles.map((profile) => (
              <option key={profile.id} value={profile.id}>
                {profile.name}
                {profile.enabled ? '' : ' (disabled)'}
                {profile.signing_configured ? ' · signed' : ''}
              </option>
            ))}
          </select>
        </label>
      </fieldset>
      {profilesQuery.isError && (
        <p role="alert" className="text-sm">
          {resolveApiErrorMessage(
            profilesQuery.error,
            'Credential profiles could not be loaded.',
          )}{' '}
          <button
            type="button"
            className={BUTTON}
            onClick={() => void profilesQuery.refetch()}
          >
            Retry profiles
          </button>
        </p>
      )}
      {draft.payload_mode === 'automation_v1' && (
        <p className="text-sm">
          The event's typed arrays, evidence and revisions form the JSON body.
          Template body fields are retained for switching back. A successful
          delivery confirms HTTP acceptance, not hunt completion.
        </p>
      )}
      {draft.payload_mode === 'automation_v1' && <WebhookArticleTextOption
        checked={draft.include_article_text ?? false}
        disabled={disabled}
        onChange={(include_article_text) => setDraft((current) => ({ ...current, include_article_text }))}
      />}
      <WebhookConditionBuilder
        eventType={draft.event_type}
        value={draft.conditions ?? null}
        disabled={disabled}
        onChange={(conditions) =>
          setDraft((current) => ({ ...current, conditions }))
        }
      />
      {open && (
        <WebhookEventPreview key={draft.event_type} draft={draft} disabled={disabled} />
      )}
    </details>
  )
}
