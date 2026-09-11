import { useEffect, useRef, useState, type RefObject } from 'react'
import { resolveApiErrorMessage } from '../api/errors'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { ROUTING_FIELDS, type ProviderDraft } from './aiProviderDraft'
import { Field, FieldError, Panel } from './aiSettingsSupport'
import type { AiProviderConnectionsController } from './useAiProviderConnections'

const inputClass =
  'mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]'
const buttonClass =
  'rounded border border-slate/30 px-3 py-2 text-sm font-semibold disabled:opacity-50 dark:border-cyan-900/40'
const FIELDS: {
  key: keyof Pick<
    ProviderDraft,
    | 'name'
    | 'base_url'
    | 'model'
    | 'temperature'
    | 'max_completion_tokens'
    | 'request_timeout_seconds'
    | 'request_max_retries'
  >
  label: string
  numeric?: boolean
}[] = [
  { key: 'name', label: 'Provider name' },
  { key: 'base_url', label: 'Provider base URL' },
  { key: 'model', label: 'Provider model' },
  { key: 'temperature', label: 'Provider temperature', numeric: true },
  { key: 'max_completion_tokens', label: 'Provider maximum completion tokens', numeric: true },
  { key: 'request_timeout_seconds', label: 'Provider request timeout (seconds)', numeric: true },
  { key: 'request_max_retries', label: 'Provider maximum retries', numeric: true },
]

export function AiProviderConnections({ controller: c }: { controller: AiProviderConnectionsController }) {
  const [confirmRoutingReload, setConfirmRoutingReload] = useState(false)
  const editorTitle = useRef<HTMLHeadingElement>(null)
  const addProviderButton = useRef<HTMLButtonElement>(null)
  const focusedDelete = useRef(c.completedDeletes)
  const focusEditor = () => requestAnimationFrame(() => editorTitle.current?.focus())

  useEffect(() => {
    if (c.busy || focusedDelete.current === c.completedDeletes) return
    const frame = requestAnimationFrame(() => {
      addProviderButton.current?.focus()
      focusedDelete.current = c.completedDeletes
    })
    return () => cancelAnimationFrame(frame)
  }, [c.busy, c.completedDeletes])

  return (
    <Panel
      title="Provider connections"
      subtitle="Add named OpenAI-compatible chat endpoints with separate credentials, then assign them to AI features. Existing settings remain available as the legacy provider."
    >
      {c.notice && (
        <p
          role={c.notice.error ? 'alert' : 'status'}
          className={`mb-3 rounded border p-3 text-sm ${c.notice.error ? 'border-red-500/30 text-red-700 dark:text-red-300' : 'border-slate/20'}`}
        >
          {c.notice.message}
        </p>
      )}
      {c.busy && (
        <p role="status" className="mb-3 text-sm">
          Provider operation in progress. Editing resumes when it completes.
        </p>
      )}
      {c.providers.isError && (
        <div role="alert" className="mb-3 space-y-2 text-sm text-red-700 dark:text-red-300">
          <p>
            {resolveApiErrorMessage(
              c.providers.error,
              'Provider connections could not be loaded. Legacy settings remain available below.',
            )}
          </p>
          <button type="button" className={buttonClass} onClick={() => void c.providers.refetch()}>
            Retry loading providers
          </button>
        </div>
      )}
      <fieldset disabled={c.busy} className="space-y-4">
        <ProviderList c={c} focusEditor={focusEditor} addProviderButton={addProviderButton} />
        <ProviderEditor c={c} editorTitle={editorTitle} />
        <ProviderRouting c={c} onReload={() => setConfirmRoutingReload(true)} />
      </fieldset>
      <ConfirmDialog
        open={c.pendingSelection !== null}
        title="Discard provider changes?"
        description="Your unsaved provider edits and any replacement key will be discarded."
        confirmLabel="Discard changes"
        onCancel={c.cancelSelection}
        onConfirm={() => {
          c.confirmSelection()
          focusEditor()
        }}
      />
      <ConfirmDialog
        open={c.deleteTarget !== null}
        title="Delete provider?"
        description={`Delete “${c.deleteTarget?.name ?? ''}” and its stored credential? Existing AI history is retained. Assigned providers must be reassigned first.`}
        confirmLabel="Delete provider"
        isConfirming={c.busy}
        onCancel={() => c.setDeleteTarget(null)}
        onConfirm={c.remove}
      >
        {c.notice?.error && <p role="alert">{c.notice.message}</p>}
      </ConfirmDialog>
      <ConfirmDialog
        open={confirmRoutingReload}
        title="Discard assignment changes?"
        description="Reload the saved feature assignments and discard your unsaved assignment changes."
        confirmLabel="Reload assignments"
        onCancel={() => setConfirmRoutingReload(false)}
        onConfirm={() => {
          c.reloadRouting()
          setConfirmRoutingReload(false)
        }}
      />
    </Panel>
  )
}

