import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'

import { apiFetch } from '../api/client'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import { hasRequiredPermissions } from '../workspace/workspaceModel'
import type {
  InvestigationActivityListResponse,
  InvestigationDetail,
  InvestigationEvidenceCandidate,
  InvestigationEvidenceCandidateListResponse,
  InvestigationEvidenceCandidateRange,
  InvestigationEvidenceListResponse,
  InvestigationEvidenceType,
  InvestigationMemberCandidateListResponse,
  InvestigationMemberRole,
  InvestigationNoteListResponse,
  InvestigationSeverity,
  InvestigationUpdateRequest,
  InvestigationVisibility,
} from '../types/investigations'
import {
  buildEvidenceCandidateRequest,
  candidateEvidenceSourceTypes,
  candidatePageCount,
  evidenceSourceAvailability,
  INVESTIGATION_EVIDENCE_MIN_SEARCH_LENGTH,
  type InvestigationEvidenceSourceFilter,
} from './investigationEvidenceFinderModel'
import {
  INVESTIGATION_ACTIVITY_PAGE_SIZE,
  INVESTIGATION_EVIDENCE_PAGE_SIZE,
  INVESTIGATION_MEMBER_PAGE_SIZE,
  INVESTIGATION_NOTE_PAGE_SIZE,
  investigationCollectionPageCount,
  isAlertOccurrenceUnavailable,
  isInvestigationVersionConflict,
  readInvestigationTab,
  resolveInvestigationAccess,
} from './investigationPageModel'

export interface InvestigationOverviewDraft {
  title: string
  description: string
  severity: InvestigationSeverity
  visibility: InvestigationVisibility
  assigneeUserId: string
}

export interface InvestigationEvidenceDraft {
  sourceType: InvestigationEvidenceType
  sourceId: string
  note: string
}

export type InvestigationMutationOperation = (
  | { kind: 'update'; changes: Omit<InvestigationUpdateRequest, 'expected_version'> }
  | { kind: 'add-member'; userId: string; role: InvestigationMemberRole }
  | { kind: 'update-member'; userId: string; role: InvestigationMemberRole }
  | { kind: 'remove-member'; userId: string }
  | { kind: 'add-evidence'; sourceType: InvestigationEvidenceType; sourceId: string; note: string }
  | { kind: 'remove-evidence'; evidenceId: string }
  | { kind: 'add-note'; body: string }
  | { kind: 'update-note'; noteId: string; noteVersion: number; body: string }
  | { kind: 'remove-note'; noteId: string; noteVersion: number }
) & { expectedVersion: number }

const EMPTY_OVERVIEW_DRAFT: InvestigationOverviewDraft = {
  title: '',
  description: '',
  severity: 'medium',
  visibility: 'private',
  assigneeUserId: '',
}

const EMPTY_EVIDENCE_DRAFT: InvestigationEvidenceDraft = {
  sourceType: 'item',
  sourceId: '',
  note: '',
}

