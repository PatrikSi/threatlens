import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { apiFetch } from '../api/client'
import { accessibleQueryData } from '../api/queryData'
import { resolveApiErrorMessage } from '../api/errors'
import type {
  WebhookCredentialProfile,
  WebhookMatchPreview,
  WebhookPreviewEvent,
} from '../types/webhookAutomation'
import type { NotificationWebhooksController } from './useNotificationWebhooksController'
import { createRequestFromDraft } from './notificationWebhookDraft'
import { WebhookConditionBuilder } from './WebhookConditionBuilder'
import { validateConditions } from './webhookConditionModel'

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
      <WebhookConditionBuilder
        value={draft.conditions ?? null}
        disabled={disabled}
        onChange={(conditions) =>
          setDraft((current) => ({ ...current, conditions }))
        }
      />
      {open && (
        <WebhookEventPreview key={draft.event_type} controller={controller} />
      )}
    </details>
  )
}

function WebhookEventPreview({
  controller,
}: {
  controller: NotificationWebhooksController
}) {
  const { draft, canManageWebhooks } = controller
  const [eventId, setEventId] = useState('')
  const payload = createRequestFromDraft(draft)
  const fingerprint = JSON.stringify(payload)
  const eventsQuery = useQuery({
    queryKey: ['notifications', 'preview-events', draft.event_type],
    queryFn: ({ signal }) =>
      apiFetch<{ events: WebhookPreviewEvent[] }>(
        `/notifications/webhooks/events?event_type=${encodeURIComponent(draft.event_type)}&limit=20`,
        { signal },
      ),
    retry: false,
  })
  const events = accessibleQueryData(eventsQuery)?.events ?? []
  const preview = useMutation({
    mutationFn: (submission: { fingerprint: string; eventId: string }) =>
      apiFetch<WebhookMatchPreview>('/notifications/webhooks/preview', {
        method: 'POST',
        body: JSON.stringify({
          webhook: JSON.parse(submission.fingerprint),
          event_id: submission.eventId,
        }),
      }),
  })
  const current =
    preview.variables?.fingerprint === fingerprint &&
    preview.variables.eventId === eventId
  const result = current && !eventsQuery.isError ? preview.data : undefined
  return (
    <section
      aria-label="Webhook matching preview"
      className="space-y-2 border-t border-slate/20 pt-3"
    >
      <h3 className="font-semibold">Preview matching</h3>
      <p className="text-xs">
        Evaluate a recent event you can access. This preview sends nothing to
        the destination.
      </p>
      <label className="block text-sm">
        Sample event
        <select
          className={INPUT}
          value={eventId}
          onChange={(event) => {
            setEventId(event.target.value)
            preview.reset()
          }}
        >
          <option value="">Select a recent event</option>
          {eventId && !events.some((event) => event.id === eventId) && (
            <option value={eventId}>Previously selected event</option>
          )}
          {events.map((event) => (
            <option key={event.id} value={event.id}>
              {event.label} · {new Date(event.created_at).toLocaleString()}
            </option>
          ))}
        </select>
      </label>
      {eventsQuery.isPending && <p role="status">Loading recent events…</p>}
      {eventsQuery.isError && (
        <p role="alert">
          {resolveApiErrorMessage(
            eventsQuery.error,
            'Recent events could not be loaded.',
          )}{' '}
          <button className={BUTTON} onClick={() => void eventsQuery.refetch()}>
            Retry events
          </button>
        </p>
      )}
      {eventsQuery.isSuccess && !events.length && (
        <p className="text-sm">
          No accessible events of this type were found in the recent sample.
          Process an article or review a hunt, then refresh the sample.
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          className={BUTTON}
          disabled={
            !canManageWebhooks ||
            controller.currentUserQuery.isError ||
            !eventId ||
            preview.isPending ||
            eventsQuery.isError ||
            Boolean(validateConditions(draft.conditions ?? null))
          }
          onClick={() => preview.mutate({ fingerprint, eventId })}
        >
          {preview.isPending ? 'Evaluating…' : 'Evaluate event'}
        </button>
        <button
          type="button"
          className={BUTTON}
          disabled={eventsQuery.isFetching}
          onClick={() => {
            preview.reset()
            void eventsQuery.refetch()
          }}
        >
          Refresh sample
        </button>
      </div>
      {preview.isError && current && (
        <p role="alert">
          {resolveApiErrorMessage(
            preview.error,
            'The event could not be evaluated.',
          )}
        </p>
      )}
      {preview.data && !current && (
        <p role="status">
          The draft changed. Evaluate again to see current matching results.
        </p>
      )}
      {result && (
        <div role="status" className="space-y-2 text-sm">
          <p className="font-semibold">
            {result.matches
              ? 'This event matches.'
              : 'This event does not match.'}
          </p>
          <ul className="list-disc pl-5">
            {result.checks.map((check, index) => (
              <li key={index}>
                {check.field}: {check.matched ? 'match' : 'no match'} —{' '}
                {check.reason}
              </li>
            ))}
          </ul>
          {result.missing_fields.length > 0 && (
            <p>Unavailable evidence: {result.missing_fields.join(', ')}.</p>
          )}
          {result.automation_payload && (
            <details>
              <summary className="cursor-pointer">Structured payload</summary>
              <pre
                tabIndex={0}
                aria-label="Automation payload preview"
                className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap break-words rounded border p-2 text-xs"
              >
                {JSON.stringify(result.automation_payload, null, 2)}
              </pre>
            </details>
          )}
        </div>
      )}
    </section>
  )
}
