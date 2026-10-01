import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { apiFetch } from '../api/client'
import { accessibleQueryData } from '../api/queryData'
import { resolveApiErrorMessage } from '../api/errors'
import type { WebhookMatchPreview, WebhookPreviewEvent } from '../types/webhookAutomation'
import { createRequestFromDraft, type NotificationWebhookDraft } from './notificationWebhookDraft'
import { validateConditions } from './webhookConditionModel'

const INPUT =
  'mt-1 w-full rounded border border-slate/30 bg-white p-2 text-sm dark:bg-[#072019]'
const BUTTON =
  'rounded border border-slate/30 px-3 py-1.5 text-sm disabled:opacity-50'

export function WebhookEventPreview({
  draft,
  disabled,
}: {
  draft: NotificationWebhookDraft
  disabled: boolean
}) {
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
    enabled: !disabled,
  })
  const events = disabled ? [] : accessibleQueryData(eventsQuery)?.events ?? []
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
  const result = !disabled && current && !eventsQuery.isError ? preview.data : undefined
  return (
    <section
      aria-label="Webhook matching preview"
      className="space-y-2 border-t border-slate/20 pt-3"
    >
      <h3 className="font-semibold">Preview matching</h3>
      <p className="text-xs">
        Evaluate this draft’s conditions and payload against an event you can
        access. Nothing is sent to the destination. This does not verify
        destination, team, or credential delivery policies.
      </p>
      <label className="block text-sm">
        Sample event
        <select
          className={INPUT}
          value={eventId}
          disabled={disabled || eventsQuery.isError}
          onChange={(event) => {
            setEventId(event.target.value)
            preview.reset()
          }}
        >
          <option value="">Select a recent event</option>
          {!disabled && eventId && !events.some((event) => event.id === eventId) && (
            <option value={eventId}>Previously selected event</option>
          )}
          {events.map((event) => (
            <option key={event.id} value={event.id}>
              {event.label} · {new Date(event.created_at).toLocaleString()}
            </option>
          ))}
        </select>
      </label>
      {!disabled && eventsQuery.isPending && <p role="status">Loading recent events…</p>}
      {!disabled && eventsQuery.isError && (
        <p role="alert">
          {resolveApiErrorMessage(
            eventsQuery.error,
            'Recent events could not be loaded.',
          )}{' '}
          <button type="button" className={BUTTON} disabled={disabled || eventsQuery.isFetching} onClick={() => void eventsQuery.refetch()}>
            Retry events
          </button>
        </p>
      )}
      {!disabled && eventsQuery.isSuccess && !events.length && (
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
            disabled ||
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
          disabled={disabled || eventsQuery.isFetching}
          onClick={() => {
            preview.reset()
            void eventsQuery.refetch()
          }}
        >
          Refresh sample
        </button>
      </div>
      {!disabled && preview.isError && current && (
        <p role="alert">
          {resolveApiErrorMessage(
            preview.error,
            'The event could not be evaluated.',
          )}
        </p>
      )}
      {!disabled && preview.data && !current && (
        <p role="status">
          The draft changed. Evaluate again to see current matching results.
        </p>
      )}
      {result && <WebhookPreviewResult result={result} />}
    </section>
  )
}

function WebhookPreviewResult({ result }: { result: WebhookMatchPreview }) {
  return (
    <div role="status" className="space-y-2 text-sm">
      <p className="font-semibold">
        {result.matches ? 'This event matches.' : 'This event does not match.'}
      </p>
      <ul className="list-disc pl-5">
        {result.checks.map((check, index) => (
          <li key={index}>
            {check.indicator_value
              ? `${check.indicator_type ?? 'Indicator'} ${check.indicator_value}`
              : check.field}: {check.matched ? 'match' : 'no match'} —{' '}
            {check.reason}
          </li>
        ))}
      </ul>
      {result.missing_fields.length > 0 && (
        <p>Unavailable evidence: {result.missing_fields.join(', ')}.</p>
      )}
      {result.template_body_error && (
        <p role="alert">Payload could not be rendered: {result.template_body_error}</p>
      )}
      {result.template_body != null && (
        <details>
          <summary className="cursor-pointer">Rendered template payload</summary>
          <pre
            tabIndex={0}
            aria-label="Template payload preview"
            className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap break-words rounded border p-2 text-xs"
          >
            {result.template_body}
          </pre>
        </details>
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
  )
}
