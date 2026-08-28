import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'

import { apiFetch } from '../api/client'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import type {
  InvestigationActivityListResponse,
  InvestigationDetail,
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
  const [alertOccurrenceUnavailable, setAlertOccurrenceUnavailable] = useState(false)
  const [memberSearch, setMemberSearch] = useState('')
  const [debouncedMemberSearch, setDebouncedMemberSearch] = useState('')
  const [memberPage, setMemberPage] = useState(1)
  const [evidencePage, setEvidencePage] = useState(1)
  const [notePage, setNotePage] = useState(1)
  const [activityPage, setActivityPage] = useState(1)
  const [conflictNotice, setConflictNotice] = useState<string | null>(null)
  const [successNotice, setSuccessNotice] = useState<string | null>(null)

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
  const access = detail
    ? resolveInvestigationAccess(
        detail,
        currentUserQuery.isError ? undefined : currentUserQuery.data?.role,
      )
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

  const evidenceQuery = useQuery({
    queryKey: ['investigations', 'evidence', investigationId, evidencePage],
    queryFn: () =>
      apiFetch<InvestigationEvidenceListResponse>(
        `/investigations/${investigationId}/evidence?page=${evidencePage}&page_size=${INVESTIGATION_EVIDENCE_PAGE_SIZE}`,
      ),
    enabled: Boolean(detail && activeTab === 'evidence'),
    staleTime: 15_000,
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
          'This investigation changed after you loaded it. Refresh and review the latest version before retrying. Your unsaved text has been preserved.',
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
    currentUserQuery,
    detailQuery,
    editingNoteBody,
    editingNoteId,
    evidenceDraft,
    evidenceDraftVersion,
    evidencePage,
    evidenceQuery,
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
    setActiveTab,
    setActivityPage,
    setEditingNoteBody,
    editingNoteInvestigationVersion,
    editingNoteVersion,
    setEvidencePage,
    setMemberPage,
    setMemberSearch,
    setNotePage,
    setOverviewDraft,
    successNotice,
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
