import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { apiFetch } from '../api/client'
import { accessibleQueryData } from '../api/queryData'
import { resolveApiErrorMessage } from '../api/errors'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import type {
  Feed,
  NotificationWebhook,
  NotificationWebhookWriteRequest,
} from '../types/api'
import {
  createDraftFromWebhook,
  createRequestFromDraft,
  EVENT_OPTIONS,
  applyEventType,
  applyBodyMode,
} from './notificationWebhookDraft'
import { KeyValueEditor } from './NotificationWebhookShared'
import { WebhookArticleTextOption } from './WebhookArticleTextOption'
import { WebhookPayloadFieldPicker } from './WebhookPayloadFieldPicker'
import { WebhookConditionBuilder } from './WebhookConditionBuilder'
import { validateConditions } from './webhookConditionModel'
import { TEAM_BUTTON } from './teamPresentation'
const INPUT =
  'w-full rounded border border-slate/30 bg-white p-2 dark:bg-[#072019]'

export function TeamIntegrationConfiguration({
  base,
  unavailable,
  onChanged,
}: {
  base: string
  unavailable: boolean
  onChanged: () => void
}) {
  const query = useQuery({
    queryKey: ['teams', 'integration-configuration', base],
    queryFn: ({ signal }) =>
      apiFetch<NotificationWebhook>(`${base}/configuration`, { signal }),
    enabled: !unavailable,
  })
  const data = accessibleQueryData(query)
  return (
    <section className="space-y-2" aria-label="Team destination configuration">
      {query.isError && (
        <p role="alert">
          {resolveApiErrorMessage(
            query.error,
            'Configuration could not be loaded.',
          )}{' '}
          <button className={TEAM_BUTTON} onClick={() => void query.refetch()}>
            Reload configuration
          </button>
        </p>
      )}
      {data?.secrets_redacted ? (
        <p>
          Adopt this destination to edit its configuration under your source
          access.
        </p>
      ) : (
        data && (
          <ConfigurationEditor
            key={base}
            base={base}
            saved={data}
            disabled={unavailable || query.isError}
            onChanged={() => {
              onChanged()
              void query.refetch()
            }}
            onReload={async () => {
              const latest = await query.refetch({ throwOnError: true })
              if (!latest.data || latest.data.secrets_redacted) throw new Error('The destination is no longer editable under your current access.')
              return latest.data
            }}
          />
        )
      )}
    </section>
  )
}