function ProviderList({
  c,
  focusEditor,
  addProviderButton,
}: {
  c: AiProviderConnectionsController
  focusEditor: () => void
  addProviderButton: RefObject<HTMLButtonElement | null>
}) {
  return (
    <>
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Search all providers" className="min-w-0 flex-1">
          <input
            className={inputClass}
            value={c.search}
            maxLength={200}
            onChange={(event) => c.setSearch(event.target.value)}
          />
        </Field>
        <button
          ref={addProviderButton}
          type="button"
          className={buttonClass}
          disabled={!c.providers.data || c.providers.isError}
          onClick={() => {
            c.select('new')
            focusEditor()
          }}
        >
          Add provider
        </button>
      </div>
      {c.providers.isLoading && (
        <p role="status" className="text-sm">
          Loading provider connections...
        </p>
      )}
      {c.providers.data && (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Saved AI provider connections</caption>
              <thead>
                <tr>
                  <th scope="col" className="p-2">
                    Provider
                  </th>
                  <th scope="col" className="p-2">
                    Model
                  </th>
                  <th scope="col" className="p-2">
                    Status
                  </th>
                  <th scope="col" className="p-2">
                    Action
                  </th>
                </tr>
              </thead>
              <tbody>
                {c.providers.data.items.map((provider) => (
                  <tr key={provider.id} className="border-t border-slate/15">
                    <td className="max-w-56 break-words p-2">
                      {provider.name}
                      {c.routing.data?.default_provider_id === provider.id && (
                        <span className="ml-2 text-xs font-semibold">Default</span>
                      )}
                    </td>
                    <td className="max-w-56 break-words p-2">{provider.model}</td>
                    <td className="p-2">
                      {!provider.enabled
                        ? 'Disabled'
                        : provider.credential_error
                          ? 'Credential unavailable'
                          : 'Enabled'}
                    </td>
                    <td className="p-2">
                      <button
                        type="button"
                        className={buttonClass}
                        aria-label={`Edit provider ${provider.name}`}
                        aria-pressed={c.editor?.baseline?.id === provider.id}
                        onClick={() => {
                          c.select(provider)
                          focusEditor()
                        }}
                      >
                        Edit
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {c.providers.data.items.length === 0 && (
            <p className="text-sm">
              {c.search
                ? 'No providers match this search.'
                : 'No named providers yet. Existing AI features continue using the legacy provider until assignments are saved.'}
            </p>
          )}
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <p role="status">
              {c.providers.data.total
                ? `${c.providers.data.offset + 1}–${c.providers.data.offset + c.providers.data.items.length} of ${c.providers.data.total} providers`
                : '0 providers'}
              {c.search ? ' matching this search' : ''}
            </p>
            <button
              type="button"
              className={buttonClass}
              disabled={c.page === 0 || c.providers.isFetching}
              onClick={() => c.setPage(c.page - 1)}
            >
              Previous providers
            </button>
            <button
              type="button"
              className={buttonClass}
              disabled={
                c.providers.data.offset + c.providers.data.items.length >= c.providers.data.total ||
                c.providers.isFetching
              }
              onClick={() => c.setPage(c.page + 1)}
            >
              Next providers
            </button>
          </div>
        </>
      )}
    </>
  )
}

function ProviderEditor({
  c,
  editorTitle,
}: {
  c: AiProviderConnectionsController
  editorTitle: RefObject<HTMLHeadingElement | null>
}) {
  const selected = c.editor?.baseline
  const isAssigned = selected && ROUTING_FIELDS.some(({ key }) => c.routing.data?.[key] === selected.id)
  const newestSelected = c.selectedProvider.data ?? c.providers.data?.items.find(({ id }) => id === selected?.id)
  const providerChanged = selected && newestSelected && newestSelected.version > selected.version
  return (
    <>
      {c.editor && (
        <section className="space-y-3 rounded border border-slate/20 p-3" aria-labelledby="ai-provider-editor-title">
          <h4 id="ai-provider-editor-title" ref={editorTitle} tabIndex={-1} className="font-semibold">
            {selected ? `Edit ${selected.name}` : 'New provider'}
          </h4>
          <p className="text-sm">
            The endpoint must support OpenAI-compatible chat completions. Save this connection before testing or
            assigning it.
          </p>
          {providerChanged && (
            <p role="alert" className="text-sm text-amber-800 dark:text-amber-200">
              This provider changed on the server. Your draft retains its original version. Reload saved provider before
              making a new edit.
            </p>
          )}
          {c.selectedProvider.isError && (
            <div role="alert" className="space-y-2 text-sm text-red-700 dark:text-red-300">
              <p>The saved provider could not be refreshed. Your draft has been kept.</p>
              <button
                type="button"
                className={buttonClass}
                disabled={c.selectedProvider.isFetching}
                onClick={() => void c.selectedProvider.refetch()}
              >
                Retry saved provider refresh
              </button>
            </div>
          )}
          {selected?.credential_error && (
            <p role="alert" className="text-sm text-red-700 dark:text-red-300">
              {selected.credential_error} Replace or remove the stored API key before using this provider.
            </p>
          )}
          <div className="grid gap-3 md:grid-cols-2">
            {FIELDS.map(({ key, label, numeric }) => (
              <Field key={key} label={label}>
                <input
                  className={inputClass}
                  value={c.editor!.draft[key]}
                  onChange={(event) => c.updateDraft(key, event.target.value)}
                  inputMode={numeric ? (key === 'temperature' ? 'decimal' : 'numeric') : undefined}
                  aria-label={label}
                  aria-invalid={Boolean(c.validation[key])}
                  aria-describedby={c.validation[key] ? `provider-error-${key}` : undefined}
                />
                {c.validation[key] && (
                  <span id={`provider-error-${key}`}>
                    <FieldError message={c.validation[key]} />
                  </span>
                )}
              </Field>
            ))}
            <ProviderKeyField c={c} />
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={c.editor.draft.enabled}
              onChange={(event) => c.updateDraft('enabled', event.target.checked)}
            />
            Provider enabled
          </label>
          {selected?.api_key_configured && (
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={c.editor.draft.clear_api_key}
                disabled={Boolean(c.editor.draft.api_key)}
                onChange={(event) => c.updateDraft('clear_api_key', event.target.checked)}
              />
              Remove stored API key when saving
            </label>
          )}
          <p className="text-xs">
            Changing the endpoint origin requires replacing or removing its stored key. Disabling a provider does not
            silently move its assigned work elsewhere.
          </p>
          {selected && (
            <p className="text-xs">
              Connection tests use a short request with saved settings. Draft changes are not included.
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className={buttonClass}
              disabled={
                Boolean(Object.keys(c.validation).length) || c.providers.isError || Boolean(selected && !c.editorDirty)
              }
              onClick={c.save}
            >
              Save provider
            </button>
            {selected && (
              <>
                <button
                  type="button"
                  className={buttonClass}
                  disabled={c.selectedProvider.isFetching || c.selectedProvider.isError}
                  onClick={() => c.select('reload')}
                >
                  Reload saved provider
                </button>
                <button
                  type="button"
                  className={buttonClass}
                  disabled={c.editorDirty || !selected.enabled || Boolean(selected.credential_error)}
                  onClick={c.test}
                >
                  Test this saved provider
                </button>
                <button
                  type="button"
                  className={buttonClass}
                  disabled={Boolean(isAssigned) || c.editorDirty || !c.routing.data || c.routing.isError}
                  onClick={() => c.setDeleteTarget(selected)}
                >
                  Delete provider
                </button>
              </>
            )}
          </div>
          {isAssigned && (
            <p className="text-xs">
              This provider is assigned to AI work. Save replacement assignments before deleting it.
            </p>
          )}
          {c.editorDirty && (
            <p className="text-xs">Unsaved provider changes. Connection tests and assignments use saved settings.</p>
          )}
          {c.testResult && (
            <p role="status" className="text-sm">
              {c.testResult.success
                ? 'Connection succeeded'
                : c.testResult.skipped
                  ? 'Connection test paused'
                  : 'Connection failed'}{' '}
              · Model: {c.testResult.model ?? 'unknown'}
              {c.testResult.latency_ms !== null ? ` · ${c.testResult.latency_ms} ms` : ''}
            </p>
          )}
        </section>
      )}
    </>
  )
}

function ProviderRouting({ c, onReload }: { c: AiProviderConnectionsController; onReload: () => void }) {
  const selected = c.editor?.baseline
  const routingChanged = Boolean(
    c.routingDirty && c.routing.data && c.visibleRouting?.version !== c.routing.data.version,
  )
  return (
    <>
      <section className="space-y-3" aria-labelledby="ai-provider-routing-title">
        <h4 id="ai-provider-routing-title" className="font-semibold">
          AI feature assignments
        </h4>
        <p className="text-sm">
          Select a saved provider above, then assign it below. Features inherit the default unless overridden. A legacy
          default preserves the existing configuration and environment credential.
        </p>
        {c.routing.isLoading && (
          <p role="status" className="text-sm">
            Loading feature assignments...
          </p>
        )}
        {c.routing.isError && (
          <div role="alert" className="text-sm text-red-700 dark:text-red-300">
            <p>
              {resolveApiErrorMessage(c.routing.error, 'Feature assignments could not be loaded. Changes are paused.')}
            </p>
            <button type="button" className={buttonClass} onClick={() => void c.routing.refetch()}>
              Retry loading assignments
            </button>
          </div>
        )}
        {routingChanged && (
          <p role="alert" className="text-sm text-amber-800 dark:text-amber-200">
            Assignments changed on the server. Your draft keeps its original version. Reload saved assignments to review
            the latest configuration.
          </p>
        )}
        {c.assignedProviders.isError && (
          <p role="alert" className="text-sm">
            Some assigned provider names could not be loaded. Their identifiers remain visible.
          </p>
        )}
        {c.visibleRouting && (
          <div className="space-y-3">
            {ROUTING_FIELDS.map(({ key, label }) => {
              const id = c.visibleRouting![key]
              const inherited = key !== 'default_provider_id' && id === null
              return (
                <div
                  key={key}
                  className="flex flex-wrap items-center justify-between gap-2 rounded border border-slate/15 p-3"
                >
                  <div className="min-w-0">
                    <p className="text-sm font-semibold">{label}</p>
                    <p className="break-words text-sm">
                      {inherited
                        ? `Default: ${c.providerName(c.visibleRouting!.default_provider_id)}`
                        : c.providerName(id)}
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      className={buttonClass}
                      aria-label={`Assign selected provider to ${label.toLowerCase()}`}
                      disabled={
                        !selected ||
                        !selected.enabled ||
                        Boolean(selected.credential_error) ||
                        c.editorDirty ||
                        c.routing.isError ||
                        id === selected.id
                      }
                      onClick={() => selected && c.assign(key, selected.id)}
                    >
                      Use selected provider
                    </button>
                    <button
                      type="button"
                      className={buttonClass}
                      aria-label={`Use ${key === 'default_provider_id' ? 'legacy settings for default provider' : `default provider for ${label.toLowerCase()}`}`}
                      disabled={id === null || c.routing.isError}
                      onClick={() => c.assign(key, null)}
                    >
                      {key === 'default_provider_id' ? 'Use legacy settings' : 'Use default'}
                    </button>
                  </div>
                </div>
              )
            })}
          </div>
        )}
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className={buttonClass}
            disabled={!c.routingDirty || c.routing.isError}
            onClick={c.saveRouting}
          >
            Save feature assignments
          </button>
          <button type="button" className={buttonClass} disabled={!c.routingDirty} onClick={onReload}>
            Reload saved assignments
          </button>
        </div>
      </section>
    </>
  )
}

function ProviderKeyField({ c }: { c: AiProviderConnectionsController }) {
  if (!c.editor) return null
  const selected = c.editor.baseline
  return (
    <Field label={selected?.api_key_configured ? 'Replacement API key' : 'Provider API key'}>
      <input
        type="password"
        autoComplete="new-password"
        maxLength={16384}
        className={inputClass}
        value={c.editor.draft.api_key}
        disabled={c.editor.draft.clear_api_key}
        onChange={(event) => c.updateDraft('api_key', event.target.value)}
        aria-label={selected?.api_key_configured ? 'Replacement API key' : 'Provider API key'}
        aria-describedby={c.validation.api_key ? 'provider-key-help provider-key-error' : 'provider-key-help'}
        aria-invalid={Boolean(c.validation.api_key)}
      />
      <span id="provider-key-help" className="mt-1 block text-xs">
        {selected?.api_key_configured
          ? 'A key is stored. Leave blank to keep it.'
          : 'Optional for endpoints that do not require authentication.'}{' '}
        Keys are saved encrypted and are never returned to this form. Named providers never inherit the legacy
        environment key.
      </span>
      {c.validation.api_key && (
        <span id="provider-key-error">
          <FieldError message={c.validation.api_key} />
        </span>
      )}
    </Field>
  )
}