export function useInvestigationDetail(investigationId: string) {
  const queryClient = useQueryClient()
  const currentUserQuery = useCurrentUser()
  const [searchParams, setSearchParams] = useSearchParams()
  const activeTab = readInvestigationTab(searchParams)
  const detailKey = useMemo(() => ['investigations', 'detail', investigationId] as const, [investigationId])
  const [overviewDraft, setOverviewDraft] = useState<InvestigationOverviewDraft>(EMPTY_OVERVIEW_DRAFT)
  const [overviewBaseline, setOverviewBaseline] = useState<InvestigationOverviewDraft>(EMPTY_OVERVIEW_DRAFT)
  const [overviewBaselineVersion, setOverviewBaselineVersion] = useState<number | null>(null)
  const [noteDraft, setNoteDraft] = useState('')
  const [noteDraftVersion, setNoteDraftVersion] = useState<number | null>(null)
  const [editingNoteId, setEditingNoteId] = useState<string | null>(null)
  const [editingNoteBody, setEditingNoteBody] = useState('')
  const [editingNoteBaseline, setEditingNoteBaseline] = useState('')
  const [editingNoteVersion, setEditingNoteVersion] = useState<number | null>(null)
  const [editingNoteInvestigationVersion, setEditingNoteInvestigationVersion] =
    useState<number | null>(null)
  const [evidenceDraft, setEvidenceDraft] = useState<InvestigationEvidenceDraft>(EMPTY_EVIDENCE_DRAFT)
  const evidenceDraftRef = useRef<InvestigationEvidenceDraft>(EMPTY_EVIDENCE_DRAFT)
  const [evidenceDraftVersion, setEvidenceDraftVersion] = useState<number | null>(null)
  const [evidenceFinderOpen, setEvidenceFinderOpen] = useState(false)
  const [evidenceSearch, setEvidenceSearchState] = useState('')
  const [debouncedEvidenceSearch, setDebouncedEvidenceSearch] = useState('')
  const [evidenceSourceFilter, setEvidenceSourceFilterState] =
    useState<InvestigationEvidenceSourceFilter>('all')
  const [evidenceRange, setEvidenceRangeState] =
    useState<InvestigationEvidenceCandidateRange>('7d')
  const [evidenceCandidatePage, setEvidenceCandidatePageState] = useState(1)
  const [evidenceCandidateAsOf, setEvidenceCandidateAsOf] = useState<string | null>(null)
  const [selectedEvidenceCandidate, setSelectedEvidenceCandidate] =
    useState<InvestigationEvidenceCandidate | null>(null)
  const [alertOccurrenceUnavailable, setAlertOccurrenceUnavailable] = useState(false)
  const [memberSearch, setMemberSearch] = useState('')
  const [debouncedMemberSearch, setDebouncedMemberSearch] = useState('')
  const [memberPage, setMemberPage] = useState(1)
  const [evidencePage, setEvidencePage] = useState(1)
  const [notePage, setNotePage] = useState(1)
  const [activityPage, setActivityPage] = useState(1)
  const [conflictNotice, setConflictNotice] = useState<string | null>(null)
  const [successNotice, setSuccessNotice] = useState<string | null>(null)
  const clearEvidenceSelectionState = () => {
    const next = {
      ...evidenceDraftRef.current,
      sourceType: 'item' as const,
      sourceId: '',
    }
    evidenceDraftRef.current = next
    setSelectedEvidenceCandidate(null)
    setEvidenceDraft(next)
    setEvidenceDraftVersion((current) =>
      sameEvidenceDraft(next, EMPTY_EVIDENCE_DRAFT) ? null : current,
    )
  }

  useEffect(() => {
    setOverviewDraft(EMPTY_OVERVIEW_DRAFT)
    setOverviewBaseline(EMPTY_OVERVIEW_DRAFT)
    setOverviewBaselineVersion(null)
    setNoteDraft('')
    setNoteDraftVersion(null)
    setEditingNoteId(null)
    setEditingNoteBody('')
    setEditingNoteBaseline('')
    setEditingNoteVersion(null)
    setEditingNoteInvestigationVersion(null)
    evidenceDraftRef.current = EMPTY_EVIDENCE_DRAFT
    setEvidenceDraft(EMPTY_EVIDENCE_DRAFT)
    setEvidenceDraftVersion(null)
    setEvidenceFinderOpen(false)
    setEvidenceSearchState('')
    setDebouncedEvidenceSearch('')
    setEvidenceSourceFilterState('all')
    setEvidenceRangeState('7d')
    setEvidenceCandidatePageState(1)
    setEvidenceCandidateAsOf(null)
    setSelectedEvidenceCandidate(null)
    setEvidencePage(1)
    setNotePage(1)
  }, [investigationId])

  const detailQuery = useQuery({
    queryKey: detailKey,
    queryFn: () => apiFetch<InvestigationDetail>(`/investigations/${investigationId}`),
    staleTime: 15_000,
    refetchInterval: 60_000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
  })
  const detail = detailQuery.data
  const canAuthor = Boolean(
    currentUserQuery.data &&
      !currentUserQuery.isError &&
      hasRequiredPermissions(
        currentUserQuery.data.access?.permissions ?? [],
        ['write:investigations'],
      ),
  )
  const access = detail
    ? resolveInvestigationAccess(detail, canAuthor)
    : null
  const overviewDirty = !sameOverviewDraft(overviewDraft, overviewBaseline)
  const hasUnsavedChanges =
    overviewDirty ||
    noteDraft.length > 0 ||
    (editingNoteId !== null && editingNoteBody !== editingNoteBaseline) ||
    !sameEvidenceDraft(evidenceDraft, EMPTY_EVIDENCE_DRAFT)
  const confirmDiscardChanges = useUnsavedChangesWarning(
    hasUnsavedChanges,
    'Discard unsaved investigation changes?',
    { ignoreSearchChanges: true },
  )

  useEffect(() => {
    if (!detail || overviewDirty) return
    const next = overviewDraftFromDetail(detail)
    setOverviewDraft(next)
    setOverviewBaseline(next)
    setOverviewBaselineVersion(detail.version)
  }, [detail, overviewDirty])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebouncedMemberSearch(memberSearch.trim())
      setMemberPage(1)
    }, 250)
    return () => window.clearTimeout(timer)
  }, [memberSearch])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebouncedEvidenceSearch(evidenceSearch.trim())
    }, 300)
    return () => window.clearTimeout(timer)
  }, [evidenceSearch])

  const memberCandidatesQuery = useQuery({
    queryKey: ['investigations', 'member-candidates', debouncedMemberSearch, memberPage],
    queryFn: () => {
      const params = new URLSearchParams({
        page: String(memberPage),
        page_size: String(INVESTIGATION_MEMBER_PAGE_SIZE),
      })
      if (debouncedMemberSearch) params.set('q', debouncedMemberSearch)
      return apiFetch<InvestigationMemberCandidateListResponse>(
        `/investigations/member-candidates?${params.toString()}`,
      )
    },
    enabled: Boolean(access?.canManageMembers && activeTab === 'members'),
    placeholderData: (previous) => previous,
    staleTime: 30_000,
  })

  const availableMemberCandidates = useMemo(() => {
    const memberIds = new Set(detail?.members.map((member) => member.user_id) ?? [])
    return (memberCandidatesQuery.data?.users ?? []).filter((candidate) => !memberIds.has(candidate.id))
  }, [detail?.members, memberCandidatesQuery.data?.users])
  const memberCandidateSelectionUnavailable =
    memberSearch.trim() !== debouncedMemberSearch ||
    memberCandidatesQuery.isFetching ||
    memberCandidatesQuery.isPlaceholderData ||
    memberCandidatesQuery.isError

  const localEvidenceSourceAvailability = useMemo(
    () =>
      evidenceSourceAvailability(
        currentUserQuery.data?.access?.permissions ?? [],
        undefined,
        alertOccurrenceUnavailable,
      ),
    [alertOccurrenceUnavailable, currentUserQuery.data?.access?.permissions],
  )
  const requestedEvidenceSourceTypes = useMemo(
    () => candidateEvidenceSourceTypes(
      localEvidenceSourceAvailability,
      evidenceSourceFilter,
      debouncedEvidenceSearch,
    ),
    [debouncedEvidenceSearch, evidenceSourceFilter, localEvidenceSourceAvailability],
  )

  const evidenceQuery = useQuery({
    queryKey: ['investigations', 'evidence', investigationId, evidencePage],
    queryFn: () =>
      apiFetch<InvestigationEvidenceListResponse>(
        `/investigations/${investigationId}/evidence?page=${evidencePage}&page_size=${INVESTIGATION_EVIDENCE_PAGE_SIZE}`,
      ),
    enabled: Boolean(detail && activeTab === 'evidence'),
    staleTime: 15_000,
  })

  const evidenceCandidatesQuery = useQuery({
    queryKey: [
      'investigations',
      'evidence-candidates',
      investigationId,
      debouncedEvidenceSearch,
      requestedEvidenceSourceTypes,
      evidenceRange,
      evidenceCandidatePage,
      evidenceCandidatePage === 1 ? null : evidenceCandidateAsOf,
    ],
    queryFn: ({ signal }) => {
      const candidateRequest = buildEvidenceCandidateRequest({
          investigationId,
          query: debouncedEvidenceSearch,
          sourceTypes: requestedEvidenceSourceTypes,
          range: evidenceRange,
          page: evidenceCandidatePage,
          asOf: evidenceCandidatePage === 1 ? null : evidenceCandidateAsOf,
      })
      return apiFetch<InvestigationEvidenceCandidateListResponse>(
        candidateRequest.path,
        {
          signal,
          method: 'POST',
          body: JSON.stringify(candidateRequest.body),
        },
      )
    },
    enabled: Boolean(
      detail &&
        access?.canWrite &&
        activeTab === 'evidence' &&
        evidenceFinderOpen &&
        evidenceSearchCanRun(debouncedEvidenceSearch) &&
        requestedEvidenceSourceTypes.length > 0,
    ),
    staleTime: 10_000,
  })

  const notesQuery = useQuery({
    queryKey: ['investigations', 'notes', investigationId, notePage],
    queryFn: () =>
      apiFetch<InvestigationNoteListResponse>(
        `/investigations/${investigationId}/notes?page=${notePage}&page_size=${INVESTIGATION_NOTE_PAGE_SIZE}`,
      ),
    enabled: Boolean(detail && activeTab === 'notes'),
    staleTime: 15_000,
  })

  useEffect(() => {
    const total = evidenceQuery.data?.total ?? detail?.evidence_count
    if (total === undefined) return
    const lastPage = investigationCollectionPageCount(total, INVESTIGATION_EVIDENCE_PAGE_SIZE)
    setEvidencePage((current) => Math.min(current, lastPage))
  }, [detail?.evidence_count, evidenceQuery.data?.total])

  useEffect(() => {
    const candidates = evidenceCandidatesQuery.data
    if (!candidates) return
    if (candidates.page === 1) setEvidenceCandidateAsOf(candidates.effective_until)
    const lastPage = candidatePageCount(candidates)
    if (evidenceCandidatePage > lastPage) {
      setEvidenceCandidatePageState(lastPage)
      clearEvidenceSelectionState()
    }
  }, [evidenceCandidatePage, evidenceCandidatesQuery.data])

  useEffect(() => {
    if (
      !selectedEvidenceCandidate ||
      !evidenceCandidatesQuery.data ||
      evidenceCandidatesQuery.isFetching
    ) return
    const refreshed = evidenceCandidatesQuery.data?.candidates.find(
      (candidate) =>
        candidate.source_type === selectedEvidenceCandidate.source_type &&
        candidate.source_id === selectedEvidenceCandidate.source_id,
    )
    if (refreshed && refreshed !== selectedEvidenceCandidate) {
      setSelectedEvidenceCandidate(refreshed)
    } else if (!refreshed) {
      clearEvidenceSelectionState()
    }
  }, [
    evidenceCandidatesQuery.data,
    evidenceCandidatesQuery.isFetching,
    selectedEvidenceCandidate,
  ])

  useEffect(() => {
    const total = notesQuery.data?.total ?? detail?.note_count
    if (total === undefined) return
    const lastPage = investigationCollectionPageCount(total, INVESTIGATION_NOTE_PAGE_SIZE)
    setNotePage((current) => Math.min(current, lastPage))
  }, [detail?.note_count, notesQuery.data?.total])

  const activityQuery = useQuery({
    queryKey: ['investigations', 'activity', investigationId, activityPage],
    queryFn: () =>
      apiFetch<InvestigationActivityListResponse>(
        `/investigations/${investigationId}/activity?page=${activityPage}&page_size=${INVESTIGATION_ACTIVITY_PAGE_SIZE}`,
      ),
    enabled: Boolean(detail && activeTab === 'activity'),
    placeholderData: (previous) => previous,
    staleTime: 15_000,
  })

  const mutation = useMutation({
    mutationKey: ['investigations', 'mutate', investigationId],
    mutationFn: (operation: InvestigationMutationOperation) =>
      executeInvestigationMutation(investigationId, operation),
    onMutate: () => {
      setConflictNotice(null)
      setSuccessNotice(null)
    },
    onSuccess: (updated, operation) => {
      queryClient.setQueryData(detailKey, updated)
      void queryClient.invalidateQueries({ queryKey: ['investigations', 'list'] })
      if (isEvidenceMutation(operation)) {
        const lastPage = investigationCollectionPageCount(
          updated.evidence_count,
          INVESTIGATION_EVIDENCE_PAGE_SIZE,
        )
        setEvidencePage((current) =>
          operation.kind === 'add-evidence' ? 1 : Math.min(current, lastPage),
        )
        void queryClient.invalidateQueries({
          queryKey: ['investigations', 'evidence', investigationId],
        })
        void queryClient.invalidateQueries({
          queryKey: ['investigations', 'evidence-candidates', investigationId],
        })
      }
      if (isNoteMutation(operation)) {
        const lastPage = investigationCollectionPageCount(
          updated.note_count,
          INVESTIGATION_NOTE_PAGE_SIZE,
        )
        setNotePage((current) =>
          operation.kind === 'add-note' ? 1 : Math.min(current, lastPage),
        )
        void queryClient.invalidateQueries({
          queryKey: ['investigations', 'notes', investigationId],
        })
      }
      if (operation.kind === 'update' && isOverviewFieldUpdate(operation.changes)) {
        const nextOverview = overviewDraftFromDetail(updated)
        setOverviewDraft(nextOverview)
        setOverviewBaseline(nextOverview)
        setOverviewBaselineVersion(updated.version)
      }
      resetSuccessfulDraft(operation)
      setSuccessNotice(successMessage(operation))
    },
    onError: (error, operation) => {
      if (isInvestigationVersionConflict(error)) {
        setConflictNotice(
          isRebasableDraftOperation(operation)
            ? 'This investigation changed after you loaded it. Review the latest version, then rebase your preserved draft before retrying.'
            : 'This investigation changed after you loaded it. Refresh and review the latest version before retrying. Your unsaved text has been preserved.',
        )
        void queryClient.invalidateQueries({ queryKey: detailKey, exact: true })
        if (isEvidenceMutation(operation)) {
          void queryClient.invalidateQueries({
            queryKey: ['investigations', 'evidence', investigationId],
          })
        }
        if (isNoteMutation(operation)) {
          void queryClient.invalidateQueries({
            queryKey: ['investigations', 'notes', investigationId],
          })
        }
      }
      if (isAlertOccurrenceUnavailable(error)) setAlertOccurrenceUnavailable(true)
    },
  })

  const resetSuccessfulDraft = (operation: InvestigationMutationOperation) => {
    if (operation.kind === 'add-note') {
      setNoteDraft('')
      setNoteDraftVersion(null)
    }
    if (operation.kind === 'update-note') {
      setEditingNoteId(null)
      setEditingNoteBody('')
      setEditingNoteBaseline('')
      setEditingNoteVersion(null)
      setEditingNoteInvestigationVersion(null)
    }
    if (operation.kind === 'add-evidence') {
      evidenceDraftRef.current = EMPTY_EVIDENCE_DRAFT
      setEvidenceDraft(EMPTY_EVIDENCE_DRAFT)
      setEvidenceDraftVersion(null)
      setSelectedEvidenceCandidate(null)
    }
  }

  const setActiveTab = (tab: typeof activeTab) => {
    const next = new URLSearchParams(searchParams)
    if (tab === 'overview') next.delete('tab')
    else next.set('tab', tab)
    setSearchParams(next, { replace: true })
  }

  const refreshLatest = async () => {
    const result = await detailQuery.refetch()
    if (!result.error) setConflictNotice(null)
  }

  const rebaseLatestDraft = async () => {
    const result = await detailQuery.refetch()
    if (result.error || !result.data) return
    if (mutation.variables?.kind === 'add-evidence') {
      setEvidenceDraftVersion(result.data.version)
    } else if (mutation.variables?.kind === 'add-note') {
      setNoteDraftVersion(result.data.version)
    } else {
      return
    }
    setConflictNotice(null)
    setSuccessNotice('Latest version loaded. Review your preserved draft, then retry.')
  }

  const canRebaseLatestDraft = hasRebasableConflict(conflictNotice, mutation.variables)

  const beginNoteEdit = (noteId: string, noteVersion: number, body: string) => {
    setEditingNoteId(noteId)
    setEditingNoteBody(body)
    setEditingNoteBaseline(body)
    setEditingNoteVersion(noteVersion)
    setEditingNoteInvestigationVersion(detail?.version ?? null)
  }

  const cancelNoteEdit = () => {
    setEditingNoteId(null)
    setEditingNoteBody('')
    setEditingNoteBaseline('')
    setEditingNoteVersion(null)
    setEditingNoteInvestigationVersion(null)
  }

  const updateNoteDraft = (value: string) => {
    setNoteDraft(value)
    setNoteDraftVersion((current) =>
      value.length > 0 ? (current ?? detail?.version ?? null) : null,
    )
  }

  const updateEvidenceDraft = (changes: Partial<InvestigationEvidenceDraft>) => {
    const next = { ...evidenceDraftRef.current, ...changes }
    evidenceDraftRef.current = next
    setEvidenceDraft(next)
    setEvidenceDraftVersion((current) =>
      sameEvidenceDraft(next, EMPTY_EVIDENCE_DRAFT)
        ? null
        : (current ?? detail?.version ?? null),
    )
  }

  const clearEvidenceSelection = () => {
    clearEvidenceSelectionState()
  }

  const setEvidenceCandidatePage = (page: number) => {
    if (page > 1) {
      setEvidenceCandidateAsOf((current) =>
        current ?? evidenceCandidatesQuery.data?.effective_until ?? null,
      )
    }
    setEvidenceCandidatePageState(page)
    clearEvidenceSelection()
  }

  const restartEvidenceCandidateSearch = () => {
    setEvidenceCandidateAsOf(null)
    setEvidenceCandidatePageState(1)
    clearEvidenceSelection()
  }

  const selectEvidenceCandidate = (candidate: InvestigationEvidenceCandidate) => {
    setSelectedEvidenceCandidate(candidate)
    updateEvidenceDraft({
      sourceType: candidate.source_type,
      sourceId: candidate.source_id,
    })
  }

  const setEvidenceSearch = (value: string) => {
    setEvidenceSearchState(value)
    setEvidenceCandidateAsOf(null)
    setEvidenceCandidatePage(1)
  }

  const setEvidenceSourceFilter = (value: InvestigationEvidenceSourceFilter) => {
    setEvidenceSourceFilterState(value)
    setEvidenceCandidateAsOf(null)
    setEvidenceCandidatePage(1)
  }

  const setEvidenceRange = (value: InvestigationEvidenceCandidateRange) => {
    setEvidenceRangeState(value)
    setEvidenceCandidateAsOf(null)
    setEvidenceCandidatePage(1)
  }

  const clearEvidenceDraft = () => {
    evidenceDraftRef.current = EMPTY_EVIDENCE_DRAFT
    setEvidenceDraft(EMPTY_EVIDENCE_DRAFT)
    setEvidenceDraftVersion(null)
    setSelectedEvidenceCandidate(null)
  }

  return {
    access,
    activeTab,
    activityPage,
    activityQuery,
    alertOccurrenceUnavailable,
    availableMemberCandidates,
    beginNoteEdit,
    cancelNoteEdit,
    confirmDiscardChanges,
    conflictNotice,
    canRebaseLatestDraft,
    currentUserQuery,
    detailQuery,
    editingNoteBody,
    editingNoteId,
    evidenceDraft,
    evidenceDraftVersion,
    evidenceCandidatesQuery,
    evidenceFinderOpen,
    evidencePage,
    evidenceQuery,
    evidenceRange,
    evidenceSearch,
    debouncedEvidenceSearch,
    evidenceSourceFilter,
    hasUnsavedChanges,
    memberCandidatesQuery,
    memberCandidateSelectionUnavailable,
    memberPage,
    memberSearch,
    mutation,
    noteDraft,
    noteDraftVersion,
    notePage,
    notesQuery,
    overviewBaseline,
    overviewBaselineVersion,
    overviewDraft,
    overviewDirty,
    refreshLatest,
    rebaseLatestDraft,
    restartEvidenceCandidateSearch,
    requestedEvidenceSourceTypes,
    selectedEvidenceCandidate,
    setActiveTab,
    setActivityPage,
    setEditingNoteBody,
    editingNoteInvestigationVersion,
    editingNoteVersion,
    setEvidenceCandidatePage,
    setEvidenceFinderOpen,
    setEvidencePage,
    setEvidenceRange,
    setEvidenceSearch,
    setEvidenceSourceFilter,
    setMemberPage,
    setMemberSearch,
    setNotePage,
    setOverviewDraft,
    successNotice,
    clearEvidenceDraft,
    selectEvidenceCandidate,
    updateEvidenceDraft,
    updateNoteDraft,
  }
}