function ConfigurationEditor({
  base,
  saved,
  disabled,
  onChanged,
  onReload,
}: {
  base: string
  saved: NotificationWebhook
  disabled: boolean
  onChanged: () => void
  onReload: () => Promise<NotificationWebhook>
}) {
  const [baseline, setBaseline] = useState(saved)
  const [draft, setDraft] = useState(() => createDraftFromWebhook(saved))
  const dirty =
    JSON.stringify(draft) !== JSON.stringify(createDraftFromWebhook(baseline))
  const discard = useUnsavedChangesWarning(
    dirty,
    'Discard unsaved team destination changes?',
  )
  const feeds = useQuery({
    queryKey: ['feeds'],
    queryFn: ({ signal }) => apiFetch<Feed[]>('/feeds', { signal }),
  })
  const save = useMutation({
    mutationFn: () =>
      apiFetch<NotificationWebhook>(
        `${base}/configuration?expected_revision=${baseline.ownership_revision ?? 1}`,
        { method: 'PUT', body: JSON.stringify(createRequestFromDraft(draft)) },
      ),
    onSuccess: (result) => {
      setBaseline(result)
      setDraft(createDraftFromWebhook(result))
      onChanged()
    },
  })
  const reload = useMutation({
    mutationFn: onReload,
    onSuccess: (latest) => {
      setBaseline(latest)
      setDraft(createDraftFromWebhook(latest))
      save.reset()
    },
  })
  const busy = disabled || save.isPending || reload.isPending
  const validation = validateConditions(draft.conditions ?? null)
  const change = <K extends keyof typeof draft>(
    key: K,
    value: (typeof draft)[K],
  ) => setDraft((current) => ({ ...current, [key]: value }))
  return (
    <div className="space-y-3">
      {discard.discardDialog}
      <fieldset disabled={busy} className="grid gap-2 md:grid-cols-2">
        <label>
          Destination name
          <input
            className={INPUT}
            value={draft.name}
            onChange={(event) => change('name', event.target.value)}
          />
        </label>
        <label>
          Destination URL
          <input
            className={INPUT}
            value={draft.url_template}
            onChange={(event) => change('url_template', event.target.value)}
          />
        </label>
        <label>
          Event type
          <select
            className={INPUT}
            value={draft.event_type}
            onChange={(event) =>
              setDraft((current) =>
                applyEventType(
                  current,
                  event.target
                    .value as NotificationWebhookWriteRequest['event_type'],
                ),
              )
            }
          >
            {EVENT_OPTIONS.map((entry) => (
              <option key={entry.value} value={entry.value}>
                {entry.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          HTTP method
          <select
            className={INPUT}
            value={draft.payload_mode === 'automation_v1' ? 'POST' : draft.method}
            disabled={draft.payload_mode === 'automation_v1'}
            onChange={(event) =>
              change(
                'method',
                event.target.value as NotificationWebhookWriteRequest['method'],
              )
            }
          >
            {['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map((value) => (
              <option key={value}>{value}</option>
            ))}
          </select>
        </label>
        <label>
          Payload format
          <select
            className={INPUT}
            value={draft.payload_mode ?? 'template'}
            onChange={(event) =>
              setDraft((current) => ({ ...current, payload_mode: event.target.value as 'template' | 'automation_v1', method: event.target.value === 'automation_v1' ? 'POST' : current.method }))
            }
          >
            <option value="template">Notification template</option>
            <option value="automation_v1">Structured automation</option>
          </select>
        </label>
        <label>
          Request timeout (seconds)
          <input
            className={INPUT}
            type="number"
            min={1}
            max={60}
            value={draft.timeout_seconds}
            onChange={(event) =>
              change('timeout_seconds', Number(event.target.value))
            }
          />
        </label>
        <label>
          Feed scope
          <select
            className={INPUT}
            value={draft.feed_scope}
            onChange={(event) =>
              change('feed_scope', event.target.value as 'all' | 'selected')
            }
          >
            <option value="all">All accessible feeds</option>
            <option value="selected">Selected feeds</option>
          </select>
        </label>
        <label>
          <input
            type="checkbox"
            checked={draft.enabled}
            onChange={(event) => change('enabled', event.target.checked)}
          />{' '}
          Enable destination
        </label>
        {draft.feed_scope === 'selected' && (
          <fieldset>
            <legend>Selected feeds</legend>
            {(accessibleQueryData(feeds) ?? []).map((feed) => (
              <label key={feed.id} className="block">
                <input
                  type="checkbox"
                  checked={draft.feed_ids.includes(feed.id)}
                  onChange={(event) =>
                    change(
                      'feed_ids',
                      event.target.checked
                        ? [...draft.feed_ids, feed.id]
                        : draft.feed_ids.filter((id) => id !== feed.id),
                    )
                  }
                />{' '}
                {feed.name}
              </label>
            ))}
          </fieldset>
        )}
      </fieldset>
      {feeds.isError && (
        <p role="alert">
          {resolveApiErrorMessage(
            feeds.error,
            'Feed choices could not be loaded.',
          )}
        </p>
      )}
      {draft.payload_mode === 'automation_v1' && <WebhookArticleTextOption checked={draft.include_article_text ?? false} disabled={busy} onChange={(value) => change('include_article_text', value)} />}
      <WebhookConditionBuilder
        value={draft.conditions ?? null}
        onChange={(value) => change('conditions', value)}
        disabled={busy}
      />
      {(['headers', 'query_params'] as const).map((key) => (
        <KeyValueEditor
          key={key}
          title={key === 'headers' ? 'Request headers' : 'Query parameters'}
          description="Values support existing notification template variables."
          fields={draft[key]}
          addLabel="Add entry"
          keyPlaceholder="Name"
          valuePlaceholder="Value"
          disabled={busy}
          onChange={(value) => change(key, value)}
        />
      ))}
      {draft.payload_mode !== 'automation_v1' && (
        <>
          <WebhookPayloadFieldPicker draft={draft} onChange={setDraft} disabled={busy} />
          <label>
            Body format
            <select
              className={INPUT}
              disabled={busy}
              value={draft.body_mode}
              onChange={(event) =>
                setDraft((current) =>
                  applyBodyMode(
                    current,
                    event.target
                      .value as NotificationWebhookWriteRequest['body_mode'],
                  ),
                )
              }
            >
              {['none', 'json', 'form', 'raw'].map((value) => (
                <option key={value}>{value}</option>
              ))}
            </select>
          </label>
          {draft.body_mode === 'raw' ? (
            <label>
              Body template
              <textarea
                className={INPUT}
                disabled={busy}
                value={draft.body_template}
                onChange={(event) =>
                  change('body_template', event.target.value)
                }
              />
            </label>
          ) : (
            draft.body_mode !== 'none' && (
              <KeyValueEditor
                title="Body fields"
                description="Existing template variables are supported."
                fields={draft.body_fields}
                addLabel="Add body field"
                keyPlaceholder="Name"
                valuePlaceholder="Value"
                disabled={busy}
                onChange={(value) => change('body_fields', value)}
              />
            )
          )}
        </>
      )}
      <p className="text-sm">
        The destination retains its cloned signing and authentication profile.
        Rotate that profile in notification credential settings while acting as
        the current custodian.
      </p>
      <div className="flex gap-2">
        <button
          className={TEAM_BUTTON}
          disabled={busy || Boolean(validation) || !dirty || feeds.isError}
          onClick={() => save.mutate()}
        >
          {save.isPending ? 'Saving…' : 'Save team destination'}
        </button>
        <button
          className={TEAM_BUTTON}
          disabled={busy}
          onClick={() =>
            discard(() => reload.mutate())
          }
        >
          {reload.isPending ? 'Reloading destination…' : 'Reload saved destination'}
        </button>
      </div>
      {validation && <p role="alert">{validation}</p>}
      {reload.isError && <p role="alert">{resolveApiErrorMessage(reload.error, 'The current destination could not be loaded. Your draft is preserved; retry reload.')}</p>}
      {save.isError && (
        <p role="alert">
          {resolveApiErrorMessage(
            save.error,
            'The destination could not be saved; reload after reviewing your draft.',
          )}
        </p>
      )}
    </div>
  )
}
