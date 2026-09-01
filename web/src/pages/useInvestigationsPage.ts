import { FormEvent, useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useSearchParams } from 'react-router-dom'

import { apiFetch } from '../api/client'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { useUnsavedChangesWarning } from '../hooks/useUnsavedChangesWarning'
import { hasRequiredPermissions } from '../workspace/workspaceModel'
import type {
  InvestigationCreateRequest,
  InvestigationDetail,
  InvestigationListFilters,
  InvestigationListResponse,
  InvestigationSeverity,
  InvestigationStatus,
  InvestigationVisibility,
} from '../types/investigations'
import {
  buildInvestigationListPath,
  readInvestigationListFilters,
  writeInvestigationListFilters,
} from './investigationPageModel'

interface InvestigationCreateDraft {
  title: string
  description: string
  severity: InvestigationSeverity
  visibility: InvestigationVisibility
  assignToMe: boolean
}

const EMPTY_CREATE_DRAFT: InvestigationCreateDraft = {
  title: '',
  description: '',
  severity: 'medium',
  visibility: 'private',
  assignToMe: true,
}

export function useInvestigationsPage() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const currentUserQuery = useCurrentUser()
  const [searchParams, setSearchParams] = useSearchParams()
  const filters = useMemo(() => readInvestigationListFilters(searchParams), [searchParams])
  const [searchDraft, setSearchDraft] = useState(filters.query)
  const [createOpen, setCreateOpen] = useState(false)
  const [createDraft, setCreateDraft] = useState<InvestigationCreateDraft>(EMPTY_CREATE_DRAFT)
  const [createdInvestigationId, setCreatedInvestigationId] = useState<string | null>(null)

  useEffect(() => setSearchDraft(filters.query), [filters.query])

  const investigationsQuery = useQuery({
    queryKey: ['investigations', 'list', filters],
    queryFn: () => apiFetch<InvestigationListResponse>(buildInvestigationListPath(filters)),
    placeholderData: (previous) => previous,
    staleTime: 15_000,
    refetchOnWindowFocus: true,
  })

  const createInvestigation = useMutation({
    mutationKey: ['investigations', 'create'],
    mutationFn: (payload: InvestigationCreateRequest) =>
      apiFetch<InvestigationDetail>('/investigations', {
        method: 'POST',
        body: JSON.stringify(payload),
      }),
    onSuccess: (investigation) => {
      queryClient.setQueryData(['investigations', 'detail', investigation.id], investigation)
      void queryClient.invalidateQueries({ queryKey: ['investigations', 'list'] })
      setCreateDraft(EMPTY_CREATE_DRAFT)
      setCreateOpen(false)
      setCreatedInvestigationId(investigation.id)
    },
  })

  useEffect(() => {
    if (!createdInvestigationId) return
    const navigationTimer = window.setTimeout(() => {
      setCreatedInvestigationId(null)
      navigate(`/investigations/${createdInvestigationId}`)
    }, 0)
    return () => window.clearTimeout(navigationTimer)
  }, [createdInvestigationId, navigate])

  const canCreate =
    !currentUserQuery.isError &&
    hasRequiredPermissions(
      currentUserQuery.data?.access?.permissions ?? [],
      ['write:investigations'],
    )
  const createDraftDirty =
    createDraft.title !== EMPTY_CREATE_DRAFT.title ||
    createDraft.description !== EMPTY_CREATE_DRAFT.description ||
    createDraft.severity !== EMPTY_CREATE_DRAFT.severity ||
    createDraft.visibility !== EMPTY_CREATE_DRAFT.visibility ||
    createDraft.assignToMe !== EMPTY_CREATE_DRAFT.assignToMe
  const confirmDiscardCreateDraft = useUnsavedChangesWarning(
    createDraftDirty,
    'You have an unfinished investigation draft. Leave without creating it?',
    { ignoreSearchChanges: true },
  )

  const updateFilters = (changes: Partial<InvestigationListFilters>) => {
    const next = { ...filters, ...changes }
    if (!('page' in changes)) next.page = 1
    setSearchParams(writeInvestigationListFilters(next))
  }

  const toggleStatus = (status: InvestigationStatus) => {
    const statuses = filters.statuses.includes(status)
      ? filters.statuses.filter((entry) => entry !== status)
      : [...filters.statuses, status]
    updateFilters({ statuses })
  }

  const toggleSeverity = (severity: InvestigationSeverity) => {
    const severities = filters.severities.includes(severity)
      ? filters.severities.filter((entry) => entry !== severity)
      : [...filters.severities, severity]
    updateFilters({ severities })
  }

  const submitSearch = (event: FormEvent) => {
    event.preventDefault()
    updateFilters({ query: searchDraft })
  }

  const clearFilters = () => {
    setSearchDraft('')
    setSearchParams(new URLSearchParams())
  }

  const submitCreate = (event: FormEvent) => {
    event.preventDefault()
    const title = createDraft.title.trim()
    if (!title || !currentUserQuery.data) return
    createInvestigation.mutate({
      title,
      description: createDraft.description.trim(),
      severity: createDraft.severity,
      visibility: createDraft.visibility,
      assignee_user_id: createDraft.assignToMe ? currentUserQuery.data.id : null,
    })
  }

  return {
    canCreate,
    clearFilters,
    confirmDiscardCreateDraft,
    createDraft,
    createInvestigation,
    createOpen,
    currentUserQuery,
    filters,
    investigationsQuery,
    searchDraft,
    setCreateDraft,
    setCreateOpen,
    setSearchDraft,
    submitCreate,
    submitSearch,
    toggleSeverity,
    toggleStatus,
    updateFilters,
  }
}

export type InvestigationsPageController = ReturnType<typeof useInvestigationsPage>
