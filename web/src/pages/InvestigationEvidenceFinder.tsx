import { FormEvent, useEffect, useMemo, useRef } from 'react'

import { CopyableIdentifier } from '../components/CopyableIdentifier'
import { resolveApiErrorMessage } from '../api/errors'
import type {
  InvestigationEvidenceCandidate,
  InvestigationEvidenceCandidateRange,
  InvestigationEvidenceQueryAnalysis,
} from '../types/investigations'
import { formatDateTime } from '../utils/datetime'
import {
  candidateIdentity,
  evidenceCandidateSourceLabel,
  evidenceSourceAvailability,
  formatEvidenceCandidateMatchReason,
  INVESTIGATION_EVIDENCE_RANGES,
  INVESTIGATION_EVIDENCE_MAX_PAGES,
  INVESTIGATION_EVIDENCE_MIN_SEARCH_LENGTH,
  INVESTIGATION_EVIDENCE_SOURCE_OPTIONS,
  type InvestigationEvidenceSourceFilter,
} from './investigationEvidenceFinderModel'
import { safeInvestigationExternalUrl } from './investigationPageModel'
import { InvestigationCollectionPagination } from './InvestigationCollectionPagination'
import { InvestigationInlineMessage } from './InvestigationShared'
import type { InvestigationDetailController } from './useInvestigationDetail'