export async function executeInvestigationMutation(
  investigationId: string,
  operation: InvestigationMutationOperation,
): Promise<InvestigationDetail> {
  if (!Number.isInteger(operation.expectedVersion) || operation.expectedVersion < 1) {
    throw new Error(
      'The draft investigation version is unavailable. Refresh the investigation before retrying.',
    )
  }
  const expectedVersion = operation.expectedVersion
  const basePath = `/investigations/${investigationId}`

  if (operation.kind === 'update') {
    return apiFetch<InvestigationDetail>(basePath, {
      method: 'PATCH',
      body: JSON.stringify({ ...operation.changes, expected_version: expectedVersion }),
    })
  }
  if (operation.kind === 'add-member') {
    return apiFetch<InvestigationDetail>(`${basePath}/members`, {
      method: 'POST',
      body: JSON.stringify({ user_id: operation.userId, role: operation.role, expected_version: expectedVersion }),
    })
  }
  if (operation.kind === 'update-member') {
    return apiFetch<InvestigationDetail>(`${basePath}/members/${operation.userId}`, {
      method: 'PATCH',
      body: JSON.stringify({ role: operation.role, expected_version: expectedVersion }),
    })
  }
  if (operation.kind === 'remove-member') {
    return apiFetch<InvestigationDetail>(
      `${basePath}/members/${operation.userId}?expected_version=${expectedVersion}`,
      { method: 'DELETE' },
    )
  }
  if (operation.kind === 'add-evidence') {
    return apiFetch<InvestigationDetail>(`${basePath}/evidence`, {
      method: 'POST',
      body: JSON.stringify({
        source_type: operation.sourceType,
        source_id: operation.sourceId,
        note: operation.note.trim() || null,
        expected_version: expectedVersion,
      }),
    })
  }
  if (operation.kind === 'remove-evidence') {
    return apiFetch<InvestigationDetail>(
      `${basePath}/evidence/${operation.evidenceId}?expected_version=${expectedVersion}`,
      { method: 'DELETE' },
    )
  }
  if (operation.kind === 'add-note') {
    return apiFetch<InvestigationDetail>(`${basePath}/notes`, {
      method: 'POST',
      body: JSON.stringify({ body: operation.body, expected_version: expectedVersion }),
    })
  }

  if (!Number.isInteger(operation.noteVersion) || operation.noteVersion < 1) {
    throw new Error('The displayed note version is unavailable. Refresh the note page before retrying.')
  }
  if (operation.kind === 'remove-note') {
    const params = new URLSearchParams({
      expected_note_version: String(operation.noteVersion),
      expected_investigation_version: String(expectedVersion),
    })
    return apiFetch<InvestigationDetail>(`${basePath}/notes/${operation.noteId}?${params.toString()}`, {
      method: 'DELETE',
    })
  }
  return apiFetch<InvestigationDetail>(`${basePath}/notes/${operation.noteId}`, {
    method: 'PATCH',
    body: JSON.stringify({
      body: operation.body,
      expected_note_version: operation.noteVersion,
      expected_investigation_version: expectedVersion,
    }),
  })
}

