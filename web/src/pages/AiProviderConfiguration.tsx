import { AITestConnectionResponse } from '../types/api'
import { Field, FieldError, Panel } from './aiSettingsSupport'
import { updateDraft } from './aiSettingsUtils'
import { AiConfigurationDraftProps } from './AiSettingsConfigurationTypes'

type AiProviderConfigurationProps = AiConfigurationDraftProps & {
  draftDirty: boolean
  configured: boolean
  testPending: boolean
  testDisabledReason: string | null
  testResult: AITestConnectionResponse | null
  onTestConnection: () => void
}

function getProviderTestMessage(draftDirty: boolean, testDisabledReason: string | null, configured: boolean) {
  if (draftDirty) {
    return 'Save your draft changes first. Test saved connection only checks the last saved provider settings.'
  }
  if (testDisabledReason) {
    return testDisabledReason
  }
  if (configured) {
    return 'Test the saved provider configuration. Unsaved draft changes are not included.'
  }
  return 'Save the provider settings before testing the saved connection.'
}

function ConnectionTestResult({ result }: { result: AITestConnectionResponse }) {
  let title = 'Connection failed'
  if (result.skipped) {
    title = 'Connection test paused'
  } else if (result.success) {
    title = 'Connection succeeded'
  }

  return (
    <div className="mt-4 rounded-xl border border-slate/20 bg-white/70 p-3 text-sm dark:border-cyan-900/40 dark:bg-[#072019]/80">
      <p className="font-semibold">{title}</p>
      <p className="mt-1 text-slate dark:text-white/70">
        Model: {result.model || 'unknown'}
        {typeof result.latency_ms === 'number' ? `, ${result.latency_ms} ms` : ''}
      </p>
      {result.skipped && (
        <p className="mt-1 text-slate dark:text-white/70">
          Running: {result.running_task_count ?? 0}, queued: {result.queued_task_count ?? 0}
        </p>
      )}
      {result.error && <p className="mt-1 text-red-600">{result.error}</p>}
    </div>
  )
}

export function AiProviderConfiguration({
  draft,
  setDraft,
  validation,
  draftDirty,
  configured,
  testPending,
  testDisabledReason,
  testResult,
  onTestConnection,
}: AiProviderConfigurationProps) {
  const testSavedConnectionDisabled = testPending || draftDirty || !configured || Boolean(testDisabledReason)
  const providerTestMessage = getProviderTestMessage(draftDirty, testDisabledReason, configured)

  return (
    <Panel title="Provider" subtitle="ThreatLens currently speaks to one OpenAI-compatible chat endpoint. Secrets stay in the environment.">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate/15 bg-slate/5 px-3 py-3 dark:border-cyan-900/30 dark:bg-white/[0.03]">
        <div className="text-sm text-slate dark:text-white/70">{providerTestMessage}</div>
        <button
          type="button"
          className="rounded border border-slate/30 px-3 py-2 text-sm font-semibold disabled:opacity-50 dark:border-cyan-900/40"
          onClick={onTestConnection}
          disabled={testSavedConnectionDisabled}
        >
          {testPending ? 'Testing...' : 'Test saved connection'}
        </button>
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <Field label="Base URL">
          <input
            className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
            value={draft.base_url}
            onChange={(event) => updateDraft(setDraft, 'base_url', event.target.value)}
            aria-invalid={Boolean(validation.base_url)}
          />
          <FieldError message={validation.base_url} />
        </Field>
        <Field label="Model">
          <input
            className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
            value={draft.model}
            onChange={(event) => updateDraft(setDraft, 'model', event.target.value)}
            aria-invalid={Boolean(validation.model)}
          />
          <FieldError message={validation.model} />
        </Field>
        <Field label="Temperature">
          <input
            className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
            value={draft.temperature}
            onChange={(event) => updateDraft(setDraft, 'temperature', event.target.value)}
            inputMode="decimal"
            aria-invalid={Boolean(validation.temperature)}
          />
          <FieldError message={validation.temperature} />
        </Field>
        <Field label="Maximum completion tokens">
          <input
            className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
            value={draft.max_completion_tokens}
            onChange={(event) => updateDraft(setDraft, 'max_completion_tokens', event.target.value)}
            inputMode="numeric"
            aria-invalid={Boolean(validation.max_completion_tokens)}
          />
          <FieldError message={validation.max_completion_tokens} />
        </Field>
        <Field label="Request timeout (seconds)" className="md:col-span-2">
          <input
            className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
            value={draft.request_timeout_seconds}
            onChange={(event) => updateDraft(setDraft, 'request_timeout_seconds', event.target.value)}
            inputMode="numeric"
            aria-invalid={Boolean(validation.request_timeout_seconds)}
          />
          <FieldError message={validation.request_timeout_seconds} />
        </Field>
        <Field label="Maximum retry attempts" className="md:col-span-2">
          <input
            className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 dark:border-cyan-900/40 dark:bg-[#072019]"
            value={draft.request_max_retries}
            onChange={(event) => updateDraft(setDraft, 'request_max_retries', event.target.value)}
            inputMode="numeric"
            aria-invalid={Boolean(validation.request_max_retries)}
          />
          <FieldError message={validation.request_max_retries} />
          <span className="mt-1 block text-xs text-slate dark:text-white/60">
            Retry malformed or failed AI responses up to X additional times before marking the run as failed.
          </span>
        </Field>
      </div>
      {testResult && <ConnectionTestResult result={testResult} />}
    </Panel>
  )
}