export function InvestigationEvidenceFinder({
  controller,
}: {
  controller: InvestigationDetailController
}) {
  const openerRef = useRef<HTMLButtonElement>(null)
  const searchInputRef = useRef<HTMLInputElement>(null)
  const wasOpen = useRef(controller.evidenceFinderOpen)
  const response = controller.evidenceCandidatesQuery.data
  const availability = useMemo(
    () =>
      evidenceSourceAvailability(
        controller.currentUserQuery.data?.access?.permissions ?? [],
        response?.source_capabilities,
        controller.alertOccurrenceUnavailable,
      ),
    [
      controller.alertOccurrenceUnavailable,
      controller.currentUserQuery.data?.access?.permissions,
      response?.source_capabilities,
    ],
  )
  const availableCount = availability.filter((entry) => entry.available).length
  const draftHasContent = Boolean(
    controller.evidenceDraft.sourceId || controller.evidenceDraft.note.trim(),
  )

  useEffect(() => {
    if (controller.evidenceFinderOpen && !wasOpen.current) {
      window.requestAnimationFrame(() => searchInputRef.current?.focus())
    } else if (!controller.evidenceFinderOpen && wasOpen.current) {
      window.requestAnimationFrame(() => openerRef.current?.focus())
    }
    wasOpen.current = controller.evidenceFinderOpen
  }, [controller.evidenceFinderOpen])

  useEffect(() => {
    if (controller.evidenceFinderOpen && controller.successNotice === 'Evidence added.') {
      window.requestAnimationFrame(() => searchInputRef.current?.focus())
    }
  }, [controller.evidenceFinderOpen, controller.successNotice])

  if (!controller.evidenceFinderOpen) {
    return (
      <div className="mt-3 border-y border-slate/15 py-3 dark:border-white/10">
        <button
          ref={openerRef}
          type="button"
          aria-expanded="false"
          aria-controls="investigation-evidence-finder"
          className="min-h-11 rounded bg-ink px-3 py-2 text-sm font-semibold text-white md:min-h-0 dark:bg-cyan dark:text-[#053c2e]"
          onClick={() => controller.setEvidenceFinderOpen(true)}
        >
          {draftHasContent ? 'Resume evidence draft' : 'Add evidence'}
        </button>
        {draftHasContent && (
          <p className="mt-1 text-xs text-slate dark:text-slate-400">
            Your unfinished evidence context is preserved.
          </p>
        )}
      </div>
    )
  }

  return (
    <section
      id="investigation-evidence-finder"
      aria-labelledby="investigation-evidence-finder-heading"
      className="mt-3 border-y border-slate/15 py-3 dark:border-white/10"
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 id="investigation-evidence-finder-heading" className="text-sm font-semibold">
            Find evidence
          </h3>
          <p className="mt-0.5 text-xs text-slate dark:text-slate-400">
            Search accessible intelligence by title, URL, indicator, report, or alert.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {draftHasContent && (
            <button
              type="button"
              className="min-h-11 rounded border border-slate/20 px-3 py-2 text-xs font-semibold md:min-h-0 md:py-1.5 dark:border-white/10"
              disabled={controller.mutation.isPending}
              onClick={() => {
                controller.clearEvidenceDraft()
                window.requestAnimationFrame(() => searchInputRef.current?.focus())
              }}
            >
              Clear draft
            </button>
          )}
          <button
            type="button"
            className="min-h-11 rounded border border-slate/20 px-3 py-2 text-xs font-semibold md:min-h-0 md:py-1.5 dark:border-white/10"
            disabled={controller.mutation.isPending}
            onClick={() => controller.setEvidenceFinderOpen(false)}
          >
            Close finder
          </button>
        </div>
      </div>

      <div className="mt-3 grid min-w-0 gap-2 sm:grid-cols-[minmax(0,1fr)_11rem_11rem]">
        <div className="min-w-0">
          <label htmlFor="investigation-evidence-search" className="text-xs font-semibold">
            Search evidence
          </label>
          <input
            ref={searchInputRef}
            id="investigation-evidence-search"
            type="search"
            autoComplete="off"
            maxLength={255}
            className="mt-1 min-h-11 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
            value={controller.evidenceSearch}
            disabled={controller.mutation.isPending || availableCount === 0}
            placeholder="Title, URL, domain, IP, hash, or report"
            onChange={(event) => controller.setEvidenceSearch(event.target.value)}
          />
        </div>
        <div>
          <label htmlFor="investigation-evidence-source-filter" className="text-xs font-semibold">
            Source
          </label>
          <select
            id="investigation-evidence-source-filter"
            className="mt-1 min-h-11 w-full rounded border border-slate/30 bg-white px-2 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
            value={controller.evidenceSourceFilter}
            disabled={controller.mutation.isPending || availableCount === 0}
            onChange={(event) =>
              controller.setEvidenceSourceFilter(
                event.target.value as InvestigationEvidenceSourceFilter,
              )
            }
          >
            <option value="all">All accessible</option>
            {INVESTIGATION_EVIDENCE_SOURCE_OPTIONS.map((option) => {
              const capability = availability.find(
                (entry) => entry.sourceType === option.value,
              )
              return (
                <option
                  key={option.value}
                  value={option.value}
                  disabled={!capability?.available}
                >
                  {option.pluralLabel}{capability?.available ? '' : ' (locked)'}
                </option>
              )
            })}
          </select>
        </div>
        <div>
          <label htmlFor="investigation-evidence-range" className="text-xs font-semibold">
            Time range
          </label>
          <select
            id="investigation-evidence-range"
            className="mt-1 min-h-11 w-full rounded border border-slate/30 bg-white px-2 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
            value={controller.evidenceRange}
            disabled={controller.mutation.isPending || availableCount === 0}
            onChange={(event) =>
              controller.setEvidenceRange(
                event.target.value as InvestigationEvidenceCandidateRange,
              )
            }
          >
            {INVESTIGATION_EVIDENCE_RANGES.map((range) => (
              <option key={range.value} value={range.value}>
                {range.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      <SourceCapabilitySummary availability={availability} />

      <div className="mt-3 grid min-w-0 gap-3 lg:grid-cols-[minmax(0,1.15fr)_minmax(18rem,0.85fr)]">
        <div className="min-w-0">
          <CandidateResults
            controller={controller}
            availability={availability}
            availableCount={availableCount}
          />
        </div>
        <CandidatePreview
          controller={controller}
          availability={availability}
        />
      </div>
    </section>
  )
}

function CandidateResults({
  controller,
  availability,
  availableCount,
}: {
  controller: InvestigationDetailController
  availability: ReturnType<typeof evidenceSourceAvailability>
  availableCount: number
}) {
  const query = controller.evidenceCandidatesQuery
  const response = query.data
  const scopeLabel = rangeLabel(controller.evidenceRange)
  const waitingForDebounce =
    controller.evidenceSearch.trim() !== controller.debouncedEvidenceSearch
  const availableTypes = new Set(
    availability.filter((entry) => entry.available).map((entry) => entry.sourceType),
  )
  const candidates = (response?.candidates ?? []).filter(
    (candidate) =>
      availableTypes.has(candidate.source_type) &&
      controller.requestedEvidenceSourceTypes.includes(candidate.source_type),
  )
  const effectiveRequestedTypes = controller.requestedEvidenceSourceTypes.filter((sourceType) =>
    availableTypes.has(sourceType),
  )

  if (availableCount === 0) {
    return (
      <InvestigationInlineMessage tone="info">
        No evidence sources are available with your current permissions. Existing investigation
        evidence remains visible below.
      </InvestigationInlineMessage>
    )
  }
  if (effectiveRequestedTypes.length === 0) {
    return (
      <InvestigationInlineMessage tone="info">
        The selected source is unavailable. Choose All accessible or another unlocked source.
      </InvestigationInlineMessage>
    )
  }
  if (
    !waitingForDebounce &&
    controller.debouncedEvidenceSearch.length > 0 &&
    controller.debouncedEvidenceSearch.length < INVESTIGATION_EVIDENCE_MIN_SEARCH_LENGTH
  ) {
    return (
      <InvestigationInlineMessage tone="info">
        Enter at least {INVESTIGATION_EVIDENCE_MIN_SEARCH_LENGTH} characters to search by name or
        text. Leave the field empty to browse recent accessible evidence.
      </InvestigationInlineMessage>
    )
  }
  if (waitingForDebounce || (query.isLoading && !response)) {
    return (
      <p role="status" aria-busy="true" className="rounded bg-slate/5 px-3 py-8 text-center text-sm text-slate dark:bg-white/[0.03] dark:text-slate-300">
        {waitingForDebounce ? 'Searching the current scope...' : `Loading evidence from ${scopeLabel.toLowerCase()}...`}
      </p>
    )
  }
  if (query.isError && !response) {
    return (
      <div role="alert" className="rounded border border-red-300/70 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-800/60 dark:bg-red-950/30 dark:text-red-200">
        <p>
          {resolveApiErrorMessage(query.error, 'Evidence search could not be loaded', {
            retryGuidance: 'Retry this search. Existing investigation evidence is unaffected.',
          })}
        </p>
        <button
          type="button"
          className="mt-2 min-h-11 rounded border border-current px-3 py-2 font-semibold md:min-h-0 md:py-1.5"
          disabled={query.isFetching}
          onClick={() => void query.refetch()}
        >
          {query.isFetching ? 'Retrying...' : 'Retry search'}
        </button>
        <button
          type="button"
          className="ml-2 mt-2 min-h-11 rounded border border-current px-3 py-2 font-semibold md:min-h-0 md:py-1.5"
          disabled={query.isFetching}
          onClick={controller.restartEvidenceCandidateSearch}
        >
          Restart results
        </button>
      </div>
    )
  }

  return (
    <>
      {query.isError && response && (
        <div role="alert" className="mb-2 flex flex-wrap items-center justify-between gap-2 rounded border border-amber-300/70 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-700/50 dark:bg-amber-950/20 dark:text-amber-100">
          <span>
            Search could not be refreshed. Last loaded results remain visible, but cannot be
            attached until they are verified.
          </span>
          <button
            type="button"
            className="min-h-11 rounded border border-current px-3 py-2 font-semibold md:min-h-0 md:py-1"
            disabled={query.isFetching}
            onClick={() => void query.refetch()}
          >
            Retry refresh
          </button>
        </div>
      )}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-slate dark:text-slate-400">
          {sourceScopeLabel(controller.evidenceSourceFilter)} · {scopeLabel}
        </p>
        <QueryAnalysis analysis={response?.query_analysis} />
      </div>
      {controller.evidenceSourceFilter === 'all' && !controller.evidenceSearch.trim() && (
        <p className="mt-1 text-xs text-slate dark:text-slate-400">
          Recent suggestions omit indicators for faster browsing. Enter an IP, domain, hash, CVE,
          or other term, or choose Indicators to browse them.
        </p>
      )}
      {candidates.length === 0 ? (
        <div role="status" className="mt-2 rounded bg-slate/5 px-3 py-6 text-center dark:bg-white/[0.03]">
          <p className="text-sm font-semibold">No evidence matches this scope</p>
          <p className="mt-1 text-xs text-slate dark:text-slate-400">
            {controller.debouncedEvidenceSearch
              ? 'Check the search value, widen the time range, or include more accessible sources.'
              : 'Widen the time range or include more accessible sources.'}
          </p>
        </div>
      ) : (
        <fieldset
          className="mt-2 min-w-0 divide-y divide-slate/15 border-y border-slate/15 dark:divide-white/10 dark:border-white/10"
          disabled={controller.mutation.isPending || query.isFetching}
        >
          <legend className="sr-only">Select one evidence record</legend>
          {candidates.map((candidate) => (
            <CandidateOption
              key={candidateIdentity(candidate)}
              candidate={candidate}
              selected={
                candidateIdentity(candidate) ===
                (controller.selectedEvidenceCandidate
                  ? candidateIdentity(controller.selectedEvidenceCandidate)
                  : '')
              }
              onSelect={() => controller.selectEvidenceCandidate(candidate)}
            />
          ))}
        </fieldset>
      )}
      {query.isFetching && response && (
        <p role="status" aria-busy="true" className="mt-2 text-xs text-slate dark:text-slate-400">
          Updating results for the current scope...
        </p>
      )}
      {response && (
        <InvestigationCollectionPagination
          label="evidence candidates"
          total={response.total}
          page={response.page}
          pageSize={response.page_size}
          itemCount={candidates.length}
          fetching={query.isFetching}
          disabled={controller.mutation.isPending}
          disabledReason="Wait for the current evidence change to finish before changing pages."
          maxPages={INVESTIGATION_EVIDENCE_MAX_PAGES}
          limitMessage={`Only the first ${(response.page_size * INVESTIGATION_EVIDENCE_MAX_PAGES).toLocaleString()} matches are available in this view. Refine the search or scope to see a different result set.`}
          totalTruncated={response.total_truncated}
          onPageChange={controller.setEvidenceCandidatePage}
        />
      )}
    </>
  )
}

function CandidateOption({
  candidate,
  selected,
  onSelect,
}: {
  candidate: InvestigationEvidenceCandidate
  selected: boolean
  onSelect: () => void
}) {
  const matchReason = formatEvidenceCandidateMatchReason(candidate.match_reason)
  return (
    <label
      className={`flex min-w-0 gap-2 px-2 py-2.5 ${
        candidate.already_attached
          ? 'cursor-not-allowed opacity-60'
          : 'cursor-pointer hover:bg-cyan/5'
      }`}
    >
      <input
        type="radio"
        name="investigation-evidence-candidate"
        className="mt-1 shrink-0 accent-cyan"
        value={candidateIdentity(candidate)}
        checked={selected}
        disabled={candidate.already_attached}
        onChange={onSelect}
      />
      <span className="min-w-0 flex-1">
        <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
          <span className="break-words text-sm font-semibold">{candidate.title}</span>
          <span className="tl-chip tl-chip-neutral">
            {evidenceCandidateSourceLabel(candidate.source_type)}
          </span>
          {candidate.already_attached && (
            <span className="tl-chip tl-chip-success">Already attached</span>
          )}
        </span>
        <span className="mt-0.5 block text-xs text-slate dark:text-slate-400">
          {[candidate.source_label, candidate.observed_at ? formatDateTime(candidate.observed_at) : null, matchReason]
            .filter(Boolean)
            .join(' · ')}
        </span>
        {candidate.description && (
          <span className="mt-1 line-clamp-2 block break-words text-xs text-slate dark:text-slate-300">
            {candidate.description}
          </span>
        )}
      </span>
    </label>
  )
}

function CandidatePreview({
  controller,
  availability,
}: {
  controller: InvestigationDetailController
  availability: ReturnType<typeof evidenceSourceAvailability>
}) {
  const candidate = controller.selectedEvidenceCandidate
  if (!candidate) {
    return (
      <aside className="rounded border border-dashed border-slate/25 px-3 py-6 text-center text-sm text-slate dark:border-white/15 dark:text-slate-400">
        Select a result to review its snapshot before attaching it.
      </aside>
    )
  }
  const sourceUrl = safeInvestigationExternalUrl(candidate.url)
  const metadata = Object.entries(candidate.metadata).filter(([key]) => key !== 'related_items')
  const relatedItems = candidate.metadata.related_items ?? []
  const sourceCapability = availability.find(
    (entry) => entry.sourceType === candidate.source_type,
  )
  const sourceUnavailable = sourceCapability?.available === false
  const addFailed =
    controller.mutation.isError &&
    controller.mutation.variables?.kind === 'add-evidence' &&
    controller.mutation.variables.sourceType === candidate.source_type &&
    controller.mutation.variables.sourceId === candidate.source_id
  const isAttaching =
    controller.mutation.isPending && controller.mutation.variables?.kind === 'add-evidence'
  const selectionVerificationPending = controller.evidenceCandidatesQuery.isFetching
  const selectionVerificationFailed = controller.evidenceCandidatesQuery.isError

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (
      candidate.already_attached ||
      sourceUnavailable ||
      !controller.evidenceDraft.sourceId ||
      controller.evidenceDraftVersion === null
    ) return
    controller.mutation.mutate({
      kind: 'add-evidence',
      sourceType: controller.evidenceDraft.sourceType,
      sourceId: controller.evidenceDraft.sourceId,
      note: controller.evidenceDraft.note,
      expectedVersion: controller.evidenceDraftVersion,
    })
  }

  return (
    <aside className="min-w-0 rounded border border-slate/20 bg-slate/5 p-3 dark:border-white/10 dark:bg-white/[0.03]">
      <p className="text-[11px] font-semibold uppercase tracking-wide text-slate dark:text-slate-400">
        Evidence preview
      </p>
      <div className="mt-1 flex flex-wrap items-center gap-2">
        <h4 className="min-w-0 break-words text-sm font-semibold">{candidate.title}</h4>
        <span className="tl-chip tl-chip-neutral">
          {evidenceCandidateSourceLabel(candidate.source_type)}
        </span>
      </div>
      <p className="mt-1 text-xs text-slate dark:text-slate-400">
        {[candidate.source_label, candidate.observed_at ? formatDateTime(candidate.observed_at) : null]
          .filter(Boolean)
          .join(' · ')}
      </p>
      {candidate.description && (
        <p className="mt-2 whitespace-pre-wrap break-words text-sm text-slate dark:text-slate-300">
          {candidate.description}
        </p>
      )}
      {sourceUrl && (
        <a
          href={sourceUrl}
          target="_blank"
          rel="noreferrer"
          className="mt-2 inline-flex min-h-11 items-center break-all text-xs font-semibold text-cyan hover:underline md:min-h-0"
        >
          Open source
        </a>
      )}
      {candidate.url && !sourceUrl && (
        <p className="mt-2 text-xs text-amber-800 dark:text-amber-200">
          This source URL cannot be opened safely.
        </p>
      )}

      {relatedItems.length > 0 && (
        <div className="mt-3">
          <p className="text-xs font-semibold">Related articles</p>
          <ul className="mt-1 space-y-1 text-xs">
            {relatedItems.map((item) => (
              <li key={item.item_id} className="min-w-0 break-words text-slate dark:text-slate-300">
                {item.title}
                {item.feed_name ? ` · ${item.feed_name}` : ''}
              </li>
            ))}
          </ul>
        </div>
      )}

      <form className="mt-3 border-t border-slate/15 pt-3 dark:border-white/10" onSubmit={submit}>
        <input type="hidden" name="source_type" value={controller.evidenceDraft.sourceType} />
        <input type="hidden" name="source_id" value={controller.evidenceDraft.sourceId} />
        <div className="flex items-center justify-between gap-2">
          <label htmlFor="investigation-evidence-note" className="text-xs font-semibold">
            Context note (optional)
          </label>
          <span className="text-[11px] text-slate dark:text-slate-400">
            {controller.evidenceDraft.note.length.toLocaleString()} / 2,000
          </span>
        </div>
        <textarea
          id="investigation-evidence-note"
          maxLength={2_000}
          rows={3}
          className="mt-1 w-full rounded border border-slate/30 bg-white px-3 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]"
          value={controller.evidenceDraft.note}
          disabled={isAttaching}
          placeholder="Why this evidence matters to the investigation"
          onChange={(event) => controller.updateEvidenceDraft({ note: event.target.value })}
        />
        {candidate.already_attached && (
          <div className="mt-2">
            <InvestigationInlineMessage tone="info">
              This source is already attached to the investigation.
            </InvestigationInlineMessage>
          </div>
        )}
        {sourceUnavailable && (
          <div className="mt-2">
            <InvestigationInlineMessage tone="warning">
              {sourceCapability?.reason ?? 'This evidence source is unavailable.'}
            </InvestigationInlineMessage>
          </div>
        )}
        {selectionVerificationPending && (
          <p role="status" className="mt-2 text-xs text-slate dark:text-slate-400">
            Verifying that this result is still available in the selected scope...
          </p>
        )}
        {selectionVerificationFailed && (
          <div className="mt-2">
            <InvestigationInlineMessage tone="warning">
              Retry the evidence search before attaching this last-known result.
            </InvestigationInlineMessage>
          </div>
        )}
        {addFailed && (
          <div className="mt-2">
            <InvestigationInlineMessage tone="error">
              {resolveApiErrorMessage(
                controller.mutation.error,
                'Evidence could not be attached',
                { retryGuidance: 'Your selection and context note have been preserved.' },
              )}
            </InvestigationInlineMessage>
          </div>
        )}
        <button
          type="submit"
          className="mt-2 min-h-11 w-full rounded bg-ink px-3 py-2 text-sm font-semibold text-white disabled:opacity-50 md:min-h-0 dark:bg-cyan dark:text-[#053c2e]"
          disabled={
            isAttaching ||
            candidate.already_attached ||
            sourceUnavailable ||
            selectionVerificationPending ||
            selectionVerificationFailed ||
            controller.evidenceDraftVersion === null ||
            !controller.evidenceDraft.sourceId
          }
        >
          {isAttaching
            ? 'Attaching...'
            : selectionVerificationPending
              ? 'Verifying selection...'
              : selectionVerificationFailed
                ? 'Retry search to attach'
                : 'Attach evidence'}
        </button>
      </form>

      <details className="mt-3 border-t border-slate/15 pt-2 text-xs dark:border-white/10">
        <summary className="min-h-11 cursor-pointer py-2 font-semibold text-slate md:min-h-0 md:py-1 dark:text-slate-300">
          Technical details
        </summary>
        <dl className="mt-1 grid min-w-0 gap-2">
          <div>
            <dt className="text-slate dark:text-slate-400">Source ID</dt>
            <dd className="mt-0.5">
              <CopyableIdentifier label="Source ID" value={candidate.source_id} />
            </dd>
          </div>
          {metadata.map(([key, value]) => (
            <div key={key} className="min-w-0">
              <dt className="break-words text-slate dark:text-slate-400">{humanizeKey(key)}</dt>
              <dd className="mt-0.5 break-all font-mono">{formatMetadataValue(value)}</dd>
            </div>
          ))}
        </dl>
      </details>
    </aside>
  )
}

function SourceCapabilitySummary({
  availability,
}: {
  availability: ReturnType<typeof evidenceSourceAvailability>
}) {
  const locked = availability.filter((entry) => !entry.available)
  if (locked.length === 0) return null
  return (
    <details className="mt-2 text-xs text-slate dark:text-slate-400">
      <summary className="min-h-11 cursor-pointer py-2 font-semibold md:min-h-0 md:py-1">
        {locked.length} source {locked.length === 1 ? 'type is' : 'types are'} unavailable
      </summary>
      <ul className="mt-1 space-y-1 pl-4">
        {locked.map((entry) => (
          <li key={entry.sourceType} className="list-disc">
            <span className="font-semibold">{evidenceCandidateSourceLabel(entry.sourceType)}:</span>{' '}
            {entry.reason}
          </li>
        ))}
      </ul>
    </details>
  )
}

function QueryAnalysis({
  analysis,
}: {
  analysis: InvestigationEvidenceQueryAnalysis | null | undefined
}) {
  if (!analysis) return null
  if (analysis.kind === 'url') {
    return (
      <span className="tl-chip tl-chip-info">
        Detected: URL{analysis.detected_ioc_type
          ? ` · ${formatIocType(analysis.detected_ioc_type)} host`
          : ''}
      </span>
    )
  }
  if (analysis.kind !== 'ioc' || !analysis.detected_ioc_type) return null
  return (
    <span className="tl-chip tl-chip-info">
      Detected: {formatIocType(analysis.detected_ioc_type)}
    </span>
  )
}

function rangeLabel(range: InvestigationEvidenceCandidateRange): string {
  return INVESTIGATION_EVIDENCE_RANGES.find((entry) => entry.value === range)?.label ?? range
}

function sourceScopeLabel(filter: InvestigationEvidenceSourceFilter): string {
  if (filter === 'all') return 'All accessible evidence'
  return (
    INVESTIGATION_EVIDENCE_SOURCE_OPTIONS.find((entry) => entry.value === filter)?.pluralLabel ??
    filter
  )
}

function formatIocType(type: string): string {
  const labels: Record<string, string> = {
    ipv4: 'IPv4',
    ipv6: 'IPv6',
    md5: 'MD5',
    hash_md5: 'MD5',
    sha1: 'SHA-1',
    hash_sha1: 'SHA-1',
    sha256: 'SHA-256',
    hash_sha256: 'SHA-256',
    sha512: 'SHA-512',
    hash_sha512: 'SHA-512',
    cve: 'CVE',
    domain: 'Domain',
    vendor: 'Vendor',
    program: 'Product',
    url: 'URL',
  }
  return labels[type.toLowerCase()] ?? type.replaceAll('_', ' ')
}

function humanizeKey(key: string): string {
  const value = key.replaceAll('_', ' ')
  return `${value.charAt(0).toUpperCase()}${value.slice(1)}`
}

function formatMetadataValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return 'Not recorded'
  if (Array.isArray(value)) return value.map((entry) => String(entry)).join(', ') || 'None'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}
