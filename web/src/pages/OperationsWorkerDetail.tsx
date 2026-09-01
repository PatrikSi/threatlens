import { Copy, RefreshCw } from 'lucide-react'
import { useState } from 'react'

import type {
  OperationsWorkerQueue,
  OperationsWorkerTopology,
} from '../types/operations'
import { formatDateTime } from '../utils/datetime'
import {
  formatDuration,
  formatWireLabel,
  workerReasonCopy,
} from './operationsHealthPresentation'
import { OperationsStatusChip } from './OperationsStatus'

export function OperationsWorkerDetail({
  topology,
  loading,
  fetching,
  error,
  onRetry,
}: {
  topology?: OperationsWorkerTopology
  loading: boolean
  fetching: boolean
  error: string
  onRetry: () => void
}) {
  if (!topology && loading) {
    return (
      <div role="status" className="py-8 text-center text-sm text-slate dark:text-slate-300">
        Inspecting worker topology and execution paths...
      </div>
    )
  }
  if (!topology) {
    return (
      <div role="alert" className="rounded border border-red-300/60 bg-red-50 px-3 py-3 text-sm text-red-900 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-100">
        <p>{error || 'Worker topology is unavailable.'}</p>
        <button
          type="button"
          className="mt-3 min-h-11 rounded border border-current px-3 py-2 font-semibold"
          onClick={onRetry}
        >
          Retry worker diagnostics
        </button>
      </div>
    )
  }

  const copy = workerReasonCopy(topology.reason)
  const requiredQueues = topology.queues.filter((queue) => queue.required)
  const affectedQueues = requiredQueues.filter((queue) => queue.status !== 'healthy')
  const evidenceIncomplete = topology.probes.some((probe) => probe.quality !== 'complete')
  return (
    <div className="space-y-4" aria-busy={fetching}>
      {error && (
        <div role="alert" className="flex flex-wrap items-center justify-between gap-2 rounded border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100">
          <span>{error}. Displaying the last successful worker snapshot.</span>
          <button type="button" className="min-h-11 rounded border border-current px-3 py-2 font-semibold md:min-h-0 md:py-1" onClick={onRetry}>
            Retry
          </button>
        </div>
      )}

      <div className="flex min-w-0 flex-col gap-3 border-b border-slate/15 pb-4 dark:border-white/10 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <OperationsStatusChip status={topology.status} />
            {evidenceIncomplete && <span className="tl-chip tl-chip-warning">Partial telemetry</span>}
            {topology.worker_inventory_truncated && (
              <span className="tl-chip tl-chip-warning">Worker inventory limited</span>
            )}
          </div>
          <h3 className="mt-2 text-base font-semibold text-ink dark:text-slate-100">{copy.headline}</h3>
          <p className="mt-1 text-sm text-slate dark:text-slate-300">{copy.explanation}</p>
          <p className="mt-1 text-xs text-slate dark:text-slate-400">
            Observed {formatDateTime(topology.generated_at)} · control probe {topology.duration_ms.toLocaleString()} ms · timeout {topology.timeout_seconds}s
          </p>
        </div>
        <button
          type="button"
          className="inline-flex min-h-11 shrink-0 items-center justify-center gap-2 rounded border border-slate/25 px-3 py-2 text-sm font-semibold disabled:opacity-60 md:min-h-0 dark:border-white/15"
          disabled={fetching}
          onClick={onRetry}
        >
          <RefreshCw className={`h-4 w-4 ${fetching ? 'animate-spin' : ''}`} aria-hidden="true" />
          {fetching ? 'Inspecting...' : 'Inspect again'}
        </button>
      </div>

      <dl className="grid grid-cols-2 gap-px overflow-hidden rounded border border-slate/15 bg-slate/15 sm:grid-cols-5 dark:border-white/10 dark:bg-white/10">
        <TopologyMetric
          label="Workers detailed"
          value={topology.worker_inventory_truncated
            ? `${topology.responding_worker_count}/${topology.observed_worker_count}`
            : topology.responding_worker_count}
        />
        <TopologyMetric label="Capacity" value={topology.total_capacity} />
        <TopologyMetric label="Active" value={topology.active_count} />
        <TopologyMetric label="Reserved" value={topology.reserved_count} />
        <TopologyMetric label="Scheduled" value={topology.scheduled_count} />
      </dl>

      {topology.worker_inventory_truncated && (
        <div role="alert" className="rounded border border-amber-300/60 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100">
          At least {topology.observed_worker_count.toLocaleString()} workers replied, but details are
          limited to {topology.responding_worker_count.toLocaleString()}. Capacity, load, and queue
          coverage are partial; do not treat an unlisted consumer as absent.
        </div>
      )}

      <div className="rounded border border-slate/15 px-3 py-2 text-xs dark:border-white/10">
        <p className="text-slate dark:text-slate-400">Canary dispatcher</p>
        <p className={topology.canary_dispatch_ok
          ? 'font-medium'
          : 'font-semibold text-amber-700 dark:text-amber-300'}>
          {canaryDispatchEvidence(topology)}
        </p>
        <p className="mt-1 text-slate dark:text-slate-400">
          A fresh scheduler heartbeat is required before stale queue canaries can prove a worker-side stall.
        </p>
      </div>

      <section aria-labelledby="worker-queue-paths-heading">
        <div className="flex flex-wrap items-end justify-between gap-2">
          <div>
            <h3 id="worker-queue-paths-heading" className="text-sm font-semibold uppercase tracking-wide text-slate dark:text-slate-400">
              Queue execution paths
            </h3>
            <p className="mt-0.5 text-xs text-slate dark:text-slate-400">
              Subscription is advisory; the execution heartbeat proves a task completed on that queue.
            </p>
          </div>
          <span className="text-xs text-slate dark:text-slate-400">
            {requiredQueues.length - affectedQueues.length}/{requiredQueues.length} required healthy
          </span>
        </div>
        <div className="mt-2 grid gap-2 xl:grid-cols-2">
          {[...topology.queues].sort(queueSort).map((queue) => (
            <QueuePath key={queue.key} queue={queue} />
          ))}
        </div>
      </section>

      {topology.reason !== 'healthy' && (
        <TroubleshootingSteps topology={topology} affectedQueues={affectedQueues} />
      )}

      <section aria-labelledby="worker-nodes-heading">
        <h3 id="worker-nodes-heading" className="text-sm font-semibold uppercase tracking-wide text-slate dark:text-slate-400">
          Responding worker nodes
        </h3>
        {topology.workers.length === 0 ? (
          <p className="mt-2 rounded border border-dashed border-slate/20 px-3 py-4 text-sm text-slate dark:border-white/10 dark:text-slate-300">
            No worker node returned usable telemetry.
          </p>
        ) : (
          <div className="mt-2 overflow-x-auto">
            <table className="w-full min-w-[760px] text-left text-xs">
              <thead>
                <tr className="border-b border-slate/20 dark:border-white/10">
                  <th scope="col" className="px-2 py-2">Worker</th>
                  <th scope="col" className="px-2 py-2">Queues</th>
                  <th scope="col" className="px-2 py-2">Load</th>
                  <th scope="col" className="px-2 py-2">Processed</th>
                  <th scope="col" className="px-2 py-2">Uptime</th>
                  <th scope="col" className="px-2 py-2">Telemetry</th>
                </tr>
              </thead>
              <tbody>
                {topology.workers.map((worker) => (
                  <tr key={worker.name} className="border-b border-slate/10 last:border-0 dark:border-white/5">
                    <td className="max-w-[16rem] px-2 py-2">
                      <div className="flex items-center gap-1">
                        <code className="truncate" title={worker.name}>{worker.name}</code>
                        <CopyValue value={worker.name} label={`worker name ${worker.name}`} />
                      </div>
                    </td>
                    <td className="max-w-xs px-2 py-2 text-slate dark:text-slate-300">{worker.queues.join(', ') || 'Unavailable'}</td>
                    <td className="whitespace-nowrap px-2 py-2">
                      {formatCount(worker.active_count)}/{formatCount(worker.capacity)} active · {formatCount(worker.reserved_count)} reserved
                      {worker.saturated && <span className="ml-1 font-semibold text-amber-700 dark:text-amber-300">Saturated</span>}
                    </td>
                    <td className="px-2 py-2">{formatCount(worker.processed_total)}</td>
                    <td className="px-2 py-2">{formatDuration(worker.uptime_seconds)}</td>
                    <td className="px-2 py-2">
                      {worker.missing_responses.length
                        ? <span className="font-semibold text-amber-700 dark:text-amber-300">Missing {worker.missing_responses.map(formatWireLabel).join(', ')}</span>
                        : 'Complete'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <details className="rounded border border-slate/15 px-3 py-2 text-xs dark:border-white/10">
        <summary className="min-h-11 cursor-pointer py-2 font-semibold md:min-h-0 md:py-1">Probe coverage</summary>
        <div className="mt-2 grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
          {topology.probes.map((probe) => (
            <div key={probe.probe} className="rounded bg-slate/5 px-2 py-2 dark:bg-white/[0.025]">
              <div className="flex items-center justify-between gap-2">
                <span className="font-semibold">{formatWireLabel(probe.probe)}</span>
                <span className={probe.quality === 'complete' ? 'text-emerald-700 dark:text-emerald-300' : 'font-semibold text-amber-700 dark:text-amber-300'}>
                  {formatWireLabel(probe.quality)}
                </span>
              </div>
              <p className="mt-1 text-slate dark:text-slate-400">
                {probe.responses_truncated
                  ? `${probe.responder_count} shown of ${probe.observed_responder_count} observed`
                  : `${probe.responder_count} replied`}{' '}
                · {probe.missing_responder_count} missing · {probe.invalid_response_count} invalid · {probe.duration_ms} ms
              </p>
            </div>
          ))}
        </div>
      </details>
    </div>
  )
}

function QueuePath({ queue }: { queue: OperationsWorkerQueue }) {
  const heartbeat = queue.execution_age_seconds == null
    ? formatWireLabel(queue.execution_reason)
    : `${formatDuration(queue.execution_age_seconds)} ago on ${queue.execution_worker ?? 'an unknown worker'}`
  return (
    <article className={`rounded border px-3 py-3 ${queue.status === 'healthy' ? 'border-slate/15 dark:border-white/10' : 'border-amber-300/70 bg-amber-50/50 dark:border-amber-500/30 dark:bg-amber-500/5'}`}>
      <div className="flex min-w-0 items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex min-w-0 items-center gap-2">
            <h4 className="truncate font-semibold" title={queue.key}>{queue.label}</h4>
            {!queue.required && <span className="tl-chip tl-chip-neutral">Optional</span>}
          </div>
          <p className="mt-0.5 truncate font-mono text-[11px] text-slate dark:text-slate-400" title={queue.key}>{queue.key}</p>
        </div>
        <OperationsStatusChip status={queue.status} />
      </div>
      <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
        <Evidence label="Consumers" value={queue.consumer_count.toLocaleString()} />
        <Evidence label="Consumer pool capacity" value={formatCount(queue.capacity)} />
        <Evidence label="Consumer pool active" value={formatCount(queue.active_count)} />
        <Evidence label="Consumer pool reserved" value={formatCount(queue.reserved_count)} />
        <div className="col-span-2">
          <dt className="text-slate dark:text-slate-400">Execution evidence</dt>
          <dd className={queue.execution_reason === 'fresh' ? 'font-medium' : 'font-semibold text-amber-700 dark:text-amber-300'}>{heartbeat}</dd>
        </div>
      </dl>
      {queue.consumers.length > 0 && (
        <p className="mt-2 truncate text-[11px] text-slate dark:text-slate-400" title={queue.consumers.join(', ')}>
          {queue.consumers.join(', ')}
        </p>
      )}
    </article>
  )
}

function TroubleshootingSteps({
  topology,
  affectedQueues,
}: {
  topology: OperationsWorkerTopology
  affectedQueues: OperationsWorkerQueue[]
}) {
  const serviceHints = topology.reason === 'canary_dispatch_unavailable'
    ? ['beat']
    : [...new Set(affectedQueues.map((queue) => queue.service_hint).filter(Boolean))]
  const likelyCauses = likelyWorkerCauses(topology)
  return (
    <section aria-labelledby="worker-troubleshooting-heading" className="rounded border border-cyan/25 bg-cyan/5 px-3 py-3 dark:border-cyan/20 dark:bg-cyan/[0.04]">
      <h3 id="worker-troubleshooting-heading" className="text-sm font-semibold uppercase tracking-wide text-slate dark:text-slate-300">
        Narrow down the failure
      </h3>
      <div className="mt-3 grid gap-4 lg:grid-cols-2">
        <div>
          <h4 className="text-xs font-semibold uppercase text-slate dark:text-slate-400">Likely causes</h4>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">
            {likelyCauses.map((cause) => <li key={cause}>{cause}</li>)}
          </ul>
        </div>
        <div>
          <h4 className="text-xs font-semibold uppercase text-slate dark:text-slate-400">Verification sequence</h4>
          <ol className="mt-2 space-y-2 text-sm">
            <Step number={1} title="Verify service state" command={serviceHints.length ? `docker compose ps ${serviceHints.join(' ')}` : 'docker compose ps'} />
            <Step number={2} title="Inspect bounded recent logs" command={serviceHints.length ? `docker compose logs --since 15m --tail 200 ${serviceHints.join(' ')}` : 'docker compose logs --since 15m --tail 200 worker worker-ai worker-maintenance worker-notifications'} />
            <Step
              number={3}
              title="Confirm recovery"
              description={topology.reason === 'canary_dispatch_unavailable'
                ? 'Run Inspect again and verify both the canary dispatcher and affected queue execution heartbeats advance.'
                : 'After correcting the service or queue configuration, run Inspect again and verify the queue execution heartbeat advances.'}
            />
          </ol>
        </div>
      </div>
    </section>
  )
}

function Step({
  number,
  title,
  description,
  command,
}: {
  number: number
  title: string
  description?: string
  command?: string
}) {
  return (
    <li className="grid grid-cols-[1.5rem_minmax(0,1fr)] gap-2">
      <span className="flex h-5 w-5 items-center justify-center rounded-full bg-ink text-[11px] font-semibold text-white dark:bg-cyan dark:text-[#053c2e]">{number}</span>
      <div className="min-w-0">
        <p className="font-semibold">{title}</p>
        {description && <p className="mt-0.5 text-slate dark:text-slate-300">{description}</p>}
        {command && (
          <div className="mt-1 flex min-w-0 items-center gap-1 rounded border border-slate/15 bg-white px-2 py-1 dark:border-white/10 dark:bg-[#041612]">
            <code className="min-w-0 flex-1 overflow-x-auto whitespace-nowrap text-[11px]">{command}</code>
            <CopyValue value={command} label={title} />
          </div>
        )}
      </div>
    </li>
  )
}

function CopyValue({ value, label }: { value: string; label: string }) {
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'failed'>('idle')
  const copy = async () => {
    try {
      if (!navigator.clipboard) throw new Error('Clipboard API unavailable')
      await navigator.clipboard.writeText(value)
      setCopyState('copied')
      window.setTimeout(() => setCopyState('idle'), 2_000)
    } catch {
      setCopyState('failed')
    }
  }
  return (
    <span className="shrink-0">
      <button
        type="button"
        className="inline-flex min-h-9 items-center gap-1 rounded px-2 text-[11px] font-semibold text-cyan hover:bg-cyan/10"
        aria-label={copyState === 'copied' ? `${label} copied` : `Copy ${label}`}
        onClick={() => void copy()}
      >
        <Copy className="h-3.5 w-3.5" aria-hidden="true" />
        {copyState === 'copied' ? 'Copied' : 'Copy'}
      </button>
      {copyState === 'failed' && (
        <span role="alert" className="sr-only">
          Clipboard access was denied. Select and copy the value manually.
        </span>
      )}
    </span>
  )
}

function TopologyMetric({ label, value }: { label: string; value: number | string | null }) {
  return (
    <div className="bg-white px-3 py-2 dark:bg-[#041612]">
      <dt className="text-[0.6875rem] font-semibold uppercase tracking-wide text-slate dark:text-slate-400">{label}</dt>
      <dd className="mt-0.5 font-mono text-base font-semibold">
        {typeof value === 'string' ? value : formatCount(value)}
      </dd>
    </div>
  )
}

function Evidence({ label, value }: { label: string; value: string }) {
  return <div><dt className="text-slate dark:text-slate-400">{label}</dt><dd className="font-medium">{value}</dd></div>
}

function formatCount(value: number | null): string {
  return value == null ? 'Unavailable' : value.toLocaleString()
}

function queueSort(left: OperationsWorkerQueue, right: OperationsWorkerQueue): number {
  if (left.status === 'healthy' && right.status !== 'healthy') return 1
  if (left.status !== 'healthy' && right.status === 'healthy') return -1
  return left.label.localeCompare(right.label)
}

function likelyWorkerCauses(topology: OperationsWorkerTopology): string[] {
  if (topology.reason === 'no_replies') {
    return ['Redis or the Celery control channel is unreachable.', 'All worker processes are stopped, restarting, or isolated from the broker.']
  }
  if (topology.reason === 'missing_consumers') {
    return ['The affected worker service is stopped or unhealthy.', 'The worker started without the required queue in its queue list.', 'The deployment is running workers from an older or mismatched configuration.']
  }
  if (topology.reason === 'canary_dispatch_unavailable') {
    return ['The beat scheduler or its watchdog is stopped or restarting.', 'The scheduler cannot write its heartbeat or dispatch canaries through Redis.', 'Scheduler configuration or credentials differ from the worker deployment.']
  }
  if (topology.reason === 'execution_stalled' || topology.reason === 'execution_evidence_missing') {
    return ['The consumer is advertised but not executing queued work.', 'The worker is blocked on a dependency or a long-running task.', 'The queue canary was introduced after this worker version started.']
  }
  if (topology.reason === 'saturated') {
    return ['Current work is using every observed execution slot.', 'A slow dependency or long-running task is holding worker capacity.']
  }
  return ['Celery inspection returned only part of the expected worker telemetry.', 'A worker may be restarting, overloaded, or on a mismatched application version.']
}

function canaryDispatchEvidence(topology: OperationsWorkerTopology): string {
  const state = formatWireLabel(topology.canary_dispatch_reason)
  if (topology.canary_dispatch_age_seconds == null) return state
  return `${state} · ${formatDuration(topology.canary_dispatch_age_seconds)} ago`
}