function overviewDraftFromDetail(detail: InvestigationDetail): InvestigationOverviewDraft {
  return {
    title: detail.title,
    description: detail.description,
    severity: detail.severity,
    visibility: detail.visibility,
    assigneeUserId: detail.assignee_user_id ?? '',
  }
}

function evidenceSearchCanRun(query: string): boolean {
  return query.length === 0 || query.length >= INVESTIGATION_EVIDENCE_MIN_SEARCH_LENGTH
}

function isRebasableDraftOperation(
  operation: InvestigationMutationOperation | undefined,
): boolean {
  return operation?.kind === 'add-evidence' || operation?.kind === 'add-note'
}

function hasRebasableConflict(
  conflictNotice: string | null,
  operation: InvestigationMutationOperation | undefined,
): boolean {
  return Boolean(conflictNotice) && isRebasableDraftOperation(operation)
}

function sameOverviewDraft(left: InvestigationOverviewDraft, right: InvestigationOverviewDraft): boolean {
  return left.title === right.title
    && left.description === right.description
    && left.severity === right.severity
    && left.visibility === right.visibility
    && left.assigneeUserId === right.assigneeUserId
}

function sameEvidenceDraft(
  left: InvestigationEvidenceDraft,
  right: InvestigationEvidenceDraft,
): boolean {
  return (
    left.sourceType === right.sourceType &&
    left.sourceId === right.sourceId &&
    left.note === right.note
  )
}

function isOverviewFieldUpdate(changes: Omit<InvestigationUpdateRequest, 'expected_version'>): boolean {
  return ['title', 'description', 'severity', 'visibility', 'assignee_user_id']
    .some((field) => field in changes)
}

function successMessage(operation: InvestigationMutationOperation): string {
  const labels: Record<InvestigationMutationOperation['kind'], string> = {
    update: 'Investigation updated.',
    'add-member': 'Member added.',
    'update-member': 'Member role updated.',
    'remove-member': 'Member removed.',
    'add-evidence': 'Evidence added.',
    'remove-evidence': 'Evidence removed.',
    'add-note': 'Note added.',
    'update-note': 'Note updated.',
    'remove-note': 'Note removed.',
  }
  return labels[operation.kind]
}

function isEvidenceMutation(operation: InvestigationMutationOperation): boolean {
  return operation.kind === 'add-evidence' || operation.kind === 'remove-evidence'
}

function isNoteMutation(operation: InvestigationMutationOperation): boolean {
  return operation.kind === 'add-note'
    || operation.kind === 'update-note'
    || operation.kind === 'remove-note'
}

export type InvestigationDetailController = ReturnType<typeof useInvestigationDetail>
